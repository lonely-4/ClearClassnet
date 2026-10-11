"""Example plugin: reject a few hosts during class.

Run with::

    clearclassnet run --config examples/clearclassnet.toml
"""

from clearclassnet import RequestEvent, Rule, on_request

_BLOCKED = ("douyin.com", "*.douyin.com", "tiktok.com", "*.tiktok.com", "ttwstatic.com")


@on_request(Rule.host(_BLOCKED), priority=10)
async def block_video(event: RequestEvent) -> None:
    event.reject(status=403, body=b"Blocked in class")
