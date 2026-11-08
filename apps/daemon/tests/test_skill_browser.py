"""Skill Recorder in a real browser (headless Chromium via Playwright).

1. recorder.js pure helpers (selectors, private-field detection, risk, selector fallback, fuzzy healing).
2. End to end: the real Orion Bridge extension + a small daemon (SkillHub on ws://localhost:9120):
   record a task on local pages -> draft card -> save (signed) -> replay with a new value, with the risky
   "Publish" step held until approved; plus self-healing (fuzzy match, user picks the element) and the
   sign-in page pause.

Skipped when Playwright or a Chromium build isn't available, or port 9120 is taken (e2e only).
"""
import asyncio
import functools
import glob
import json
import os
import socket
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.async_api")

HERE = Path(__file__).resolve().parent
PAGES = HERE / "skill_pages"
EXT = HERE.parents[1] / "extension"
RECORDER = EXT / "recorder.js"


def _chromium() -> str | None:
    env = os.environ.get("ORION_TEST_CHROMIUM")
    if env and Path(env).exists():
        return env
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache" / "ms-playwright"))
    for pattern in ("chromium-*/chrome-linux*/chrome", "chromium-*/chrome-win*/chrome.exe",
                    "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
        hits = sorted(glob.glob(os.path.join(root, pattern)), reverse=True)
        if hits:
            return hits[0]
    return None


CHROME = _chromium()
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chromium build for Playwright")


@pytest.fixture(scope="module")
def site():
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(PAGES))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


# ====================================================================== recorder.js helpers
def test_recorder_helpers_in_chromium(site):
    async def go():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME, headless=True)
            page = await browser.new_page()
            await page.goto(site + "/new.html")
            await page.add_script_tag(path=str(RECORDER))
            r = await page.evaluate("""() => {
              const R = window.__orionSkillRecorder;
              const $ = (s) => document.querySelector(s);
              return {
                publishSel: R.selectorsFor($('[data-testid=publish]')),
                tagSel: R.selectorsFor($('#tag')).map((s) => s.kind),
                tagName: R.accessibleName($('#tag')),
                priv: ['#pw', '#cc', '#otp', '#tag'].map((s) => R.sensitiveFieldReason($(s))),
                risk: ['[data-testid=publish]', '#preview-btn'].map((s) => R.riskReason($(s))),
                page: R.pageSensitiveReason(),
                css: R.cssPath($('#channel option:nth-child(2)')),
              };
            }""")
            assert [s["kind"] for s in r["publishSel"]] == ["testid", "role", "text", "css", "coords"]
            assert r["publishSel"][1] == {"kind": "role", "role": "button", "name": "Publish release"}
            assert r["tagSel"] == ["role", "label", "css", "coords"] and r["tagName"] == "Tag name"
            assert r["priv"] == ["password", "card", "code", ""]
            assert r["risk"] == ["submits a form", ""]
            assert r["page"] == ""  # a long form with a password box is not a sign-in page
            assert r["css"] == "#channel > option:nth-of-type(2)"

            # fallback order: a broken test id falls through to role+name; then fuzzy; then nothing
            found = await page.evaluate("""() => {
              const R = window.__orionSkillRecorder;
              const t = (sels, extra = {}) => { const h = R.findTarget({ selectors: sels, ...extra });
                return h.el ? [h.matched, h.healed, h.el.textContent.trim()] : [null, !!h.ambiguous]; };
              return [
                t([{ kind: 'testid', value: 'publish' }, { kind: 'role', role: 'button', name: 'Publish release' }]),
                t([{ kind: 'testid', value: 'gone' }, { kind: 'role', role: 'button', name: 'Publish release' }]),
                t([{ kind: 'css', value: '#nope' }, { kind: 'text', value: 'Preview notes' }]),
                t([{ kind: 'role', role: 'button', name: 'Publish the release' }], { name: 'Publish the release', role: 'button' }),
                t([{ kind: 'role', role: 'button', name: 'Frobnicate' }], { name: 'Frobnicate', role: 'button' }),
                t([{ kind: 'css', value: 'p' }]),
              ];
            }""")
            assert found[0] == ["testid", False, "Publish release"]
            assert found[1] == ["role", True, "Publish release"]
            assert found[2] == ["text", True, "Preview notes"]
            assert found[3] == ["fuzzy", True, "Publish release"]
            assert found[4] == [None, False]
            assert found[5] == [None, True]  # many matches is not a match

            # replay refuses a risky element without approval and blocks a sneaky submit
            await page.goto(site + "/heal.html")
            await page.add_script_tag(path=str(RECORDER))
            res = await page.evaluate("""async () => {
              const R = window.__orionSkillRecorder;
              R._setMode('replay');
              const del = await R.execStep({ action: 'click', target: { selectors: [{ kind: 'role', role: 'button', name: 'Delete account' }] } }, false);
              const sneaky = await R.execStep({ action: 'click', target: { selectors: [{ kind: 'css', value: '#sneaky' }] } }, false);
              await new Promise((r) => setTimeout(r, 300));
              return { del, sneaky, status: document.getElementById('status').textContent, url: location.pathname };
            }""")
            assert res["del"] == {"ok": False, "needs_approval": True, "reason": "looks like it will delete"}
            assert res["status"] == "idle"
            assert res["sneaky"]["ok"] and res["url"] == "/heal.html"  # click ran, the form submit was blocked

            await page.goto(site + "/signin.html")
            await page.add_script_tag(path=str(RECORDER))
            assert await page.evaluate("window.__orionSkillRecorder.pageSensitiveReason()") == "this looks like a sign-in page"
            await browser.close()
    asyncio.run(go())


