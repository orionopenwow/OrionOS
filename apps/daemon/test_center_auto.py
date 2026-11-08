"""Test Center "Do it for me": one-click actions for the manual checklist items, and the watchers that tick an item
off by themselves when Orion can see it really happened (test_center.py uses these).

Every action here does the harmless half of a test and then waits for you:
  - "Say something" speaks one sentence, then asks "Did you hear it?" (Yes / No).
  - The practice skill page is served on 127.0.0.1 only (never on the network). Recording stops by itself when
    you press its Finish button; saving the skill stays your tap in the Skills card.
  - The practice screen change only ever recolours a scratch banner (testcenter/PracticeBanner.tsx). It is built
    and tested in a copy of Orion and shown as a ghost preview. It is never applied by the Test Center, not even
    when your levels would apply screen changes by themselves: applying it is always your choice.
  - "Start a project" only opens the Projects window with a sample idea filled in; you press Start.
  - The privacy test turns everything off (that is the test) and then reads every switch back.

What the Test Center never does: approve, sign, merge, send, post or push anything. Watchers only read state.

Watchers return None (nothing seen yet), ("pass", words), ("fail", words) for the result of an action the
Test Center itself started, or ("ask", words) to switch the item to a Yes / No question.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.test_center_auto")

PRACTICE_PORT = 9137                  # fixed when free, so a saved practice skill still finds its page later
PRACTICE_SKILL_NAME = "Orion practice skill"
BANNER_PATH = "apps/desktop/src/components/testcenter/PracticeBanner.tsx"
BANNER_COLOURS = ("#7C5CFF", "#5CFFB0")
ARM_TTL_S = 6 * 60 * 60               # an action's "watch for it" window
SAMPLE_PROJECT = {
    "name": "Star Catcher Test",
    "starter": "web-game",
    "idea": ("A tiny test game: a little ship moves left and right and catches falling stars. One point per star. "
             "Keep it very small; this is only to check that projects work."),
}
HELLO_MISSION = "Add a hello file"


# =====================================================================================  small helpers
def since(tc: Any, iid: str) -> float:
    """When to look from: when the item's action ran (if recently), else when this round of tests started."""
    r = tc._result(iid)
    armed = float(r.get("armed") or 0)
    if armed and tc.clock() - armed <= ARM_TTL_S:
        return armed
    return float(tc.state.get("started") or 0)


def armed(tc: Any, iid: str) -> bool:
    a = float(tc._result(iid).get("armed") or 0)
    return bool(a) and tc.clock() - a <= ARM_TTL_S


async def publish_open(tc: Any, what: str, **extra: Any) -> None:
    await tc.publish({"event": "HUD_OPEN", "what": what, **extra})


