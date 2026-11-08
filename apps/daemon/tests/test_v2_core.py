"""Unit tests for the V2 daemon modules (no network, no UI)."""
import asyncio
import json
import time
import types
from pathlib import Path

import pytest

from approval_queue import ApprovalQueue
from comms_hub import CommsHub, _coerce_ts
from discord_bridge import DiscordPromptDetector, heuristic_triage
from alignment_drafter import ResponseDrafter
from memory_core import MemoryCore
from pacing_tracker import PacingTracker
from self_evolve import PatchError, SelfEvolveEngine, detect_evolve_request
from security import origin_allowed


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- memory / training
def test_alignment_examples_and_finetune_export(tmp_path):
    mem = MemoryCore(db_path=str(tmp_path / "db"))

    async def go():
        await mem.record_alignment(platform="discord_server", category="bug", system="S",
                                   user_prompt="game crashes on load", draft="d",
                                   final_response="Thanks! Which build?", decision="edited")
        await mem.record_alignment(platform="discord_server", category="bug", system="S",
                                   user_prompt="spam", draft="d", final_response="x", decision="rejected")
        ex = await mem.alignment_examples("crash when loading game", 5)
        assert [e["final_response"] for e in ex] == ["Thanks! Which build?"]  # rejections never used
        res = await mem.export_finetune(tmp_path / "ft.jsonl")
        lines = Path(res["path"]).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert [m["role"] for m in json.loads(lines[0])["messages"]] == ["system", "user", "assistant"]
        assert await mem.ingest_memory("telegram", "hello from phone")
    run(go())


# ---------------------------------------------------------------- approval queue
def test_approval_claim_is_exclusive_and_persisted(tmp_path):
    q = ApprovalQueue(tmp_path / "a.json")
    item = q.add("discord_prompt", author="x", draft="hi")
    assert q.claim(item["id"]) is not None
    assert q.claim(item["id"]) is None  # second (double-click) decision loses
    # a crash mid-send comes back as pending with a warning, never silently "sent"
    q2 = ApprovalQueue(tmp_path / "a.json")
    assert q2.get(item["id"])["status"] == "pending" and q2.get(item["id"])["error"]


def test_snapshot_keeps_every_pending_item(tmp_path):
    q = ApprovalQueue(tmp_path / "a.json")
    old = q.add("discord_prompt", draft="old")
    for i in range(80):
        it = q.add("discord_prompt", draft=str(i))
        q.update(it["id"], status="sent")
    snap = q.snapshot()
    assert any(i["id"] == old["id"] for i in snap)
    assert len(snap) <= 41


# ---------------------------------------------------------------- comms hub
def _hub(tmp_path, events=None):
    async def pub(e):
        if events is not None:
            events.append(e)
    return CommsHub(tmp_path, pub)


def test_comms_dedupe_reply_and_echo(tmp_path):
    async def go():
        hub = _hub(tmp_path)
        sent = []

        async def sender(thread, text):
            sent.append((thread["thread_id"], text))
            return True
        hub.register_sender("extension_cdp_inject", sender)
        kw = dict(platform="messenger", thread_id="123", thread_name="Sam", route="extension_cdp_inject")
        assert await hub.ingest(author="Sam", text="yo", external_id="a1", **kw)
        assert await hub.ingest(author="Sam", text="yo", external_id="a1", **kw) is None  # dedupe
        ok, _, _ = await hub.reply("messenger:123", "ok")
        assert ok and sent == [("123", "ok")]
        # our own reply echoing back through the DOM observer is dropped once...
        assert await hub.ingest(author="You", text="ok", direction="out", external_id="e1", **kw) is None
        # ...but the contact saying the same word still arrives
        assert await hub.ingest(author="Sam", text="ok", external_id="a2", **kw)
        assert len(_hub(tmp_path).threads["messenger:123"]["messages"]) == 3  # restored from disk
    run(go())


def test_comms_bad_timestamp_does_not_lose_message(tmp_path):
    async def go():
        hub = _hub(tmp_path)
        kw = dict(platform="discord", thread_id="9", thread_name="kyro", author="kyro", text="hi",
                  route="extension_cdp_inject", external_id="x1")
        msg = await hub.ingest(timestamp="2026-01-01", **kw)
        assert msg and abs(msg["timestamp"] - time.time()) < 5
    run(go())
    assert _coerce_ts(1_700_000_000_000) == 1_700_000_000.0  # milliseconds accepted


