"""The PC side of the phone pair: /phone/home, /phone/draft, /phone/inbox, /phone/hotmic, /phone/agent/..."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import agent_swarm
import boardroom as br
import freeze
import phone_pair as pp
from approval_queue import ApprovalQueue
from comms_hub import CommsHub
from tests.test_phones import FakePhone, paired


async def _nopublish(event):
    return None


class FakeMic:
    def __init__(self):
        self.on = False

    @property
    def listening(self):
        return self.on

    def start(self):
        self.on = True
        return {"listening": True}

    def stop(self, remember=True):
        self.on = False
        return {"listening": False}


class FakeSwarm:
    def __init__(self):
        self.calls = []

    async def act(self, agent_id, action):
        self.calls.append((agent_id, action))
        if agent_id.startswith("codex:"):
            return False, agent_swarm.EXTERNAL_NOTE
        return True, {"pause": "Paused.", "stop": "Stopping it.", "resume": "Running again.",
                      "open": "Opened orion."}[action]


class FakeToolkit:
    def __init__(self):
        self.swarm = FakeSwarm()

    def agents(self):
        return [{"id": "claude:run1", "name": "Claude · orion", "tool": "claude", "status": "Editing files",
                 "state": "working", "cwd": "/secret/path", "events": ["x"]},
                {"id": "codex:abc", "name": "Codex", "tool": "codex", "status": "Finished", "state": "done"}]


class FakeDrafter:
    """Pretends to be the style drafter; its model 'leaks' a key so we can check the redaction."""

    def __init__(self, reply):
        self.reply = reply
        self.memory = SimpleNamespace()
        self.seen = []
        drafter = self

        class Orch:
            async def ollama_available(self):
                return False

            async def frontier_text(self, system, user, **kw):
                drafter.seen.append(user)
                if drafter.reply is None:
                    raise RuntimeError("no model")
                return drafter.reply, "fake-model"

        self.orchestrator = Orch()


def fake_key():
    return "sk-" + "proj-" + "Q7f" * 8 + "x9Z" * 4


@pytest.fixture()
def pc(tmp_path):
    freeze.set_dir(tmp_path)
    board = br.Boardroom(tmp_path)
    d = SimpleNamespace(
        board=board,
        approvals=ApprovalQueue(tmp_path / "approvals.json"),
        comms=CommsHub(tmp_path, _nopublish),
        hotmic=FakeMic(),
        agent_toolkit=FakeToolkit(),
        health=SimpleNamespace(last={"status": "warn", "words": "Mostly fine. Disk is 91% full."}),
        drafter=FakeDrafter("sure, see you at 6"),
    )
    pair = pp.attach(d, tmp_path, name="lab-pc")
    client = TestClient(br.build_remote_app(board))
    phone = FakePhone()
    paired(board, client, phone)
    yield SimpleNamespace(d=d, pair=pair, client=client, phone=phone, board=board)
    freeze.set_dir(None)


def call(pc, method, path, body=None, key="read"):
    raw = b"" if body is None else json.dumps(body).encode()
    k = pc.phone.read if key == "read" else pc.phone.sign
    h = pc.phone.headers(k, method, path, raw)
    return pc.client.request(method, path, content=raw or None, headers=h)


# ---------------------------------------------------------------- auth
@pytest.mark.parametrize("method,path", [("GET", "/phone/home"), ("POST", "/phone/draft"), ("POST", "/phone/inbox"),
                                         ("POST", "/phone/hotmic"), ("GET", "/phone/hotmic"), ("POST", "/phone/agent/claude:run1/pause")])
def test_unsigned_requests_are_refused(pc, method, path):
    body = json.dumps({"state": "off", "text": "hi", "items": []}).encode()
    r = pc.client.request(method, path, content=body if method == "POST" else None,
                          headers={"X-Orion-Device": pc.phone.device_id, "Content-Type": "application/json"})
    assert r.status_code == 401


# ---------------------------------------------------------------- home
def test_home_matches_the_contract(pc):
    pc.d.approvals.add("self_cards", title="Turn on nightly backups", category="normal",
                       original="the full secret-ish details", draft="Approve = go ahead.")
    pc.d.approvals.add("secret_shield", title="Rotate " + fake_key(), category="urgent")
    r = call(pc, "GET", "/phone/home")
    assert r.status_code == 200, r.text
    h = r.json()
    assert set(h) == {"core", "approvals", "agents", "health", "frozen", "pc", "missions"}
    assert {"voice", "speaking", "thinking", "heat", "messages", "approvals", "health", "quiet"} <= set(h["core"])
    assert h["core"]["approvals"] == 2 and h["core"]["health"] == 1
    for a in h["approvals"]:
        assert set(a) == {"id", "title", "risk", "kind"}
    titles = [a["title"] for a in h["approvals"]]
    assert "Turn on nightly backups" in titles
    assert not any("sk-" in t for t in titles)          # secret-looking titles are hidden
    assert "secret-ish" not in r.text and "Approve =" not in r.text   # never the contents
    assert {a["risk"] for a in h["approvals"]} == {"medium", "high"}
    assert [set(a) for a in h["agents"]] == [{"id", "name", "tool", "status", "state"}] * 2
    assert "/secret/path" not in r.text
    assert h["health"] == {"level": 1, "text": "Mostly fine. Disk is 91% full."}
    assert h["frozen"] is False
    assert h["pc"]["name"] == "lab-pc" and h["pc"]["online"] is True
    assert isinstance(h["pc"]["uptime_s"], int)
    assert h["pc"]["cpu"] is None or isinstance(h["pc"]["cpu"], float)
    assert h["pc"]["battery"] is None or isinstance(h["pc"]["battery"], int)


def test_home_is_cached_for_three_seconds(pc):
    clock = [1000.0]
    pc.pair.clock = lambda: clock[0]
    pc.pair._home = None
    first = pc.pair.home()
    pc.d.approvals.add("self_cards", title="New one")
    assert pc.pair.home() is first
    clock[0] += 3.5
    assert pc.pair.home()["core"]["approvals"] == 1


def test_frozen_shows_level_three(pc, tmp_path):
    (tmp_path / "freeze.json").write_text(json.dumps({"frozen": True, "since": time.time(), "by": "you"}))
    pc.pair._home = None
    h = call(pc, "GET", "/phone/home").json()
    assert h["frozen"] is True and h["health"]["level"] == 3


# ---------------------------------------------------------------- draft
def test_draft_uses_the_style_drafter_and_never_sends(pc):
    r = call(pc, "POST", "/phone/draft", {"app": "Discord", "sender": "Sam", "text": "dinner at 6?"})
    assert r.status_code == 200 and r.json() == {"draft": "sure, see you at 6"}
    assert "dinner at 6?" in pc.d.drafter.seen[0]
    assert pc.d.approvals.pending() == []          # nothing queued to send


def test_draft_redacts_secrets_both_ways(pc):
    key = fake_key()
    pc.d.drafter.reply = f"here you go: {key} and the password: hunter2hunter2"
    r = call(pc, "POST", "/phone/draft", {"app": "Telegram", "sender": "Sam", "text": f"my key is {key}"})
    out = r.json()["draft"]
    assert key not in out and "hunter2hunter2" not in out and pp.HIDDEN in out
    assert out.startswith("here you go:")
    assert key not in pc.d.drafter.seen[-1]          # the model never saw it either


def test_draft_falls_back_to_a_plain_reply_without_a_model(pc):
    pc.d.drafter.reply = None
    r = call(pc, "POST", "/phone/draft", {"app": "SMS", "sender": "Mum", "text": "thanks for today"})
    assert r.status_code == 200 and r.json()["draft"]


def test_draft_size_limits(pc):
    assert call(pc, "POST", "/phone/draft", {"app": "SMS", "sender": "x", "text": "  "}).status_code == 400
    big = {"app": "SMS", "sender": "x", "text": "a" * (pp.DRAFT_BODY_MAX + 10)}
    assert call(pc, "POST", "/phone/draft", big).status_code == 413
    pc.d.drafter.reply = "b" * 5000
    assert len(call(pc, "POST", "/phone/draft", {"app": "SMS", "sender": "x", "text": "hi"}).json()["draft"]) \
        == pp.DRAFT_OUT_MAX


# ---------------------------------------------------------------- inbox mirror
def test_inbox_refused_while_the_mirror_is_off(pc):
    r = call(pc, "POST", "/phone/inbox", {"items": [{"app": "WhatsApp", "sender": "Sam", "text": "hi", "ts": 1}]})
    assert r.status_code == 403 and "Mirror inbox" in r.json()["error"]
    assert pc.d.comms.threads == {}


def test_inbox_mirror_stores_read_only_messages_and_forgets_after_a_week(pc):
    import asyncio
    pc.pair.set_inbox_mirror(True)
    now = time.time()
    items = [{"app": "WhatsApp", "sender": "Sam", "text": "running late", "ts": int(now), "thread": "Sam"},
             {"app": "SMS", "sender": "Bank", "text": "code is 1234", "ts": now - 8 * 86400}]
    r = call(pc, "POST", "/phone/inbox", {"items": items})
    assert r.status_code == 200 and r.json()["ok"] is True
    threads = list(pc.d.comms.threads.values())
    assert [t["platform"] for t in threads] == ["phone"]       # the 8-day-old one was dropped at once
    t = threads[0]
    assert t["route"] == pp.INBOX_ROUTE and list(t["messages"])[0]["text"] == "running late"
    ok, detail, _ = asyncio.new_event_loop().run_until_complete(pc.d.comms.reply(t["key"], "on my way"))
    assert not ok and "read-only" in detail
    # a week later it is gone, from memory and from the history file
    pc.pair.clock = lambda: now + 8 * 86400
    assert pc.pair.prune_inbox(force=True) == 1
    assert pc.d.comms.threads == {}
    assert "running late" not in (pc.d.comms.log_path.read_text() if pc.d.comms.log_path.exists() else "")


def test_phone_cannot_turn_the_mirror_on(pc):
    assert call(pc, "POST", "/phone-pair/settings", {"inbox_mirror": True}).status_code == 404
    assert pc.pair.settings()["inbox_mirror"] is False


def test_local_settings_router(pc):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(pp.make_router(lambda: pc.d))
    c = TestClient(app)
    assert c.get("/phone-pair/settings").json() == {"inbox_mirror": False}
    assert c.post("/phone-pair/settings", json={"inbox_mirror": True}).json()["inbox_mirror"] is True
    assert pc.pair.settings()["inbox_mirror"] is True


# ---------------------------------------------------------------- hot mic
def test_read_key_can_turn_the_hot_mic_off_but_not_on(pc):
    pc.d.hotmic.on = True
    assert call(pc, "GET", "/phone/hotmic").json()["listening"] is True
    assert call(pc, "POST", "/phone/hotmic", {"state": "off"}).json()["listening"] is False
    r = call(pc, "POST", "/phone/hotmic", {"state": "on"})
    assert r.status_code == 403 and "fingerprint" in r.json()["error"] and pc.d.hotmic.on is False
    assert call(pc, "POST", "/phone/hotmic", {"state": "on"}, key="sign").json()["listening"] is True
    assert call(pc, "GET", "/phone/hotmic").json()["listening"] is True
    assert call(pc, "POST", "/phone/hotmic", {"state": "loud"}).status_code == 400


# ---------------------------------------------------------------- agents
def test_agent_controls_need_the_sign_key(pc):
    r = call(pc, "POST", "/phone/agent/claude:run1/pause")
    assert r.status_code == 401 and pc.d.agent_toolkit.swarm.calls == []
    r = call(pc, "POST", "/phone/agent/claude:run1/pause", key="sign")
    assert r.status_code == 200 and r.json() == {"ok": True, "message": "Paused."}
    assert pc.d.agent_toolkit.swarm.calls == [("claude:run1", "pause")]


def test_external_agents_are_refused_with_a_reason(pc):
    r = call(pc, "POST", "/phone/agent/codex:abc/stop", key="sign")
    assert r.status_code == 409 and r.json()["error"] == agent_swarm.EXTERNAL_NOTE
    assert call(pc, "POST", "/phone/agent/claude:run1/delete", key="sign").status_code == 400


def test_resume_is_refused_while_frozen(pc, tmp_path):
    (tmp_path / "freeze.json").write_text(json.dumps({"frozen": True, "since": time.time(), "by": "you"}))
    r = call(pc, "POST", "/phone/agent/claude:run1/resume", key="sign")
    assert r.status_code == 409 and "frozen" in r.json()["error"]
    assert call(pc, "POST", "/phone/agent/claude:run1/stop", key="sign").status_code == 200


def test_redact_keeps_normal_words():
    assert pp.redact("I forgot my password again") == "I forgot my password again"
    assert pp.redact("see you at 6, bring snacks") == "see you at 6, bring snacks"


def test_simple_agent_route_for_the_launcher(pc):
    r = call(pc, "POST", "/phone/agent", {"id": "claude:run1", "action": "pause"})
    assert r.status_code == 200 and r.json()["message"] == "Paused."
    r = call(pc, "POST", "/phone/agent", {"id": "codex:abc", "action": "stop"})
    assert r.status_code == 409 and r.json()["error"] == agent_swarm.EXTERNAL_NOTE
    assert call(pc, "POST", "/phone/agent", {"id": "claude:run1", "action": "resume"}).status_code == 400
    assert call(pc, "POST", "/phone/agent", {"id": "claude:run1", "action": "open"}).status_code == 200
    assert ("claude:run1", "open") in pc.d.agent_toolkit.swarm.calls


def test_fallback_routes_still_answer(pc):
    assert call(pc, "GET", "/phone/pending").status_code == 200
