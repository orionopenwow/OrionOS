"""Desktop Skill Recorder (after-1.0 plan §1, Phase 2): accessibility identity, privacy pauses, replay with approval,
self-healing (fuzzy -> OCR -> point at it), pattern spotting on desktop activity, the nightly review and Python
import/export. Everything runs on fakes: no accessibility bus, no display, no keyboard hooks."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import desktop_access
import desktop_recorder
import desktop_replay
import desktop_skill
import skill_dreams
import skill_routes
from approval_queue import ApprovalQueue
from desktop_access import make_element
from desktop_skills_hub import ActivityLog, DesktopSkills
from skill_routes import SkillHub

DAEMON = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


# ====================================================================================== fakes
class FakeBackend(desktop_access.NullBackend):
    """A tiny accessibility tree: windows -> elements with rects."""
    name = "fake"
    identity = True

    def __init__(self):
        self.wins = [{"app": "inkscape", "title": "Drawing - Inkscape", "id": 1},
                     {"app": "inkscape", "title": "Export", "id": 2}]
        self.active = self.wins[0]
        self.els = {
            "Drawing - Inkscape": [make_element(app="inkscape", window="Drawing - Inkscape", role="menu item",
                                                name="Export…", rect=(10, 10, 60, 20))],
            "Export": [make_element(app="inkscape", window="Export", role="push button", name="Export As",
                                    automation_id="export_btn", rect=(100, 200, 80, 30)),
                       make_element(app="inkscape", window="Export", role="text", name="Version",
                                    rect=(100, 100, 200, 24)),
                       make_element(app="inkscape", window="Export", role="push button", name="Plot drawing",
                                    rect=(300, 200, 80, 30))],
            "Bitwarden": [make_element(app="bitwarden", window="Bitwarden", role="password text", name="Master password",
                                       rect=(0, 0, 100, 20), password=True)],
            "Settings": [make_element(app="settings", window="Settings", role="password text", name="Wi-Fi key",
                                      rect=(0, 0, 100, 20), password=True),
                         make_element(app="settings", window="Settings", role="text", name="Network name",
                                      rect=(0, 40, 100, 20))],
        }
        self.focused = None
        self.did = []

    def active_window(self):
        return {"app": self.active["app"], "title": self.active["title"]}

    def element_at(self, x, y):
        for el in self.els.get(self.active["title"], []):
            r = el["rect"]
            if r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
                return dict(el)
        return None

    def focused_element(self):
        return dict(self.focused) if self.focused else None

    def elements(self, app="", window=""):
        out = []
        for title, els in self.els.items():
            if window and title != window:
                continue
            out += [dict(e, _ref=object()) for e in els if not app or e["app"] == app]
        return out

    def windows(self):
        return list(self.wins)

    def activate(self, win):
        self.did.append(("activate", win["title"]))
        self.active = win
        return True

    def do(self, el, action="click"):
        self.did.append((action, el.get("name")))
        return True

    def set_text(self, el, text):
        self.did.append(("set_text", el.get("name"), text))
        return True

    def go(self, title):
        self.active = next(w for w in self.wins if w["title"] == title) if any(w["title"] == title for w in self.wins) \
            else {"app": title.lower().split()[0], "title": title}


class FakeAutomation:
    def __init__(self):
        self.calls = []

    def click(self, x=None, y=None, button="left", clicks=1):
        self.calls.append(("click", x, y, button, clicks))
        return True

    def type_text(self, text, interval=0.02):
        self.calls.append(("type", text))
        return True

    def hotkey(self, *keys):
        self.calls.append(("hotkey",) + keys)
        return True

    def press_key(self, key):
        self.calls.append(("press", key))
        return True


class FakeOcr:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.asked = []

    def available(self):
        return True

    async def find(self, text, window_title=""):
        self.asked.append(text)
        return list(self.hits)


class FakeBus:
    def __init__(self):
        self.clients, self.published, self.logs = {}, [], []

    async def send(self, client, event):
        return True

    async def publish(self, event, **_):
        self.published.append(event)

    async def log(self, message, level="info"):
        self.logs.append(message)


class PickSource:
    """An input source that 'captures' the user's next click right away."""
    name = "fake"

    def __init__(self, xy):
        self.xy = xy

    def start(self, rec, pick=None):
        if pick is not None:
            pick(*self.xy)

    def stop(self):
        pass


