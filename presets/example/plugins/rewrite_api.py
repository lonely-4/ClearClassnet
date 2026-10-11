"""Example plugin: rewrite a JSON API request."""

from clearclassnet.dispatch import Rule, on_request, on_response
from clearclassnet.event import RequestEvent, ResponseEvent


@on_request(Rule.method("POST") & Rule.path("/api/*"))
async def rewrite_body(event: RequestEvent) -> None:
    data = await event.json()
    data["school"] = "demo"
    event.rewrite(json=data, headers={"X-Tag": "classnet"})

@on_response(Rule.method("GET"))
async def rewrite_response(event: ResponseEvent) -> None:
    event.rewrite(headers={"X-Mitm-By": "classnet"})