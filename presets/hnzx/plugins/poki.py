"""Block Poki on poki.com and poki.cn, including subdomains."""

from html import escape

from clearclassnet.dispatch import Rule, on_request
from clearclassnet.event import RequestEvent
from clearclassnet.response import HTMLResponse

_BLOCKED = ("poki.com", "*.poki.com", "poki.cn", "*.poki.cn")

_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>已拦截</title>
</head>
<body>
  <h1>本设备禁止访问此网站。</h1>
  <p><strong>{host}</strong> 在本设备上被屏蔽。</p>
  <hr />
  <p>Blocked by ClassNET.</p>
</body>
</html>
"""


@on_request(Rule.host(_BLOCKED), priority=10)
async def block_poki(event: RequestEvent) -> None:
    event.reject(HTMLResponse(_PAGE.format(host=escape(event.host)), status_code=403))
