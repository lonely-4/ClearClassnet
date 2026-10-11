"""Read h11 events from an asyncio stream."""

from __future__ import annotations

import asyncio

import h11

from clearclassnet.event.body import BodyBuffer
from clearclassnet.proxy.errors import ClientClosed, IdleClose, ProtocolFailure, ReadTimeout


async def next_event(
    reader: asyncio.StreamReader,
    protocol: h11.Connection,
    timeout: float,
    *,
    idle: bool,
) -> h11.Event:
    """Return the next h11 event, reading the socket only when h11 asks."""
    while True:
        try:
            event = protocol.next_event()
        except h11.RemoteProtocolError as exc:
            raise ProtocolFailure(str(exc)) from exc
        if event is h11.NEED_DATA:
            try:
                data = await asyncio.wait_for(reader.read(65536), timeout)
            except TimeoutError as exc:
                if idle:
                    raise IdleClose() from exc
                raise ReadTimeout() from exc
            try:
                protocol.receive_data(data)
            except RuntimeError as exc:
                raise ClientClosed() from exc
            continue
        if event is h11.PAUSED:
            # The message is already complete and extra bytes belong to the
            # next cycle or a protocol switch. Callers stop at EndOfMessage.
            raise ProtocolFailure("HTTP parser paused before the message ended")
        if isinstance(event, h11.ConnectionClosed):
            raise ClientClosed()
        return event


async def read_body(
    reader: asyncio.StreamReader,
    protocol: h11.Connection,
    limit: int,
    timeout: float,
) -> BodyBuffer:
    body = BodyBuffer(limit)
    while True:
        event = await next_event(reader, protocol, timeout, idle=False)
        if isinstance(event, h11.Data):
            body.append(event.data)
            continue
        if isinstance(event, h11.EndOfMessage):
            return body
        raise ProtocolFailure(f"unexpected event while reading body: {type(event).__name__}")
