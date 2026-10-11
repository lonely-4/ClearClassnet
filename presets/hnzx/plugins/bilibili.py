"""Block Bilibili searches that contain a hardcoded keyword."""

from html import escape

from clearclassnet.dispatch import Rule, on_request
from clearclassnet.event import RequestEvent
from clearclassnet.response import HTMLResponse

_SEARCH_HOSTS = ("www.bilibili.com", "search.bilibili.com")

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
    if event.host.casefold() == "www.bilibili.com":
        if event.url.query.get("page", "") != "SearchResults":
            return ""
    return event.url.query.get("keyword", "").strip()


def _hit(keyword: str) -> str:
    folded = "".join(keyword.casefold().split())
    for word in _BANNED:
        if word.casefold() in folded:
            return word
    return ""


@on_request(Rule.host(_SEARCH_HOSTS), priority=10)
async def block_bilibili(event: RequestEvent) -> None:
    keyword = _keyword(event)
    if not keyword or not _hit(keyword):
        return
    event.reject(HTMLResponse(_PAGE.format(keyword=escape(keyword)), status_code=403))
