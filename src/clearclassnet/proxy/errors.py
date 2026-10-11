"""Failures raised by the proxy transport."""


class ProxyError(Exception):
    """Base class for transport failures."""


class ClientClosed(ProxyError):
    """The client closed the connection before the message finished."""


class ProtocolFailure(ProxyError):
    """The peer sent bytes that are not valid HTTP/1.1."""


class IdleClose(ProxyError):
    """A keep-alive connection was idle until the read timeout."""


class ReadTimeout(ProxyError):
    """A read timed out in the middle of a message."""


class UpstreamError(ProxyError):
    """The upstream connection failed."""

    def __init__(self, host: str, port: int, reason: str) -> None:
        self.host = host
        self.port = port
        super().__init__(f"{host}:{port}: {reason}")


class UpstreamTimeout(UpstreamError):
    """Connecting to or reading from the upstream server timed out."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(host, port, "timed out")
