"""Everyone can use it (complete plan step 6): guest-mode gating and the HUD's accessibility settings."""
import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import everyone


def test_guest_mode_from_env_and_user(monkeypatch):
    monkeypatch.setenv("ORION_GUEST_MODE", "1")
    assert everyone.guest_mode()
    monkeypatch.setenv("ORION_GUEST_MODE", "0")
    monkeypatch.setattr(everyone, "GUEST_USER", "no-such-user-here")
    assert not everyone.guest_mode()
    import pwd, os
    monkeypatch.setattr(everyone, "GUEST_USER", pwd.getpwuid(os.getuid()).pw_name)
    assert everyone.guest_mode()            # the guest account can't switch it off in its own daemon.env


def test_http_gate_rules():
    ok = everyone.http_allowed
    assert ok("GET", "/health") and ok("GET", "/memory/search") and ok("POST", "/say") and ok("OPTIONS", "/x")
    for method, path in [("GET", "/approvals"), ("POST", "/approvals/abc"), ("GET", "/comms"),
                         ("POST", "/telegram/send"), ("POST", "/agent/ping"), ("GET", "/boardroom/state"),
                         ("POST", "/setup/check"), ("POST", "/wow/request"), ("POST", "/vault/drop"),
                         ("POST", "/apps/install-request"), ("POST", "/automation/execute"), ("GET", "/phone/state"),
                         ("POST", "/hotmic"), ("POST", "/quick"), ("GET", "/stardust/ledger"), ("GET", "/mcp/tools"),
                         ("POST", "/shutdown"), ("GET", "/memoryx")]:
        assert not ok(method, path), (method, path)


def test_ws_gate():
    assert everyone.ws_allowed("USER_PROMPT") and everyone.ws_allowed("PING")
    for ev in ("APPROVAL_DECISION", "AGENT_TOGGLE", "SEND_TELEGRAM", "COMMS_REPLY", "EXECUTE_SCREEN_ACTION",
               "SELF_EVOLVE_REQUEST", "BOARDROOM_SIMULATE", None):
        assert not everyone.ws_allowed(ev)


def _app():
    app = FastAPI()
    everyone.install(app, lambda: None)

    @app.get("/approvals")
    def approvals():
        return {"items": []}

    @app.get("/health")
    def health():
        return {"ok": True}
    return app


def test_middleware_blocks_only_in_guest_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("ORION_ACCESS_FILE", str(tmp_path / "access.json"))
    monkeypatch.setattr(everyone, "GUEST_USER", "no-such-user-here")
    monkeypatch.setenv("ORION_GUEST_MODE", "0")
    c = TestClient(_app())
    assert c.get("/approvals").status_code == 200
    assert c.get("/guest").json() == {"guest": False}
    monkeypatch.setenv("ORION_GUEST_MODE", "1")
    assert c.get("/approvals").status_code == 403
    assert c.get("/health").status_code == 200
    assert c.get("/guest").json() == {"guest": True}
    assert c.get("/access").json()["text_scale"] == 1.0


def test_read_access(tmp_path):
    f = tmp_path / "access.json"
    assert everyone.read_access(f) == {"contrast": False, "text_scale": 1.0, "hud_zoom": 1.0, "screen_reader": False,
                                       "keyboard": False}
    f.write_text(json.dumps({"contrast": True, "text_scale": 1.6, "hud_zoom": "big", "screen_reader": "yes"}))
    a = everyone.read_access(f)
    assert a["contrast"] is True and a["text_scale"] == 1.5 and a["hud_zoom"] == 1.0 and a["screen_reader"] is False
    f.write_text("[1, 2]")
    assert everyone.read_access(f)["contrast"] is False


class _Bus:
    def __init__(self):
        self.sent = []

    async def send(self, client, msg):
        self.sent.append(msg)


class _Part:
    def __init__(self):
        self.started = False

    async def start(self, *a, **k):
        self.started = True


class _Daemon:
    def __init__(self):
        self.agents = {"system_capturer": True, "proactive_observer": True}
        self.board, self.setup, self.wow = _Part(), _Part(), _Part()
        self.bus = _Bus()
        self.seen = []

    async def dispatch(self, client, msg):
        self.seen.append(msg["event"])


def test_limit_daemon_in_guest_mode(monkeypatch):
    monkeypatch.setenv("ORION_GUEST_MODE", "1")
    d = _Daemon()
    everyone.limit_daemon(d)
    assert not any(d.agents.values())

    async def go():
        await d.board.start(simulator_every_s=1)
        await d.setup.start()
        await d.dispatch(None, {"event": "APPROVAL_DECISION", "id": "x"})
        await d.dispatch(None, {"event": "USER_PROMPT", "text": "hi"})
    asyncio.run(go())
    assert not d.board.started and not d.setup.started
    assert d.seen == ["USER_PROMPT"]
    assert "guest" in d.bus.sent[0]["message"]


def test_limit_daemon_leaves_normal_sessions_alone(monkeypatch):
    monkeypatch.setenv("ORION_GUEST_MODE", "0")
    monkeypatch.setattr(everyone, "GUEST_USER", "no-such-user-here")
    d = _Daemon()
    everyone.limit_daemon(d)
    assert all(d.agents.values())
    asyncio.run(d.dispatch(None, {"event": "APPROVAL_DECISION"}))
    assert d.seen == ["APPROVAL_DECISION"]
