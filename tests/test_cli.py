from pathlib import Path

import pytest

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


def test_loader_failure_is_not_fatal(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    config = Config(plugins=PluginSettings(dirs=[str(tmp_path)], builtins=False))
    set_config(config)
    assert load_plugins(config) == []
