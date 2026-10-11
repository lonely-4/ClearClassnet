from pathlib import Path

import pytest

from clearclassnet.autostart import launch_argv, render_launcher
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


def test_autostart_registers_a_hidden_windows_launcher(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "win32")
    monkeypatch.setattr("clearclassnet.autostart.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "clearclassnet.autostart.launch_argv",
        lambda config: [r"C:\Python\pythonw.exe", "-m", "clearclassnet", "run", "--config", config],
    )
    recorded: dict[str, str] = {}
    monkeypatch.setattr(
        "clearclassnet.autostart.set_run_value",
        lambda name, command: recorded.update(name=name, command=command),
    )
    main(["autostart", "--config", "presets/hnzx/clearclassnet.toml"])
    script = (tmp_path / "autostart.vbs").read_text(encoding="utf-8")
    assert "pythonw.exe" in script
    assert ", 0, False" in script
    assert "wscript.exe" in recorded["command"]
    assert recorded["name"] == "ClearClassnet"
    assert "autostart registered" in capsys.readouterr().out


def test_autostart_disable_removes_the_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "win32")
    removed: list[str] = []
    monkeypatch.setattr("clearclassnet.autostart.delete_run_value", removed.append)
    main(["autostart", "--disable"])
    assert removed == ["ClearClassnet"]


def test_autostart_rejects_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clearclassnet.autostart.sys.platform", "linux")
    with pytest.raises(SystemExit, match="only supported on Windows"):
        main(["autostart"])


def test_hidden_launcher_uses_pythonw_and_keeps_the_working_directory(tmp_path: Path) -> None:
    monkeypatch_executable = r"C:\Python\python.exe"
    windowless = Path(monkeypatch_executable).with_name("pythonw.exe")
    argv = launch_argv(None)
    assert argv[1:] == ["-m", "clearclassnet", "run"]
    script = render_launcher(tmp_path, f'"{windowless}" -m clearclassnet run')
    assert str(tmp_path) in script
    assert ", 0, False" in script


def test_loader_failure_is_not_fatal(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    config = Config(plugins=PluginSettings(dirs=[str(tmp_path)], builtins=False))
    set_config(config)
    assert load_plugins(config) == []
