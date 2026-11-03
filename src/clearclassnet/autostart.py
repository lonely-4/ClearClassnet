"""Windows background process and logon registration.

`run` stays in the foreground. `start` detaches a windowless interpreter and
records its pid. `install` only writes the current user's Run key so the next
logon calls `start` with the same config and working directory. `--daemon`
on either command makes that `start` also detach a process that keeps the
Windows system proxy pointed at this proxy.
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


def start_background(
    config_path: str | None,
    *,
    cwd: Path | None = None,
    daemon: bool = False,
) -> int:
    """Start `run` in the background. Return the process id."""
    _require_windows()
    config = load_config(Path(config_path).resolve() if config_path else None)
    existing = running_pid()
    if existing is not None:
        pid = existing
    else:
        directory = _ensure_dir()
        workdir = (cwd or Path.cwd()).resolve()
        log_path = directory / "proxy.log"
        pid = spawn_hidden(run_argv(config_path), workdir, log_path)
        (directory / "proxy.pid").write_text(str(pid), encoding="ascii")
        if not wait_until_listening(config.proxy.host, config.proxy.port):
            terminate_pid(pid)
            _remove_pid()
            detail = _log_tail(log_path)
            address = f"{config.proxy.host}:{config.proxy.port}"
            raise SystemExit(f"proxy did not start listening on {address}\n{detail}")
    if daemon:
        ensure_proxy_guard(config.proxy.host, config.proxy.port)
    return pid


def stop_background() -> int | None:
    """Stop the background proxy and its system-proxy guard."""
    _require_windows()
    stop_proxy_guard()
    pid = _read_pid()
    if pid is None or not pid_alive(pid):
        _remove_pid()
        return None
    terminate_pid(pid)
    _remove_pid()
    return pid


def install_autostart(
    config_path: str | None,
    *,
    cwd: Path | None = None,
    daemon: bool = False,
) -> str:
    """Register logon autostart that calls `start` with no console window."""
    _require_windows()
    workdir = (cwd or Path.cwd()).resolve()
    command = subprocess.list2cmdline(start_argv(config_path, cwd=workdir, daemon=daemon))
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


def start_argv(config_path: str | None, *, cwd: Path, daemon: bool = False) -> list[str]:
    argv = [_windowless_python(), "-m", "clearclassnet", "start"]
    if config_path:
        argv.extend(["--config", str(Path(config_path).resolve())])
    argv.extend(["--cwd", str(cwd)])
    if daemon:
        argv.append("--daemon")
    return argv


def guard_argv(host: str, port: int) -> list[str]:
    return [
        _windowless_python(),
        "-m",
        "clearclassnet",
        "proxy-guard",
        "--host",
        host,
        "--port",
        str(port),
    ]


def ensure_proxy_guard(host: str, port: int) -> int:
    """Start the system-proxy guard when it is not already running."""
    existing = _read_named_pid("proxy-guard.pid")
    if existing is not None and pid_alive(existing):
        return existing
    directory = _ensure_dir()
    pid = spawn_hidden(guard_argv(host, port), directory, directory / "proxy-guard.log")
    (directory / "proxy-guard.pid").write_text(str(pid), encoding="ascii")
    return pid


def stop_proxy_guard() -> int | None:
    """Stop the system-proxy guard. Return the pid that was stopped."""
    pid = _read_named_pid("proxy-guard.pid")
    if pid is not None and pid_alive(pid):
        terminate_pid(pid)
    else:
        pid = None
    _remove_named_pid("proxy-guard.pid")
    return pid


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
    return _read_named_pid("proxy.pid")


def _remove_pid() -> None:
    _remove_named_pid("proxy.pid")


def _read_named_pid(name: str) -> int | None:
    path = config_dir() / name
    if not path.is_file():
        return None
    text = path.read_text(encoding="ascii").strip()
    if not text.isdigit():
        return None
    return int(text)


def _remove_named_pid(name: str) -> None:
    path = config_dir() / name
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
