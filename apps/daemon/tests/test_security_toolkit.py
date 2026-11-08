"""The four security tools wired together (security_toolkit.py): clock, cards, alerts, phrases, routes."""
import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest

import security_toolkit as ST
from sec_fakes import FakeShell, fixture


class FakeBus:
    def __init__(self):
        self.events, self.logs = [], []

    async def publish(self, e, **k):
        self.events.append(e)

    async def log(self, m, level="info"):
        self.logs.append((level, m))


class FakeDaemon:
    def __init__(self, tmp):
        from approval_queue import ApprovalQueue
        self.approvals = ApprovalQueue(Path(tmp) / "approvals.json")
        self.bus = FakeBus()


def shell(funnel=False, tools=("ss", "ip", "tailscale", "osv-scanner")):
    from exposure_map import UFW_DUMP
    return FakeShell({"ss -tulpnH": (0, fixture("ss.txt")), "ip -j addr": (0, fixture("ip_addr.json")),
                      "tailscale status --json": (0, fixture("tailscale_status.json")),
                      "tailscale debug prefs": (0, "{}"),
                      "tailscale serve status --json": (0, fixture("tailscale_serve.json") if funnel else "{}"),
                      "osv-scanner scan source": (1, fixture("osv.json"))},
                     tools=tools, files={str(UFW_DUMP): fixture("ufw.txt")})


@pytest.fixture
def tk(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr(ST, "alert", lambda t, b, ref: alerts.append((t, b)))
    monkeypatch.setattr(ST, "machine_name", lambda: "orionos")
    monkeypatch.setattr("exposure_map.machine_name", lambda: "orionos")
    monkeypatch.setattr("host_health.machine_name", lambda: "orionos")
    proj = tmp_path / "openwow"
    proj.mkdir()
    (proj / "Cargo.lock").write_text("# lock")
    d = FakeDaemon(tmp_path)
    t = ST.SecurityToolkit(d, data_dir=tmp_path / "data", sh=shell(), repo_root=tmp_path)
    t.projects = lambda: [proj]
    t.alerts = alerts
    t.spawned = []
    t.spawn = lambda coro: (t.spawned.append(coro), coro.close())
    return t


SUNDAY = dt.datetime(2026, 10, 18, 10, 0).timestamp()


def test_tick_runs_what_is_due(tk, monkeypatch):
    monkeypatch.setattr("host_health.gather", lambda sh, *a, **k: {
        "lynis": None, "aide": None, "battery": None, "disks": [], "bruteforce": {"on": False}, "profile": "",
        "notes": []})
    did = asyncio.run(tk.tick(now=SUNDAY))
    assert did["exposure"] == 0 and did["supply"] == 1 and did["supply_card"] and did["health"]
    assert did["deps"] == "error"                     # the fixture project isn't a repo, so it's only noted
    titles = sorted(c["title"] for c in tk.d.approvals.pending())
    assert titles == ["Weekly check-up: orionos scores 70/100", "Weekly dependency check: 2 known holes (1 serious)"]
    assert tk.alerts[0][0] == "1 new serious hole in your dependencies"
    assert len(tk.spawned) == 1                       # the security fix starts Dependency Autopilot at once
    # an hour later: nothing new is due, and the same hole doesn't alert again
    did = asyncio.run(tk.tick(now=SUNDAY + 3600))
    assert set(did) == {"exposure"} and len(tk.alerts) == 1
    asyncio.run(tk.supply_scan())
    assert len(tk.alerts) == 1


def test_exposure_change_becomes_a_card_and_alert(tk):
    asyncio.run(tk.exposure_scan())
    assert tk.d.approvals.pending() == []             # the first map is the baseline
    tk.sh = shell(funnel=True)
    m = asyncio.run(tk.exposure_scan())
    assert [c["row"]["port"] for c in m["changes"]] == [3000]
    card = tk.d.approvals.pending()[0]
    assert card["title"] == "Now open to the internet on orionos" and card["category"] == "urgent"
    assert tk.alerts[-1][0] == card["title"]
    asyncio.run(tk.decide(card, "approve"))
    assert tk.d.approvals.get(card["id"])["status"] == "handled"


def test_phrases(tk, monkeypatch):
    plugins = {p.name: p for p in tk.plugins()}
    assert plugins["exposure"].match("what's open on the server") == "the server"
    assert plugins["hosthealth"].match("how healthy is the laptop") == "the laptop"
    assert plugins["imagesafe"].match("is the usb image safe to ship") == "image"
    assert plugins["depsauto"].match("check the dependencies for openwow") == "openwow"
    assert plugins["depsauto"].match("update dependencies") == "*"
    asyncio.run(tk.exposure_scan())
    out = asyncio.run(plugins["exposure"].run("the server"))
    assert "I don't have a map of “the server” yet" in out and "On orionos" in out
    assert asyncio.run(plugins["exposure"].run("this computer")).startswith("On orionos")
    assert "Report-Listeners.ps1" in asyncio.run(plugins["exposure"].run("the pc"))
    assert "no image scan found" in asyncio.run(plugins["imagesafe"].run("image"))
    out = asyncio.run(plugins["depsauto"].run("openwow"))
    assert out.startswith("Checking updates for openwow") and len(tk.spawned) == 1


def test_config_validation(tk):
    with pytest.raises(ValueError):
        tk.set_config({"exposure": {"nope": True}})
    with pytest.raises(ValueError):
        tk.set_config({"deps": {"projects": "x"}})
    assert tk.set_config({"exposure": {"lan_scan": 1}})["exposure"]["lan_scan"] is True


def test_routes(tk):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class D:
        security_toolkit = tk
    app = FastAPI()
    app.include_router(ST.make_router(lambda: D))
    c = TestClient(app)
    st = c.get("/security/status").json()
    assert st["machine"] == "orionos" and st["supply"]["image"]["safe"] is None
    r = c.post("/exposure/report", json=json.loads(fixture("windows_report.json"))).json()
    print(r)
    assert r["ok"] and r["machine"] == "GAMING-PC" and "3389/tcp" in r["text"]
    assert "GAMING-PC" in c.get("/exposure").json()["maps"]
    assert c.get("/exposure", params={"machine": "the pc"}).json()["map"]["machine"] == "GAMING-PC"
    assert c.post("/exposure/report", json={"listeners": "x"}).status_code == 400
    assert c.post("/security/config", json={"bad": {}}).status_code == 400
    assert c.post("/exposure/lan-scan").status_code == 400            # off unless you turn it on
    assert c.post("/supply/scan").json()["serious"] == 1
    assert c.get("/supply").json()["report"]["tools"] == ["osv-scanner"]


def test_mcp_tools_are_registered():
    from mcp_tools import OrionTools
    names = set(OrionTools().tools)
    assert {"exposure_map", "host_health", "supply_chain_scan", "image_safe_to_ship",
            "dependency_autopilot"} <= names
