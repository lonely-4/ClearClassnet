"""Local MITM proxy framework. Policy lives in plugins, not in this package.

Plugin code imports from the subsystem it uses:

- `clearclassnet.event` for request, response, and CONNECT events
- `clearclassnet.dispatch` for rules and matcher registration
- `clearclassnet.response` for synthetic responses
- `clearclassnet.httpx` for HTTP clients that bypass the ambient proxy
- `clearclassnet.config.plugin_config` for a plugin's own settings
"""

__version__ = "0.1.0"

__all__ = ["main"]


def main(argv: list[str] | None = None) -> None:
    from clearclassnet.cli import main as cli_main

    cli_main(argv)
