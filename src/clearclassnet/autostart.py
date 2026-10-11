"""Register ClearClassnet to start at logon on Windows, without a console window."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from clearclassnet.config import config_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClearClassnet"


def launch_argv(config_path: str | None) -> list[str]:
    """Argv for a windowless interpreter running `clearclassnet run`."""
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.is_file():
        executable = windowless
    argv = [str(executable), "-m", "clearclassnet", "run"]
    if config_path:
        argv.extend(["--config", str(Path(config_path).resolve())])
    return argv


def render_launcher(cwd: Path, command: str) -> str:
    """VBScript that starts the proxy with a hidden window in `cwd`."""
    return (
        'Set shell = CreateObject("Wscript.Shell")\r\n'
        f'shell.CurrentDirectory = "{_vbs(str(cwd))}"\r\n'
        f'shell.Run "{_vbs(command)}", 0, False\r\n'
    )


def enable_autostart(config_path: str | None, *, cwd: Path | None = None) -> Path:
    """Write the hidden launcher and register it for the current user."""
    _require_windows()
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    launcher = directory / "autostart.vbs"
    command = subprocess.list2cmdline(launch_argv(config_path))
    launcher.write_text(render_launcher(cwd or Path.cwd(), command), encoding="utf-8")
    set_run_value(VALUE_NAME, _wscript_command(launcher))
    return launcher


def disable_autostart() -> None:
    """Remove the logon registration for the current user."""
    _require_windows()
    delete_run_value(VALUE_NAME)


def set_run_value(name: str, command: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def delete_run_value(name: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


def _require_windows() -> None:
    if sys.platform != "win32":
        raise SystemExit("autostart is only supported on Windows")


def _wscript_command(launcher: Path) -> str:
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    wscript = root / "System32" / "wscript.exe"
    return subprocess.list2cmdline([str(wscript), "//B", "//Nologo", str(launcher)])


def _vbs(value: str) -> str:
    return value.replace('"', '""')
