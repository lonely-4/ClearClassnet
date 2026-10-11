"""HTTP flow events plugins react to.

`RequestEvent`, `ResponseEvent`, and `ConnectEvent` share no policy. They only
expose the message and the reactions a plugin can take: allow, reject, rewrite,
respond, intercept, or tunnel.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qs, urlsplit

from clearclassnet.event.body import BodyBuffer
from clearclassnet.event.headers import HeaderMap
from clearclassnet.response import Response


class Decision(Enum):
    """What the proxy should do after the matcher chain finishes."""

    NONE = "none"
    FORWARD = "forward"
    RESPOND = "respond"
    INTERCEPT = "intercept"
    TUNNEL = "tunnel"
    REJECT = "reject"


def _as_bytes(body: bytes | str) -> bytes:
    if isinstance(body, str):
        return body.encode("utf-8")
    return body


def _charset(content_type: str | None) -> str:
    if not content_type:
        return "utf-8"
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"')
    return "utf-8"


@dataclass
class RequestMessage:
    """The request as it will be forwarded, including plugin mutations."""

    method: str
    scheme: str
    host: str
    port: int
    path: str
    query: str
    headers: HeaderMap
    body: BodyBuffer
    http_version: str = "1.1"

    @property
    def url(self) -> URL:
        return URL(format_url(self.scheme, self.host, self.port, self.path, self.query), self.query)

    @property
    def target_path(self) -> str:
        if self.query:
            return f"{self.path}?{self.query}"
        return self.path or "/"


@dataclass
class ResponseMessage:
    """The response as it will be written back to the client."""

    status: int
    headers: HeaderMap
    body: BodyBuffer
    http_version: str = "1.1"
    trailing: bytes = b""


@dataclass
class HttpFlow:
    """State shared by the request and response events of one exchange."""

    request: RequestMessage
    client_host: str
    client_port: int
    response: ResponseMessage | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class RequestEvent:
    """A request that plugins may inspect, mutate, short-circuit, or forward."""

    def __init__(self, flow: HttpFlow) -> None:
        self.flow = flow
        self._decision = Decision.NONE
        self._stop = False
        self._synthetic_status = 200
        self._synthetic_headers = HeaderMap()
        self._synthetic_body: bytes | Response = b""

    @property
    def meta(self) -> dict[str, Any]:
        return self.flow.meta

    @property
    def method(self) -> str:
        return self.flow.request.method

    @property
    def scheme(self) -> str:
        return self.flow.request.scheme

    @property
    def host(self) -> str:
        return self.flow.request.host

    @property
    def port(self) -> int:
        return self.flow.request.port

    @property
    def path(self) -> str:
        return self.flow.request.path

    @property
    def query(self) -> str:
        return self.flow.request.query

    @property
    def url(self) -> URL:
        return self.flow.request.url

    @property
    def headers(self) -> HeaderMap:
        return self.flow.request.headers

    @property
    def client_address(self) -> tuple[str, int]:
        return (self.flow.client_host, self.flow.client_port)

    @property
    def propagation_stopped(self) -> bool:
        return self._stop

    @property
    def decision(self) -> Decision:
        return self._decision

    async def bytes(self) -> bytes:
        return self.flow.request.body.content()

    async def text(self) -> str:
        raw = await self.bytes()
        return raw.decode(_charset(self.headers.get("Content-Type")), errors="replace")

    async def json(self) -> Any:
        return json.loads(await self.bytes())

    def allow(self) -> None:
        """Forward the request as it stands and skip later matchers."""
        self._decision = Decision.FORWARD
        self._stop = True

    def reject(
        self,
        status: int | Response = 403,
        headers: Mapping[str, str] | None = None,
        body: bytes | str = b"",
    ) -> None:
        """Answer the client and do not contact the upstream server."""
        self.respond(status=status, headers=headers, body=body)

    def respond(
        self,
        status: int | Response = 200,
        headers: Mapping[str, str] | None = None,
        body: bytes | str = b"",
        *,
        json: Any = None,
        text: str | None = None,
    ) -> None:
        """Return a synthetic response and do not contact the upstream server.

        Pass a `clearclassnet.response.Response` as `status`, or build one
        from `status`, `headers`, `body`, `json`, or `text`.
        """
        if isinstance(status, Response):
            if headers or body or json is not None or text is not None:
                raise TypeError("a Response replaces status, headers, and body")
            self._install(status)
            return
        payload, extra = _payload(body=body, json_body=json, text=text)
        merged = HeaderMap(headers.items() if headers else ())
        for key, value in extra:
            if key not in merged:
                merged[key] = value
        self._decision = Decision.RESPOND
        self._synthetic_status = status
        self._synthetic_headers = merged
        self._synthetic_body = payload
        self._stop = True

    def _install(self, response: Response) -> None:
        self._decision = Decision.RESPOND
        self._synthetic_status = response.status_code
        self._synthetic_headers = response.headers
        self._synthetic_body = response if response.stream else response.body
        self._stop = True

    def rewrite(
        self,
        *,
        method: str | None = None,
        url: str | None = None,
        path: str | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes | str | None = None,
        json: Any = None,
        text: str | None = None,
    ) -> None:
        """Mutate the request that will be forwarded. Does not stop the chain."""
        message = self.flow.request
        if method is not None:
            message.method = method.upper()
        if url is not None:
            _apply_url(message, url)
        if path is not None:
            _apply_path(message, path)
        if headers:
            message.headers.update(headers)
        _replace_body(message.body, message.headers, body=body, json_body=json, text=text)

    def synthetic_response(self) -> tuple[int, HeaderMap, bytes | Response] | None:
        if self._decision is not Decision.RESPOND:
            return None
        return (self._synthetic_status, self._synthetic_headers, self._synthetic_body)


class ResponseEvent:
    """A response that plugins may inspect or rewrite before it reaches the client."""

    def __init__(self, flow: HttpFlow) -> None:
        if flow.response is None:
            raise ValueError("response event requires a response message")
        self.flow = flow
        self._stop = False

    @property
    def meta(self) -> dict[str, Any]:
        return self.flow.meta

    @property
    def status(self) -> int:
        assert self.flow.response is not None
        return self.flow.response.status

    @status.setter
    def status(self, value: int) -> None:
        assert self.flow.response is not None
        self.flow.response.status = value

    @property
    def headers(self) -> HeaderMap:
        assert self.flow.response is not None
        return self.flow.response.headers

    @property
    def method(self) -> str:
        return self.flow.request.method

    @property
    def scheme(self) -> str:
        return self.flow.request.scheme

    @property
    def host(self) -> str:
        return self.flow.request.host

    @property
    def port(self) -> int:
        return self.flow.request.port

    @property
    def path(self) -> str:
        return self.flow.request.path

    @property
    def url(self) -> URL:
        return self.flow.request.url

    @property
    def client_address(self) -> tuple[str, int]:
        return (self.flow.client_host, self.flow.client_port)

    @property
    def propagation_stopped(self) -> bool:
        return self._stop

    async def bytes(self) -> bytes:
        assert self.flow.response is not None
        return self.flow.response.body.content()

    async def text(self) -> str:
        raw = await self.bytes()
        return raw.decode(_charset(self.headers.get("Content-Type")), errors="replace")

    async def json(self) -> Any:
        return json.loads(await self.bytes())

    def allow(self) -> None:
        """Keep the response as it stands and skip later matchers."""
        self._stop = True

    def rewrite(
        self,
        *,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes | str | None = None,
        json: Any = None,
        text: str | None = None,
    ) -> None:
        """Mutate the response that will be written to the client."""
        message = self.flow.response
        assert message is not None
        if status is not None:
            message.status = status
        if headers:
            message.headers.update(headers)
        _replace_body(message.body, message.headers, body=body, json_body=json, text=text)


class ConnectEvent:
    """A CONNECT tunnel request. Plugins decide whether to decrypt it."""

    def __init__(self, host: str, port: int, *, client_address: tuple[str, int] = ("", 0)) -> None:
        self.host = host
        self.port = port
        self.client_address = client_address
        self.method = "CONNECT"
        self.scheme = "https" if port == 443 else "http"
        self.path = ""
        self.meta: dict[str, Any] = {}
        self._decision = Decision.NONE
        self._stop = False
        self._reject_status = 403
        self._reject_body = b""

    @property
    def url(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def propagation_stopped(self) -> bool:
        return self._stop

    @property
    def decision(self) -> Decision:
        """Resolved tunnel decision. Undecided connections are intercepted."""
        if self._decision is Decision.NONE:
            return Decision.INTERCEPT
        return self._decision

    @property
    def reject_status(self) -> int:
        return self._reject_status

    @property
    def reject_body(self) -> bytes:
        return self._reject_body

    def intercept(self) -> None:
        """Terminate TLS on this proxy and expose HTTP events for the tunnel."""
        self._decision = Decision.INTERCEPT

    def tunnel(self) -> None:
        """Blind-forward the bytes and leave TLS end to end."""
        self._decision = Decision.TUNNEL

    def reject(self, status: int = 403, body: bytes | str = b"") -> None:
        """Refuse the CONNECT and stop later matchers."""
        self._decision = Decision.REJECT
        self._reject_status = status
        self._reject_body = _as_bytes(body)
        self._stop = True


class QueryParams:
    """Decoded query string. `get` returns the first value for a key."""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self._items = parse_qs(raw, keep_blank_values=True)

    def get(self, key: str, default: str = "") -> str:
        values = self._items.get(key)
        if not values:
            return default
        return values[0]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._items

    def __str__(self) -> str:
        return self._raw


class URL(str):
    """Request URL that still behaves as a string and exposes `query`."""

    def __new__(cls, value: str, query: str) -> URL:
        obj = str.__new__(cls, value)
        obj._query = query
        return obj

    @property
    def query(self) -> QueryParams:
        return QueryParams(self._query)


def format_url(scheme: str, host: str, port: int, path: str, query: str) -> str:
    default_port = 443 if scheme == "https" else 80
    netloc = _netloc(host, port, omit_port=port == default_port)
    target = path or "/"
    if query:
        target = f"{target}?{query}"
    return f"{scheme}://{netloc}{target}"


def split_host_port(value: str, default_port: int) -> tuple[str, int]:
    """Split `host`, `host:port`, or `[ipv6]:port`."""
    text = value.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        if rest.startswith(":"):
            return host, int(rest[1:])
        return host, default_port
    if text.count(":") == 1:
        host, _, port_text = text.partition(":")
        return host, int(port_text)
    return text, default_port


def _netloc(host: str, port: int, *, omit_port: bool) -> str:
    formatted = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if omit_port:
        return formatted
    return f"{formatted}:{port}"


def _apply_url(message: RequestMessage, url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme:
        message.scheme = parsed.scheme
    if parsed.hostname:
        message.host = parsed.hostname
        default_port = 443 if message.scheme == "https" else 80
        message.port = parsed.port or default_port
        message.headers["Host"] = _netloc(
            message.host,
            message.port,
            omit_port=message.port == default_port,
        )
    if parsed.path:
        message.path = parsed.path
    message.query = parsed.query


def _apply_path(message: RequestMessage, path: str) -> None:
    if "?" in path:
        message.path, message.query = path.split("?", 1)
        return
    message.path = path or "/"


def _payload(
    *,
    body: bytes | str,
    json_body: Any,
    text: str | None,
) -> tuple[bytes, list[tuple[str, str]]]:
    if json_body is not None:
        return (
            json.dumps(json_body, separators=(",", ":")).encode("utf-8"),
            [("Content-Type", "application/json")],
        )
    if text is not None:
        return (text.encode("utf-8"), [("Content-Type", "text/plain; charset=utf-8")])
    return (_as_bytes(body), [])


def _replace_body(
    buffer: BodyBuffer,
    headers: HeaderMap,
    *,
    body: bytes | str | None,
    json_body: Any,
    text: str | None,
) -> None:
    if json_body is None and text is None and body is None:
        return
    payload, extra = _payload(body=b"" if body is None else body, json_body=json_body, text=text)
    buffer.replace(payload)
    for key, value in extra:
        headers[key] = value
    headers["Content-Length"] = str(len(payload))