# =====================================================================================  practice skill page
PRACTICE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orion practice page</title>
<style>
 body{font:16px system-ui,sans-serif;background:#0b0d1a;color:#d8deff;margin:0;padding:32px;max-width:560px}
 h1{font-size:22px} ol{line-height:2} button,input{font:inherit;padding:8px 14px;border-radius:10px;
 border:1px solid #3a3f6b;background:#1a1d36;color:#fff} #swatch{display:inline-block;width:28px;height:28px;
 border-radius:8px;background:#333;vertical-align:middle;margin-left:8px} .done{color:#5cffb0}
</style></head><body>
<h1>Orion practice page</h1>
<p>Nothing here goes anywhere. It only lives on this computer, for the Test Center.</p>
<ol>
 <li>Type any word: <input id="word" data-testid="practice-word" autocomplete="off" placeholder="any word"></li>
 <li><button type="button" id="blue" data-testid="practice-blue">Make it blue</button><span id="swatch"></span></li>
 <li><button type="button" id="finish" data-testid="practice-finish">Finish</button></li>
</ol>
<p id="msg"></p>
<script>
 document.getElementById('blue').onclick=function(){document.getElementById('swatch').style.background='#5c8aff'};
 document.getElementById('finish').onclick=function(){
   var w=document.getElementById('word').value||'(nothing)';
   document.getElementById('msg').innerHTML='<span class="done">Done. You typed: '+w.replace(/[<>&]/g,'')+'</span>';
   fetch('/done',{method:'POST'}).catch(function(){});
 };
</script></body></html>
"""


class PracticePage:
    """The practice page on 127.0.0.1 (never on the network). /done is called by the page's Finish button."""

    def __init__(self, on_done: Callable[[], None], port: int = PRACTICE_PORT) -> None:
        self.on_done = on_done
        self.finished = 0.0
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, code: int, body: bytes, kind: str = "text/html; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.split("?")[0] in ("/", "/index.html"):
                    self._send(200, PRACTICE_HTML.encode("utf-8"))
                else:
                    self._send(404, b"not here")

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?")[0] == "/done":
                    page.finished = time.time()
                    try:
                        page.on_done()
                    except Exception:
                        log.debug("practice page done callback failed", exc_info=True)
                    self._send(200, b'{"ok":true}', "application/json")
                else:
                    self._send(404, b"not here")

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, name="orion-practice-page", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass


def practice_page(tc: Any) -> PracticePage:
    page = getattr(tc, "_practice", None)
    if page is not None:
        return page
    loop = asyncio.get_running_loop()

    def done() -> None:      # Finish pressed: stop a recording that's running (browser or desktop)
        asyncio.run_coroutine_threadsafe(_stop_recording(tc), loop)
    page = PracticePage(done, port=getattr(tc, "practice_port", PRACTICE_PORT))
    tc._practice = page
    return page


def _hub(tc: Any) -> Any:
    return getattr(tc.daemon, "skills", None)


def _recording(hub: Any) -> Optional[dict[str, Any]]:
    try:
        return hub.state().get("recording")
    except Exception:
        return None


async def _stop_recording(tc: Any) -> None:
    hub = _hub(tc)
    if hub is None:
        return
    rec = _recording(hub)
    if not rec:
        return
    try:
        if rec.get("surface") == "desktop":
            await hub.desktop.stop_recording()
        else:
            await hub.stop_recording()
    except Exception:
        log.debug("couldn't stop the practice recording", exc_info=True)


def open_browser(tc: Any, url: str) -> bool:
    opener = getattr(tc, "browser", None)
    if opener is not None:
        return bool(opener(url))
    try:
        import webbrowser
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


async def record_practice_skill(tc: Any) -> tuple:
    hub = _hub(tc)
    if hub is None:
        return "todo", "The Skills part isn't running. Open Skills and press Record yourself."
    import freeze
    if (frozen := freeze.blocked("record skills")):
        return "todo", frozen
    page = practice_page(tc)
    open_browser(tc, page.url)
    await asyncio.sleep(getattr(tc, "practice_wait_s", 1.5))   # let the page open in front first
    ok = False
    try:
        ok = bool(await hub.start_recording())
    except Exception:
        ok = False
    how = "the browser"
    if not ok:
        try:
            ok, _msg = await hub.desktop.start_recording()
            how = "the desktop"
        except Exception:
            ok = False
    await publish_open(tc, "skills")
    if not ok:
        return "todo", ("The practice page is open, but Orion can't record yet (it needs the Orion Bridge "
                        "extension in Chrome). In Skills, press Record, do the 3 steps on the page, then Stop.")
    return "todo", (f"Recording {how}. On the practice page: type any word, press Make it blue, press Finish. "
                    "Recording stops by itself; then press Save on the skill card.")


async def replay_practice_skill(tc: Any) -> tuple:
    hub = _hub(tc)
    if hub is None:
        return "todo", "The Skills part isn't running."
    try:
        skills = hub.store.summaries()
    except Exception:
        skills = []
    if not skills:
        return "todo", "There's no skill to replay yet. Do Record a tiny skill first."
    page = practice_page(tc)
    practice = [s for s in skills if str(s.get("start_url") or "").startswith(page.url)]
    pick = max(practice or skills, key=lambda s: float(s.get("updated_at") or 0))
    ours = bool(practice)
    try:
        if ours:   # the practice skill only clicks on the practice page: it runs straight away
            open_browser(tc, page.url)
            await hub.run_skill(str(pick["id"]), mode="all", preview=False, source="test-center")
        else:      # someone's own skill: show its steps first, you press Start
            await hub.run_skill(str(pick["id"]), preview=True, source="test-center")
    except PermissionError as exc:
        return "todo", str(exc)
    except Exception as exc:
        return "todo", f"Couldn't start the replay: {type(exc).__name__}."
    await publish_open(tc, "skills")
    if ours:
        return "todo", f"Replaying “{pick.get('name')}” on the practice page. Watch it repeat your clicks."
    return "todo", f"“{pick.get('name')}” is ready to replay. Check its steps in Skills, then press Start."


async def watch_skill_saved(tc: Any) -> Optional[tuple]:
    hub = _hub(tc)
    if hub is None:
        return None
    t0 = since(tc, "record_skill")
    try:
        new = [s for s in hub.store.summaries() if float(s.get("updated_at") or 0) >= t0]
    except Exception:
        return None
    if new:
        s = max(new, key=lambda s: float(s.get("updated_at") or 0))
        return "pass", f"Orion saved the skill “{s.get('name')}” ({s.get('steps')} steps)."
    return None


async def watch_skill_replayed(tc: Any) -> Optional[tuple]:
    hub = _hub(tc)
    if hub is None:
        return None
    t0 = since(tc, "replay_skill")
    runs = []
    for runner in (getattr(hub, "runner", None), getattr(getattr(hub, "desktop", None), "runner", None)):
        try:
            runs += list(runner.runs.values()) if runner is not None else []
        except Exception:
            pass
    for r in runs:
        if r.status == "done" and float(getattr(r, "started_at", 0) or 0) >= t0:
            return "pass", f"Orion replayed “{r.skill.get('name')}” from start to end."
    return None


# =====================================================================================  practice screen change
def _banner(colour: str) -> str:
    return ("/**\n * Practice banner for the Test Center's \"screen change shows a preview first\" test.\n"
            " * The Test Center proposes changing only the colour below; you decide whether it is applied.\n */\n"
            f"export const PRACTICE_COLOUR = '{colour}';\n\n"
            "export default function PracticeBanner() {\n"
            "  return (\n"
            "    <div className=\"rounded-xl px-3 py-2 text-[12px] font-semibold text-white\"\n"
            "      style={{ background: `${PRACTICE_COLOUR}33`, border: `1px solid ${PRACTICE_COLOUR}` }}>\n"
            "      Practice banner: a screen change test can recolour this.\n"
            "    </div>\n"
            "  );\n"
            "}\n")


def practice_proposal(current: Optional[str]) -> dict[str, Any]:
    """A self-change that only swaps the practice banner's colour (both directions, so it is always a change)."""
    cur = current if current is not None else _banner(BANNER_COLOURS[0])
    m = re.search(r"PRACTICE_COLOUR = '(#[0-9A-Fa-f]{6})'", cur)
    old = m.group(1) if m else BANNER_COLOURS[0]
    new = BANNER_COLOURS[1] if old.upper() == BANNER_COLOURS[0].upper() else BANNER_COLOURS[0]
    after = cur.replace(f"PRACTICE_COLOUR = '{old}'", f"PRACTICE_COLOUR = '{new}'") if m else _banner(new)
    diff = "".join(difflib.unified_diff(cur.splitlines(True) if current is not None else [],
                                        after.splitlines(True), f"a/{BANNER_PATH}", f"b/{BANNER_PATH}"))
    return {"id": f"patch-{secrets.token_hex(4)}",
            "request": "Test Center practice: recolour the practice banner",
            "summary": f"Practice: make the Test Center's practice banner {('green' if new == BANNER_COLOURS[1] else 'violet')}",
            "reason": "A harmless practice change so you can see a ghost preview. Approve or reject it, your choice.",
            "diff": diff,
            "files": [{"path": BANNER_PATH, "action": "modify" if current is not None else "create",
                       "additions": 1, "deletions": 1 if current is not None else 0, "new_content": after}]}


async def stage_practice_change(tc: Any) -> tuple:
    sp = getattr(tc.daemon, "selfprog", None)
    if sp is None:
        return "todo", "Orion's self-change part isn't running here."
    import freeze
    if (frozen := freeze.blocked("change its own code")):
        return "todo", frozen
    try:
        root = sp.view_root()
        p = Path(root) / BANNER_PATH
        current = p.read_text(encoding="utf-8") if p.is_file() else None
    except Exception:
        current = None
    proposal = practice_proposal(current)
    try:
        c = sp.new_change(proposal)
    except Exception as exc:
        return "todo", f"Couldn't start the practice change: {str(exc)[:160] or type(exc).__name__}."
    tc.state["practice_change"] = c["id"]
    tc.save()

    async def build() -> None:
        # build_and_test only: never sp.stage(), whose "tell me after" level could apply it without you
        try:
            sp._emit({"event": "SELFPROG_CHANGE", "change": sp.public(c, diff=False)})
            done = await asyncio.to_thread(sp.build_and_test, c["id"])
            sp._emit({"event": "SELFPROG_CHANGE", "change": sp.public(done, diff=False)})
        except Exception:
            log.warning("practice change build failed", exc_info=True)
    t = asyncio.get_running_loop().create_task(build(), name="test-center-practice-change")
    tc._bg.add(t)
    t.add_done_callback(tc._bg.discard)
    return "todo", ("Orion is building a practice change in a copy of itself (it only recolours a practice "
                    "banner). The ghost preview appears in the corner when it's ready; nothing changes unless you "
                    "approve it.")


async def watch_ghost_ready(tc: Any) -> Optional[tuple]:
    sp = getattr(tc.daemon, "selfprog", None)
    cid = tc.state.get("practice_change")
    if sp is None or not cid:
        return None
    try:
        c = sp.get(cid)
    except Exception:
        c = None
    if not c:
        return None
    st = c.get("status")
    if st in ("ready", "applied", "rejected", "rolled_back"):
        return "pass", ("The ghost preview was ready before anything changed. Approving or rejecting it is up to "
                        "you." if st == "ready" else f"The ghost preview was shown first (you {st.replace('_', ' ')} it).")
    if st == "failed":
        return "fail", f"The practice change didn't pass its checks: {str(c.get('error') or '')[:200]}"
    return None


# =====================================================================================  undo
async def undo_practice_or_open(tc: Any) -> tuple:
    sp = getattr(tc.daemon, "selfprog", None)
    cid = tc.state.get("practice_change")
    if sp is not None and cid:
        try:
            import orion_layer
            live = [s for s in orion_layer.history(sp.data) if s.get("status") in ("probation", "kept")]
        except Exception:
            live = []
        if live and live[-1].get("change_id") == cid:      # only ever the Test Center's own practice change
            try:
                await asyncio.to_thread(sp.undo_last, by="you (Test Center)")
                return "todo", "Putting back the practice banner change…"
            except Exception as exc:
                return "todo", f"Couldn't undo it: {str(exc)[:160]}"
    await publish_open(tc, "selfchanges")
    return "todo", ("Opened Self-changes history. Press Undo on the last change (approve the practice change "
                    "first if there's nothing to undo yet).")


async def watch_undo(tc: Any) -> Optional[tuple]:
    t0 = since(tc, "undo_last")
    sp = getattr(tc.daemon, "selfprog", None)
    if sp is not None:
        try:
            for c in sp.list():
                if c.get("status") == "rolled_back" and float(c.get("rolled_back_at") or 0) >= t0:
                    return "pass", f"Orion put back a change: {str(c.get('summary') or '')[:120]}"
        except Exception:
            pass
    log_ = getattr(tc, "change_log", None)
    if log_ is None:
        try:
            import changes
            log_ = changes._log      # only if the daemon already made it (never create one here)
        except Exception:
            log_ = None
    if log_ is not None:
        try:
            for row in log_.own(50):
                if row.get("undoes") and float(row.get("ts") or 0) >= t0:
                    return "pass", f"Undone: {str(row.get('what') or '')[:120]}"
        except Exception:
            pass
    return None


# =====================================================================================  privacy
async def privacy_off_all(tc: Any) -> tuple:
    d = tc.daemon
    pd = getattr(d, "privacy_dashboard", None)
    if pd is None:
        import privacy_dashboard
        pd = privacy_dashboard.PrivacyDashboard(d)
        try:
            d.privacy_dashboard = pd
        except Exception:
            pass
    res = await pd.off_all()
    await publish_open(tc, "privacy")
    snap = pd.snapshot()
    still = [r["label"] for r in snap.get("sensors", []) if r.get("on")]
    n = len(snap.get("sensors", []))
    if still:
        return "fail", f"Still on after Turn everything off: {', '.join(still)}. {res.get('message', '')}".strip()
    return "pass", (f"Turned everything off and checked all {n} switches: nothing is watching or listening. "
                    "Turn back on what you want in each feature's own settings.")


# =====================================================================================  projects
async def open_project_wizard(tc: Any) -> tuple:
    name = SAMPLE_PROJECT["name"]
    taken = set()
    pj = _projects(tc)
    if pj is not None:
        try:
            taken = {str(p.get("name", "")).lower() for p in pj.list()}
        except Exception:
            taken = set()
    n = 2
    base = name
    while name.lower() in taken:
        name, n = f"{base} {n}", n + 1
    await publish_open(tc, "projects", prefill={**SAMPLE_PROJECT, "name": name})
    return "todo", (f"Opened Projects with a sample idea filled in (“{name}”). Press Start; this ticks itself "
                    "when Claude's first version reaches review.")


def _projects(tc: Any) -> Any:
    pj = getattr(tc.daemon, "projects", None)
    if pj is not None:
        return pj
    try:
        import projects
        return projects.attach(tc.daemon)
    except Exception:
        return None


async def watch_project_review(tc: Any) -> Optional[tuple]:
    pj = _projects(tc)
    if pj is None:
        return None
    t0 = since(tc, "start_project")
    try:
        items = pj.list()
    except Exception:
        return None
    for p in items:
        latest = p.get("latest") or {}
        if float(p.get("created") or 0) >= t0 and latest.get("state") == "review":
            return "pass", f"The project “{p.get('name')}” was made and its first mission reached review."
    return None


# =====================================================================================  a real mission
async def start_hello_mission(tc: Any) -> tuple:
    if not tc.which("claude"):
        return "skip", "Claude Code isn't installed, so there's no real mission to try."
    mc = getattr(tc.daemon, "mission_control", None)
    if mc is None:
        await publish_open(tc, "missions")
        return "todo", "Mission Control isn't running yet. Opened it for you."
    import freeze
    if (frozen := freeze.blocked("start missions")):
        return "todo", frozen
    repo = Path(tc.path).parent / "test-center" / "hello-repo"
    try:
        if not (repo / ".git").is_dir():
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "README.md").write_text("A small test repo for Orion's Test Center. Safe to delete.\n",
                                            encoding="utf-8")
            from test_center import _git
            for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "start")):
                if await asyncio.to_thread(_git, repo, *args) != 0:
                    return "todo", "Couldn't make the test repo (is Git installed?)."
        m = mc.create(HELLO_MISSION, repo=str(repo), agent="claude", source="test-center",
                      description="Add hello.txt with one friendly line. Nothing else.", budget_usd=0.5)
    except Exception as exc:
        return "todo", f"Couldn't start the mission: {str(exc)[:160] or type(exc).__name__}."
    tc.state["hello_mission"] = m.get("id")
    tc.save()
    await publish_open(tc, "missions")
    return "todo", ("Started “Add a hello file” with Claude Code on a test repo (about 50 cents at most). This "
                    "ticks itself when the review card arrives; merging is up to you.")


