"""Public matcher registration.

Import `Rule` and `on_request` / `on_response` / `on_connect` from this package.
The bus and registry underneath stay internal.
"""

from clearclassnet.dispatch.bus import dispatch
from clearclassnet.dispatch.matcher import Matcher, on_connect, on_request, on_response
from clearclassnet.dispatch.registry import Registry, get_registry
from clearclassnet.dispatch.rule import Rule

__all__ = [
    "Matcher",
    "Registry",
    "Rule",
    "dispatch",
    "get_registry",
    "on_connect",
    "on_request",
    "on_response",
]
