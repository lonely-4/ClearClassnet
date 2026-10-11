"""Shared HTTP framing helpers for the proxy."""

from __future__ import annotations

from http import HTTPStatus

import h11

from clearclassnet.event.headers import HeaderMap

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "proxy-connection",
}


def reason_bytes(status: int) -> bytes:
    try:
        return HTTPStatus(status).phrase.encode("ascii")
    except ValueError:
        return b"Unknown"


def connection_requested_close(headers: HeaderMap, http_version: str) -> bool:
    tokens = [part.strip().lower() for part in (headers.get("Connection") or "").split(",")]
    if "close" in tokens:
        return True
    if http_version == "1.0":
        return "keep-alive" not in tokens
    return False


def host_header(host: str, port: int, scheme: str) -> str:
    default = 443 if scheme == "https" else 80
    formatted = f"[{host}]" if ":" in host else host
    if port == default:
        return formatted
    return f"{formatted}:{port}"


def forwarding_headers(
    headers: HeaderMap,
    *,
    body_size: int | None,
    host: str | None = None,
    port: int | None = None,
    scheme: str | None = None,
) -> HeaderMap:
    """Drop hop-by-hop headers and set a fresh content length.

    `Upgrade` is preserved so a later 101 can switch into a byte tunnel.
    """
    drop = set(_HOP_BY_HOP)
    connection = headers.get("Connection")
    if connection:
        drop.update(part.strip().lower() for part in connection.split(",") if part.strip())
    # `Connection: Upgrade` names Upgrade, but that header is the signal we keep.
    drop.discard("upgrade")
    cleaned = HeaderMap()
    for name, value in headers.items():
        if name.lower() in drop or name.lower() == "content-length":
            continue
        cleaned.add(name, value)
    if host is not None and port is not None and scheme is not None:
        cleaned["Host"] = host_header(host, port, scheme)
    if cleaned.get("Upgrade") and "Connection" not in cleaned:
        cleaned["Connection"] = "Upgrade"
    if body_size is not None:
        cleaned["Content-Length"] = str(body_size)
    return cleaned


def pending_bytes(protocol: h11.Connection) -> bytes:
    """Bytes already read past the current HTTP message."""
    data, _closed = protocol.trailing_data
    return data