async def watch_hello_mission(tc: Any) -> Optional[tuple]:
    mc = getattr(tc.daemon, "mission_control", None)
    mid = tc.state.get("hello_mission")
    if mc is None or not mid:
        return None
    try:
        m = mc.get(mid) or {}
    except Exception:
        return None
    if m.get("state") in ("review", "done"):
        return "pass", "Claude Code finished the mission and a review card arrived."
    if m.get("state") in ("failed", "stopped"):
        return "fail", f"The mission ended without a review card: {str(m.get('summary') or m.get('state'))[:160]}"
    return None


# =====================================================================================  phone draft
async def watch_phone_draft(tc: Any) -> Optional[tuple]:
    pp = getattr(tc.daemon, "phone_pair", None)
    at = float(getattr(pp, "phone_draft_at", 0) or 0)
    if at and at >= since(tc, "phone_draft"):
        return "pass", "Your phone asked for a draft and got one. The PC sent nothing."
    return None


# =====================================================================================  OrionOS
SUSPEND_STATS = Path("/sys/power/suspend_stats/success")
VAULT_DEVICE = Path("/dev/mapper/orionvault")


def suspend_count(path: Path = SUSPEND_STATS) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


async def watch_usb_boot(tc: Any) -> Optional[tuple]:
    if tc.platform == "orionos" and tc.profile == "image":
        return "pass", "You're running OrionOS from the USB stick right now, so it started the computer."
    return None


