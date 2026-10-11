import pytest

from clearclassnet.event.body import BodyBuffer
from clearclassnet.event.errors import BodyUnavailable


def test_small_body_is_readable_and_replaceable() -> None:
    body = BodyBuffer(8)
    body.append(b"abc")
    body.append(b"de")
    assert body.content() == b"abcde"
    body.replace(b"xyz")
    assert body.content() == b"xyz"
    assert list(body.iter_chunks()) == [b"xyz"]


def test_overflow_is_forwardable_but_not_inspectable() -> None:
    body = BodyBuffer(4)
    body.append(b"abcd")
    assert body.streamed is False
    body.append(b"ef")
    assert body.streamed is True
    assert body.size == 6
    with pytest.raises(BodyUnavailable):
        body.content()
    with pytest.raises(BodyUnavailable):
        body.replace(b"nope")
    assert b"".join(body.iter_chunks()) == b"abcdef"
