"""Matchers bind a rule and a handler to one event type."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from clearclassnet.dispatch.rule import Rule
from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent

type EventType = type[RequestEvent] | type[ResponseEvent] | type[ConnectEvent]
type Handler = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class Matcher:
    event_type: EventType
    rule: Rule
    handler: Handler
    priority: int
    block: bool
    order: int = 0

    @property
    def qualname(self) -> str:
        return f"{self.handler.__module__}:{self.handler.__qualname__}"

    @property
    def phase(self) -> str:
        return self.event_type.__name__.removesuffix("Event").lower()


def _register(
    event_type: EventType,
    rule: Rule | None,
    handler: Handler,
    priority: int,
    block: bool,
) -> Handler:
    from clearclassnet.dispatch.registry import get_registry

    get_registry().add(
        Matcher(
            event_type=event_type,
            rule=rule if rule is not None else Rule.all(),
            handler=handler,
            priority=priority,
            block=block,
        )
    )
    return handler


def on_request(
    rule: Rule | None = None,
    *,
    priority: int = 0,
    block: bool = True,
) -> Callable[[Handler], Handler]:
    """Register a request handler. Higher `priority` runs first."""

    def decorate(handler: Handler) -> Handler:
        return _register(RequestEvent, rule, handler, priority, block)

    return decorate


def on_response(
    rule: Rule | None = None,
    *,
    priority: int = 0,
    block: bool = True,
) -> Callable[[Handler], Handler]:
    """Register a response handler. Higher `priority` runs first."""

    def decorate(handler: Handler) -> Handler:
        return _register(ResponseEvent, rule, handler, priority, block)

    return decorate


def on_connect(
    rule: Rule | None = None,
    *,
    priority: int = 0,
    block: bool = True,
) -> Callable[[Handler], Handler]:
    """Register a CONNECT handler. Higher `priority` runs first."""

    def decorate(handler: Handler) -> Handler:
        return _register(ConnectEvent, rule, handler, priority, block)

    return decorate
