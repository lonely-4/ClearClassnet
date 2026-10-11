"""Client-side HTTP/1.1 session: parse, dispatch, and write the result."""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import h11

from clearclassnet.event.body import BodyBuffer
from clearclassnet.event.headers import HeaderMap
from clearclassnet.event.http import (
    ConnectEvent,
    Decision,
    HttpFlow,
    RequestEvent,
    RequestMessage,
    ResponseEvent,
    ResponseMessage,
    split_host_port,
)
from clearclassnet.proxy.errors import (
    ClientClosed,
    IdleClose,
    ProtocolFailure,
    ReadTimeout,
    UpstreamError,
    UpstreamTimeout,
)
from clearclassnet.proxy.framing import (
    connection_requested_close,
    forwarding_headers,
    pending_bytes,
    reason_bytes,
)
from clearclassnet.proxy.io import next_event, read_body
from clearclassnet.proxy.tunnel import relay
from clearclassnet.proxy.upstream import UpstreamSession

if TYPE_CHECKING:
    from clearclassnet.proxy.engine import ProxyEngine

log = logging.getLogger(__name__)

_NO_BODY = frozenset({204, 304})


class ClientConnection:
    """One incoming TCP connection, including the HTTP conversation after CONNECT."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        engine: ProxyEngine,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.engine = engine
        self.upstream = UpstreamSession(engine)
        peer = writer.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= 2:
            self.client_host = str(peer[0])
            self.client_port = int(peer[1])
        else:
            self.client_host = ""
            self.client_port = 0

    @property
    def timeout(self) -> float:
        return self.engine.config.proxy.timeout

    async def run(self) -> None:
        try:
            await self._serve(default_scheme="http", default_port=80)
        except IdleClose:
            return
        except (ClientClosed, ProtocolFailure, ReadTimeout) as exc:
            log.debug("client session ended: %s", exc)
        finally:
            await self.upstream.close()
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, OSError, RuntimeError):
                return

    async def _serve(self, *, default_scheme: str, default_port: int) -> None:
        protocol = h11.Connection(h11.SERVER)
        while True:
            try:
                event = await next_event(self.reader, protocol, self.timeout, idle=True)
            except (IdleClose, ClientClosed):
                return
            if not isinstance(event, h11.Request):
                raise ProtocolFailure(f"expected a request, got {type(event).__name__}")
            keep = await self._handle_request(
                protocol,
                event,
                default_scheme=default_scheme,
                default_port=default_port,
            )
            if not keep:
                return
            if protocol.our_state is h11.DONE and protocol.their_state is h11.DONE:
                try:
                    protocol.start_next_cycle()
                except h11.LocalProtocolError:
                    return
            else:
                return

    async def _handle_request(
        self,
        protocol: h11.Connection,
        event: h11.Request,
        *,
        default_scheme: str,
        default_port: int,
    ) -> bool:
        if protocol.they_are_waiting_for_100_continue:
            self.writer.write(
                protocol.send(h11.InformationalResponse(status_code=100, headers=[]))
            )
            await self.writer.drain()
        body = await read_body(
            self.reader,
            protocol,
            self.engine.config.proxy.body_limit,
            self.timeout,
        )
        headers = HeaderMap.from_h11(event.headers)
        method = event.method.decode("ascii", errors="replace").upper()
        try:
            if method == "CONNECT":
                await self._handle_connect(protocol, event.target.decode("latin-1"), headers)
                body.close()
                return False
            message = _request_message(
                event,
                headers,
                body,
                default_scheme=default_scheme,
                default_port=default_port,
            )
        except ProtocolFailure as exc:
            body.close()
            await self._write_error(protocol, 400, str(exc).encode("utf-8"), method="GET")
            return False

        flow = HttpFlow(
            request=message,
            client_host=self.client_host,
            client_port=self.client_port,
        )
        request_event = RequestEvent(flow)
        log.debug("%s %s", request_event.method, request_event.url)
        try:
            await self.engine.dispatch(request_event)
            synthetic = request_event.synthetic_response()
            client_close = connection_requested_close(headers, message.http_version)
            if synthetic is not None:
                status, response_headers, payload = synthetic
                return await self._write_response(
                    protocol,
                    status,
                    response_headers,
                    payload,
                    method=message.method,
                    force_close=client_close,
                )
            try:
                response = await self.upstream.exchange(message)
            except UpstreamTimeout:
                await self._write_error(protocol, 504, b"Gateway Timeout", method=message.method)
                return False
            except UpstreamError as exc:
                log.debug("upstream error: %s", exc)
                await self._write_error(protocol, 502, b"Bad Gateway", method=message.method)
                return False
            flow.response = response
            response_event = ResponseEvent(flow)
            await self.engine.dispatch(response_event)
            if response_event.status == 101:
                await self._upgrade(protocol, response)
                return False
            upstream_close = connection_requested_close(response.headers, response.http_version)
            if upstream_close:
                await self.upstream.close()
            return await self._write_response(
                protocol,
                response_event.status,
                response_event.headers,
                response.body,
                method=message.method,
                force_close=client_close,
            )
        finally:
            body.close()
            if flow.response is not None:
                flow.response.body.close()

    async def _handle_connect(
        self,
        protocol: h11.Connection,
        target: str,
        headers: HeaderMap,
    ) -> None:
        try:
            host, port = split_host_port(target, 443)
        except ValueError:
            await self._write_error(protocol, 400, b"Bad CONNECT target", method="CONNECT")
            return
        event = ConnectEvent(
            host,
            port,
            client_address=(self.client_host, self.client_port),
        )
        await self.engine.dispatch(event)
        if event.decision is Decision.REJECT:
            await self._write_error(
                protocol,
                event.reject_status,
                event.reject_body or b"CONNECT rejected",
                method="CONNECT",
            )
            return
        if event.decision is Decision.TUNNEL:
            await self._tunnel(protocol, host, port)
            return
        await self._intercept(protocol, host, port, headers)

    async def _tunnel(self, protocol: h11.Connection, host: str, port: int) -> None:
        try:
            upstream_reader, upstream_writer = await self._open_tunnel(host, port)
        except UpstreamTimeout:
            await self._write_error(protocol, 504, b"Gateway Timeout", method="CONNECT")
            return
        except UpstreamError:
            await self._write_error(protocol, 502, b"Bad Gateway", method="CONNECT")
            return
        self._send_established(protocol)
        await self.writer.drain()
        try:
            await relay(
                self.reader,
                self.writer,
                upstream_reader,
                upstream_writer,
                to_right=pending_bytes(protocol),
            )
        finally:
            upstream_writer.close()

    async def _intercept(
        self,
        protocol: h11.Connection,
        host: str,
        port: int,
        _headers: HeaderMap,
    ) -> None:
        self._send_established(protocol)
        await self.writer.drain()
        try:
            await self.writer.start_tls(self.engine.ca.client_context(host))
        except (ConnectionError, OSError, ssl.SSLError) as exc:
            log.debug("client TLS handshake failed: %s", exc)
            return
        await self._serve(default_scheme="https", default_port=port)

    def _send_established(self, protocol: h11.Connection) -> None:
        # A 2xx CONNECT response switches protocols inside h11. The header block
        # is the whole message; EndOfMessage is not a legal follow-up event.
        self.writer.write(
            protocol.send(
                h11.Response(
                    status_code=200,
                    headers=[],
                    reason=b"Connection Established",
                )
            )
        )

    async def _open_tunnel(
        self,
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(host, port),
                self.timeout,
            )
        except TimeoutError as exc:
            raise UpstreamTimeout(host, port) from exc
        except OSError as exc:
            raise UpstreamError(host, port, str(exc)) from exc

    async def _upgrade(self, protocol: h11.Connection, response: ResponseMessage) -> None:
        headers = forwarding_headers(response.headers, body_size=None)
        headers["Connection"] = "Upgrade"
        if "Upgrade" not in headers:
            headers["Upgrade"] = "websocket"
        self.writer.write(
            protocol.send(
                h11.InformationalResponse(
                    status_code=101,
                    headers=headers.as_bytes(),
                    reason=b"Switching Protocols",
                )
            )
        )
        await self.writer.drain()
        upstream_reader, upstream_writer = self.upstream.detach()
        try:
            await relay(
                self.reader,
                self.writer,
                upstream_reader,
                upstream_writer,
                to_left=response.trailing,
            )
        finally:
            upstream_writer.close()

    async def _write_response(
        self,
        protocol: h11.Connection,
        status: int,
        headers: HeaderMap,
        body: bytes | BodyBuffer,
        *,
        method: str,
        force_close: bool,
    ) -> bool:
        send_body = method != "HEAD" and status not in _NO_BODY and status >= 200
        declare_length = send_body or method == "HEAD"
        size = len(body) if isinstance(body, bytes) else body.size
        outbound = forwarding_headers(
            headers,
            body_size=size if declare_length and status not in _NO_BODY else None,
        )
        if force_close:
            outbound["Connection"] = "close"
        self.writer.write(
            protocol.send(
                h11.Response(
                    status_code=status,
                    headers=outbound.as_bytes(),
                    reason=reason_bytes(status),
                )
            )
        )
        if send_body:
            chunks: list[bytes]
            if isinstance(body, bytes):
                chunks = [body] if body else []
            elif isinstance(body, BodyBuffer):
                chunks = list(body.iter_chunks())
            else:
                raise TypeError(f"unsupported body type: {type(body).__name__}")
            for chunk in chunks:
                if chunk:
                    self.writer.write(protocol.send(h11.Data(data=chunk)))
        self.writer.write(protocol.send(h11.EndOfMessage()))
        await self.writer.drain()
        return (
            not force_close
            and protocol.our_state is h11.DONE
            and protocol.their_state is h11.DONE
        )

    async def _write_error(
        self,
        protocol: h11.Connection,
        status: int,
        payload: bytes,
        *,
        method: str,
    ) -> None:
        try:
            await self._write_response(
                protocol,
                status,
                HeaderMap([("Content-Type", "text/plain; charset=utf-8")]),
                payload,
                method=method,
                force_close=True,
            )
        except (h11.LocalProtocolError, ConnectionError, OSError):
            return


def _request_message(
    event: h11.Request,
    headers: HeaderMap,
    body: BodyBuffer,
    *,
    default_scheme: str,
    default_port: int,
) -> RequestMessage:
    method = event.method.decode("ascii", errors="replace").upper()
    target = event.target.decode("latin-1")
    version = event.http_version.decode("ascii")
    parsed = urlsplit(target)
    if parsed.scheme and parsed.hostname:
        scheme = parsed.scheme
        host = parsed.hostname
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        query = parsed.query
    else:
        host_value = headers.get("Host")
        if not host_value:
            raise ProtocolFailure("missing Host header")
        try:
            host, port = split_host_port(host_value, default_port)
        except ValueError as exc:
            raise ProtocolFailure("invalid Host header") from exc
        scheme = default_scheme
        path, _, query = target.partition("?")
        if not path.startswith("/"):
            path = "/" + path
    return RequestMessage(
        method=method,
        scheme=scheme,
        host=host,
        port=port,
        path=path or "/",
        query=query,
        headers=headers,
        body=body,
        http_version=version,
    )