async def no_sleep(_s):
    await asyncio.sleep(0)


def record_export(backend, words=None):
    """Record: open the Export menu, type a version, Ctrl+S, click Export As, Enter in the version box."""
    rec = desktop_recorder.DesktopRecorder(backend, words=words or [])
    assert rec.start()[0]
    rec.click(30, 20)                         # menu item "Export…"
    backend.go("Export")
    rec.click(150, 110)                       # focus click into Version (dropped: typing follows)
    backend.focused = backend.els["Export"][1]
    for ch in "1.4.2":
        rec.key(char=ch)
    rec.key("ctrl")
    rec.key(char="s")
    rec.key("ctrl", down=False)
    rec.click(140, 210)                       # Export As
    return rec


# ====================================================================================== recording
def test_recorder_saves_identity_typed_text_and_shortcuts():
    b = FakeBackend()
    rec = record_export(b)
    steps, skipped = rec.stop()
    draft = desktop_skill.build_draft(steps, skipped=skipped)
    assert draft["surface"] == "desktop" and skipped == 0
    assert draft["card"] == [
        "1. Switch to inkscape — “Drawing - Inkscape”",
        "2. Click the “Export…” menu item in “Drawing - Inkscape”",
        "3. Switch to inkscape — “Export”",
        "4. Type {{version}} into “Version”",
        "5. Press Ctrl+S",
        "6. Click the “Export As” button in “Export”",
    ]
    click = draft["steps"][5]
    kinds = [s["kind"] for s in click["target"]["selectors"]]
    assert kinds == ["automation_id", "role", "text", "coords"]        # position is only the backup
    assert click["target"]["role"] == "button" and click["target"]["app"] == "inkscape"
    assert draft["variables"][0]["example"] == "1.4.2"
    assert not any(s["risky"] for s in draft["steps"])


def test_private_windows_pause_and_password_fields_are_never_recorded():
    b = FakeBackend()
    states = []
    rec = desktop_recorder.DesktopRecorder(b, words=[], on_state=lambda s, r: states.append((s, r)))
    rec.start()
    b.go("Bitwarden")
    rec.click(10, 5)
    for ch in "hunter2":
        rec.key(char=ch)
    assert rec.paused_reason == "a password manager"
    assert any(s == "paused" and "password manager" in r for s, r in states)
    b.go("Sign in to Steam")
    rec.key(char="x")
    assert "login" in rec.paused_reason
    # an ordinary window with a password field: the field is skipped, the rest is kept
    b.wins.append({"app": "settings", "title": "Settings", "id": 3})
    b.go("Settings")
    rec.click(10, 10)
    b.focused = b.els["Settings"][0]
    for ch in "secretkey":
        rec.key(char=ch)
    rec.key("enter")
    b.focused = b.els["Settings"][1]
    for ch in "home":
        rec.key(char=ch)
    steps, skipped = rec.stop()
    text = json.dumps(steps)
    assert "hunter2" not in text and "secretkey" not in text and "Master password" not in text
    assert any(s["action"] == "type" and s["value"] == "home" for s in steps)
    assert skipped >= 1 and ("resumed", "") in states
    assert not any(s.get("window") in ("Bitwarden", "Sign in to Steam") for s in steps)


