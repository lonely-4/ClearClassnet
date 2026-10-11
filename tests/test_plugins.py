import json
import os
import sys
from pathlib import Path

import pytest

from clearclassnet.builtin.blocklist import apply_blocklist
from clearclassnet.builtin.mitm_policy import apply_mitm_policy
from clearclassnet.config import Config, PluginSettings, set_config
from clearclassnet.dispatch.bus import dispatch
from clearclassnet.dispatch.matcher import on_request
from clearclassnet.dispatch.registry import get_registry
from clearclassnet.dispatch.rule import Rule
from clearclassnet.event.http import ConnectEvent, RequestEvent
from clearclassnet.plugin import load_plugins
from tests.helpers import request_event


@pytest.mark.asyncio
async def test_blocklist_matches_host_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "block.txt"
    path.write_text("# comment\n*.blocked.test\nhttp://ok.test/secret\n", encoding="utf-8")
    set_config(Config(plugin_sections={"blocklist": {"path": str(path)}}))
    blocked = request_event(host="video.blocked.test")
    await apply_blocklist(blocked)
    assert blocked.synthetic_response() is not None

    allowed = request_event(host="ok.test", path="/public")
    await apply_blocklist(allowed)
    assert allowed.synthetic_response() is None

    secret = request_event(host="ok.test", path="/secret", scheme="http")
    await apply_blocklist(secret)
    assert secret.synthetic_response() is not None

    path.write_text("other.test\n", encoding="utf-8")
    later = path.stat().st_mtime_ns + 1_000_000
    os.utime(path, ns=(later, later))
    previous = request_event(host="video.blocked.test")
    await apply_blocklist(previous)
    assert previous.synthetic_response() is None


@pytest.mark.asyncio
async def test_blocklist_leaves_later_request_plugins_running(tmp_path: Path) -> None:
    (tmp_path / "block.txt").write_text("", encoding="utf-8")
    set_config(Config(plugin_sections={"blocklist": {"path": str(tmp_path / "block.txt")}}))
    from clearclassnet.builtin.blocklist import register

    register()

    @on_request(Rule.host("www.douyin.com"), priority=10)
    async def block_video(event: RequestEvent) -> None:
        event.reject(status=403, body=b"Blocked in class")

    event = request_event(host="www.douyin.com", scheme="https", port=443)
    await dispatch(event)
    synthetic = event.synthetic_response()
    assert synthetic is not None
    assert synthetic[2] == b"Blocked in class"


@pytest.mark.asyncio
async def test_mitm_policy_exclude_wins() -> None:
    set_config(
        Config(
            plugin_sections={
                "mitm_policy": {"include": ["*"], "exclude": ["*.bank.test"]},
            }
        )
    )
    excluded = ConnectEvent("login.bank.test", 443)
    await apply_mitm_policy(excluded)
    assert excluded.decision.value == "tunnel"

    included = ConnectEvent("class.test", 443)
    await apply_mitm_policy(included)
    assert included.decision.value == "intercept"


def test_loader_skips_disabled_builtins_and_loads_a_directory(tmp_path: Path) -> None:
    plugin = tmp_path / "sample.py"
    plugin.write_text(
        "from clearclassnet.dispatch import Rule, on_request\n"
        "from clearclassnet.event import RequestEvent\n"
        "@on_request(Rule.host('loaded.test'))\n"
        "async def mark(event: RequestEvent) -> None:\n"
        "    event.flow.meta['loaded'] = True\n",
        encoding="utf-8",
    )
    config = Config(
        plugins=PluginSettings(dirs=[str(tmp_path)], builtins=True, disabled=["audit"]),
    )
    set_config(config)
    loaded = load_plugins(config)
    names = [matcher.qualname for matcher in get_registry().all()]
    assert "clearclassnet.builtin.audit" not in " ".join(names)
    assert any(name.endswith("apply_blocklist") for name in names)
    assert any("sample" in name or name.endswith(":mark") for name in names)
    assert any(item.startswith("clearclassnet_plugins.") for item in loaded)

    sys.modules.pop("clearclassnet_plugins.sample", None)


@pytest.mark.asyncio
async def test_audit_writes_jsonl(tmp_path: Path) -> None:
    from clearclassnet.builtin.audit import audit_request

    path = tmp_path / "audit.jsonl"
    set_config(Config(plugin_sections={"audit": {"path": str(path)}}))
    await audit_request(request_event(path="/watched"))
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["phase"] == "request"
    assert record["url"].endswith("/watched")
