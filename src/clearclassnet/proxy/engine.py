"""Wire the transport to the event bus.

The engine owns configuration, the certificate authority, and plugin dispatch.
It does not decide which traffic to block or decrypt.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Callable

from clearclassnet.config import Config
from clearclassnet.dispatch.bus import dispatch
from clearclassnet.dispatch.registry import Registry, get_registry
from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent
from clearclassnet.proxy.connection import ClientConnection
from clearclassnet.proxy.server import ProxyServer
from clearclassnet.tls.ca import CertificateAuthority

log = logging.getLogger(__name__)

type UpstreamSSL = ssl.SSLContext | Callable[[str], ssl.SSLContext]


class ProxyEngine:
    """Accept local proxy connections and turn each exchange into events."""

    def __init__(
        self,
        config: Config,
        ca: CertificateAuthority,
        *,
        registry: Registry | None = None,
        upstream_ssl: UpstreamSSL | None = None,
    ) -> None:
        self.config = config
        self.ca = ca
        self.registry = registry if registry is not None else get_registry()
        self._upstream_ssl = upstream_ssl
        self._server = ProxyServer(self._handle_client)

    def upstream_ssl_context(self, host: str) -> ssl.SSLContext:
        """Context used when this proxy connects to an upstream TLS server."""
        factory = self._upstream_ssl
        if factory is None:
            return ssl.create_default_context()
        if isinstance(factory, ssl.SSLContext):
            return factory
        return factory(host)

    async def dispatch(self, event: RequestEvent | ResponseEvent | ConnectEvent) -> None:
        await dispatch(event, self.registry)

    async def listen(self, host: str | None = None, port: int | None = None) -> ProxyServer:
        bound_host = self.config.proxy.host if host is None else host
        bound_port = self.config.proxy.port if port is None else port
        await self._server.start(bound_host, bound_port)
        names = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        log.info("ClearClassnet listening on %s", names)
        return self._server

    async def serve(self) -> None:
        await self.listen()
        await self._server.serve_forever()

    async def close(self) -> None:
        await self._server.close()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await ClientConnection(reader, writer, self).run()