def test_risky_desktop_steps():
    r = desktop_skill.classify_risk
    btn = lambda n: {"action": "click", "target": {"role": "button", "name": n}}  # noqa: E731
    assert r(btn("Send")) and r(btn("Delete file")) and r(btn("Buy now")) and r(btn("Post"))
    assert r(btn("Export As")) is None
    assert r({"action": "press", "key": "enter", "target": {"role": "textbox", "name": "Message"}})
    assert r({"action": "shortcut", "keys": ["ctrl", "enter"]}) == "looks like it will send"
    assert r({"action": "press", "key": "delete", "target": {"role": "listitem", "name": "photo.png"}})
    assert r({"action": "shortcut", "keys": ["ctrl", "s"]}) is None
    # skills_recorder's shared helpers know about desktop steps
    import skills_recorder
    assert skills_recorder.classify_risk({"surface": "desktop", **btn("Send")})
    assert skills_recorder.describe_step({"surface": "desktop", "action": "shortcut", "keys": ["ctrl", "s"]}) == "Press Ctrl+S"


def test_evdev_keys_feed_the_recorder():
    b = FakeBackend()
    b.go("Export")
    b.focused = b.els["Export"][1]
    rec = desktop_recorder.DesktopRecorder(b, words=[])
    rec.start()
    src = desktop_recorder.EvdevSource()
    src.FOCUS_DELAY_S = 0
    src._rec = rec
    for code in ("KEY_LEFTSHIFT", "KEY_V"):
        src.feed(code, 1)
    src.feed("KEY_LEFTSHIFT", 0)
    for code in ("KEY_2", "KEY_DOT", "KEY_1"):
        src.feed(code, 1)
        src.feed(code, 0)
    src.feed("KEY_LEFTCTRL", 1)
    src.feed("KEY_Z", 1)
    src.feed("KEY_LEFTCTRL", 0)
    b.focused = b.els["Export"][0]
    src.feed("BTN_LEFT", 1)          # Wayland: no position, the focused element identifies the click
    steps, _ = rec.stop()
    assert [s["action"] for s in steps] == ["focus", "type", "shortcut", "click"]
    assert steps[1]["value"] == "V2.1" and steps[2]["keys"] == ["ctrl", "z"]
    assert steps[3]["target"]["name"] == "Export As"


# ====================================================================================== replay
def make_exec(backend, ocr=None, launcher=None):
    return desktop_replay.DesktopExecutor(backend, automation=FakeAutomation(), ocr=ocr or FakeOcr(),
                                          launcher=launcher or (lambda a: False), sleep=no_sleep,
                                          find_timeout=0.0)


def saved_skill(tmp_path, backend):
    rec = record_export(backend)
    steps, skipped = rec.stop()
    draft = desktop_skill.build_draft(steps, skipped=skipped)
    from skills_recorder import SkillStore, finalize
    store = SkillStore(tmp_path)
    skill = store.save(finalize(draft, "Export drawing", ["export the drawing"]))
    return store, skill


def test_executor_finds_by_identity_and_heals(tmp_path):
    b = FakeBackend()
    _, skill = saved_skill(tmp_path, b)
    ex = make_exec(b)
    steps = skill["steps"]

    async def go():
        res = await ex.exec_step(None, 0, steps[0], False)               # focus
        assert res["ok"] and ("activate", "Drawing - Inkscape") in b.did
        res = await ex.exec_step(None, 5, steps[5], False)               # click Export As by automation id
        assert res == {"ok": True, "matched": "automation_id"}
        assert ("click", "Export As") in b.did
        # the button was renamed and lost its id: fuzzy match, and the fix is offered for saving
        b.els["Export"][0].update(name="Export as…", automation_id="")
        res = await ex.exec_step(None, 5, steps[5], False)
        assert res["ok"] and res["matched"] == "fuzzy" and res["healed"]
        assert res["target"]["name"] == "Export as…"
        # gone from the accessibility tree: OCR finds exactly one match -> click its centre
        b.els["Export"].pop(0)
        ex.ocr = FakeOcr([{"x": 400, "y": 300, "w": 60, "h": 20, "score": 0.97}])
        res = await ex.exec_step(None, 5, steps[5], False)
        assert res["ok"] and res["matched"] == "ocr" and res["healed"]
        assert ex.automation.calls[-1] == ("click", 430, 310, "left", 1)
        assert res["target"]["selectors"][0] == {"kind": "ocr", "value": "Export As"}
        # two OCR matches is not certain: ask the user to point
        ex.ocr = FakeOcr([{"x": 1, "y": 1, "w": 5, "h": 5}, {"x": 90, "y": 90, "w": 5, "h": 5}])
        res = await ex.exec_step(None, 5, steps[5], False)
        assert res.get("need_pick")
        # typing goes into the field through the accessibility tree
        res = await ex.exec_step(None, 3, {**steps[3], "value": "2.0.0"}, False)
        assert res["ok"] and ("set_text", "Version", "2.0.0") in b.did
        res = await ex.exec_step(None, 4, steps[4], False)
        assert res["ok"] and ex.automation.calls[-1] == ("hotkey", "ctrl", "s")
    run(go())


