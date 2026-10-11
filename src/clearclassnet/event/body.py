"""Bounded message-body storage.

Bodies at or under the limit stay in memory and are readable by plugins.
Larger bodies are spooled for forwarding only; content access raises
`BodyUnavailable`.
"""

from __future__ import annotations

from collections.abc import Iterator
from tempfile import SpooledTemporaryFile

from clearclassnet.event.errors import BodyUnavailable

_SPOOL_MEMORY = 1024 * 1024
_CHUNK = 64 * 1024


class BodyBuffer:
    """A request or response body with a hard inspection limit."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("body limit must be >= 0")
        self.limit = limit
        self._buf = bytearray()
        self._spool: SpooledTemporaryFile[bytes] | None = None
        self._size = 0
        self.streamed = False

    @property
    def size(self) -> int:
        return self._size

    def append(self, data: bytes) -> None:
        if not data:
            return
        if self.streamed:
            assert self._spool is not None
            self._spool.write(data)
            self._size += len(data)
            return
        if self._size + len(data) > self.limit:
            self.streamed = True
            self._spool = SpooledTemporaryFile(max_size=_SPOOL_MEMORY)
            if self._buf:
                self._spool.write(self._buf)
                self._buf.clear()
            self._spool.write(data)
            self._size += len(data)
            return
        self._buf.extend(data)
        self._size += len(data)

    def content(self) -> bytes:
        """Return the full body. Fails when the body was too large to inspect."""
        if self.streamed:
            raise BodyUnavailable(
                "body exceeds the configured buffer limit and cannot be inspected"
            )
        return bytes(self._buf)

    def replace(self, data: bytes) -> None:
        """Replace the body. Only allowed while the body is still inspectable."""
        if self.streamed:
            raise BodyUnavailable("cannot rewrite a body that exceeded the buffer limit")
        self._buf = bytearray(data)
        self._size = len(data)

    def iter_chunks(self, chunk_size: int = _CHUNK) -> Iterator[bytes]:
        """Yield the bytes that should be forwarded, including spooled overflow."""
        if not self.streamed:
            if self._buf:
                yield bytes(self._buf)
            return
        assert self._spool is not None
        self._spool.seek(0)
        while True:
            chunk = self._spool.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        if self._spool is not None:
            self._spool.close()
            self._spool = None
