"""Keep the current user's Windows proxy pointed at this process.

The guard is a separate process so it keeps rewriting
`Internet Settings` after other software clears `ProxyEnable` or
`ProxyServer`. It only touches the current user hive.
"""

from __future__ import annotations

import sys
import time

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_SETTINGS_CHANGED = 39
_REFRESH = 37


def proxy_server_value(host: str, port: int) -> str:
    """Per-protocol value stored in `ProxyServer`."""
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    endpoint = f"{host}:{port}"
    return f"http={endpoint};https={endpoint}"


def current_system_proxy() -> tuple[int, str] | None:
    """Return `(ProxyEnable, ProxyServer)`, or `None` when either value is missing."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS) as key:
            enabled, _kind = winreg.QueryValueEx(key, "ProxyEnable")
            server, _kind = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    return int(enabled), str(server)


def apply_system_proxy(server: str) -> None:
    """Enable the user proxy and point it at `server`."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        try:
            winreg.QueryValueEx(key, "ProxyOverride")
        except OSError:
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
    notify_system_proxy()


def notify_system_proxy() -> None:
    """Ask WinINet to reload the proxy settings just written."""
    import ctypes

    wininet = ctypes.WinDLL("wininet")
    for option in (_SETTINGS_CHANGED, _REFRESH):
        wininet.InternetSetOptionW(None, option, None, 0)


def enforce_system_proxy(host: str, port: int) -> bool:
    """Rewrite the system proxy when it is not already the desired value."""
    desired = proxy_server_value(host, port)
    if current_system_proxy() == (1, desired):
        return False
    apply_system_proxy(desired)
    return True


def run_guard(host: str, port: int, *, interval: float = 1.0) -> None:
    """Reapply the system proxy until the process is killed."""
    if sys.platform != "win32":
        raise SystemExit("the proxy guard only runs on Windows")
    while True:
        try:
            enforce_system_proxy(host, port)
        except Exception as exc:
            print(f"proxy guard: {exc}", file=sys.stderr)
        time.sleep(interval)
