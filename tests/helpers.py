"""Helpers shared by proxy tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from clearclassnet.config import Config, ProxySettings
from clearclassnet.event.body import BodyBuffer
from clearclassnet.event.headers import HeaderMap
from clearclassnet.event.http import HttpFlow, RequestEvent, RequestMessage
from clearclassnet.proxy.engine import ProxyEngine


def request_event(
    *,
    method: str = "GET",
    scheme: str = "http",
    host: str = "example.com",
    port: int = 80,
    path: str = "/",
    query: str = "",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    limit: int = 1024 * 1024,
) -> RequestEvent:
    buffer = BodyBuffer(limit)
    buffer.append(body)
    header_map = HeaderMap((headers or {"Host": host}).items())
    message = RequestMessage(
        method=method,
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        query=query,
        headers=header_map,
        body=buffer,
    )
    return RequestEvent(HttpFlow(request=message, client_host="127.0.0.1", client_port=1))


@dataclass
class Origin:
    """Minimal HTTP origin that records requests and writes scripted responses."""

    responses: list[bytes] = field(default_factory=list)
    requests: list[tuple[str, bytes]] = field(default_factory=list)
    connections: int = 0
    server: asyncio.Server | None = None

    async def start(self, ssl: object | None = None) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=ssl)
        assert self.server.sockets is not None
        return int(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        buffer = b""
        try:
            while True:
                message = await _read_http_message(reader, buffer)
                if message is None:
                    return
                head, body, buffer = message
                self.requests.append((head.decode("latin-1"), body))
                payload = (
                    self.responses.pop(0)
                    if self.responses
                    else b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
                )
                writer.write(payload)
                await writer.drain()
                if b"connection: close" in head.lower():
                    return
        finally:
            writer.close()


async def read_response(reader: asyncio.StreamReader) -> tuple[int, dict[str, str], bytes]:
    message = await _read_http_message(reader, b"")
    if message is None:
        raise AssertionError("connection closed before a response")
    head, body, _rest = message
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.lower()] = value.strip()
    return status, headers, body


async def open_proxy(engine: ProxyEngine) -> int:
    server = await engine.listen("127.0.0.1", 0)
    sockets = server.sockets
    assert sockets
    return int(sockets[0].getsockname()[1])


def proxy_config(**overrides: object) -> Config:
    settings = {"host": "127.0.0.1", "port": 0, "body_limit": 1024 * 1024, "timeout": 2.0}
    settings.update(overrides)
    return Config(proxy=ProxySettings(**settings))  # type: ignore[arg-type]


async def _read_http_message(
    reader: asyncio.StreamReader,
    buffer: bytes,
) -> tuple[bytes, bytes, bytes] | None:
    while b"\r\n\r\n" not in buffer:
        chunk = await reader.read(65536)
        if not chunk:
            return None
        buffer += chunk
    head, _, rest = buffer.partition(b"\r\n\r\n")
    header_lines = head.decode("latin-1").split("\r\n")[1:]
    if any(
        line.lower().startswith("transfer-encoding:") and "chunked" in line.lower()
        for line in header_lines
    ):
        body, rest = await _read_chunked(reader, rest)
        return head, body, rest
    length = 0
    for line in header_lines:
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    while len(rest) < length:
        chunk = await reader.read(65536)
        if not chunk:
            break
        rest += chunk
    return head, rest[:length], rest[length:]


async def _read_chunked(reader: asyncio.StreamReader, buffer: bytes) -> tuple[bytes, bytes]:
    body = bytearray()
    while True:
        while b"\r\n" not in buffer:
            chunk = await reader.read(65536)
            if not chunk:
                raise AssertionError("truncated chunked body")
            buffer += chunk
        line, _, buffer = buffer.partition(b"\r\n")
        size = int(line.split(b";", 1)[0], 16)
        if size == 0:
            while b"\r\n\r\n" not in buffer:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                buffer += chunk
            _, _, buffer = buffer.partition(b"\r\n\r\n")
            return bytes(body), buffer
        while len(buffer) < size + 2:
            chunk = await reader.read(65536)
            if not chunk:
                raise AssertionError("truncated chunk")
            buffer += chunk
        body.extend(buffer[:size])
        buffer = buffer[size + 2 :]

