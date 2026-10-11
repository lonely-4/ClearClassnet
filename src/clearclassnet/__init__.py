"""Local MITM proxy framework. Policy lives in plugins, not in this package."""

from clearclassnet.config import plugin_config
from clearclassnet.dispatch.matcher import on_connect, on_request, on_response
from clearclassnet.dispatch.rule import Rule
from clearclassnet.event.errors import BodyUnavailable
from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent

__version__ = "0.1.0"

__all__ = [
    "BodyUnavailable",
    "ConnectEvent",
    "RequestEvent",
    "ResponseEvent",
    "Rule",
    "main",
    "on_connect",
    "on_request",
    "on_response",
    "plugin_config",
]


def main(argv: list[str] | None = None) -> None:
    from clearclassnet.cli import main as cli_main

    cli_main(argv)