def test_live_element_that_sends_needs_approval(tmp_path):
    b = FakeBackend()
    ex = make_exec(b)
    step = {"surface": "desktop", "action": "click", "id": "s1",
            "target": {"app": "inkscape", "window": "Export", "role": "button", "name": "Go",
                       "automation_id": "export_btn",
                       "selectors": [{"kind": "automation_id", "value": "export_btn"}]}}
    b.els["Export"][0]["name"] = "Send now"
    res = run(ex.exec_step(None, 0, step, False))
    assert res["needs_approval"] and "send" in res["reason"]
    assert not any(d[0] == "click" for d in b.did)
    assert run(ex.exec_step(None, 0, step, True))["ok"]


def test_preview_highlights_targets(tmp_path):
    b = FakeBackend()
    _, skill = saved_skill(tmp_path, b)
    ex = make_exec(b)
    from skills_recorder import render_step
    hl = run(ex.preview([render_step(s, {}) for s in skill["steps"]]))
    found = {h["index"]: h for h in hl}
    assert found[5]["found"] and found[5]["rect"] == [100, 200, 80, 30]
    assert found[0]["found"] is None  # a window switch has no element to highlight


def test_match_ocr_finds_phrases():
    data = {"text": ["File", "Export", "As", "", "Cancel"], "conf": [95, 91, 90, -1, 88],
            "left": [5, 100, 160, 0, 300], "top": [5, 200, 200, 0, 200], "width": [30, 50, 20, 0, 50],
            "height": [12, 14, 14, 0, 14], "block_num": [1, 2, 2, 2, 3], "par_num": [1, 1, 1, 1, 1],
            "line_num": [1, 1, 1, 1, 1]}
    hits = desktop_replay.match_ocr(data, "Export As")
    assert hits == [{"x": 100, "y": 200, "w": 80, "h": 14, "score": 1.0}]
    assert desktop_replay.match_ocr(data, "Delete everything") == []


def test_full_run_with_approval_and_point_to_fix(tmp_path):
    async def go():
        b = FakeBackend()
        bus = FakeBus()
        approvals = ApprovalQueue(tmp_path / "a.json")
        hub = SkillHub(bus, approvals, tmp_path)
        ex = make_exec(b)
        hub.desktop = DesktopSkills(hub, tmp_path, backend=b, executor=ex,
                                    input_factory=lambda: PickSource((340, 215)))
        steps = [
            {"surface": "desktop", "action": "focus", "app": "inkscape", "window": "Export"},
            {"surface": "desktop", "action": "click",
             "target": {"app": "inkscape", "window": "Export", "role": "button", "name": "Print it",
                        "selectors": [{"kind": "role", "role": "button", "name": "Print it"}]}},
            {"surface": "desktop", "action": "click",
             "target": {"app": "inkscape", "window": "Export", "role": "button", "name": "Post",
                        "selectors": [{"kind": "role", "role": "button", "name": "Post"},
                                      {"kind": "coords", "x": 1, "y": 1}]}},
        ]
        from skills_recorder import finalize
        draft = desktop_skill.build_draft(steps)
        assert draft["steps"][2]["risky"] and not draft["steps"][1]["risky"]
        b.els["Export"].append(make_element(app="inkscape", window="Export", role="push button", name="Post",
                                            rect=(500, 200, 60, 30)))
        hub.store.save(finalize(draft, "Print and post", []))
        r = await hub.run_skill("print_and_post", mode="all", preview=True)
        assert r.status == "preview"
        assert any(e["event"] == "SKILL_HIGHLIGHT" for e in bus.published)
        assert hub.state()["runs"][0]["surface"] == "desktop"
        hub.control(r.id, "run_all")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if approvals.pending():
                break
        # step 2 ("Print it") wasn't found: the user pointed at "Plot drawing", the fix was saved
        saved = hub.store.load("print_and_post")
        assert saved["version"] == 2 and saved["steps"][1]["target"]["name"] == "Plot drawing"
        assert saved["trusted"] and saved["fixes"][-1]["how"] == "picked"
        # step 3 posts: it waits for the OK before anything is clicked
        item = approvals.pending()[0]
        assert item["kind"] == "skill_step" and "Post" in item["draft"]
        assert ("click", "Post") not in b.did
        await hub.decide(item, "approve")
        await hub.runner.wait(r.id) if r.id in hub.runner.runs else await hub.desktop.runner.wait(r.id)
        assert r.status == "done" and ("click", "Post") in b.did
    run(go())


