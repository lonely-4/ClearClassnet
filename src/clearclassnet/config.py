"""Process configuration.

The core reads only proxy and logging settings. Plugin tables are stored raw and
returned through `plugin_config` so policy stays in plugins.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PLUGIN_KEYS = {"dirs", "builtins", "disabled", "enabled"}


@dataclass(slots=True)
class ProxySettings:
    host: str = "127.0.0.1"
    port: int = 8899
    body_limit: int = 1024 * 1024
    timeout: float = 30.0


@dataclass(slots=True)
class LoggingSettings:
    level: str = "INFO"


@dataclass(slots=True)
class PluginSettings:
    dirs: list[str] = field(default_factory=list)
    builtins: bool = True
    disabled: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Config:
    proxy: ProxySettings = field(default_factory=ProxySettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    plugins: PluginSettings = field(default_factory=PluginSettings)
    plugin_sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None


_current: Config = Config()


def config_dir() -> Path:
    """Per-user directory for the CA and other runtime files."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "clearclassnet"


def set_config(config: Config) -> None:
    global _current
    _current = config


def get_config() -> Config:
    return _current


def plugin_config(name: str) -> dict[str, Any]:
    """Return the `[plugins.<name>]` table, or an empty dict when it is absent."""
    section = _current.plugin_sections.get(name, {})
    return dict(section)


def load_config(path: Path | None = None) -> Config:
    """Load TOML config. A missing file yields defaults."""
    if path is None:
        return Config()
    source = path.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"config file not found: {source}")
    with source.open("rb") as handle:
        raw = tomllib.load(handle)
    return _from_table(raw, source)


def _from_table(raw: dict[str, Any], path: Path | None) -> Config:
    proxy_raw = raw.get("proxy", {})
    logging_raw = raw.get("logging", {})
    plugins_raw = raw.get("plugins", {})
    if not isinstance(plugins_raw, dict):
        raise ValueError("[plugins] must be a table")
    sections = {
        key: dict(value)
        for key, value in plugins_raw.items()
        if key not in _PLUGIN_KEYS and isinstance(value, dict)
    }
    return Config(
        proxy=ProxySettings(
            host=str(proxy_raw.get("host", "127.0.0.1")),
            port=int(proxy_raw.get("port", 8899)),
            body_limit=int(proxy_raw.get("body_limit", 1024 * 1024)),
            timeout=float(proxy_raw.get("timeout", 30.0)),
        ),
        logging=LoggingSettings(level=str(logging_raw.get("level", "INFO")).upper()),
        plugins=PluginSettings(
            dirs=[str(item) for item in plugins_raw.get("dirs", [])],
            builtins=bool(plugins_raw.get("builtins", True)),
            disabled=[str(item) for item in plugins_raw.get("disabled", [])],
            enabled=[str(item) for item in plugins_raw.get("enabled", [])],
        ),
        plugin_sections=sections,
        path=path,
    )
