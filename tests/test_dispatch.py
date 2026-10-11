import pytest
from tests.helpers import request_event

from clearclassnet.dispatch.bus import dispatch
from clearclassnet.dispatch.matcher import on_connect, on_request, on_response
from clearclassnet.dispatch.rule import Rule
from clearclassnet.event.body import BodyBuffer
from clearclassnet.event.headers import HeaderMap
from clearclassnet.event.http import ConnectEvent, HttpFlow, ResponseEvent, ResponseMessage


@pytest.mark.asyncio
async def test_higher_priority_runs_first_and_block_stops_the_chain() -> None:
    order: list[str] = []

    @on_request(Rule.all(), priority=1)
    async def later(event: object) -> None:
        order.append("later")

    @on_request(Rule.all(), priority=10)
    async def earlier(event: object) -> None:
        order.append("earlier")

    await dispatch(request_event())
    assert order == ["earlier"]


@pytest.mark.asyncio
async def test_block_false_observes_and_reject_stops() -> None:
    seen: list[str] = []

    @on_request(Rule.all(), priority=50, block=False)
    async def observe(event: object) -> None:
        seen.append("observe")

    @on_request(Rule.host("*.blocked.test"), priority=10)
    async def reject(event) -> None:
        seen.append("reject")
        event.reject(status=403, body=b"no")

    @on_request(Rule.all(), priority=0)
    async def after(event: object) -> None:
        seen.append("after")

    event = request_event(host="video.blocked.test")
    await dispatch(event)
    assert seen == ["observe", "reject"]
    synthetic = event.synthetic_response()
    assert synthetic is not None
    assert synthetic[0] == 403
    assert synthetic[2] == b"no"


@pytest.mark.asyncio
async def test_rewrite_json_and_headers_without_stopping_observers() -> None:
    @on_request(Rule.method("POST") & Rule.path("/api/*"), priority=10, block=False)
    async def rewrite(event) -> None:
        payload = await event.json()
        payload["school"] = "demo"
        event.rewrite(json=payload, headers={"X-Tag": "classnet"})
        event.headers["X-Foo"] = "bar"

    @on_request(Rule.all(), priority=0, block=False)
    async def after(event) -> None:
        event.flow.meta["url"] = event.url

    event = request_event(
        method="POST",
        path="/api/chat",
        headers={"Host": "example.com", "Content-Type": "application/json"},
        body=b'{"n":1}',
    )
    await dispatch(event)
    assert await event.json() == {"n": 1, "school": "demo"}
    assert event.headers["X-Tag"] == "classnet"
    assert event.headers["X-Foo"] == "bar"
    assert event.meta["url"].endswith("/api/chat")


@pytest.mark.asyncio
async def test_allow_stops_later_matchers() -> None:
    called = False

    @on_request(Rule.all(), priority=5)
    async def allow(event) -> None:
        event.allow()

    @on_request(Rule.all(), priority=1)
    async def later(event) -> None:
        nonlocal called
        called = True

    await dispatch(request_event())
    assert called is False


@pytest.mark.asyncio
async def test_response_rewrite_and_shared_meta() -> None:
    flow = request_event().flow
    flow.meta["mark"] = "from-request"
    body = BodyBuffer(100)
    body.append(b"<html></body>")
    flow.response = ResponseMessage(
        status=200,
        headers=HeaderMap([("Content-Type", "text/html")]),
        body=body,
    )
    event = ResponseEvent(flow)

    @on_response(Rule.host("example.com"))
    async def inject(response) -> None:
        assert response.meta["mark"] == "from-request"
        text = await response.text()
        response.rewrite(body=text.replace("</body>", "<p>hi</p></body>"))

    await dispatch(event)
    assert b"<p>hi</p>" in await event.bytes()


@pytest.mark.asyncio
async def test_connect_decision_defaults_to_intercept() -> None:
    event = ConnectEvent("example.com", 443)

    @on_connect(Rule.host("skip.example"), priority=10)
    async def tunnel(connect) -> None:
        connect.tunnel()

    await dispatch(event)
    assert event.decision.value == "intercept"

    skipped = ConnectEvent("skip.example", 443)
    await dispatch(skipped)
    assert skipped.decision.value == "tunnel"


@pytest.mark.asyncio
async def test_handler_error_does_not_stop_later_observers() -> None:
    seen: list[str] = []

    @on_request(Rule.all(), priority=10, block=False)
    async def broken(event: object) -> None:
        raise RuntimeError("plugin bug")

    @on_request(Rule.all(), priority=0, block=False)
    async def after(event: object) -> None:
        seen.append("after")

    await dispatch(request_event())
    assert seen == ["after"]


def test_response_requires_a_message() -> None:
    with pytest.raises(ValueError):
        ResponseEvent(HttpFlow(request=request_event().flow.request, client_host="", client_port=0))
