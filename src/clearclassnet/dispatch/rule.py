"""Composable predicates that decide whether a matcher runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from fnmatch import fnmatch

from clearclassnet.event.http import ConnectEvent, RequestEvent, ResponseEvent

Matchable = RequestEvent | ResponseEvent | ConnectEvent
Predicate = Callable[[Matchable], bool]


class Rule:
    """A predicate over an event. Combine with `&`, `|`, and `~`."""

    def __init__(self, predicate: Predicate, description: str) -> None:
        self._predicate = predicate
        self.description = description

    def matches(self, event: Matchable) -> bool:
        return self._predicate(event)

    def __and__(self, other: Rule) -> Rule:
        return Rule(
            lambda event: self.matches(event) and other.matches(event),
            f"({self.description} & {other.description})",
        )

    def __or__(self, other: Rule) -> Rule:
        return Rule(
            lambda event: self.matches(event) or other.matches(event),
            f"({self.description} | {other.description})",
        )

    def __invert__(self) -> Rule:
        return Rule(lambda event: not self.matches(event), f"~({self.description})")

    def __repr__(self) -> str:
        return self.description

    @staticmethod
    def all() -> Rule:
        return Rule(lambda _event: True, "Rule.all()")

    @staticmethod
    def custom(predicate: Predicate, description: str = "custom") -> Rule:
        return Rule(predicate, description)

    @staticmethod
    def host(pattern: str | Iterable[str]) -> Rule:
        """Match a host glob, or any glob in a list.

        ``*.douyin.com`` does not match the apex ``douyin.com``. Pass both
        when a list of sites should include the bare domain.
        """
        patterns = (pattern,) if isinstance(pattern, str) else tuple(pattern)
        needles = tuple(item.casefold() for item in patterns)

        def matches(event: Matchable) -> bool:
            host = event.host.casefold()
            return any(fnmatch(host, needle) for needle in needles)

        return Rule(matches, f"Rule.host({pattern!r})")

    @staticmethod
    def method(name: str) -> Rule:
        expected = name.upper()
        return Rule(
            lambda event: event.method.upper() == expected,
            f"Rule.method({name!r})",
        )

    @staticmethod
    def path(pattern: str) -> Rule:
        return Rule(
            lambda event: fnmatch(getattr(event, "path", ""), pattern),
            f"Rule.path({pattern!r})",
        )

    @staticmethod
    def scheme(name: str) -> Rule:
        expected = name.lower()
        return Rule(
            lambda event: event.scheme.lower() == expected,
            f"Rule.scheme({name!r})",
        )

    @staticmethod
    def port(number: int) -> Rule:
        return Rule(lambda event: event.port == number, f"Rule.port({number})")
