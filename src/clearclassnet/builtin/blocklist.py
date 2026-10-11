"""Reference plugin: reject requests whose host or URL matches a pattern file.

The file is re-read when its modification time changes. Each non-comment line
is a `fnmatch` pattern. Lines without a slash match the host; other lines
match the full URL.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from clearclassnet.config import plugin_config
from clearclassnet.dispatch import Rule, on_request
from clearclassnet.event import RequestEvent

_cache_key: tuple[str, int] | None = None
_patterns: list[str] = []


def register() -> None:
    # reject() stops the chain. block=False keeps later request plugins running
    # when this file does not match.
    on_request(Rule.all(), priority=100, block=False)(apply_blocklist)


async def apply_blocklist(event: RequestEvent) -> None:
    for pattern in _load_patterns():
        if _matches(event, pattern):
            event.reject(status=403, body=b"Blocked")
            return


def _load_patterns() -> list[str]:
    global _cache_key, _patterns
    configured = plugin_config("blocklist").get("path", "")
    if not configured:
        return []
    path = Path(str(configured))
    if not path.is_file():
        return []
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    if key != _cache_key:
        _patterns = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        _cache_key = key
    return list(_patterns)


def _matches(event: RequestEvent, pattern: str) -> bool:
    folded = pattern.casefold()
    if "://" in pattern or "/" in pattern:
        return fnmatch(event.url.casefold(), folded)
    return fnmatch(event.host.casefold(), folded)
