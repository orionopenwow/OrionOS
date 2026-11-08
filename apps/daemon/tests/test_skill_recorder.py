"""Skill Recorder (browser): events -> card, variables, privacy, signing, approval gating, selectors,
Playwright export/import, pattern spotting, and the WebSocket hub."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import skills_recorder as sr
from approval_queue import ApprovalQueue
from skill_replay import SkillRunner
from skill_routes import SkillHub

SNAP = Path(__file__).parent / "snapshots"


def btn(name, *, tag="button", type_="", in_form=False, testid=None, role="button"):
    sels = []
    if testid:
        sels.append({"kind": "testid", "attr": "data-testid", "value": testid})
    sels += [{"kind": "coords", "x": 10, "y": 20}, {"kind": "css", "value": f"#{name.lower().replace(' ', '-')}"},
             {"kind": "text", "value": name}, {"kind": "role", "role": role, "name": name}]
    return {"tag": tag, "type": type_, "role": role, "name": name, "text": name, "in_form": in_form, "selectors": sels}


def field(label, *, type_="text", role="textbox", **extra):
    return {"tag": "input", "type": type_, "role": role, "name": label, "label": label, "in_form": True,
            "selectors": [{"kind": "role", "role": role, "name": label}, {"kind": "label", "value": label},
                          {"kind": "css", "value": f"input[name={label.lower().replace(' ', '_')}]"}], **extra}


def release_events():
    return [
        {"t": 0, "action": "navigate", "url": "https://github.com/acme/app/releases"},
        {"t": 1000, "action": "click", "target": {**btn("Draft a new release", tag="a", role="link"), "href": "x"}},
        {"t": 1500, "action": "navigate", "url": "https://github.com/acme/app/releases/new"},
        {"t": 3000, "action": "click", "target": field("Tag name")},
        {"t": 3500, "action": "fill", "target": field("Tag name"), "value": "v2.4.1"},
        {"t": 4000, "action": "fill", "target": field("Release date"), "value": "2026-10-15"},
        {"t": 4500, "action": "select", "target": {**field("Channel", role="combobox"), "tag": "select"},
         "value": "stable", "option_text": "Stable"},
        {"t": 5000, "action": "check", "target": field("Pre-release", type_="checkbox", role="checkbox"),
         "checked": False},
        {"t": 5500, "action": "upload", "target": field("Attach binaries", type_="file", role=""),
         "files": ["C:\\builds\\app-2.4.1.zip"]},
        {"t": 6000, "action": "fill", "target": field("Password", type_="password"), "value": "hunter2"},
        {"t": 6500, "action": "click", "target": btn("Publish release", in_form=True)},
        {"t": 6600, "action": "submit", "target": {"tag": "form", "selectors": [{"kind": "css", "value": "form"}]}},
        {"t": 7000, "action": "navigate", "url": "https://github.com/acme/app/releases/tag/v2.4.1"},
        {"t": 8000, "action": "wait_text", "text": "Release v2.4.1 published"},
        {"t": 9000, "action": "download", "filename": "/home/me/Downloads/notes-v2.4.1.txt"},
    ]


# ---------------------------------------------------------------- events -> card
def test_events_become_readable_card():
    draft = sr.build_draft(release_events(), start_url="https://github.com/acme/app/releases")
    actions = [s["action"] for s in draft["steps"]]
    assert actions == ["open", "click", "wait_load", "fill", "fill", "select", "check", "upload", "click",
                       "wait_load", "wait_text", "download"]
    card = draft["card"]
    assert card[0] == "1. Go to github.com/acme/app/releases"
    assert card[1] == "2. Click the “Draft a new release” link"
    assert card[2] == "3. Wait until the page has loaded"
    assert card[3] == "4. Type {{version}} into “Tag name”"
    assert card[4] == "5. Type {{date}} into “Release date”"
    assert card[5] == "6. Choose “Stable” in “Channel”"
    assert card[6] == "7. Untick “Pre-release”"
    assert card[7].startswith("8. Pick the file {{")
    assert card[8] == "9. Click the “Publish release” button — needs your OK (submits a form)"
    assert card[10] == "11. Wait until “Release {{version}} published” appears"
    assert draft["skipped_private"] == 1
    assert "hunter2" not in json.dumps(draft)


def test_click_into_field_then_typing_is_one_step_and_double_clicks_merge():
    steps, _ = sr.normalize_events([
        {"t": 0, "action": "click", "target": btn("Go")},
        {"t": 100, "action": "click", "target": btn("Go")},
        {"t": 1000, "action": "fill", "target": field("Name"), "value": "a"},
        {"t": 1200, "action": "fill", "target": field("Name"), "value": "ab"},
    ])
    assert [(s["action"], s.get("value")) for s in steps] == [("click", None), ("fill", "ab")]


# ---------------------------------------------------------------- privacy
@pytest.mark.parametrize("target", [
    {"tag": "input", "type": "password"},
    {"tag": "input", "type": "text", "autocomplete": "cc-number"},
    {"tag": "input", "type": "text", "autocomplete": "billing cc-csc"},
    {"tag": "input", "type": "text", "autocomplete": "one-time-code"},
    {"tag": "input", "type": "text", "inputmode": "numeric", "maxlength": "6", "label": "Enter the code we sent"},
    {"tag": "input", "type": "tel", "maxlength": "6", "nearby": "Verification code"},
    {"tag": "input", "type": "text", "label": "Card number"},
    {"tag": "input", "type": "text", "field_name": "otp_code", "label": "OTP"},
])
def test_sensitive_fields_are_never_recorded(target):
    assert sr.sensitive_field_reason(target)
    steps, skipped = sr.normalize_events([{"t": 1, "action": "fill", "target": target, "value": "123456"}])
    assert steps == [] and skipped == 1


def test_private_url_params_are_scrubbed():
    steps, _ = sr.normalize_events([{"t": 0, "action": "navigate",
                                     "url": "https://x.com/done?tag=v1&password=hunter2&otp_code=123456#token=abc"}])
    assert steps[0]["url"] == "https://x.com/done?tag=v1&password=&otp_code=#token="


def test_ordinary_fields_are_not_sensitive():
    assert sr.sensitive_field_reason({"tag": "input", "type": "text", "label": "Tag name"}) is None
    assert sr.sensitive_field_reason({"tag": "input", "type": "number", "maxlength": "3", "label": "Quantity"}) is None


def test_sensitive_pages_drop_events_and_secret_values_are_not_kept():
    steps, skipped = sr.normalize_events([
        {"t": 0, "action": "navigate", "url": "https://accounts.example.com/signin"},
        {"t": 1, "action": "click", "target": btn("Next"), "page_url": "https://accounts.example.com/signin"},
        {"t": 2, "action": "click", "target": btn("Next"), "page_url": "https://x.com", "page_title": "Online Banking"},
        {"t": 3, "action": "fill", "target": field("API key"), "value": "sk-" + "ant-" + "api03-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"},
        {"t": 4, "action": "click", "target": btn("Save"), "paused": True},
    ])
    assert skipped == 4
    assert len(steps) == 1 and steps[0]["action"] == "fill" and steps[0]["value"] is None and steps[0]["ask"]
    assert "Orion asks you each time" in sr.describe_step(steps[0])


# ---------------------------------------------------------------- variables
def test_variable_detection_and_user_edits():
    draft = sr.build_draft([
        {"t": 0, "action": "navigate", "url": "https://shop.example.com/search?q=usb+c+cable&page=1"},
        {"t": 10000, "action": "fill", "target": field("Search", role="searchbox", type_="search"), "value": "usb c cable"},
        {"t": 11000, "action": "fill", "target": field("Version"), "value": "1.2.3"},
        {"t": 12000, "action": "fill", "target": field("Due"), "value": "15/10/2026"},
        {"t": 13000, "action": "fill", "target": field("Note"), "value": "hello there"},
        {"t": 14000, "action": "upload", "target": field("File", type_="file", role=""), "files": ["report.pdf"]},
    ])
    kinds = {v["kind"]: v for v in draft["variables"]}
    assert set(kinds) == {"search", "version", "date", "file"}
    assert kinds["search"]["example"] == "usb c cable"
    assert draft["steps"][0]["url"] == "https://shop.example.com/search?q={{" + kinds["search"]["name"] + "}}&page=1"
    assert draft["steps"][4]["value"] == "hello there"  # ordinary text stays fixed
    # render: URL parts get encoded, field values don't
    s0 = sr.render_step(draft["steps"][0], {kinds["search"]["name"]: "red & blue"})
    assert s0["url"] == "https://shop.example.com/search?q=red+%26+blue&page=1"
    # rename + keep fixed
    edited = sr.apply_variable_edits(draft, [{"name": "version", "new_name": "App Version"},
                                             {"name": "date", "keep_fixed": True}])
    names = {v["name"] for v in edited["variables"]}
    assert "app_version" in names and "date" not in names
    assert edited["steps"][2]["value"] == "{{app_version}}"
    assert edited["steps"][3]["value"] == "15/10/2026"


# ---------------------------------------------------------------- signing
def test_signature_detects_tampering_and_versions(tmp_path):
    store = sr.SkillStore(tmp_path)
    draft = sr.build_draft(release_events(), start_url="https://github.com/acme/app/releases")
    saved = store.save(sr.finalize(draft, "Publish release", ["publish the release"]))
    assert saved["version"] == 1 and saved["trusted"]
    assert store.load("publish_release")["trusted"] is True
    assert (tmp_path / "skills" / ".signing_key").stat().st_size == 32

    again = store.save(sr.finalize(draft, "Publish release", ["publish the release"]))
    assert again["version"] == 2
    assert (tmp_path / "skills" / "history" / "publish_release.v1.json").exists()

    path = tmp_path / "skills" / "publish_release.json"
    data = json.loads(path.read_text())
    data["steps"][8]["risky"] = False  # someone tries to switch off the approval gate
    path.write_text(json.dumps(data))
    loaded = store.load("publish_release")
    assert loaded["trusted"] is False
    runner = SkillRunner(exec_step=None, ask_approval=None, ask_pick=None, save_fix=None, on_update=None)
    with pytest.raises(PermissionError):
        runner.start(loaded)
    assert sr.grimoire_entry(loaded)["trusted"] is False


# ---------------------------------------------------------------- risk + approval gating
@pytest.mark.parametrize("step,risky", [
    ({"action": "click", "target": {"tag": "button", "name": "Send", "in_form": False}}, True),
    ({"action": "click", "target": {"tag": "a", "name": "Buy now"}}, True),
    ({"action": "click", "target": {"tag": "button", "name": "Delete repository"}}, True),
    ({"action": "click", "target": {"tag": "input", "type": "submit", "name": "Go"}}, True),
    ({"action": "click", "target": {"tag": "button", "name": "Next", "in_form": True}}, True),   # default type=submit
    ({"action": "click", "target": {"tag": "button", "type": "button", "name": "Next", "in_form": True}}, False),
    ({"action": "press", "key": "Enter", "target": {"tag": "input", "in_form": True}}, True),
    ({"action": "submit", "target": {}}, True),
    ({"action": "click", "target": {"tag": "a", "name": "Docs"}}, False),
    ({"action": "fill", "target": {"tag": "input", "name": "Post title"}, "value": "x"}, False),
])
def test_risk_classification(step, risky):
    assert bool(sr.classify_risk(step)) is risky


def _skill(tmp_path, steps):
    store = sr.SkillStore(tmp_path)
    return store.save({"id": "t", "name": "T", "steps": steps, "variables": []})


def _runner(results, approvals_answer=True, picks=None):
    calls = {"exec": [], "approve": [], "pick": [], "fix": [], "updates": []}

    async def exec_step(run, i, step, approved):
        calls["exec"].append((i, approved))
        r = results.pop(0) if results else {"ok": True}
        return r(step, approved) if callable(r) else r

    async def ask_approval(run, i, step, reason):
        calls["approve"].append((i, reason))
        return approvals_answer

    async def ask_pick(run, i, step):
        calls["pick"].append(i)
        return (picks or [None]).pop(0)

    async def save_fix(run, i, target, how):
        calls["fix"].append((i, how))
        return sr.apply_fix(run.skill, i, target, healed_kind=how) | {"trusted": True}

    async def on_update(run):
        calls["updates"].append(run.status)

    return SkillRunner(exec_step=exec_step, ask_approval=ask_approval, ask_pick=ask_pick, save_fix=save_fix,
                       on_update=on_update), calls


def test_risky_steps_ask_every_time_and_no_stops_before_acting(tmp_path):
    skill = _skill(tmp_path, [
        {"id": "s1", "action": "click", "target": btn("Compose")},
        {"id": "s2", "action": "click", "target": btn("Send"), "risky": True, "risk_reason": "looks like it will send"},
    ])

    async def go():
        runner, calls = _runner([])
        for _ in range(2):
            run = runner.start(skill, mode="all", preview=False)
            await runner.wait(run.id)
            assert run.status == "done"
        assert calls["approve"] == [(1, "looks like it will send")] * 2
        assert calls["exec"] == [(0, False), (1, True)] * 2

        runner, calls = _runner([], approvals_answer=False)
        run = runner.start(skill, mode="all", preview=False)
        await runner.wait(run.id)
        assert run.status == "stopped"
        assert calls["exec"] == [(0, False)]  # the Send click never reached the page
    asyncio.run(go())


def test_page_side_risk_is_gated_too(tmp_path):
    skill = _skill(tmp_path, [{"id": "s1", "action": "click", "target": btn("Continue")}])

    async def go():
        runner, calls = _runner([{"ok": False, "needs_approval": True, "reason": "submits a form"}, {"ok": True}])
        run = runner.start(skill, mode="all", preview=False)
        await runner.wait(run.id)
        assert run.status == "done"
        assert calls["approve"] == [(0, "submits a form")]
        assert calls["exec"] == [(0, False), (0, True)]
    asyncio.run(go())


def test_step_mode_waits_for_next_and_preview_waits_for_start(tmp_path):
    skill = _skill(tmp_path, [{"id": f"s{i}", "action": "click", "target": btn(f"B{i}")} for i in range(3)])

    async def go():
        runner, calls = _runner([])
        run = runner.start(skill, mode="step", preview=True)
        await asyncio.sleep(0.05)
        assert run.status == "preview" and calls["exec"] == []
        runner.control(run.id, "start")
        await asyncio.sleep(0.05)
        assert run.status == "waiting_step" and len(calls["exec"]) == 1
        runner.control(run.id, "next")
        await asyncio.sleep(0.05)
        assert len(calls["exec"]) == 2
        runner.control(run.id, "run_all")
        await runner.wait(run.id)
        assert run.status == "done" and len(calls["exec"]) == 3
    asyncio.run(go())


# ---------------------------------------------------------------- selectors + self-healing
def test_selector_fallback_ordering():
    ordered = sr.order_selectors(btn("Save", testid="save-btn")["selectors"] + [{"kind": "label", "value": "x"},
                                                                               {"kind": "weird", "value": 1}])
    assert [s["kind"] for s in ordered] == ["testid", "role", "label", "text", "css", "weird", "coords"]


def test_pick_fix_is_saved_with_new_selectors_first(tmp_path):
    skill = _skill(tmp_path, [{"id": "s1", "action": "click", "target": btn("Old name")}])
    picked = btn("New name", testid="new-btn")

    async def go():
        runner, calls = _runner([{"ok": False, "need_pick": True}, {"ok": True}], picks=[picked])
        run = runner.start(skill, mode="all", preview=False)
        await runner.wait(run.id)
        assert run.status == "done"
        assert calls["pick"] == [0] and calls["fix"] == [(0, "picked")]
        sels = run.skill["steps"][0]["target"]["selectors"]
        assert sels[0] == {"kind": "testid", "attr": "data-testid", "value": "new-btn"}
        assert {"kind": "role", "role": "button", "name": "Old name"} in sels  # old ones kept as fallbacks
        assert sels[-1]["kind"] == "coords" and sum(s["kind"] == "coords" for s in sels) == 1
    asyncio.run(go())


def test_picked_element_that_is_risky_needs_approval(tmp_path):
    skill = _skill(tmp_path, [{"id": "s1", "action": "click", "target": btn("Archive")}])

    async def go():
        runner, calls = _runner([{"ok": False, "need_pick": True}, {"ok": True}], picks=[btn("Delete")])
        run = runner.start(skill, mode="all", preview=False)
        await runner.wait(run.id)
        assert calls["approve"] == [(0, "looks like it will delete")]
        assert calls["exec"][-1] == (0, True)
    asyncio.run(go())


def test_no_pick_stops_the_run(tmp_path):
    skill = _skill(tmp_path, [{"id": "s1", "action": "click", "target": btn("Gone")}])

    async def go():
        runner, calls = _runner([{"ok": False, "need_pick": True}], picks=[None])
        run = runner.start(skill, mode="all", preview=False)
        await runner.wait(run.id)
        assert run.status == "stopped"
    asyncio.run(go())


# ---------------------------------------------------------------- Playwright export / import
def _export_skill():
    draft = sr.build_draft(release_events(), start_url="https://github.com/acme/app/releases")
    skill = sr.finalize(draft, "Publish release", ["publish the release"])
    skill["created_at"] = skill["updated_at"] = 0
    return skill


@pytest.mark.parametrize("lang,ext", [("python", "py"), ("js", "js")])
def test_playwright_export_snapshot(lang, ext):
    code = sr.export_playwright(_export_skill(), lang)
    snap = SNAP / f"skill_export.{ext}.txt"
    if not snap.exists():  # first run writes the snapshot; review it, then commit
        SNAP.mkdir(exist_ok=True)
        snap.write_text(code, encoding="utf-8")
    assert code == snap.read_text(encoding="utf-8")
    if lang == "python":
        compile(code, "export.py", "exec")
        assert 'confirm("Click the “Publish release” button (submits a form)")' in code
        assert 'VERSION = "v2.4.1"' in code
    elif shutil.which("node"):
        out = subprocess.run(["node", "--check", "-"], input=code, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


def test_playwright_import_round_trip_and_codegen():
    code = sr.export_playwright(_export_skill(), "python")
    draft, skipped = sr.import_playwright(code, "Round trip")
    assert [s["action"] for s in draft["steps"]][:5] == ["open", "click", "wait_load", "fill", "fill"]
    assert any(s.get("risky") for s in draft["steps"])

    codegen = '''
import re
from playwright.sync_api import Playwright, sync_playwright, expect

def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.example.com/search?q=lamps")
    page.get_by_placeholder("Search").fill("lamps")
    page.get_by_role("button", name="Search").click()
    page.get_by_label("Size").select_option("Large")
    page.get_by_test_id("add-to-cart").click()
    page.locator("#qty").fill("2")
    page.frame_locator("#pay").get_by_role("button").nth(2).hover()
    for i in range(3):
        page.mouse.wheel(0, 100)
    expect(page.get_by_text("Added to cart")).to_be_visible()
    context.close()
'''
    draft, skipped = sr.import_playwright(codegen, "Lamps")
    acts = [(s["action"], sr.target_label(s.get("target") or {}) or s.get("url") or s.get("text")) for s in draft["steps"]]
    assert acts[0][0] == "open"
    assert ("fill", "Search") in acts and ("click", "Search") in acts and ("select", "Size") in acts
    assert ("wait_text", "Added to cart") in acts
    assert any(v["kind"] == "search" for v in draft["variables"])
    assert any("hover" in s for s in skipped) and any("wheel" in s for s in skipped)
    assert draft["import_limits"]
    js = 'await page.getByRole("link", { name: "Docs" }).click();\nawait page.getByLabel("Email").fill("a@b.c");'
    d2, _ = sr.import_playwright(js)
    assert [(s["action"], sr.target_label(s["target"])) for s in d2["steps"]] == [("click", "Docs"), ("fill", "Email")]


# ---------------------------------------------------------------- pattern spotting
def test_pattern_spotting():
    seq = ["open|a.com/x", "click|a.com|export", "click|a.com|csv"]
    log = seq + ["click|b|other"] + seq + ["fill|b|q|"] + seq
    found = sr.spot_patterns(log)
    assert found and found[0]["sequence"] == seq and found[0]["count"] == 3
    assert sr.spot_patterns(seq + seq) == []                       # only twice
    assert sr.spot_patterns(["click|a|x"] * 12) == []              # one repeated click isn't a sequence
    card = sr.suggestion_card(found[0])
    assert card["steps"][0] == "Go to a.com/x" and "3 times" in card["body"]


# ---------------------------------------------------------------- hub over the WebSocket protocol
class FakeClient:
    def __init__(self, kind):
        self.kind = kind
        self.sent = []


class FakeBus:
    def __init__(self):
        self.clients = {}
        self.published = []

    async def send(self, client, event):
        client.sent.append(event)
        return True

    async def publish(self, event, **_):
        self.published.append(event)

    async def log(self, message, level="info"):
        self.published.append({"event": "LOG", "message": message})


def test_hub_record_save_and_grimoire(tmp_path):
    async def go():
        bus = FakeBus()
        ext, hud = FakeClient("extension"), FakeClient("desktop")
        bus.clients = {"e": ext, "h": hud}
        hub = SkillHub(bus, ApprovalQueue(tmp_path / "a.json"), tmp_path)
        assert await hub.handle_text("Orion, watch this", "voice")
        assert ext.sent[-1]["event"] == "SKILL_RECORD_START"
        await hub.dispatch(ext, "SKILL_REC_STATE", {"state": "started", "rec_id": "r1",
                                                   "url": "https://github.com/acme/app/releases"})
        for ev in release_events()[1:]:
            await hub.dispatch(ext, "SKILL_REC_EVENT", {"rec_id": "r1", "ev": ev})
        await hub.dispatch(ext, "SKILL_REC_STATE", {"state": "stopped", "rec_id": "r1"})
        draft = next(e["draft"] for e in bus.published if e["event"] == "SKILL_DRAFT")
        assert draft["steps"][0]["action"] == "open"
        # the extension can't save, run or approve
        await hub.dispatch(ext, "SKILL_SAVE", {"draft_id": draft["draft_id"], "name": "x"})
        assert "not allowed" in ext.sent[-1]["message"] and draft["draft_id"] in hub.drafts
        await hub.dispatch(hud, "SKILL_SAVE", {"draft_id": draft["draft_id"], "name": "Publish release",
                                               "voice_triggers": ["ship it"],
                                               "variables": [{"name": "version", "new_name": "tag"}]})
        saved = hud.sent[-1]["skill"]
        assert saved["version"] == 1 and any(v["name"] == "tag" for v in saved["variables"])

        from grimoire import Grimoire

        class _Pre:
            grimoire = []
        g = Grimoire(_Pre(), tmp_path, embedder=None)
        entry = g.match_trigger("ship it")
        assert entry and entry["kind"] == "skill" and entry["requires_approval"] and entry["trusted"]

        # voice trigger starts a preview run in the page
        assert await hub.handle_text("ship it", "voice")
        assert any(e["event"] == "SKILL_PREVIEW" for e in ext.sent)
        run = next(iter(hub.runner.runs.values()))
        assert run.status == "preview"
        hub.control(run.id, "stop")
        await hub.runner.wait(run.id)
    asyncio.run(go())


def test_hub_approval_goes_through_queue_and_only_desktop_can_decide(tmp_path):
    async def go():
        bus = FakeBus()
        ext, hud = FakeClient("extension"), FakeClient("desktop")
        bus.clients = {"e": ext, "h": hud}
        approvals = ApprovalQueue(tmp_path / "a.json")
        hub = SkillHub(bus, approvals, tmp_path)
        hub.store.save({"id": "mail", "name": "Mail", "variables": [], "steps": [
            {"id": "s1", "action": "click", "target": btn("Send"), "risky": True, "risk_reason": "looks like it will send"}]})
        run = await hub.run_skill("mail", mode="all", preview=False)
        await asyncio.sleep(0.05)
        item = approvals.pending()[0]
        assert item["kind"] == "skill_step" and "Send" in item["draft"]
        assert not any(e["event"] == "SKILL_EXEC_STEP" for e in ext.sent)
        await hub.dispatch(ext, "SKILL_STEP_DECISION", {"id": item["id"], "decision": "approve"})
        await asyncio.sleep(0.05)
        assert approvals.get(item["id"])["status"] == "pending"   # an extension can't approve
        await hub.dispatch(hud, "SKILL_STEP_DECISION", {"id": item["id"], "decision": "approve"})
        await asyncio.sleep(0.05)
        exec_ev = next(e for e in ext.sent if e["event"] == "SKILL_EXEC_STEP")
        assert exec_ev["approved"] is True
        await hub.dispatch(ext, "SKILL_STEP_RESULT", {"run_id": run.id, "nonce": exec_ev["nonce"], "ok": True})
        await hub.runner.wait(run.id)
        assert run.status == "done"
    asyncio.run(go())


def test_hub_suggests_a_skill_for_repeated_activity(tmp_path):
    async def go():
        bus = FakeBus()
        ext = FakeClient("extension")
        bus.clients = {"e": ext}
        hub = SkillHub(bus, ApprovalQueue(tmp_path / "a.json"), tmp_path)
        seq = [{"action": "open", "url": "https://crm.example.com/reports"},
               {"action": "click", "target": btn("Export")},
               {"action": "click", "target": btn("CSV"), "value": "dropped"}]
        for i in range(3):
            await hub.dispatch(ext, "SKILL_ACTIVITY", {"url": "https://crm.example.com/reports",
                                                      "steps": [dict(s) for s in seq]})
            await hub.dispatch(ext, "SKILL_ACTIVITY", {"url": "https://other.example.com",
                                                      "steps": [{"action": "click", "target": btn(f"Other {i}")}]})
        cards = [e["card"] for e in bus.published if e["event"] == "SKILL_SUGGESTION"]
        assert len(cards) == 1 and cards[0]["steps"][:2] == ["Go to crm.example.com/reports", "Click “export”"]
        assert hub.state()["suggestions"]
        await hub.dispatch(FakeClient("desktop"), "SKILL_SUGGESTION_DISMISS", {"id": cards[0]["id"]})
        assert hub.state()["suggestions"] == []
    asyncio.run(go())
