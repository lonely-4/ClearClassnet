"""Process-wide matcher registry.

Plugins register at import time, before the event loop starts serving traffic.
The registry is append-only during a run; tests clear it between cases.
"""

from __future__ import annotations

from clearclassnet.dispatch.matcher import Matcher
from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent

type Event = RequestEvent | ResponseEvent | ConnectEvent


class Registry:
    def __init__(self) -> None:
        self._matchers: list[Matcher] = []
        self._seq = 0

    def add(self, matcher: Matcher) -> None:
        for existing in self._matchers:
            if existing.handler is matcher.handler and existing.event_type is matcher.event_type:
                return
        matcher.order = self._seq
        self._seq += 1
        self._matchers.append(matcher)

    def clear(self) -> None:
        self._matchers.clear()
        self._seq = 0

    def all(self) -> list[Matcher]:
        return sorted(self._matchers, key=lambda matcher: (-matcher.priority, matcher.order))

    def for_event(self, event: Event) -> list[Matcher]:
        event_type = type(event)
        selected = [matcher for matcher in self._matchers if matcher.event_type is event_type]
        selected.sort(key=lambda matcher: (-matcher.priority, matcher.order))
        return selected


_registry = Registry()


def get_registry() -> Registry:
    return _registry
