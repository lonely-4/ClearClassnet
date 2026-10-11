"""Load plugins from built-in modules, directories, and entry points.

Importing a plugin module is what registers its matchers. A plugin that fails
to import is logged and skipped so one bad file does not stop the proxy.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path

from clearclassnet.config import Config

log = logging.getLogger(__name__)

BUILTIN_MODULES = (
    "clearclassnet.builtin.audit",
    "clearclassnet.builtin.blocklist",
    "clearclassnet.builtin.mitm_policy",
)


def load_plugins(config: Config) -> list[str]:
    """Import enabled plugins and return the module names that loaded."""
    loaded: list[str] = []
    disabled = config.plugins.disabled
    if config.plugins.builtins:
        for name in BUILTIN_MODULES:
            if _is_disabled(name, disabled):
                continue
            if _import_module(name):
                loaded.append(name)
    for name in config.plugins.enabled:
        if _is_disabled(name, disabled):
            continue
        if _import_module(name):
            loaded.append(name)
    for directory in config.plugins.dirs:
        loaded.extend(_load_directory(Path(directory), disabled))
    loaded.extend(_load_entry_points(disabled))
    return loaded


def _is_disabled(name: str, disabled: list[str]) -> bool:
    short = name.rsplit(".", 1)[-1]
    return name in disabled or short in disabled


def _import_module(name: str) -> bool:
    try:
        module = importlib.import_module(name)
        register = getattr(module, "register", None)
        if callable(register):
            register()
    except Exception:
        log.exception("failed to load plugin %s", name)
        return False
    return True


def _load_directory(directory: Path, disabled: list[str]) -> list[str]:
    if not directory.is_dir():
        log.warning("plugin directory does not exist: %s", directory)
        return []
    loaded: list[str] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"clearclassnet_plugins.{path.stem}"
        if _is_disabled(path.stem, disabled) or _is_disabled(module_name, disabled):
            continue
        if _import_path(module_name, path):
            loaded.append(module_name)
    return loaded


def _import_path(module_name: str, path: Path) -> bool:
    if module_name in sys.modules:
        return True
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if callable(register):
            register()
    except Exception:
        log.exception("failed to load plugin file %s", path)
        sys.modules.pop(module_name, None)
        return False
    return True


def _load_entry_points(disabled: list[str]) -> list[str]:
    from importlib.metadata import entry_points

    loaded: list[str] = []
    try:
        group = entry_points(group="clearclassnet.plugins")
    except Exception:
        log.exception("failed to read plugin entry points")
        return []
    for entry in group:
        if _is_disabled(entry.name, disabled) or _is_disabled(entry.value, disabled):
            continue
        try:
            module = entry.load()
        except Exception:
            log.exception("failed to load plugin entry point %s", entry.name)
            continue
        register = getattr(module, "register", None)
        if callable(register):
            try:
                register()
            except Exception:
                log.exception("failed to register plugin entry point %s", entry.name)
                continue
        loaded.append(entry.value)
    return loaded
