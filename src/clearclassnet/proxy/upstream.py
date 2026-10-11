"""Upstream HTTP/1.1 client used to forward one exchange."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import h11

from clearclassnet.event.body import BodyBuffer
from clearclassnet.event.headers import HeaderMap
from clearclassnet.event.http import RequestMessage, ResponseMessage
from clearclassnet.proxy.errors import (
    ClientClosed,
    ProtocolFailure,
    ReadTimeout,
    UpstreamError,
    UpstreamTimeout,
)
from clearclassnet.proxy.framing import forwarding_headers, pending_bytes
from clearclassnet.proxy.io import next_event

if TYPE_CHECKING:
    from clearclassnet.proxy.engine import ProxyEngine


class UpstreamSession:
    """One origin connection, reused while h11 says the cycle can restart."""

    def __init__(self, engine: ProxyEngine) -> None:
        self._engine = engine
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._protocol: h11.Connection | None = None
        self._key: tuple[str, int, bool] | None = None

    async def exchange(self, message: RequestMessage) -> ResponseMessage:
        try:
            protocol = await self._ensure(message)
            writer = self.writer
            assert writer is not None
            headers = forwarding_headers(
                message.headers,
                body_size=message.body.size,
                host=message.host,
                port=message.port,
                scheme=message.scheme,
            )
            writer.write(
                protocol.send(
                    h11.Request(
                        method=message.method.encode("ascii"),
                        target=message.target_path.encode("latin-1"),
                        headers=headers.as_bytes(),
                    )
                )
            )
            for chunk in message.body.iter_chunks():
                writer.write(protocol.send(h11.Data(data=chunk)))
            writer.write(protocol.send(h11.EndOfMessage()))
            await writer.drain()
            return await self._read_response(protocol)
        except UpstreamError:
            await self.close()
            raise
        except (
            OSError,
            h11.LocalProtocolError,
            ProtocolFailure,
            ClientClosed,
            ReadTimeout,
        ) as exc:
            host, port = message.host, message.port
            await self.close()
            if isinstance(exc, ReadTimeout | TimeoutError):
                raise UpstreamTimeout(host, port) from exc
            raise UpstreamError(host, port, str(exc)) from exc

    def detach(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Hand the live socket to a byte relay and forget it here."""
        reader, writer = self.reader, self.writer
        self.reader = None
        self.writer = None
        self._protocol = None
        self._key = None
        if reader is None or writer is None:
            raise UpstreamError("", 0, "upstream socket is not connected")
        return reader, writer

    async def close(self) -> None:
        writer = self.writer
        self.reader = None
        self.writer = None
        self._protocol = None
        self._key = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError, RuntimeError):
            return

    async def _ensure(self, message: RequestMessage) -> h11.Connection:
        use_tls = message.scheme == "https"
        key = (message.host, message.port, use_tls)
        protocol = self._protocol
        if (
            self._key == key
            and protocol is not None
            and self.writer is not None
            and protocol.our_state is h11.DONE
            and protocol.their_state is h11.DONE
        ):
            protocol.start_next_cycle()
            return protocol
        await self.close()
        timeout = self._engine.config.proxy.timeout
        try:
            reader, writer = await asyncio.wait_for(self._open(message, use_tls), timeout)
        except TimeoutError as exc:
            raise UpstreamTimeout(message.host, message.port) from exc
        except OSError as exc:
            raise UpstreamError(message.host, message.port, str(exc)) from exc
        self.reader = reader
        self.writer = writer
        self._protocol = h11.Connection(h11.CLIENT)
        self._key = key
        return self._protocol

    async def _open(
        self,
        message: RequestMessage,
        use_tls: bool,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if not use_tls:
            return await asyncio.open_connection(message.host, message.port)
        context = self._engine.upstream_ssl_context(message.host)
        return await asyncio.open_connection(
            message.host,
            message.port,
            ssl=context,
            server_hostname=message.host,
        )

    async def _read_response(self, protocol: h11.Connection) -> ResponseMessage:
        reader = self.reader
        assert reader is not None
        timeout = self._engine.config.proxy.timeout
        while True:
            event = await next_event(reader, protocol, timeout, idle=False)
            if isinstance(event, h11.InformationalResponse):
                if event.status_code == 101:
                    return ResponseMessage(
                        status=101,
                        headers=HeaderMap.from_h11(event.headers),
                        body=BodyBuffer(0),
                        http_version=event.http_version.decode("ascii"),
                        trailing=pending_bytes(protocol),
                    )
                continue
            if isinstance(event, h11.Response):
                body = BodyBuffer(self._engine.config.proxy.body_limit)
                while True:
                    part = await next_event(reader, protocol, timeout, idle=False)
                    if isinstance(part, h11.Data):
                        body.append(part.data)
                        continue
                    if isinstance(part, h11.EndOfMessage):
                        return ResponseMessage(
                            status=event.status_code,
                            headers=HeaderMap.from_h11(event.headers),
                            body=body,
                            http_version=event.http_version.decode("ascii"),
                        )
                    raise UpstreamError("", 0, f"unexpected response event {type(part).__name__}")
            raise UpstreamError("", 0, f"unexpected response event {type(event).__name__}")
