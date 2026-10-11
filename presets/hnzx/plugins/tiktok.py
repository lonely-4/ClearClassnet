"""Block Douyin searches that contain a hardcoded keyword.

Only https://www.douyin.com/search/<keyword>. Other Douyin hosts are left alone.
"""

from html import escape
from urllib.parse import unquote

from clearclassnet.dispatch import Rule, on_request
from clearclassnet.event import RequestEvent
from clearclassnet.response import HTMLResponse

_BANNED = (
    "色情",
    "黄片",
    "裸体",
    "赌博",
    "毒品",
    "大麻",
    "血腥",
    "自杀",
)

_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>关键词拦截</title>
</head>
<body>
  <h1>本设备禁止搜索此关键词。</h1>
  <p><strong>{keyword}</strong> 在本设备上被屏蔽。</p>
  <hr />
  <p>Blocked by ClassNET.</p>
</body>
</html>
"""


def _keyword(event: RequestEvent) -> str:
    prefix = "/search/"
    if not event.path.startswith(prefix):
        return ""
    raw = event.path[len(prefix) :].split("/", 1)[0]
    return unquote(raw).strip()


def _hit(keyword: str) -> bool:
    folded = "".join(keyword.casefold().split())
    return any(word.casefold() in folded for word in _BANNED)


@on_request(Rule.host("www.douyin.com") & (Rule.path("/search/*") | Rule.path("/jingxuan/search/*")), priority=10)
async def block_douyin(event: RequestEvent) -> None:
    keyword = _keyword(event)
    if not keyword or not _hit(keyword):
        return
    event.reject(HTMLResponse(_PAGE.format(keyword=escape(keyword)), status_code=403))
