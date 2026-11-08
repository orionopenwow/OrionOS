"""Feature Bible: preload kit, command router, grimoire, runner, comms routing, pacing, extensions wiring."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

import commands
from clipboard_alchemy import classify
from comms_hub import CommsHub
from harvester import SECRET_RE, describe_project
from pacing_tracker import PacingTracker, circadian_state
from preload import Preload, parse_rules
from runner import diagnose, is_destructive


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- command router
@pytest.mark.parametrize("text,kind", [
    ("Orion, watch what I do next", "macro_start"),
    ("stop watching", "macro_stop"),
    ("switch to OpenWoW mode", "battle"),
    ("log a realm idea: low-gravity CTF battleground", "idea"),
    ("Tell Alex on Telegram we're recording at 6", "voice_reply"),
    ("write the devlog", "devlog"),
    ("grab the reconnect hook from orion hub and adapt it for openwow", "harvest_code"),
    ("start podcast mode", "producer_start"),
    ("mark that", "highlight"),
    ("I drank a glass of water", "water"),
    ("from now on keep announcements casual", "rule"),
    ("harvest my projects", "harvest"),
    ("run the script that converts GLB models to M2", "grimoire"),
])
def test_command_router(text, kind):
    cmd = commands.parse(text)
    assert cmd is not None and cmd.kind == kind


def test_router_ignores_chat_and_gates_send():
    assert commands.parse("what's the weather like on mars?") is None
    assert commands.parse("send it") is None or commands.parse("send it").kind != "reply_send"
    assert commands.parse("send it", reply_pending=True).kind == "reply_send"
    vr = commands.parse("tell Alex on Telegram we're recording at 6")
    assert vr.args["platform"] == "telegram" and vr.args["name"] == "Alex"


# ---------------------------------------------------------------- preload
def test_preload_templates_and_rules(tmp_path):
    p = Preload(tmp_path, Path(__file__).resolve().parents[1], tmp_path)
    p.ensure_templates()
    for name in ("identity_and_rules.md", "workspace_manifest.json", "grimoire_scripts.json",
                 "comms_routing.json", "conditioning_deck.json"):
        assert (tmp_path / "preload" / name).exists(), name
    p.reload()
    assert p.rules, "seed identity file should yield rules"
    assert any(e["id"] == "kill_stuck_ports" for e in p.grimoire)
    assert p.conditioning.get("routines")
    assert parse_rules("# Rules\n- Be brief\n* Never send without approval\nplain line") == [
        "Be brief", "Never send without approval"]


# ---------------------------------------------------------------- safety
def test_secret_filter_and_destructive():
    assert SECRET_RE.search("export OPENAI_API_KEY=" + "sk-" + "abc123456789")
    assert SECRET_RE.search("git clone https://" + "ghp" + "_abcdefghijklmnopqrstu@github.com/x")
    assert not SECRET_RE.search("npm run dev")
    assert is_destructive("rm -rf build")
    assert is_destructive("git push --force")
    assert not is_destructive("npm run dev")
    assert classify('{"name": "orion", "tags": ["a", "b"], "n": 1}') == "json"
    assert classify("password=hunter2 token=abc") is None


def test_self_healing_quick_fixes():
    d = run(diagnose("npm run dev", "Error: listen EADDRINUSE: address already in use :::5173"))
    assert "5173" in (d.get("fix_command") or "")
    d = run(diagnose("python app.py", "ModuleNotFoundError: No module named 'httpx'"))
    assert "httpx" in (d.get("fix_command") or "")


def test_describe_project(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "demo", "scripts": {"dev": "vite"}}))
    info = describe_project(tmp_path)
    assert info and "dev" in json.dumps(info)


# ---------------------------------------------------------------- comms routing
def test_comms_vip_and_muted(tmp_path):
    async def pub(_):
        pass
    hub = CommsHub(tmp_path, pub, 20)
    hub.set_routing({"vip_contacts": [{"name": "Alex"}], "low_priority_threads": ["Guild Chat"]})

    async def go():
        await hub.ingest(platform="telegram", thread_id="1", thread_name="Alex", author="Alex", text="yo", route="telegram_api")
        await hub.ingest(platform="discord", thread_id="2", thread_name="Guild Chat", author="x", text="spam", route="discord_bot")
    since = time.time() - 5
    run(go())
    alex = hub.find_thread("alex", "telegram")
    assert alex and hub.flags(alex)["vip"]
    muted = hub.messages_since(since, muted_only=True)
    assert list(muted) and all("Guild" in k or "2" in k for k in muted)


# ---------------------------------------------------------------- health
def test_pacing_is_pause_aware(monkeypatch):
    import pacing_tracker
    t = PacingTracker(1, 5, 5)
    now = time.time()
    monkeypatch.setattr(pacing_tracker, "os_idle_seconds", lambda: 1.0)  # actively typing
    t.evaluate(now, now - 120)
    assert t.evaluate(now, now) is None, "must not interrupt mid-typing"
    monkeypatch.setattr(pacing_tracker, "os_idle_seconds", lambda: 30.0)  # natural pause
    out = None
    for i in range(3):
        out = out or t.evaluate(now, now + i)
    assert out is not None


def test_circadian():
    s = circadian_state("23:00", 90)
    assert s["phase"] in ("normal", "wind_down", "night")


# ---------------------------------------------------------------- review regressions
@pytest.mark.parametrize("line", [
    "export K=" + "sk-" + "ant-api03-abcdefghijklmnop", "sk-" + "proj-abcdefghijklmn", "curl -u admin:hunter2 http://x",
    "git clone https://me:S3cret@gitlab.com/a", "glp" + "at-xxxxxxxxxxxx", "AI" + "zaSyA1234567890abcdefghijk",
    "mysql -u root -pS3cretPass",
])
def test_more_secrets_are_skipped(line):
    from harvester import looks_secret
    assert looks_secret(line)


@pytest.mark.parametrize("line", ["npm run dev", "docker run -p 8080:80 nginx", "ssh -p 2222 host",
                                  "docker run -u 1000:1000 img", "git push origin main"])
def test_ordinary_commands_are_kept(line):
    from harvester import looks_secret
    assert not looks_secret(line)


@pytest.mark.parametrize("text", ["remember to call mom at 5pm tomorrow", "always the same bug with this build, why?",
                                  "keep it running while I grab food"])
def test_everyday_speech_is_not_a_rule(text):
    cmd = commands.parse(text)
    assert cmd is None or cmd.kind != "rule"


def test_trigger_match_is_exact(tmp_path):
    from grimoire import Grimoire
    p = Preload(tmp_path, Path(__file__).resolve().parents[1], tmp_path)
    p.ensure_templates()
    p.reload()
    g = Grimoire(p, tmp_path, embedder=None)
    assert g.match_trigger("clear stuck ports please")["id"] == "kill_stuck_ports"
    assert g.match_trigger("clear stuck ports and apps") is None


def test_destructive_vault_script_needs_approval(tmp_path):
    from grimoire import Grimoire
    p = Preload(tmp_path, Path(__file__).resolve().parents[1], tmp_path)
    p.ensure_templates()
    p.reload()
    g = Grimoire(p, tmp_path, embedder=None)
    g.vault.mkdir(parents=True, exist_ok=True)
    (g.vault / "wipe_build.bat").write_text("@echo off\nrd /s /q build\n")
    (g.vault / "hello.bat").write_text("@echo off\necho hi\n")
    entries = {e["id"]: e for e in g.entries()}
    assert entries["vault:wipe_build.bat"]["requires_approval"]
    assert not entries["vault:hello.bat"]["requires_approval"]


def test_runner_refuses_one_click_rerun_of_destructive():
    from runner import ProcessRunner, Run

    events = []

    async def pub(ev):
        events.append(ev)

    async def go():
        r = ProcessRunner(pub)
        run = Run(id="r1", command="rm -rf build", cwd=None, label="wipe")
        run.ended_at, run.exit_code = time.time(), 1
        r.runs["r1"] = run
        return await r.fix_and_restart("r1")
    assert asyncio.run(go()) is None


# ---------------------------------------------------------------- widgets: suggestions, agent pet, media
def test_offline_suggestions_follow_typing_style():
    from alignment_drafter import offline_suggestions, style_profile
    casual = style_profile(["yeah sounds good", "lol ok", "omw"])
    formal = style_profile(["Sounds good, thanks!", "I'll be there at 6.", "Perfect."])
    assert casual["lower"] and not casual["end_punct"]
    assert all(r == r.lower() for r in offline_suggestions("can you hop on at 6?", casual))
    assert any(r[0].isupper() for r in offline_suggestions("can you hop on at 6?", formal))


def test_agent_watcher_reads_claude_transcripts(tmp_path, monkeypatch):
    import agent_watch
    monkeypatch.setattr(agent_watch.Path, "home", staticmethod(lambda: tmp_path))
    proj = tmp_path / ".claude" / "projects" / "C--Users-me-code-orion-app"
    proj.mkdir(parents=True)
    f = proj / "s.jsonl"
    f.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "npm test", "description": "Run the unit tests"}}]}}) + "\n")
    w = agent_watch.AgentWatcher()
    s = w.poll()
    assert s["state"] == "working" and s["status"] == "Claude · Run the unit tests" and s["project"] == "orion-app"
    assert "Claude" in s["watching"]
    later = time.time() + agent_watch.ACTIVE_S + 1
    assert w.poll(later)["state"] == "done"
    assert w.poll(later + agent_watch.DONE_HOLD_S + 1)["state"] == "idle"


def test_media_session_is_quiet_off_windows():
    from media_session import MediaSession
    m = MediaSession()
    if not m.available:
        assert run(m.now_playing()) is None and run(m.control("toggle")) is False


def test_agent_watcher_codex_ping_and_file_burst(tmp_path, monkeypatch):
    import agent_watch
    monkeypatch.setattr(agent_watch.Path, "home", staticmethod(lambda: tmp_path))
    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "14"
    day.mkdir(parents=True)
    (day / "rollout-1.jsonl").write_text(json.dumps({"type": "response_item", "payload": {
        "type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["npm", "test"]})}}) + "\n")
    w = agent_watch.AgentWatcher()
    assert w.poll()["status"] == "Codex · Running tests"

    # cloud agents check in over HTTP
    w2 = agent_watch.AgentWatcher(sources=[])
    w2.ping("Claude", "Editing App.tsx", "orion")
    s = w2.poll()
    assert s["state"] == "working" and "Editing App.tsx" in s["status"] and s["project"] == "orion"
    w2.ping("Claude", state="done")

    # a burst of edits inside a known workspace with no agent logs
    ws = tmp_path / "proj"
    ws.mkdir()
    w3 = agent_watch.AgentWatcher(sources=[], workspaces=lambda: [{"name": "proj", "path": str(ws), "exists": True}])
    for i in range(4):
        (ws / f"f{i}.py").write_text("x")
        os.utime(ws / f"f{i}.py", (time.time() + i, time.time() + i))
        w3._ws_next = 0
        w3.poll(time.time() + i)
    assert w3.state["state"] == "working" and w3.state.get("probable") and "proj" in w3.state["status"]


# ---------------------------------------------------------------- V3 personal pass
def test_sun_and_moon_wind_down_at_sunset():
    from datetime import datetime, timedelta, timezone
    from pacing_tracker import circadian_state
    tz = timezone(timedelta(hours=-7))
    loc = {"lat": 33.45, "lon": -112.07}
    assert circadian_state("23:00", 90, now=datetime(2026, 10, 14, 15, 0, tzinfo=tz), location=loc)["phase"] == "normal"
    dusk = circadian_state("23:00", 90, now=datetime(2026, 10, 14, 19, 0, tzinfo=tz), location=loc)
    assert dusk["phase"] == "wind_down" and dusk["trigger"] == "sunset" and dusk["sun"]["sunset"]
    assert circadian_state("23:00", 90, now=datetime(2026, 10, 14, 23, 30, tzinfo=tz), location=loc)["phase"] == "night"


def test_xp_levels_caps_and_skins(tmp_path):
    from xp import XPLedger, level_for, momentum
    led = XPLedger(tmp_path / "xp.json")
    assert level_for(0)[0] == 1 and level_for(100)[0] == 2 and level_for(400)[0] == 3
    for _ in range(5):
        led.award("focus_block")
    assert led.snapshot()["level"] == 2 and led.snapshot()["xp"] == 250
    assert not led.set_skin("runic") and led.set_skin("default")
    for _ in range(30):
        led.award("water")
    assert led.snapshot()["today"] == 250 + 20  # water capped at 20/day
    m = momentum(focus_minutes_today=180, commits_today=5, hydration={"today": 8, "goal": 8},
                 approvals_pending=9, failed_runs=2, night_activity=True)
    assert 0 < m["score"] < 1 and {d["id"] for d in m["debris"]} == {"approvals", "runs", "sleep"}