# ====================================================================== end to end with the extension
def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _mini_daemon(data_dir: Path):
    """The parts of the daemon the Skill Recorder uses: event bus, approval queue, SkillHub, /ws."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    from approval_queue import ApprovalQueue
    from event_bus import EventBus
    from security import is_extension_origin
    from skill_routes import SkillHub

    bus = EventBus()
    hub = SkillHub(bus, ApprovalQueue(data_dir / "approvals.json"), data_dir)
    raw: list[dict] = []
    app = FastAPI()

    @app.websocket("/")
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if ws.query_params.get("token") != "test-key":   # the extension sends the key it got by pairing
            await ws.close(code=1008)
            return
        await ws.accept()
        client = await bus.register(ws)
        if is_extension_origin(ws.headers.get("origin")):
            client.kind = "extension"
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = str(msg.get("event"))
                raw.append(msg)
                if kind.startswith("SKILL_"):
                    asyncio.create_task(hub.dispatch(client, kind, msg))
        except WebSocketDisconnect:
            pass
        finally:
            await bus.unregister(client)

    return app, hub, bus, raw


async def _until(cond, timeout=15.0, what="condition"):
    # A real Chromium runs here; on a busy machine it can take a few times longer, so allow 3x before failing.
    timeout *= 3
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        v = cond()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.skipif(not _port_free(9120), reason="port 9120 busy (a daemon is running)")
def test_record_and_replay_end_to_end(site):
    import uvicorn

    async def go():
        data_dir = Path(tempfile.mkdtemp(prefix="orion-skill-e2e-"))
        app, hub, bus, raw = _mini_daemon(data_dir)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=9120, log_level="warning"))
        server_task = asyncio.create_task(server.serve())
        try:
            await _until(lambda: server.started, what="mini daemon")
            async with pw.async_playwright() as p:
                ctx = await p.chromium.launch_persistent_context(
                    tempfile.mkdtemp(prefix="orion-chrome-"), headless=True, executable_path=CHROME,
                    args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                # Review B1: the extension only connects once paired; give it a key as the popup would.
                sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
                await sw.evaluate("chrome.storage.local.set({orion_pair_key: 'test-key'}).then(() => connectWebSocket())")
                await _until(lambda: any(c.kind == "extension" for c in bus.clients.values()), 20, "extension socket")
                await page.goto(site + "/index.html")
                await page.bring_to_front()

                # ---------------------------------------------------- record ("Orion, watch this")
                assert await hub.handle_text("Orion, watch this", "voice")
                await _until(lambda: any(r.get("active") for r in hub.recordings.values()), what="recording start")
                await _until(lambda: page.locator("orion-skill-pill").count(), what="recording pill")
                assert await page.locator("orion-skill-pill").count() == 1
                await page.wait_for_timeout(300)
                await page.click("text=New release")
                await page.wait_for_url("**/new.html")
                await page.wait_for_timeout(600)  # the extension re-injects the recorder after navigation
                await page.fill("#tag", "v1.2.3")
                await page.focus("#channel")  # a real (trusted) choice; select_option() fires synthetic events
                await page.keyboard.press("ArrowDown")
                await page.uncheck("#pre")
                await page.fill("#pw", "hunter2")
                await page.fill("#cc", "4111111111111111")
                await page.fill("#otp", "123456")
                await page.click("[data-testid=publish]")
                await page.wait_for_url("**/done.html*")
                await page.wait_for_selector("text=Release v1.2.3 published")
                await page.wait_for_timeout(600)
                # a sign-in page pauses recording
                await page.goto(site + "/signin.html")
                await _until(lambda: any(r.get("paused") for r in hub.recordings.values()), what="pause on sign-in")
                await page.fill("#u", "me@example.com")
                await page.fill("#p", "hunter2")
                await page.click("#go")
                await page.wait_for_timeout(300)
                await hub.handle_text("stop watching", "voice")
                await _until(lambda: hub.drafts, what="draft")
                draft = next(iter(hub.drafts.values()))

                # (BROWSER_TAB_MUTATED is the extension's older tab telemetry, not part of the Skill Recorder)
                everything = json.dumps([m for m in raw if str(m.get("event")).startswith("SKILL_")])
                for secret in ("hunter2", "4111111111111111", "123456", "me@example.com"):
                    assert secret not in everything, f"{secret} reached the daemon"
                acts = [(s["action"], s.get("value") or s.get("option_text") or s.get("url", "").rsplit("/", 1)[-1] or None)
                        for s in draft["steps"]]
                assert acts[:8] == [
                    ("open", "index.html"), ("click", None), ("wait_load", None), ("fill", "{{version}}"),
                    ("select", "stable"), ("check", None), ("click", None), ("wait_load", None)], acts
                assert not any(s["action"] == "open" and "signin" in s.get("url", "") for s in draft["steps"])
                assert draft["steps"][6]["risky"] and draft["steps"][6]["risk_reason"] == "submits a form"
                assert draft["card"][6] == "7. Click the “Publish release” button — needs your OK (submits a form)"

                saved = await hub.save_draft(draft["draft_id"], "Publish release", ["ship it"])
                steps = saved["steps"][:8] + [{"id": "s9", "action": "wait_text", "text": "Release {{version}} published"}]
                hub.store.save({**saved, "steps": steps}, bump=False)

                # ---------------------------------------------------- replay with a new value
                await page.goto(site + "/index.html")
                run = await hub.run_skill("publish_release", {"version": "v9.9.9"}, mode="step")
                await _until(lambda: run.status == "preview", what="preview")
                await _until(lambda: page.locator("orion-skill-mark").count(), what="preview highlights")
                hub.control(run.id, "run_all")
                item = await _until(lambda: next(iter(hub.approvals.pending()), None), 30, "approval request")
                assert item["kind"] == "skill_step" and "Publish release" in item["draft"]
                await page.wait_for_timeout(500)
                assert page.url.endswith("/new.html")  # held before publishing
                assert await page.input_value("#tag") == "v9.9.9"
                assert not await page.is_checked("#pre") and await page.input_value("#channel") == "stable"
                assert await page.input_value("#pw") == ""
                await hub.decide(item, "approve")
                await _until(lambda: run.status in ("done", "failed", "stopped"), 30, "run end")
                assert run.status == "done", run.message
                assert await page.text_content("#msg") == "Release v9.9.9 published (stable)"

                # a second run asks again (approval is per run, every time)
                run2 = await hub.run_skill("publish_release", {"version": "v10.0.0"}, mode="all", preview=False)
                item2 = await _until(lambda: next(iter(hub.approvals.pending()), None), 30, "second approval")
                await hub.decide(item2, "reject")
                await _until(lambda: run2.status in ("done", "failed", "stopped"), 15, "run2 end")
                assert run2.status == "stopped" and page.url.endswith("/new.html")

                # ---------------------------------------------------- self-healing
                hub.store.save({"id": "heal", "name": "Heal", "variables": [], "steps": [
                    {"id": "s1", "action": "open", "url": site + "/heal.html"},
                    {"id": "s2", "action": "click", "target": {"name": "Save change", "role": "button", "tag": "button",
                     "selectors": [{"kind": "css", "value": "#save-v1"}, {"kind": "role", "role": "button", "name": "Save change"}]}},
                    {"id": "s3", "action": "click", "target": {"name": "Frobnicate", "role": "button", "tag": "button",
                     "selectors": [{"kind": "css", "value": "#frob"}]}},
                ]})
                run3 = await hub.run_skill("heal", mode="all", preview=False)
                await _until(lambda: run3.status == "waiting_fix", 20, "ask to pick")
                assert await page.text_content("#status") == "saved"  # fuzzy match healed step 2
                await page.wait_for_timeout(300)
                await page.click("#x1")  # the user shows Orion the right element
                await _until(lambda: run3.status in ("done", "failed", "stopped"), 20, "run3 end")
                assert run3.status == "done", run3.message
                assert await page.text_content("#status") == "exported"
                healed = hub.store.load("heal")
                assert healed["trusted"] and healed["version"] == 3
                assert healed["steps"][1]["target"]["selectors"][0]["kind"] == "role"
                assert healed["steps"][1]["target"]["name"] == "Save changes"
                assert healed["steps"][2]["target"]["name"] == "Export data"
                assert [f["how"] for f in healed["fixes"]] == ["healed:fuzzy", "picked"]
                await ctx.close()
        finally:
            server.should_exit = True
            await server_task
    asyncio.run(go())
