"""HTTP clients that do not inherit the ambient proxy.

httpx normally trusts `HTTP_PROXY` and, on Windows, the system proxy from the
registry. A plugin request sent that way comes back into this process. These
clients are created with `trust_env=False`, so they connect directly.

They are process-wide. Do not close them and do not use them as context
managers.
"""

from __future__ import annotations

import httpx as _httpx

httpx = _httpx.Client(trust_env=False)
httpxAsync = _httpx.AsyncClient(trust_env=False)

__all__ = ["httpx", "httpxAsync"]