async def watch_vault(tc: Any) -> Optional[tuple]:
    if Path(getattr(tc, "vault_device", VAULT_DEVICE)).exists():
        return "pass", "Your vault is open, so your passphrase worked."
    return None


async def arm_sleep(tc: Any) -> tuple:
    n = suspend_count(Path(getattr(tc, "suspend_stats", SUSPEND_STATS)))
    tc.state["suspend_base"] = n
    tc.save()
    if n is None:
        return "todo", "Close the lid for ten seconds, open it, then answer below."
    return "todo", "Now close the lid for ten seconds and open it again. Orion notices the sleep by itself."


async def watch_sleep(tc: Any) -> Optional[tuple]:
    base = tc.state.get("suspend_base")
    if base is None:
        return None
    n = suspend_count(Path(getattr(tc, "suspend_stats", SUSPEND_STATS)))
    if n is not None and n > int(base):
        return "ask", "The computer slept and woke up. Did it ask for your password when you opened it?"
    return None


# =====================================================================================  Windows
def desktop_dirs() -> list[Path]:
    home = Path.home()
    out = [home / "Desktop", home / "OneDrive" / "Desktop"]
    pub = os.environ.get("PUBLIC")
    if pub:
        out.append(Path(pub) / "Desktop")
    return out


def find_shortcut(dirs: Optional[list[Path]] = None) -> Optional[Path]:
    for d in dirs or desktop_dirs():
        try:
            for p in d.glob("*.lnk"):
                if p.name.lower().startswith("orion"):
                    return p
        except OSError:
            continue
    return None


