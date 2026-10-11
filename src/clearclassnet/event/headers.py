"""Case-insensitive HTTP header map that preserves insertion order."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping


def _decode(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    return value.decode("latin-1")


class HeaderMap:
    """Mutable HTTP headers.

    Lookup is case-insensitive. Assigning a name replaces every existing value
    for that name; `add` appends another line.
    """

    def __init__(self, items: Iterable[tuple[str | bytes, str | bytes]] | None = None) -> None:
        self._items: list[tuple[str, str]] = []
        if items:
            for key, value in items:
                self.add(key, value)

    @classmethod
    def from_h11(cls, headers: Iterable[tuple[bytes, bytes]]) -> HeaderMap:
        return cls(headers)

    def add(self, key: str | bytes, value: str | bytes) -> None:
        self._items.append((_decode(key), _decode(value)))

    def __setitem__(self, key: str, value: str) -> None:
        lowered = key.lower()
        self._items = [(name, item) for name, item in self._items if name.lower() != lowered]
        self._items.append((key, value))

    def __getitem__(self, key: str) -> str:
        found = [value for name, value in self._items if name.lower() == key.lower()]
        if not found:
            raise KeyError(key)
        return found[-1]

    def get(self, key: str, default: str | None = None) -> str | None:
        try:
            return self[key]
        except KeyError:
            return default

    def __delitem__(self, key: str) -> None:
        lowered = key.lower()
        self._items = [(name, value) for name, value in self._items if name.lower() != lowered]

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return any(name.lower() == key.lower() for name, _ in self._items)

    def __iter__(self) -> Iterator[str]:
        seen: set[str] = set()
        for name, _ in self._items:
            lowered = name.lower()
            if lowered not in seen:
                seen.add(lowered)
                yield name

    def __len__(self) -> int:
        return len({name.lower() for name, _ in self._items})

    def items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def update(self, headers: Mapping[str, str]) -> None:
        for key, value in headers.items():
            self[key] = value

    def as_bytes(self) -> list[tuple[bytes, bytes]]:
        return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in self._items]

    def __repr__(self) -> str:
        return f"HeaderMap({self._items!r})"
