import asyncio
import ssl

import pytest

from clearclassnet.dispatch.matcher import on_connect, on_request, on_response
from clearclassnet.dispatch.rule import Rule
from clearclassnet.event.errors import BodyUnavailable
from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent
from clearclassnet.proxy.engine import ProxyEngine
from clearclassnet.tls.ca import CertificateAuthority
from tests.helpers import Origin, open_proxy, proxy_config, read_response

_SWITCHING = (
    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\nHELLO"
)


@pytest.mark.asyncio
async def test_plain_request_is_forwarded_and_rewritten(tmp_path) -> None:
    origin = Origin()
    port = await origin.start()
    seen: list[str] = []

    @on_request(Rule.path("/hello"))
    async def rewrite(event: RequestEvent) -> None:
        seen.append(event.url)
        event.headers["X-From-Plugin"] = "yes"
        event.flow.meta["id"] = "42"

    @on_response(Rule.path("/hello"), block=False)
    async def stamp(event: ResponseEvent) -> None:
        assert event.meta["id"] == "42"
        event.rewrite(text=(await event.text()) + event.meta["id"])

    engine = ProxyEngine(proxy_config(), CertificateAuthority(tmp_path))
    proxy_port = await open_proxy(engine)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        target = f"http://127.0.0.1:{port}/hello"
        writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        await writer.drain()
        status, _headers, body = await read_response(reader)
        writer.close()
        await writer.wait_closed()
    finally:
        await engine.close()
        await origin.close()

    assert status == 200
    assert body == b"ok42"
    assert seen == [target]
    head, _payload = origin.requests[0]
    assert "x-from-plugin: yes" in head.lower()


@pytest.mark.asyncio
async def test_reject_and_respond_do_not_contact_origin(tmp_path) -> None:
    origin = Origin()
    port = await origin.start()

    @on_request(Rule.path("/blocked"))
    async def block(event: RequestEvent) -> None:
        event.reject(status=403, body=b"no")

    @on_request(Rule.path("/fake"))
    async def fake(event: RequestEvent) -> None:
        event.respond(status=200, json={"ok": True})

    engine = ProxyEngine(proxy_config(), CertificateAuthority(tmp_path))
    proxy_port = await open_proxy(engine)
    try:
        status, body = await _proxy_get(proxy_port, port, "/blocked")
        assert status == 403
        assert body == b"no"
        status, body = await _proxy_get(proxy_port, port, "/fake")
        assert status == 200
        assert body == b'{"ok":true}'
    finally:
        await engine.close()
        await origin.close()
    assert origin.connections == 0


@pytest.mark.asyncio
async def test_post_rewrite_chunked_keepalive_and_large_body(tmp_path) -> None:
    origin = Origin(
        responses=[
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nB",
        ]
    )
    port = await origin.start()
    notes: list[str] = []

    @on_request(Rule.all(), block=False)
    async def watch(event: RequestEvent) -> None:
        if event.path == "/small":
            payload = await event.json()
            payload["added"] = 1
            event.rewrite(json=payload)
        else:
            with pytest.raises(BodyUnavailable):
                await event.bytes()
            notes.append("streamed")

    config = proxy_config(body_limit=16)
    engine = ProxyEngine(config, CertificateAuthority(tmp_path))
    proxy_port = await open_proxy(engine)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        small = b'{"n":1}'
        writer.write(
            (
                f"POST http://127.0.0.1:{port}/small HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(small)}\r\n"
                "\r\n"
            ).encode()
            + small
        )
        large = b"y" * 40
        writer.write(
            (
                f"POST http://127.0.0.1:{port}/large HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Content-Length: {len(large)}\r\n"
                "\r\n"
            ).encode()
            + large
        )
        await writer.drain()
        first = await read_response(reader)
        second = await read_response(reader)
        writer.close()
        await writer.wait_closed()
    finally:
        await engine.close()
        await origin.close()

    assert first[2] == b"hello"
    assert second[2] == b"B"
    assert notes == ["streamed"]
    assert origin.connections == 1
    assert b'"added":1' in origin.requests[0][1]
    assert origin.requests[1][1] == large


