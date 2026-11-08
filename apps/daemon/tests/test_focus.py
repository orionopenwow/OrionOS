"""Focus dashboard (focus.py): ranking, the 5-card cap, learning from what you tap, decay, persistence, routes,
and that nothing is ever sent or approved."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import focus


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeHub:
    """A comms hub that records any attempt to send."""

    def __init__(self) -> None:
        self.threads: dict = {}
        self.sent: list = []
        self.senders = {"telegram_user": self._send}

    async def _send(self, thread, text):
        self.sent.append((thread, text))
        return True

    async def reply(self, key, text):   # pragma: no cover - must never be called
        self.sent.append((key, text))
        return True, "sent", None

    def add(self, key, name, msgs, unread=1, platform="telegram", vip=False, muted=False):
        self.threads[key] = {"key": key, "platform": platform, "thread_name": name, "unread": unread, "vip": vip,
                             "muted": muted, "messages": deque(msgs, maxlen=200), "route": "telegram_user"}

    def last_inbound(self, key):
        return next((m for m in reversed(self.threads[key]["messages"]) if m["direction"] == "in"), None)

    def context_for(self, key, limit=8):
        return [f"{m['author']}: {m['text']}" for m in list(self.threads[key]["messages"])[-limit:]]

    def style_samples(self, limit=40):
        return ["sounds good", "on it"]


class FakeQueue:
    def __init__(self, items=None):
        self.items = items or []
        self.decided = []

    def pending(self):
        return list(self.items)

    def decide(self, *a, **k):   # pragma: no cover - must never be called
        self.decided.append(a)


def msg(author, text, ts, direction="in", mid=None):
    return {"id": mid or f"m{int(ts)}{direction}", "author": author, "text": text, "timestamp": ts,
            "direction": direction}


def daemon(clock, **kw):
    d = SimpleNamespace(comms=FakeHub(), approvals=FakeQueue(), mission_control=None, agent_toolkit=None,
                        health=None, test_center=None, phone_link=None, trust_kit=None, ext=None,
                        drafter=None, orchestrator=None)
    for k, v in kw.items():
        setattr(d, k, v)
    return d


@pytest.fixture
def clock():
    return Clock()


def make(tmp_path, clock, **kw):
    d = daemon(clock, **kw)
    return d, focus.Focus(d, tmp_path, clock)


def kinds(res):
    return [c["kind"] for c in res["cards"]]


# ---------------------------------------------------------------- ranking
def test_all_clear_when_nothing_matters(tmp_path, clock):
    _, f = make(tmp_path, clock)
    res = f.cards()
    assert res["cards"] == [] and res["all_clear"] is True and res["line"] == focus.ALL_CLEAR


def test_ranks_now_and_approvals_above_ordinary_messages(tmp_path, clock):
    mc = SimpleNamespace(list=lambda: [{"id": "m1", "title": "Fix the login page", "state": "running",
                                        "phase": "checking", "agent_label": "Claude", "cost_usd": 0.4,
                                        "budget_usd": 2.0, "started": clock.t - 600}])
    d, f = make(tmp_path, clock, mission_control=mc)
    d.approvals.items = [{"id": "a1", "kind": "self_cards", "title": "Turn on backups", "status": "pending",
                          "created_at": clock.t}]
    d.comms.add("telegram:1", "Sam", [msg("Sam", "hey, are you free later?", clock.t - 120)])
    res = f.cards()
    assert kinds(res) == ["now", "approval", "message"]
    now = res["cards"][0]
    assert now["title"] == "Fix the login page" and now["meta"]["progress"] == 0.75
    assert "OK" in now["meta"]["waiting_on"]
    assert res["cards"][1]["meta"]["count"] == 1 and "Turn on backups" in res["cards"][1]["detail"]
    m = res["cards"][2]
    assert m["meta"]["sender"] == "Sam" and m["meta"]["app_label"] == "Telegram"
    assert m["actions"] == ["open", "draft"] and m["why"]


def test_never_more_than_five(tmp_path, clock):
    agents = [{"id": f"a{i}", "name": f"Agent {i}", "state": "stuck", "reason": "No progress"} for i in range(4)]
    mc = SimpleNamespace(list=lambda: [{"id": "m1", "title": "Build", "state": "running", "phase": "working"}])
    d, f = make(tmp_path, clock, mission_control=mc, agent_toolkit=SimpleNamespace(agents=lambda: agents))
    d.approvals.items = [{"id": f"x{i}", "title": f"thing {i}", "status": "pending"} for i in range(6)]
    for i in range(8):
        d.comms.add(f"telegram:{i}", f"P{i}", [msg(f"P{i}", "hello", clock.t - 60)], vip=True)
    res = f.cards()
    assert len(res["cards"]) == 5
    assert kinds(res).count("message") <= focus.MAX_MESSAGES
    assert kinds(res).count("needs_you") <= focus.MAX_NEEDS_YOU
    assert len(f.cards(limit=99)["cards"]) == 5 and len(f.cards(limit=2)["cards"]) == 2


def test_muted_and_read_threads_are_left_out(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Group", [msg("Lee", "lol", clock.t)], muted=True)
    d.comms.add("telegram:2", "Kai", [msg("Kai", "ok", clock.t)], unread=0)
    assert f.cards()["cards"] == []


def test_vip_starts_high_and_says_why(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Stranger", [msg("Stranger", "hi", clock.t - 60)])
    d.comms.add("telegram:2", "Maya", [msg("Maya", "call me when you can", clock.t - 60)], vip=True)
    cards = f.cards()["cards"]
    assert cards[0]["meta"]["sender"] == "Maya" and "VIP" in cards[0]["why"]


def test_reply_habit_is_noticed(tmp_path, clock):
    t = clock.t - 5 * 86400
    hist = []
    for i in range(4):
        hist += [msg("Maya", f"q{i}", t + i * 3600), msg("You", f"a{i}", t + i * 3600 + 120, "out")]
    hist.append(msg("Maya", "dinner tonight?", clock.t - 300))
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Maya", hist)
    d.comms.add("telegram:2", "Shop", [msg("Shop", "sale today", clock.t - 300)])
    cards = f.cards()["cards"]
    assert cards[0]["meta"]["sender"] == "Maya"
    assert cards[0]["why"] == "You usually reply to Maya within minutes."
    h = focus.reply_habit(hist)
    assert h["rate"] == 1.0 and h["median_s"] == 120 and h["n"] == 4


def test_old_messages_count_for_less(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Old", [msg("Old", "hi", clock.t - 2 * 86400)])
    d.comms.add("telegram:2", "New", [msg("New", "hi", clock.t - 60)])
    scores = {c["meta"]["sender"]: c["score"] for c in f.cards()["cards"] if c["kind"] == "message"}
    assert scores.get("New", 0) > scores.get("Old", 0)


def test_gist_is_one_line_and_hides_secrets():
    pw = "pass" + "word"
    g = focus.gist(f"my {pw} is hunter2xyz\nsecond line")
    assert "\n" not in g and "hunter2xyz" not in g and "[hidden]" in g
    assert len(focus.gist("x " * 200)) <= focus.GIST_MAX


# ---------------------------------------------------------------- learning
def test_dismiss_lowers_and_open_raises_a_sender(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Ann", [msg("Ann", "hi", clock.t - 60)])
    d.comms.add("telegram:2", "Bob", [msg("Bob", "hi", clock.t - 60)])
    cards = {c["meta"]["sender"]: c for c in f.cards()["cards"]}
    assert cards["Ann"]["score"] == cards["Bob"]["score"]
    f.feedback(cards["Ann"]["id"], "dismiss")
    f.feedback(cards["Bob"]["id"], "open")
    assert f.model.weight("senders", "ann") < 0 < f.model.weight("senders", "bob")
    res = f.cards()["cards"]
    assert [c["meta"]["sender"] for c in res] == ["Bob"]       # Ann's card is hidden
    # a new message from Ann comes back, but now ranks below Bob
    d.comms.threads["telegram:1"]["messages"].append(msg("Ann", "hello again", clock.t - 30, mid="new"))
    res = f.cards()["cards"]
    assert [c["meta"]["sender"] for c in res] == ["Bob", "Ann"]


def test_pin_keeps_a_card_on_top_until_unpinned(tmp_path, clock):
    mc = SimpleNamespace(list=lambda: [{"id": "m1", "title": "Build", "state": "running", "phase": "working"}])
    d, f = make(tmp_path, clock, mission_control=mc)
    d.comms.add("telegram:1", "Ann", [msg("Ann", "hi", clock.t - 60)])
    cid = next(c["id"] for c in f.cards()["cards"] if c["kind"] == "message")
    f.feedback(cid, "pin")
    top = f.cards()["cards"][0]
    assert top["id"] == cid and top["pinned"] and top["why"] == "You pinned this."
    f.feedback(cid, "unpin")
    assert f.cards()["cards"][0]["kind"] == "now"


def test_ignoring_a_card_for_hours_lowers_it_once(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Ann", [msg("Ann", "hi", clock.t - 60)])
    f.cards()
    clock.t += focus.IGNORE_AFTER_S + 1
    f.cards()
    w1 = f.model.weight("senders", "ann")
    f.cards()
    assert w1 == pytest.approx(focus.STEP["ignored"]) and f.model.weight("senders", "ann") == w1


def test_weights_decay_back_over_time(tmp_path, clock):
    _, f = make(tmp_path, clock)
    f.model.nudge("senders", "ann", 0.8)
    clock.t += focus.HALF_LIFE_DAYS * 86400
    assert f.model.weight("senders", "ann") == pytest.approx(0.4, abs=1e-3)
    f.model.nudge("senders", "vip", -0.6, prior=focus.VIP_START)
    clock.t += focus.HALF_LIFE_DAYS * 86400
    assert f.model.weight("senders", "vip", focus.VIP_START) == pytest.approx(0.3, abs=1e-3)


def test_weights_are_saved_and_loaded(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.comms.add("telegram:1", "Ann", [msg("Ann", "hi", clock.t - 60)])
    cid = f.cards()["cards"][0]["id"]
    f.feedback(cid, "open")
    f.feedback(cid, "pin")
    data = json.loads((Path(tmp_path) / "focus.json").read_text(encoding="utf-8"))
    assert data["senders"]["ann"]["open"] == 1 and cid in data["pins"]
    f2 = focus.Focus(d, tmp_path, clock)
    assert f2.model.weight("senders", "ann") > 0 and f2.model.pinned(cid)


def test_a_broken_file_starts_fresh(tmp_path, clock):
    (Path(tmp_path) / "focus.json").write_text("{not json", encoding="utf-8")
    _, f = make(tmp_path, clock)
    assert f.cards()["all_clear"]


def test_bad_feedback_is_refused(tmp_path, clock):
    _, f = make(tmp_path, clock)
    with pytest.raises(ValueError):
        f.feedback("msg:x", "send")
    with pytest.raises(ValueError):
        f.feedback("", "open")


# ---------------------------------------------------------------- other kinds
def test_needs_you_next_up_and_phone(tmp_path, clock):
    agents = [{"id": "c1", "name": "Codex", "state": "stuck", "reason": "No progress for 14 min"}]
    promises = SimpleNamespace(promises=lambda status: [{"id": "p1", "action": "Send the build", "to": "Sam",
                                                         "due": clock.t + 2 * 3600}])
    health = SimpleNamespace(last={"status": "fail", "words": "The disk is almost full."})
    d, f = make(tmp_path, clock, agent_toolkit=SimpleNamespace(agents=lambda: agents),
                trust_kit=SimpleNamespace(promises=promises), health=health)
    res = f.cards()
    ks = kinds(res)
    assert ks.count("needs_you") == 2 and "next_up" in ks
    nxt = next(c for c in res["cards"] if c["kind"] == "next_up")
    assert nxt["title"] == "Send the build" and "in 2 hours" in nxt["detail"] and "Sam" in nxt["detail"]


def test_a_broken_source_does_not_break_the_rest(tmp_path, clock):
    def boom():
        raise RuntimeError("down")
    d, f = make(tmp_path, clock, mission_control=SimpleNamespace(list=boom))
    d.comms.add("telegram:1", "Ann", [msg("Ann", "hi", clock.t - 60)])
    assert kinds(f.cards()) == ["message"]


# ---------------------------------------------------------------- nothing is ever sent or approved
def test_nothing_is_sent_or_approved(tmp_path, clock):
    d, f = make(tmp_path, clock)
    d.approvals.items = [{"id": "a1", "title": "Pay the invoice", "status": "pending"}]
    d.comms.add("telegram:1", "Ann", [msg("Ann", "can you send it?", clock.t - 60)])
    res = f.cards()
    for c in res["cards"]:
        for a in ("open", "pin", "unpin", "dismiss"):
            f.feedback(c["id"], a)
    mid = next(c["id"] for c in f.cards()["cards"] + list(f._last.values()) if c["kind"] == "message")
    out = asyncio.run(f.draft(mid))
    assert out["draft"] and out["thread"] == "telegram:1"
    assert d.comms.sent == [] and d.approvals.decided == []
    src = Path(focus.__file__).read_text(encoding="utf-8")
    assert ".reply(" not in src and ".decide(" not in src and "frontier_text(system" not in src.split("class _LocalOnly")[0]


def test_draft_only_for_message_cards(tmp_path, clock):
    _, f = make(tmp_path, clock)
    with pytest.raises(ValueError):
        asyncio.run(f.draft("approval:a1"))


# ---------------------------------------------------------------- routes
def test_routes(tmp_path, clock, monkeypatch):
    d, f = make(tmp_path, clock)
    focus._focus.clear()
    focus._focus[id(d)] = f
    d.comms.add("telegram:1", "Ann", [msg("Ann", "hi there", clock.t - 60)])
    app = FastAPI()
    app.include_router(focus.make_router(lambda: d))
    c = TestClient(app)
    r = c.get("/focus")
    assert r.status_code == 200 and r.json()["cards"][0]["meta"]["sender"] == "Ann"
    cid = r.json()["cards"][0]["id"]
    assert c.post("/focus/feedback", json={"id": cid, "action": "open"}).json() == {"ok": True}
    assert c.post("/focus/feedback", json={"id": cid, "action": "explode"}).status_code == 400
    r = c.post("/focus/draft", json={"id": cid})
    assert r.status_code == 200 and r.json()["draft"]
    assert c.post("/focus/draft", json={"id": "nope"}).status_code == 400
    assert d.comms.sent == []
    assert focus.for_phone(d)[0]["kind"] == "message"
    focus._focus.clear()
