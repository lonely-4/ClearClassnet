"""Windows background process and logon registration.

`run` stays in the foreground. `start` detaches a windowless interpreter and
records its pid. `install` only writes the current user's Run key so the next
logon calls `start` with the same config and working directory.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from clearclassnet.config import config_dir, load_config

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClearClassnet"
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000


def start_background(config_path: str | None, *, cwd: Path | None = None) -> int:
    """Start `run` in the background. Return the process id."""
    _require_windows()
    existing = running_pid()
    if existing is not None:
        return existing
    directory = _ensure_dir()
    workdir = (cwd or Path.cwd()).resolve()
    config = load_config(Path(config_path).resolve() if config_path else None)
    log_path = directory / "proxy.log"
    pid = spawn_hidden(run_argv(config_path), workdir, log_path)
    (directory / "proxy.pid").write_text(str(pid), encoding="ascii")
    if not wait_until_listening(config.proxy.host, config.proxy.port):
        terminate_pid(pid)
        _remove_pid()
        detail = _log_tail(log_path)
        raise SystemExit(
            f"proxy did not start listening on {config.proxy.host}:{config.proxy.port}\n{detail}"
        )
    return pid


def stop_background() -> int | None:
    """Stop the background proxy. Return the pid that was stopped."""
    _require_windows()
    pid = _read_pid()
    if pid is None or not pid_alive(pid):
        _remove_pid()
        return None
    terminate_pid(pid)
    _remove_pid()
    return pid


def install_autostart(config_path: str | None, *, cwd: Path | None = None) -> str:
    """Register logon autostart that calls `start` with no console window."""
    _require_windows()
    workdir = (cwd or Path.cwd()).resolve()
    command = subprocess.list2cmdline(start_argv(config_path, cwd=workdir))
    set_run_value(VALUE_NAME, command)
    leftover = config_dir() / "autostart.vbs"
    if leftover.is_file():
        leftover.unlink()
    return command


def uninstall_autostart() -> None:
    """Remove the logon registration. The running proxy is left alone."""
    _require_windows()
    delete_run_value(VALUE_NAME)
    leftover = config_dir() / "autostart.vbs"
    if leftover.is_file():
        leftover.unlink()


def run_argv(config_path: str | None) -> list[str]:
    argv = [_windowless_python(), "-m", "clearclassnet", "run"]
    if config_path:
        argv.extend(["--config", str(Path(config_path).resolve())])
    return argv


def start_argv(config_path: str | None, *, cwd: Path) -> list[str]:
    argv = [_windowless_python(), "-m", "clearclassnet", "start"]
    if config_path:
        argv.extend(["--config", str(Path(config_path).resolve())])
    argv.extend(["--cwd", str(cwd)])
    return argv


def running_pid() -> int | None:
    pid = _read_pid()
    if pid is None or not pid_alive(pid):
        return None
    return pid


def pid_alive(pid: int) -> bool:
    import ctypes

    process_query = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def spawn_hidden(argv: list[str], cwd: Path, log_path: Path) -> int:
    log_handle = log_path.open("ab")
    try:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            startupinfo=startup,
            creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS,
            close_fds=True,
        )
    finally:
        log_handle.close()
    return process.pid


def terminate_pid(pid: int) -> None:
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and pid_alive(pid):
        raise SystemExit(completed.stderr.strip() or f"failed to stop process {pid}")


def wait_until_listening(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


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


def _windowless_python() -> str:
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.is_file():
        return str(windowless)
    return str(executable)


def _ensure_dir() -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _read_pid() -> int | None:
    path = config_dir() / "proxy.pid"
    if not path.is_file():
        return None
    text = path.read_text(encoding="ascii").strip()
    if not text.isdigit():
        return None
    return int(text)


def _remove_pid() -> None:
    path = config_dir() / "proxy.pid"
    if path.is_file():
        path.unlink()


def _log_tail(path: Path) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-20:])


def _require_windows() -> None:
    if sys.platform != "win32":
        raise SystemExit("background control is only supported on Windows")
