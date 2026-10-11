"""Reference plugin: choose which CONNECT targets are decrypted.

`include` and `exclude` are `fnmatch` host patterns from `[plugins.mitm_policy]`.
Exclude wins. Hosts that match neither are tunneled. With no configuration,
every host is intercepted, which matches the core default.
"""

from __future__ import annotations

from fnmatch import fnmatch

from clearclassnet.config import plugin_config
from clearclassnet.dispatch.matcher import on_connect
from clearclassnet.dispatch.rule import Rule
from clearclassnet.event.http import ConnectEvent


def register() -> None:
    on_connect(Rule.all(), priority=0)(apply_mitm_policy)


async def apply_mitm_policy(event: ConnectEvent) -> None:
    section = plugin_config("mitm_policy")
    include = _as_patterns(section.get("include", ["*"]))
    exclude = _as_patterns(section.get("exclude", []))
    host = event.host.casefold()
    if any(fnmatch(host, pattern) for pattern in exclude):
        event.tunnel()
        return
    if any(fnmatch(host, pattern) for pattern in include):
        event.intercept()
        return
    event.tunnel()


def _as_patterns(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.casefold()]
    if isinstance(value, list):
        return [str(item).casefold() for item in value]
    return []