async def check_shortcut(tc: Any) -> tuple:
    if find_shortcut(getattr(tc, "desktop_dirs", None)) is None:
        return "todo", "There's no Orion shortcut on your desktop yet. Double-click Start Orion.cmd once to make it."
    return "todo", "The Orion shortcut is on your desktop. Close Orion, double-click it, then answer below."


async def watch_shortcut(tc: Any) -> Optional[tuple]:
    if armed(tc, "win_shortcut") and find_shortcut(getattr(tc, "desktop_dirs", None)) is not None:
        return "ask", "Did double-clicking the Orion shortcut open Orion?"
    return None


def _phone_link(tc: Any) -> Any:
    return getattr(tc.daemon, "phone_link", None)


async def let_phone_in(tc: Any) -> tuple:
    pl = _phone_link(tc)
    if pl is None:
        return "todo", "The phone part isn't running. Open Phone and press Pair my phone."
    try:
        if not pl.port_running():
            await pl.ensure_port()
        lan = await asyncio.to_thread(pl.lan, True)
    except Exception as exc:
        return "todo", f"Couldn't check: {type(exc).__name__}."
    if lan.get("ok"):
        return "pass", str(lan.get("words") or "Your phone can reach this PC on your home Wi-Fi.")
    if lan.get("fix") == "open_access":
        res = pl.open_access()
        return "todo", str(res.get("words") or "")
    await publish_open(tc, "phone")
    return "todo", f"{lan.get('words', '')} {lan.get('fix_words', '')}".strip()


