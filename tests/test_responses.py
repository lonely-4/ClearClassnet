import json

import pytest

from clearclassnet.response import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from tests.helpers import request_event


def test_buffered_responses_set_media_type_and_length() -> None:
    html = HTMLResponse("<h1>Blocked</h1>", status_code=403)
    assert html.status_code == 403
    assert html.body == b"<h1>Blocked</h1>"
    assert html.headers["Content-Type"] == "text/html; charset=utf-8"
    assert html.headers["Content-Length"] == str(len(html.body))

    text = PlainTextResponse("no")
    assert text.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert text.body == b"no"

    payload = JSONResponse({"ok": False, "name": "课"})
    assert payload.headers["Content-Type"] == "application/json"
    assert json.loads(payload.body) == {"ok": False, "name": "课"}

    base = Response(b"\x00\x01", media_type="application/octet-stream")
    assert base.headers["Content-Type"] == "application/octet-stream"
    assert base.body == b"\x00\x01"


def test_redirect_sets_location() -> None:
    response = RedirectResponse("https://school.example/blocked?q=1")
    assert response.status_code == 307
    assert response.headers["location"] == "https://school.example/blocked?q=1"
    assert response.body == b""


def test_respond_and_reject_accept_response_objects() -> None:
    event = request_event()
    event.respond(HTMLResponse("<p>hi</p>"))
    synthetic = event.synthetic_response()
    assert synthetic is not None
    status, headers, body = synthetic
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert body == b"<p>hi</p>"

    blocked = request_event()
    blocked.reject(JSONResponse({"blocked": True}, status_code=403))
    synthetic = blocked.synthetic_response()
    assert synthetic is not None
    assert synthetic[0] == 403
    assert synthetic[2] == b'{"blocked":true}'
    assert blocked.propagation_stopped


@pytest.mark.asyncio
async def test_streaming_response_yields_chunks() -> None:
    async def chunks():
        yield b"hel"
        yield "lo"

    response = StreamingResponse(chunks(), media_type="text/plain")
    assert "content-length" not in response.headers
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert b"".join([chunk async for chunk in response.iter_bytes()]) == b"hello"


def test_file_response_describes_the_file(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_bytes(b"class notes")
    response = FileResponse(path, filename="笔记.txt")
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert response.headers["Content-Length"] == "11"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert "filename*=utf-8''" in response.headers["Content-Disposition"]
    assert "ETag" in response.headers
    assert "Last-Modified" in response.headers
