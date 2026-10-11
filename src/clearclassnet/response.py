"""Synthetic responses a plugin can hand back with `respond()` or `reject()`.

The constructors follow Starlette's response classes: content, status code,
headers, and a media type. Buffered responses are sent with `Content-Length`.
`StreamingResponse` is written with chunked transfer encoding unless the caller
sets `Content-Length`.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.parse import quote

from clearclassnet.event.headers import HeaderMap

_NO_LENGTH = frozenset({204, 304})


class Response:
    """A complete response body with status and headers."""

    media_type: str | None = None
    charset = "utf-8"
    stream = False

    def __init__(
        self,
        content: bytes | str | None = None,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        self.status_code = status_code
        if media_type is not None:
            self.media_type = media_type
        self.body = self.render(content)
        self.headers = HeaderMap()
        self._apply_headers(headers, include_length=True)

    def render(self, content: bytes | str | None) -> bytes:
        if content is None:
            return b""
        if isinstance(content, bytes):
            return content
        return content.encode(self.charset)

    def _apply_headers(
        self,
        headers: Mapping[str, str] | None,
        *,
        include_length: bool,
    ) -> None:
        if headers:
            self.headers.update(headers)
        if self.media_type is not None and "content-type" not in self.headers:
            content_type = self.media_type
            if content_type.startswith("text/"):
                content_type = f"{content_type}; charset={self.charset}"
            self.headers["Content-Type"] = content_type
        if (
            include_length
            and "content-length" not in self.headers
            and self.status_code >= 200
            and self.status_code not in _NO_LENGTH
        ):
            self.headers["Content-Length"] = str(len(self.body))

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        if self.body:
            yield self.body


class HTMLResponse(Response):
    """`text/html` body."""

    media_type = "text/html"


class PlainTextResponse(Response):
    """`text/plain` body."""

    media_type = "text/plain"


class JSONResponse(Response):
    """JSON body encoded with compact separators."""

    media_type = "application/json"

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content=content, status_code=status_code, headers=headers)

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")


class RedirectResponse(Response):
    """Redirect to `url`. Defaults to 307 so the client keeps the method."""

    def __init__(
        self,
        url: str,
        status_code: int = 307,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content=b"", status_code=status_code, headers=headers)
        self.headers["location"] = quote(str(url), safe=":/%#?=@[]!$&'()*+,;")


class StreamingResponse(Response):
    """Write `content` as it is produced.

    `content` may be an async iterable, or a sync iterable of `bytes` or `str`
    chunks. A bare `bytes` or `str` value is one chunk. Without an explicit
    `Content-Length` header the proxy uses chunked transfer encoding.
    """

    stream = True

    def __init__(
        self,
        content: AsyncIterable[bytes | str] | Iterable[bytes | str] | bytes | str,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        self.status_code = status_code
        if media_type is not None:
            self.media_type = media_type
        self.body = b""
        self._source = content
        self.headers = HeaderMap()
        self._apply_headers(headers, include_length=False)

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        source = self._source
        if isinstance(source, bytes | str):
            chunk = source.encode(self.charset) if isinstance(source, str) else source
            if chunk:
                yield chunk
            return
        if isinstance(source, AsyncIterable):
            async for chunk in source:
                encoded = _chunk_bytes(chunk, self.charset)
                if encoded:
                    yield encoded
            return
        for chunk in source:
            encoded = _chunk_bytes(chunk, self.charset)
            if encoded:
                yield encoded


class FileResponse(Response):
    """Send a file from disk without loading it all at once."""

    chunk_size = 64 * 1024
    stream = True

    def __init__(
        self,
        path: str | Path,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        filename: str | None = None,
        content_disposition_type: str = "attachment",
    ) -> None:
        self.path = Path(path)
        self.status_code = status_code
        self.filename = filename
        stat = self.path.stat()
        if media_type is None:
            guessed, _encoding = mimetypes.guess_type(filename or self.path.name)
            media_type = guessed or "text/plain"
        self.media_type = media_type
        self.body = b""
        self.headers = HeaderMap()
        self._apply_headers(headers, include_length=False)
        if filename is not None and "content-disposition" not in self.headers:
            self.headers["Content-Disposition"] = _content_disposition(
                content_disposition_type,
                filename,
            )
        if "content-length" not in self.headers:
            self.headers["Content-Length"] = str(stat.st_size)
        if "last-modified" not in self.headers:
            self.headers["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
        if "etag" not in self.headers:
            self.headers["ETag"] = f'"{int(stat.st_mtime):x}-{stat.st_size:x}"'

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        handle = await loop.run_in_executor(None, self.path.open, "rb")
        try:
            while True:
                chunk = await loop.run_in_executor(None, handle.read, self.chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            await loop.run_in_executor(None, handle.close)


def _chunk_bytes(chunk: bytes | str, charset: str) -> bytes:
    if isinstance(chunk, str):
        return chunk.encode(charset)
    return chunk


def _content_disposition(disposition: str, filename: str) -> str:
    quoted = quote(filename)
    if quoted != filename:
        return f"{disposition}; filename*=utf-8''{quoted}"
    return f'{disposition}; filename="{filename}"'