def test_comms_restore_survives_corrupt_lines(tmp_path):
    (tmp_path / "comms").mkdir()
    good = {"id": "m1", "platform": "telegram", "thread_id": "5", "thread_name": "Dad", "author": "Dad",
            "text": "call me", "direction": "in", "timestamp": time.time(), "route": "telegram_user"}
    lines = ["[1,2]", "{not json", json.dumps({"platform": "telegram"}), json.dumps(good)]
    (tmp_path / "comms" / "messages.jsonl").write_bytes(("\n".join(lines) + "\n").encode() + b"\xe2\x82")
    hub = _hub(tmp_path)
    assert "telegram:5" in hub.threads


# ---------------------------------------------------------------- pacing
def test_pacing_checkin_snooze_break(monkeypatch):
    import pacing_tracker
    monkeypatch.setattr(pacing_tracker, "os_idle_seconds", lambda: None)
    p = PacingTracker(limit_minutes=90, break_reset_minutes=5)
    t0 = 1_000_000.0
    c = None
    for i in range(0, 400):
        now = t0 + i * 15
        c = p.evaluate(last_screen_change=now, now=now) or c
        if c:
            break
    assert c and c["minutes"] >= 90
    p.snooze()
    assert p.pending_checkin is None
    p.start_break()
    assert p.snapshot()["phase"] == "resting"


def test_pacing_video_alone_is_not_desk_time(monkeypatch):
    import pacing_tracker
    monkeypatch.setattr(pacing_tracker, "os_idle_seconds", lambda: 900.0)  # nobody touched input for 15 min
    p = PacingTracker()
    now = time.time()
    p.evaluate(last_screen_change=now, now=now)
    assert p.session_start is None


# ---------------------------------------------------------------- discord triage
class _Orch:
    async def ollama_json(self, *a, **k):
        raise RuntimeError("offline")

    async def frontier_text(self, system, user, **k):
        self.last = (system, user)
        return '"Thanks for the report! Which platform are you on?"', "gpt-test"


class _Channel:
    id = 55
    name = "🐛│bug-reports"

    def __init__(self):
        self.sent = []

    def history(self, limit, before):
        async def gen():
            yield types.SimpleNamespace(author=types.SimpleNamespace(display_name="bob"), content="same here")
        return gen()

    async def fetch_message(self, mid):
        ch = self

        class M:
            async def reply(self, t, mention_author=False):
                ch.sent.append(("reply", t))
        return M()

    async def send(self, t):
        self.sent.append(("send", t))


def test_triage_draft_and_send(tmp_path):
    async def go():
        mem = MemoryCore(db_path=str(tmp_path / "db"))
        orch, got = _Orch(), []

        async def on_req(p):
            got.append(p)
        det = DiscordPromptDetector(orch, ResponseDrafter(orch, mem), on_req)
        ch = _Channel()
        msg = types.SimpleNamespace(guild=types.SimpleNamespace(name="G"), channel=ch, id=9, jump_url="u",
                                    author=types.SimpleNamespace(bot=False, display_name="alice"),
                                    content="The game crashes every time I open the inventory")
        await det.on_discord_message(msg)
        assert got and got[0]["category"] == "bug" and "bob: same here" in got[0]["user_prompt"]
        det.client = types.SimpleNamespace(get_channel=lambda i: ch)
        assert await det.send_reply(got[0], "x" * 2500)
        assert ch.sent[0][0] == "reply" and len(ch.sent) == 2
        got.clear()
        msg.content = "lol nice"
        await det.on_discord_message(msg)
        assert not got
    run(go())


def test_heuristic_triage():
    assert heuristic_triage("the game crashes when I open the map", "bug-reports")["category"] == "bug"
    assert heuristic_triage("Pitch: a frozen sky realm with floating islands", "realm-pitches")["category"] == "realm_pitch"
    assert heuristic_triage("lol", "general")["needs_admin_approval"] is False


# ---------------------------------------------------------------- self-evolve
@pytest.mark.parametrize("text", [
    "Orion, add a mute button to the comms tab",
    "can you add a dark mode toggle to your settings",
    "Orion, give yourself a hotkey for the queue tab",
])
def test_evolve_trigger_positive(text):
    assert detect_evolve_request(text)


