"""Bidirectional byte copy used by CONNECT tunnels and protocol upgrades."""

from __future__ import annotations

import asyncio


async def relay(
    left_reader: asyncio.StreamReader,
    left_writer: asyncio.StreamWriter,
    right_reader: asyncio.StreamReader,
    right_writer: asyncio.StreamWriter,
    *,
    to_right: bytes = b"",
    to_left: bytes = b"",
) -> None:
    """Copy bytes in both directions until one side finishes."""
    if to_right:
        right_writer.write(to_right)
        await right_writer.drain()
    if to_left:
        left_writer.write(to_left)
        await left_writer.drain()

    async def _copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await source.read(65536)
                if not chunk:
                    break
                destination.write(chunk)
                await destination.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            return
        finally:
            try:
                destination.close()
            except Exception:
                return

    tasks = {
        asyncio.create_task(_copy(left_reader, right_writer)),
        asyncio.create_task(_copy(right_reader, left_writer)),
    }
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
