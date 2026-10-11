"""Reference plugin: append one JSON line per request and response.

Observes only (`block=False`) and runs at a high priority so it records exchanges
that a later matcher rejects.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from clearclassnet.config import plugin_config
from clearclassnet.dispatch import Rule, on_request, on_response
from clearclassnet.event import RequestEvent, ResponseEvent

log = logging.getLogger("clearclassnet.audit")


def register() -> None:
    on_request(Rule.all(), priority=1000, block=False)(audit_request)
    on_response(Rule.all(), priority=1000, block=False)(audit_response)


async def audit_request(event: RequestEvent) -> None:
    _record("request", event.method, event.url, None, event.client_address)


async def audit_response(event: ResponseEvent) -> None:
    _record("response", event.method, event.url, event.status, event.client_address)


def _record(
    phase: str,
    method: str,
    url: str,
    status: int | None,
    client: tuple[str, int],
) -> None:
    record = {
        "time": datetime.now(UTC).isoformat(),
        "phase": phase,
        "method": method,
        "url": str(url),
        "status": status,
        "client": f"{client[0]}:{client[1]}",
    }
    log.info("%s %s %s", phase, method, url)
    path = plugin_config("audit").get("path")
    if not path:
        return
    target = Path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