@pytest.mark.parametrize("text", [
    "Orion, create a card for this bug in Trello",
    "Orion, make a new page in my Notion workspace",
    "Orion, update the Discord bot command for my server",
    "Hey Orion, build me a landing page for my game",
    "Orion, change my Windows display mode to dark",
    "Orion create a calendar event for the dentist and add the option to remind me",
    "what's the weather",
])
def test_evolve_trigger_negative(text):
    assert detect_evolve_request(text) is None


def test_evolve_slash_command():
    assert detect_evolve_request("/evolve add dark mode toggle") == "add dark mode toggle"


class _FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)

    async def frontier_text(self, system, user, **kw):
        return self.responses.pop(0), "fake-model"


def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / "apps/daemon").mkdir(parents=True)
    f = root / "apps/daemon/thing.py"
    f.write_bytes(b"def a():\r\n    return 1\r\n")
    return root, f


def _plan():
    return json.dumps({"files_to_read": ["apps/daemon/thing.py"], "approach": "x"})


def _edit(find, repl, new_files=None):
    return json.dumps({"summary": "s", "edits": [{"path": "apps/daemon/thing.py", "find": find, "replace": repl}],
                       "new_files": new_files or []})


def test_self_evolve_apply_revert_preserves_crlf(tmp_path):
    root, f = _repo(tmp_path)
    eng = SelfEvolveEngine(_FakeModel([_plan(), _edit("return 9", "x"), _edit("return 1", "return 2",
                           [{"path": "apps/daemon/new_mod.py", "content": "X = 1\n"}])]), root, tmp_path / "d")
    prop = run(eng.propose("make a return 2"))  # first edit is invalid -> automatic repair round
    assert "return 2" in prop["diff"] and prop["touches_daemon"]
    eng.apply(prop["id"])
    assert f.read_bytes() == b"def a():\r\n    return 2\r\n"
    with pytest.raises(PatchError):
        eng.apply(prop["id"])
    eng.revert(prop["id"])
    assert f.read_bytes() == b"def a():\r\n    return 1\r\n"
    assert not (root / "apps/daemon/new_mod.py").exists()


def test_revert_refuses_to_erase_later_edits(tmp_path):
    root, f = _repo(tmp_path)
    eng = SelfEvolveEngine(_FakeModel([_plan(), _edit("return 1", "return 2")]), root, tmp_path / "d")
    prop = run(eng.propose("x"))
    eng.apply(prop["id"])
    f.write_bytes(f.read_bytes() + b"# my manual edit\r\n")
    with pytest.raises(PatchError):
        eng.revert(prop["id"])
    assert b"my manual edit" in f.read_bytes()


def test_malformed_model_output_gets_repair_round(tmp_path):
    root, _ = _repo(tmp_path)
    bad = json.dumps({"summary": "s", "edits": "not a list"})
    eng = SelfEvolveEngine(_FakeModel([_plan(), bad, _edit("return 1", "return 3")]), root, tmp_path / "d")
    assert "return 3" in run(eng.propose("x"))["diff"]


@pytest.mark.parametrize("bad", ["../x.py", ".env", "apps/daemon/.venv/x.py", "node_modules/a.js",
                                 "apps/desktop/package.json", "apps/extension/manifest.json"])
def test_protected_paths(tmp_path, bad):
    eng = SelfEvolveEngine(None, tmp_path, tmp_path / "d")
    with pytest.raises(PatchError):
        eng._safe_path(bad)


def test_new_files_only_in_source_trees(tmp_path):
    eng = SelfEvolveEngine(None, tmp_path, tmp_path / "d")
    with pytest.raises(PatchError):
        eng._safe_new_path("apps/desktop/evil.js")
    eng._safe_new_path("apps/desktop/src/components/New.tsx")


def test_patch_ids_cannot_escape(tmp_path):
    eng = SelfEvolveEngine(None, tmp_path, tmp_path / "d")
    with pytest.raises(PatchError):
        eng.apply("patch-xxxx/../../ESCAPED")


# ---------------------------------------------------------------- security
@pytest.mark.parametrize("origin,ok", [
    (None, True), ("http://localhost:1420", True), ("http://tauri.localhost", True),
    ("chrome-extension://abcdef", True), ("https://evil.example", False), ("null", False),
    ("http://localhost:1421", False),
])
def test_origin_allowlist(origin, ok):
    assert origin_allowed(origin) is ok