async def watch_phone_access(tc: Any) -> Optional[tuple]:
    pl = _phone_link(tc)
    if pl is None:
        return None
    try:
        if not pl.port_running():
            return None
        lan = await asyncio.to_thread(pl.lan)          # cached for 30 s inside
    except Exception:
        return None
    if lan.get("ok"):
        return "pass", str(lan.get("words") or "Your phone can reach this PC on your home Wi-Fi.")
    return None


READY_REPORT = "Orion readiness report.txt"
READY_CMD = "Check if my PC is ready for Orion.cmd"


async def run_ready_check(tc: Any) -> tuple:
    root = getattr(tc, "repo_root", None)
    if root is None:
        try:
            from config import settings
            root = settings.repo_root
        except Exception:
            root = Path(__file__).resolve().parents[2]
    root = Path(root)
    cmd = root / READY_CMD
    if not cmd.is_file():
        return "todo", f"Couldn't find “{READY_CMD}” in the Orion folder. Double-click it yourself."
    popen = getattr(tc, "popen", None) or subprocess.Popen
    try:
        popen(["cmd.exe", "/c", "start", "", str(cmd)], cwd=str(root),
              creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except Exception as exc:
        return "todo", f"Couldn't start it: {type(exc).__name__}. Double-click “{READY_CMD}” yourself."
    return "todo", "The readiness check is running in its own window. This ticks itself when its report is saved."


async def watch_ready_report(tc: Any) -> Optional[tuple]:
    t0 = since(tc, "win_ready")
    for d in getattr(tc, "desktop_dirs", None) or desktop_dirs():
        p = d / READY_REPORT
        try:
            if not p.is_file() or p.stat().st_mtime < t0:
                continue
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        m = re.search(r"(Ready to replace Windows[^\r\n]*|Ready for side by side[^\r\n]*|Keep Windows for now[^\r\n]*)",
                      text)
        if not m:
            continue
        verdict = m.group(1).strip()[:220]
        return ("fail", verdict) if verdict.startswith("Keep Windows") else ("pass", verdict)
    return None


# =====================================================================================  Yes / No questions
def ask_only(words: str) -> Callable[[Any], Any]:
    async def run(tc: Any) -> tuple:
        return "todo", words, {"ask": True}
    return run