def test_changed_desktop_skill_never_runs(tmp_path):
    async def go():
        b = FakeBackend()
        hub = SkillHub(FakeBus(), ApprovalQueue(tmp_path / "a.json"), tmp_path)
        store, skill = saved_skill(tmp_path, b)
        p = tmp_path / "skills" / f"{skill['id']}.json"
        data = json.loads(p.read_text())
        data["steps"][3]["value"] = "rm -rf"
        p.write_text(json.dumps(data))
        with pytest.raises(PermissionError):
            await hub.run_skill(skill["id"])
    run(go())


def test_voice_starts_and_stops_desktop_recording(tmp_path):
    class Src:
        name = "fake"

        def start(self, rec, pick=None):
            self.rec = rec

        def stop(self):
            pass

    async def go():
        b = FakeBackend()
        bus = FakeBus()
        hub = SkillHub(bus, ApprovalQueue(tmp_path / "a.json"), tmp_path)
        hub.desktop = DesktopSkills(hub, tmp_path, backend=b, input_factory=Src)
        assert await hub.handle_text("Orion, record a desktop skill", "voice")
        rec = hub.desktop.recorder
        assert rec.recording and hub.state()["recording"]["surface"] == "desktop"
        rec.click(30, 20)
        b.go("Export")
        rec.click(140, 210)
        assert await hub.handle_text("stop watching", "voice")
        draft = next(e["draft"] for e in bus.published if e["event"] == "SKILL_DRAFT")
        assert draft["surface"] == "desktop" and len(draft["steps"]) == 4
        saved = await hub.save_draft(draft["draft_id"], "Export it", ["export it"])
        assert saved["surface"] == "desktop" and hub.store.summaries()[0]["surface"] == "desktop"
        # the activity log keeps identity only
        rows = ActivityLog(tmp_path).since(0)
        assert rows and all("coords" not in json.dumps(r) for r in rows)
    run(go())


def test_pattern_spotting_over_desktop_activity(tmp_path):
    async def go():
        bus = FakeBus()
        hub = SkillHub(bus, ApprovalQueue(tmp_path / "a.json"), tmp_path)
        seq = [{"surface": "desktop", "action": "focus", "app": "gimp", "window": "GIMP"},
               {"surface": "desktop", "action": "shortcut", "keys": ["ctrl", "shift", "e"], "app": "gimp"},
               {"surface": "desktop", "action": "click", "target": {"app": "gimp", "role": "button", "name": "Export"}}]
        for i in range(3):
            await hub.desktop.note([dict(s) for s in seq])
            await hub.desktop.note([{"surface": "desktop", "action": "click",
                                     "target": {"app": "files", "name": f"thing {i}"}}])
        cards = [e["card"] for e in bus.published if e["event"] == "SKILL_SUGGESTION"]
        assert len(cards) == 1 and cards[0]["count"] == 3
    run(go())


