"""Example plugin: reject a few hosts during class.

Run with::

    clearclassnet run --config presets/example/clearclassnet.toml
"""

from html import escape

from clearclassnet.dispatch import Rule, on_request
from clearclassnet.event import RequestEvent
from clearclassnet.response import HTMLResponse

_BLOCKED = ("douyin.com", "*.douyin.com", "tiktok.com", "*.tiktok.com", "ttwstatic.com")

_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>已拦截</title>
</head>
<body>
  <h1>已在课堂上拦截</h1>
  <p><strong>{host}</strong> 在本设备上被屏蔽。</p>
  <line />
  <p>Blocked by ClassNET.</p>
</body>
</html>
"""


@on_request(Rule.host(_BLOCKED), priority=10)
async def block_video(event: RequestEvent) -> None:
    page = _PAGE.format(host=escape(event.host))
    event.reject(HTMLResponse(page, status_code=403))
