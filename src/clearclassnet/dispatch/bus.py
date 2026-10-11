"""Run matching handlers in priority order."""

from __future__ import annotations

import logging

from clearclassnet.dispatch.registry import Registry, get_registry
from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent

log = logging.getLogger(__name__)

type Event = RequestEvent | ResponseEvent | ConnectEvent


async def dispatch(event: Event, registry: Registry | None = None) -> None:
    """Invoke matching handlers until one stops propagation.

    `allow()`, `reject()`, and `respond()` always stop the chain. `rewrite()`,
    `intercept()`, and `tunnel()` stop it only when the matcher was registered
    with `block=True` (the default). A matcher with `block=False` is observational
    unless it calls a stopping reaction.
    """
    active = registry if registry is not None else get_registry()
    for matcher in active.for_event(event):
        if not matcher.rule.matches(event):
            continue
        try:
            await matcher.handler(event)
        except Exception:
            log.exception("plugin handler failed: %s", matcher.qualname)
        if event.propagation_stopped or matcher.block:
            return