# ====================================================================================== import / export
def test_python_export_and_import_round_trip(tmp_path):
    b = FakeBackend()
    _, skill = saved_skill(tmp_path, b)
    code = desktop_skill.export_python(skill)
    compile(code, "skill.py", "exec")
    assert 'VERSION = "1.4.2"' in code and 'shortcut("ctrl", "s")' in code
    assert "x=140, y=210" in code       # the position travels along as the backup
    draft, skipped = desktop_skill.import_python(code, "Back again")
    assert skipped == []
    assert draft["card"] == skill_routes.card_lines(skill)
    assert draft["variables"][0]["name"] == "version"


def test_import_plain_python_without_running_it(tmp_path):
    marker = tmp_path / "ran.txt"
    code = f'''
import pyautogui, time, subprocess, os
import uiautomation as auto
subprocess.Popen(["gimp"])
time.sleep(2)
auto.WindowControl(Name="Export Image").SetActive()
auto.EditControl(Name="Name").SendKeys("poster-v2.png")
pyautogui.hotkey("ctrl", "shift", "e")
pyautogui.click(640, 480)
auto.ButtonControl(Name="Delete layer").Click()
open({str(marker)!r}, "w").write("x")
for i in range(3):
    pyautogui.press("down")
'''
    draft, skipped = skill_routes.import_code(code, "Poster")
    assert not marker.exists()
    assert draft["surface"] == "desktop"
    assert [s["action"] for s in draft["steps"]] == ["focus", "wait", "focus", "type", "shortcut", "click", "click"]
    assert draft["steps"][-1]["risky"] and draft["variables"][0]["kind"] == "file"
    assert len(skipped) == 2 and any("range" in s for s in skipped)
    # Playwright still goes to the browser importer
    pw, _ = skill_routes.import_code('page.goto("https://example.com")\npage.get_by_role("button", name="Go").click()')
    assert pw.get("surface") != "desktop" and pw["steps"][0]["action"] == "open"


