"""Get apps, daemon side: agents can only ask to install an app; the owner's approval runs the install."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

import get_apps as ga
from approval_queue import ApprovalQueue
from mcp_tools import DaemonUnavailable, McpConfig, OrionTools


class FakeBus:
    def __init__(self):
        self.events, self.logs = [], []

    async def publish(self, e, **k):
        self.events.append(e)

    async def log(self, m, level="info"):
        self.logs.append((level, m))


class FakeDaemon:
    def __init__(self, tmp):
        self.approvals = ApprovalQueue(Path(tmp) / "approvals.json")
        self.bus = FakeBus()


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    """A stand-in orion-apps: records its arguments, answers like the real one."""
    marker = tmp_path / "cli-calls.txt"
    script = tmp_path / "orion-apps"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(marker)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "app = sys.argv[2]\n"
        "ok = app != 'com.example.Broken'\n"
        "print(json.dumps({'ok': ok, 'app': app, 'message': ('Spotify is installed.' if ok else "
        "'It couldn\\'t be installed. Nothing was changed.')}))\n"
        "sys.exit(0 if ok else 1)\n")
    script.chmod(0o755)
    monkeypatch.setenv("ORION_APPS_CLI", str(script))
    import alerts
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append(a))
    return {"marker": marker, "alerts": sent}


def calls(cli):
    return cli["marker"].read_text().splitlines() if cli["marker"].exists() else []


def test_request_makes_one_card_and_installs_nothing(tmp_path, cli):
    d = FakeDaemon(tmp_path)
    r = asyncio.run(ga.request_install(d, "com.spotify.Client", "music while coding", "Claude"))
    assert r["status"] == "pending" and r["name"] == "Spotify"
    card = d.approvals.get(r["id"])
    assert card["kind"] == "app_install" and card["title"] == "Install Spotify?"
    assert "Claude asks to install Spotify (com.spotify.Client)" in card["original"]
    assert "one of Orion's picks" in card["original"] and "music while coding" in card["original"]
    assert d.bus.events[-1]["event"] == "APPROVAL_REQUIRED"
    assert cli["alerts"] and cli["alerts"][0][0] == "approval"
    again = asyncio.run(ga.request_install(d, "com.spotify.Client", "", "Codex"))
    assert again["id"] == r["id"] and len(d.approvals.pending()) == 1        # no duplicate card
    other = asyncio.run(ga.request_install(d, "org.example.Unknown", "", ""))
    assert "isn't one of Orion's picks" in d.approvals.get(other["id"])["original"]
    assert calls(cli) == []                                                   # nothing ran


@pytest.mark.parametrize("bad", ["", "spotify", "com.spotify.Client; rm -rf /", "../../etc/passwd", "a" * 300])
def test_bad_ids_refused(tmp_path, cli, bad):
    d = FakeDaemon(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(ga.request_install(d, bad))
    assert d.approvals.pending() == []


def test_reject_installs_nothing(tmp_path, cli):
    d = FakeDaemon(tmp_path)
    r = asyncio.run(ga.request_install(d, "com.spotify.Client"))
    asyncio.run(ga.decide_card(d, d.approvals.get(r["id"]), "reject"))
    assert d.approvals.get(r["id"])["status"] == "rejected"
    assert calls(cli) == []


def test_approve_installs_once(tmp_path, cli):
    d = FakeDaemon(tmp_path)
    r = asyncio.run(ga.request_install(d, "com.spotify.Client"))
    item = d.approvals.get(r["id"])
    asyncio.run(ga.decide_card(d, item, "approve"))
    done = d.approvals.get(r["id"])
    assert done["status"] == "installed" and done["result"] == "Spotify is installed."
    assert calls(cli) == ["install com.spotify.Client --json"]
    asyncio.run(ga.decide_card(d, item, "approve"))                          # a second click does nothing
    assert calls(cli) == ["install com.spotify.Client --json"]
    statuses = [e["item"]["status"] for e in d.bus.events if e["event"] == "APPROVAL_RESOLVED"]
    assert statuses == ["sending", "installed"]


def test_failed_install_is_reported(tmp_path, cli):
    d = FakeDaemon(tmp_path)
    r = asyncio.run(ga.request_install(d, "com.example.Broken"))
    asyncio.run(ga.decide_card(d, d.approvals.get(r["id"]), "approve"))
    item = d.approvals.get(r["id"])
    assert item["status"] == "failed" and "Nothing was changed" in item["error"]


def test_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_APPS_CLI", str(tmp_path / "nope"))
    res = asyncio.run(ga.run_install("com.spotify.Client"))
    assert res["ok"] is False


def test_routes(tmp_path, cli):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    d = FakeDaemon(tmp_path)
    app = FastAPI()
    app.include_router(ga.make_router(lambda: d))
    c = TestClient(app)
    res = c.post("/apps/install-request", json={"app_id": "org.blender.Blender", "requested_by": "Claude"})
    assert res.status_code == 200
    rid = res.json()["request"]["id"]
    assert c.get(f"/apps/install-request/{rid}").json()["request"]["status"] == "pending"
    assert c.get("/apps/install-request/nope").status_code == 404
    assert c.post("/apps/install-request", json={"app_id": "x; reboot"}).status_code == 400
    assert c.post("/apps/notify", json={"title": "Apps updated", "body": "Updated 2 apps."}).json() == {"ok": True}
    assert ("system", "Apps updated", "Updated 2 apps.") in cli["alerts"]
    assert c.post("/apps/notify", json={}).status_code == 400
    assert calls(cli) == []


class FakeBackend:
    def __init__(self, up=True):
        self.up, self.calls = up, []

    async def call(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        if not self.up:
            raise DaemonUnavailable("down")
        if method == "GET":
            return 200, {"request": {"id": "apr-1", "app_id": "org.blender.Blender", "name": "Blender",
                                     "status": "installed", "result": "Blender is installed."}}
        return 200, {"request": {"id": "apr-1", "name": "Blender", "status": "pending"}}


def test_mcp_tools(tmp_path):
    be = FakeBackend()
    tools = OrionTools(cfg=McpConfig(roots=[tmp_path]), backend=be, default_project=str(tmp_path))
    risks = {t["name"]: t["_meta"]["orion/risk"] for t in tools.list_tools()}
    assert risks["apps_install"] == "approval" and risks["apps_request_status"] == "read"
    text, err = asyncio.run(tools.call("apps_install", {"app_id": "org.blender.Blender", "why": "3D for the game"}))
    assert not err and "Nothing is installed until they approve" in text and "apr-1" in text
    assert be.calls[-1] == ("POST", "/apps/install-request", {"app_id": "org.blender.Blender",
                                                             "why": "3D for the game",
                                                             "requested_by": "Coding agent (MCP)"})
    text, err = asyncio.run(tools.call("apps_request_status", {"request_id": "apr-1"}))
    assert not err and "approved and installed" in text
    text, err = asyncio.run(tools.call("apps_install", {"app_id": "not an id"}))
    assert err
    tools.backend = FakeBackend(up=False)
    text, err = asyncio.run(tools.call("apps_install", {"app_id": "org.blender.Blender"}))
    assert err and "Nothing was installed" in text


def test_main_routes_app_install_decisions():
    src = (Path(ga.__file__).parent / "main.py").read_text()
    assert 'current["kind"] == "app_install"' in src and "get_apps.decide_card" in src
    assert "get_apps.make_router" in src
