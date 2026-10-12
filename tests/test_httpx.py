import asyncio
import threading
from collections.abc import Iterator

import pytest

from clearclassnet.httpx import httpx, httpxAsync


@pytest.fixture
def origin() -> Iterator[str]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b"ok"
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n"
            b"\r\n" + body
        )
        await writer.drain()
        writer.close()

    async def serve() -> None:
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        sockets = server.sockets
        assert sockets is not None
        port = sockets[0].getsockname()[1]
        address.append(f"http://127.0.0.1:{port}/")
        ready.set()
        async with server:
            await server.serve_forever()

    ready = threading.Event()
    address: list[str] = []
    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    yield address[0]


def test_clients_ignore_environment_and_system_proxy(
    origin: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    assert httpx.trust_env is False
    assert httpxAsync.trust_env is False
    response = httpx.get(origin)
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.asyncio
async def test_async_client_ignores_environment_proxy(origin: str) -> None:
    response = await httpxAsync.get(origin)
    assert response.status_code == 200
    assert response.content == b"ok"
