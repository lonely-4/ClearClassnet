"""Accept local TCP connections and track the tasks that serve them."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

type ClientHandler = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter],
    Awaitable[None],
]


class ProxyServer:
    """asyncio server that keeps a set of active client tasks for shutdown."""

    def __init__(self, handler: ClientHandler) -> None:
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def sockets(self) -> tuple[socket.socket, ...]:
        if self._server is None or self._server.sockets is None:
            return ()
        return tuple(self._server.sockets)

    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._accept, host, port)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("server is not listening")
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._run(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._handler(reader, writer)
        except Exception:
            log.exception("client connection failed")
            writer.close()