@pytest.mark.asyncio
async def test_upstream_errors_map_to_502_and_504(tmp_path) -> None:
    hanging = await asyncio.start_server(_hang, "127.0.0.1", 0)
    resetting = await asyncio.start_server(_reset, "127.0.0.1", 0)
    hang_port = hanging.sockets[0].getsockname()[1]
    reset_port = resetting.sockets[0].getsockname()[1]
    engine = ProxyEngine(proxy_config(timeout=0.2), CertificateAuthority(tmp_path))
    proxy_port = await open_proxy(engine)
    try:
        missing, _body = await _proxy_get(proxy_port, reset_port, "/")
        slow, _slow_body = await _proxy_get(proxy_port, hang_port, "/")
    finally:
        await engine.close()
        hanging.close()
        resetting.close()
        await hanging.wait_closed()
        await resetting.wait_closed()
    assert missing == 502
    assert slow == 504


@pytest.mark.asyncio
async def test_connect_tunnel_reject_and_intercept(tmp_path) -> None:
    upstream_ca = CertificateAuthority(tmp_path / "upstream")
    mitm_ca = CertificateAuthority(tmp_path / "mitm")
    origin = Origin(
        responses=[b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndone"]
    )
    origin_port = await origin.start(ssl=upstream_ca.context_for("127.0.0.1"))
    mode = {"value": "tunnel"}

    @on_connect(Rule.all())
    async def decide(event: ConnectEvent) -> None:
        if mode["value"] == "reject":
            event.reject(status=403, body=b"denied")
        elif mode["value"] == "tunnel":
            event.tunnel()
        else:
            event.intercept()

    trust_upstream = ssl.create_default_context()
    trust_upstream.load_verify_locations(cafile=str(upstream_ca.cert_path))
    engine = ProxyEngine(
        proxy_config(),
        mitm_ca,
        upstream_ssl=trust_upstream,
    )
    proxy_port = await open_proxy(engine)
    try:
        tunneled = await _connect_get(
            proxy_port,
            origin_port,
            trust_upstream,
        )
        assert tunneled == b"done"
        mode["value"] = "reject"
        rejected = await _connect_status(proxy_port, origin_port)
        assert rejected == 403
        mode["value"] = "intercept"
        trust_mitm = ssl.create_default_context()
        trust_mitm.load_verify_locations(cafile=str(mitm_ca.cert_path))
        trust_mitm.set_alpn_protocols(["http/1.1"])
        origin.responses.append(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nmitmd"
        )
        intercepted = await _connect_get(proxy_port, origin_port, trust_mitm)
        assert intercepted == b"mitmd"
    finally:
        await engine.close()
        await origin.close()


@pytest.mark.asyncio
async def test_websocket_upgrade_is_relayed(tmp_path) -> None:
    origin = Origin(responses=[_SWITCHING])
    port = await origin.start()
    engine = ProxyEngine(proxy_config(), CertificateAuthority(tmp_path))
    proxy_port = await open_proxy(engine)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(
            (
                f"GET http://127.0.0.1:{port}/ws HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "\r\n"
            ).encode()
        )
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        payload = await asyncio.wait_for(reader.read(5), 2)
        writer.close()
    finally:
        await engine.close()
        await origin.close()
    assert b"101" in head
    assert payload == b"HELLO"


async def _proxy_get(proxy_port: int, origin_port: int, path: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(
        (
            f"GET http://127.0.0.1:{origin_port}{path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{origin_port}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode()
    )
    await writer.drain()
    status, _headers, body = await read_response(reader)
    writer.close()
    await writer.wait_closed()
    return status, body


async def _connect_get(proxy_port: int, origin_port: int, context: ssl.SSLContext) -> bytes:
    reader, writer = await _connected(proxy_port, origin_port)
    await writer.start_tls(context, server_hostname="127.0.0.1")
    writer.write(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    await writer.drain()
    _status, _headers, body = await read_response(reader)
    writer.close()
    await writer.wait_closed()
    return body


async def _connect_status(proxy_port: int, origin_port: int) -> int:
    reader, writer = await _connected(proxy_port, origin_port, read=False)
    status, _headers, _body = await read_response(reader)
    writer.close()
    await writer.wait_closed()
    return status


async def _connected(
    proxy_port: int,
    origin_port: int,
    *,
    read: bool = True,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    host = f"127.0.0.1:{origin_port}"
    writer.write(f"CONNECT {host} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    await writer.drain()
    if read:
        await reader.readuntil(b"\r\n\r\n")
    return reader, writer


async def _hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.read()
    writer.close()


async def _reset(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.close()