def test_http_routes_and_export(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    b = FakeBackend()
    store, skill = saved_skill(tmp_path, b)
    daemon = SimpleNamespace(bus=FakeBus(), approvals=ApprovalQueue(tmp_path / "a.json"))
    app = FastAPI()
    app.include_router(skill_routes.attach(daemon, tmp_path))
    c = TestClient(app)
    st = c.get("/desktop-skills").json()
    assert st["settings"] == {"notice_patterns": False, "nightly_review": False, "review_hour": 3}
    res = c.post("/desktop-skills/settings", json={"nightly_review": True})
    assert res.json()["nightly_review"] is True, res.text
    code = c.get(f"/browser-skills/{skill['id']}/export?lang=python").text
    assert "def run():" in code and "playwright" not in code
    imp = c.post("/browser-skills/import", json={"code": code, "name": "Again"}).json()
    assert imp["draft"]["surface"] == "desktop" and imp["skipped"] == []


# ====================================================================================== nightly review
class FakeMemory:
    def __init__(self):
        self.rules = []

    async def add_rule(self, text, source="learned", tier=1):
        self.rules.append((text, source))


class FakeQuick:
    def __init__(self):
        self.saved = {}

    def commands(self):
        return {"status": ["say:status"]}

    def save_command(self, name, steps):
        self.saved[name] = steps


def _dream_world(tmp_path):
    bus = FakeBus()
    approvals = ApprovalQueue(tmp_path / "a.json")
    daemon = SimpleNamespace(bus=bus, approvals=approvals, memory=FakeMemory(), quick=FakeQuick())
    daemon.skills = SkillHub(bus, approvals, tmp_path, daemon=daemon)
    log = ActivityLog(tmp_path)
    now = time.time()
    seq = [{"surface": "desktop", "action": "focus", "app": "gimp", "window": "GIMP"},
           {"surface": "desktop", "action": "click", "target": {"app": "gimp", "role": "menuitem", "name": "Export As"}},
           {"surface": "desktop", "action": "type", "value": "secret-ish", "target": {"app": "gimp", "role": "textbox", "name": "Name"}},
           {"surface": "desktop", "action": "click", "target": {"app": "gimp", "role": "button", "name": "Export"}}]
    from skills_recorder import step_signature
    t = now - 3600
    for i in range(3):
        for s in seq:
            t += 5
            log.append(s, step_signature(s), t)
        for _ in range(2):
            t += 5
            other = {"surface": "desktop", "action": "focus", "app": "firefox", "window": f"Tab {i}"}
            log.append(other, step_signature(other), t)
    for i in range(2):
        it = approvals.add("discord_prompt", platform="discord", channel="#general", draft="x" * 120)
        approvals.update(it["id"], status="sent", final="x" * 40)
    return daemon, now


def test_nightly_review_is_opt_in_and_proposes_cards(tmp_path):
    async def go():
        daemon, now = _dream_world(tmp_path)
        rev = skill_dreams.NightlyReview(daemon, tmp_path, clock=lambda: now)
        assert await rev.tick() is None                      # off by default
        await daemon.skills.desktop.set_settings(nightly_review=True, review_hour=0)
        props = await rev.tick()
        types = sorted(p["type"] for p in props)
        assert types == ["rule", "shortcut", "skill"]
        assert "secret-ish" not in (tmp_path / "skills" / "activity").glob("*.jsonl").__next__().read_text()
        cards = [i for i in daemon.approvals.pending() if i["kind"] == skill_dreams.CARD_KIND]
        assert len(cards) == 3
        assert await rev.tick() is None                      # once a day
        by = {c["dream_type"]: c for c in cards}
        await skill_dreams.decide_card(daemon, by["rule"], "approve", rev.state)
        assert daemon.memory.rules and "short" in daemon.memory.rules[0][0]
        await skill_dreams.decide_card(daemon, by["shortcut"], "approve", rev.state)
        assert daemon.quick.saved == {"firefox": ["app:firefox"]}
        await skill_dreams.decide_card(daemon, by["skill"], "approve", rev.state)
        draft = next(e["draft"] for e in daemon.bus.published if e["event"] == "SKILL_DRAFT")
        assert draft["surface"] == "desktop" and any(v["name"].startswith("text_") for v in draft["variables"])
        assert draft["draft_id"] in daemon.skills.drafts      # still only a draft: you name and save it
        # a dismissed or accepted proposal never comes back
        rev.state.update(lambda d: d.__setitem__("last_day", ""))
        assert await rev.tick() == []
    run(go())


def test_nightly_review_does_nothing_while_frozen(tmp_path, monkeypatch):
    import freeze

    async def go():
        daemon, now = _dream_world(tmp_path)
        await daemon.skills.desktop.set_settings(nightly_review=True, review_hour=0)
        monkeypatch.setattr(freeze, "is_frozen", lambda: True)
        rev = skill_dreams.NightlyReview(daemon, tmp_path, clock=lambda: now)
        assert await rev.tick() is None and not daemon.approvals.pending()
    run(go())


def test_self_running_routes_dream_cards():
    import self_running
    assert skill_dreams.CARD_KIND in self_running.KINDS


# ====================================================================================== start-up safety
def test_desktop_modules_import_without_optional_packages():
    code = ("import sys\n"
            "for m in ('gi', 'pyatspi', 'uiautomation', 'evdev', 'pynput', 'pytesseract', 'pyautogui'):\n"
            "    sys.modules[m] = None\n"
            "sys.path.insert(0, %r)\n"
            "import desktop_access, desktop_recorder, desktop_replay, desktop_skill, desktop_skills_hub, skill_dreams\n"
            "assert desktop_access.pick_backend().name == 'none'\n"
            "assert desktop_recorder.pick_input() is None\n"
            "assert desktop_replay.OcrFinder().available() is False\n"
            "sys.platform = 'win32'\n"
            "assert desktop_access.pick_backend().name == 'none'\n"
            "print('ok')\n") % str(DAEMON)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-1500:]
