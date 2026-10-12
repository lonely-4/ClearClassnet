from pathlib import Path

import pytest

from clearclassnet.autostart import run_argv, start_argv
from clearclassnet.cli import format_matchers, main
from clearclassnet.config import Config, PluginSettings, set_config
from clearclassnet.plugin import load_plugins
from clearclassnet.tls.store import trust_command


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "--config"],
        ["--config", "check"],
    ],
)
def test_check_prints_loaded_matchers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    config_path = tmp_path / "clearclassnet.toml"
    config_path.write_text(
        "[plugins]\nbuiltins = true\ndisabled = ['audit']\n",
        encoding="utf-8",
    )
    config_at = argv.index("--config")
    main([*argv[: config_at + 1], str(config_path), *argv[config_at + 1 :]])
    output = capsys.readouterr().out
    assert "apply_blocklist" in output
    assert "apply_mitm_policy" in output
    assert "audit_request" not in output


def test_format_matchers_when_empty() -> None:
    assert format_matchers() == "No matchers registered."


def test_ca_path_and_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    main(["ca", "path"])
    printed = Path(capsys.readouterr().out.strip())
    assert printed.is_file()
    destination = tmp_path / "exported.pem"
    main(["ca", "export", "--out", str(destination)])
    assert destination.read_bytes() == printed.read_bytes()
    assert str(destination) in trust_command(destination)


def test_start_detaches_a_hidden_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "win32")
    monkeypatch.setattr("clearclassnet.autostart.config_dir", lambda: tmp_path)
    monkeypatch.setattr("clearclassnet.autostart.pid_alive", lambda _pid: False)
    monkeypatch.setattr("clearclassnet.autostart.wait_until_listening", lambda host, port: True)
    seen: dict[str, object] = {}

    def spawn(argv: list[str], cwd: Path, log_path: Path) -> int:
        seen["argv"] = argv
        seen["cwd"] = cwd
        seen["log"] = log_path
        return 4242

    monkeypatch.setattr("clearclassnet.autostart.spawn_hidden", spawn)
    main(["start", "--cwd", str(tmp_path)])
    assert seen["argv"] == run_argv(None)
    assert seen["cwd"] == tmp_path.resolve()
    assert (tmp_path / "proxy.pid").read_text(encoding="ascii") == "4242"
    assert "4242" in capsys.readouterr().out


def test_stop_terminates_the_recorded_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "win32")
    monkeypatch.setattr("clearclassnet.autostart.config_dir", lambda: tmp_path)
    (tmp_path / "proxy.pid").write_text("99", encoding="ascii")
    monkeypatch.setattr("clearclassnet.autostart.pid_alive", lambda pid: pid == 99)
    stopped: list[int] = []
    monkeypatch.setattr("clearclassnet.autostart.terminate_pid", stopped.append)
    main(["stop"])
    assert stopped == [99]
    assert not (tmp_path / "proxy.pid").exists()


def test_install_registers_start_without_a_script(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "win32")
    monkeypatch.setattr("clearclassnet.autostart.config_dir", lambda: tmp_path)
    (tmp_path / "autostart.vbs").write_text("old", encoding="utf-8")
    recorded: dict[str, str] = {}
    monkeypatch.setattr(
        "clearclassnet.autostart.set_run_value",
        lambda name, command: recorded.update(name=name, command=command),
    )
    config = tmp_path / "clearclassnet.toml"
    main(["install", "--config", str(config)])
    assert recorded["name"] == "ClearClassnet"
    assert " start " in f" {recorded['command']} "
    assert "--cwd" in recorded["command"]
    assert "wscript" not in recorded["command"]
    assert not (tmp_path / "autostart.vbs").exists()
    assert "logon autostart registered" in capsys.readouterr().out


def test_uninstall_removes_the_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "win32")
    removed: list[str] = []
    monkeypatch.setattr("clearclassnet.autostart.delete_run_value", removed.append)
    main(["uninstall"])
    assert removed == ["ClearClassnet"]


def test_background_commands_reject_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "linux")
    with pytest.raises(SystemExit, match="only supported on Windows"):
        main(["start"])


def test_hidden_interpreter_runs_the_foreground_command() -> None:
    argv = run_argv(None)
    assert argv[1:] == ["-m", "clearclassnet", "run"]
    registered = start_argv(None, cwd=Path(r"C:\classnet"))
    assert registered[1:4] == ["-m", "clearclassnet", "start"]
    assert registered[-2:] == ["--cwd", r"C:\classnet"]


def test_loader_failure_is_not_fatal(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    config = Config(plugins=PluginSettings(dirs=[str(tmp_path)], builtins=False))
    set_config(config)
    assert load_plugins(config) == []
