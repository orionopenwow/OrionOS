"""Guide Mode: Orion walks you through a task on your screen (OrionOS 1.0 plan, D. "Guide Mode" + "pointer ring and
'step done' watcher", milestone M5).

Say "Orion, show me how to install Discord" (or type it in the HUD's Guide box). Orion picks a guide, then for each
step:
  1. says the step in one sentence and shows it on a small card;
  2. finds the button to press, by its name in the accessibility tree (AT-SPI on Linux) or, failing that, by reading
     the screen (OCR, the same helper the phone's "next step" cards use, guide_cards.py), and puts a glowing ring
     around it:
       - OrionOS (Sway): a click-through overlay window above every app (os/desktop/orion-guide-ring, a
         gtk-layer-shell surface) that follows $XDG_RUNTIME_DIR/orion-guide.json;
       - Windows: the HUD opens a transparent, click-through, always-on-top window for the ring
         (apps/desktop/src/lib/guideOverlay.ts);
  3. moves on by itself when it sees you did the step (the window it expects opened, the text it expects showed up,
     or the button went away), or when you press Done.

Rules kept:
  - Orion never clicks for you here. "Orion does it" stays on the phone's next-step cards, behind your fingerprint
    (guide_cards.py, unchanged). Guide Mode can mirror its steps to those cards if you ask (send_to_phone).
  - The screen is only read while a guide runs, it is shown on the privacy dashboard, and nothing leaves the
    computer. On password, banking, private or lock screens (capture_privacy.py) nothing is read: the card says
    "Do this step yourself, then press Done."
  - Guide Mode never types passwords.

Routes:  GET /guide  ·  POST /guide/start {"task", "send_to_phone"?}  ·  POST /guide/next  ·  POST /guide/back
         POST /guide/stop  ·  GET /guide/list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel

log = logging.getLogger("orion.guide")

POLL_S = 1.5
GONE_POLLS = 2               # the target must be missing this many checks in a row to count as "done"
MAX_STEPS = 10
STATE_FILE = "orion-guide.json"

START_RE = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:orion[\s,]*)?(?:please\s+)?(?:show me how (?:to|do i)|"
                      r"walk me through(?: how to)?|guide me (?:through|to)|teach me (?:how )?to|"
                      r"help me (?:to )?step by step to)\s+(?P<task>.+?)[\s.?!]*$", re.I)
NEXT_RE = re.compile(r"^\s*(?:orion[\s,]*)?(?:next(?: step)?|done|i did it|i'?ve done it|got it)[\s.!]*$", re.I)
BACK_RE = re.compile(r"^\s*(?:orion[\s,]*)?(?:go back|back|previous step)[\s.!]*$", re.I)
STOP_RE = re.compile(r"^\s*(?:orion[\s,]*)?(?:stop|cancel|end|quit|exit)(?: the)? guid(?:e|ing)(?: mode)?[\s.!]*$",
                     re.I)

# Built-in guides. "find" is the visible name of what to press; "done" is how Orion sees you did it:
#   {"window": regex}       the focused window's title matches
#   {"screen": regex}       this text shows up on screen
#   {"gone": true}          the ringed button disappears (a menu closed, a page moved on)
# A step without "done" waits for the Done button.
GUIDES: list[dict[str, Any]] = [
    {"orionos": True, "id": "pair-phone", "title": "Pair your phone", "match": [r"pair (my |the |a )?phone", r"connect (my )?phone"],
     "steps": [
         {"text": "Open Settings: press Super, type Settings, then press Enter.", "done": {"window": r"settings"}},
         {"text": "Click Orion in the list on the left.", "find": "Orion", "done": {"screen": r"pair my phone"}},
         {"text": "Click Pair my phone. A code and a QR picture appear.", "find": "Pair my phone",
          "done": {"screen": r"\bcode\b|scan"}},
         {"text": "On your phone, open Orion and tap Pair, then scan the picture. Press Done when it says paired."},
     ]},
    {"orionos": True, "id": "wifi", "title": "Connect to Wi-Fi", "match": [r"(connect|join|get on)( to)? (the )?(wi-?fi|wireless|internet|network)"],
     "steps": [
         {"text": "Open Settings: press Super, type Settings, then press Enter.", "done": {"window": r"settings"}},
         {"text": "Click Wi-Fi & network.", "find": "Wi-Fi", "done": {"screen": r"networks?"}},
         {"text": "Click your network's name.", "done": {"screen": r"password"}},
         {"text": "Type the Wi-Fi password and press Connect. Orion doesn't read or type passwords, so press Done "
                  "when it says connected."},
     ]},
    {"orionos": True, "id": "screenshot", "title": "Take a screenshot", "match": [r"(take|make|grab) (a )?screen ?shot", r"screen ?shot"],
     "steps": [
         {"text": "Press Print Screen for the whole screen, or Shift+Print Screen to pick an area.",
          "done": {"window": r"swappy|screenshot"}},
         {"text": "Draw on it if you like, then press Save. It's in Pictures/Screenshots and already copied."},
     ]},
    {"orionos": True, "id": "screen-reader", "title": "Turn on the screen reader", "match": [r"screen ?reader", r"read (the )?screen (out )?(aloud|to me)", r"orca"],
     "steps": [
         {"text": "Press Super+Alt+S. Orion's screen reader starts and reads out what's focused."},
         {"text": "To change its voice or speed, open Settings and click Accessibility.", "find": "Accessibility"},
     ]},
    {"orionos": True, "id": "hot-mic", "title": "Talk to Orion hands-free", "match": [r"hot ?mic", r"hands[- ]free", r"wake word", r"talk to (you|orion)"],
     "steps": [
         {"text": "Open a terminal: press Super+Return.", "done": {"window": r"foot|terminal"}},
         {"text": "Type: orion-ctl voice enroll, press Enter and read the lines aloud. It learns your voice."},
         {"text": "Type: orion-ctl mic on. A pink light at the top shows Orion is listening. Ctrl+Alt+M mutes it."},
     ]},
    {"orionos": True, "id": "install-app", "title": "Install an app", "match": [r"\b(install|get|download) (?P<app>[\w .+-]{2,30}?)( app)?$"],
     "steps": [
         {"text": "Open Get apps: press Super, type Get apps, then press Enter.", "done": {"window": r"get apps"}},
         {"text": "Click the search box and type {app}.", "find": "Search", "done": {"screen": r"{app}"}},
         {"text": "Click Install next to {app}.", "find": "Install", "done": {"screen": r"\b(installed|open|installing)\b"}},
         {"text": "Your phone may ask for your fingerprint to approve the install. When it's done, open {app} from "
                  "the launcher.", "done": {"window": r"{app}"}},
     ]},
]


def _fill(text: str, slots: dict[str, str]) -> str:
    for k, v in slots.items():
        text = text.replace("{" + k + "}", v)
    return text


def match_guide(task: str, guides: list[dict[str, Any]] = GUIDES,
                orionos: Optional[bool] = None) -> Optional[dict[str, Any]]:
    """A ready guide for this task with its blanks filled in ("install Discord" -> app=Discord), or None.
    The built-in guides describe OrionOS; on Windows a model writes the guide instead."""
    t = " ".join(task.strip().rstrip(".?!").split())
    orionos = sys.platform.startswith("linux") if orionos is None else orionos
    for g in guides:
        if g.get("orionos") and not orionos:
            continue
        for pat in g.get("match", []):
            m = re.search(pat, t, re.I)
            if not m:
                continue
            slots = {k: v.strip() for k, v in m.groupdict().items() if v}
            if "app" in slots:
                if slots["app"].lower() in ("it", "this", "that", "apps", "an app", "app", "something"):
                    continue
                slots["app"] = slots["app"][:1].upper() + slots["app"][1:]
            steps = []
            for s in g["steps"]:
                st = {"text": _fill(s["text"], slots)}
                if s.get("find"):
                    st["find"] = _fill(s["find"], slots)
                if s.get("done"):
                    esc = {k2: re.escape(v2) for k2, v2 in slots.items()}
                    st["done"] = {k: _fill(v, esc) if isinstance(v, str) else v for k, v in s["done"].items()}
                steps.append(st)
            title = g["title"] if "app" not in slots else f"Install {slots['app']}"
            return {"id": g["id"], "title": title, "steps": steps}
    return None


def clean_plan(raw: Any) -> list[dict[str, Any]]:
    """Steps from a model's answer, made safe: short text, a button name, done = gone or the Done button."""
    steps = raw.get("steps") if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, str):
            s = {"text": s}
        if not isinstance(s, dict) or not str(s.get("text", "")).strip():
            continue
        text = " ".join(str(s["text"]).split())[:240]
        if re.search(r"\b(password|passcode|pin|card number|cvv)\b", text, re.I) and "yourself" not in text.lower():
            text += " Orion doesn't read or type this; press Done when you've done it."
        st: dict[str, Any] = {"text": text}
        find = " ".join(str(s.get("find") or s.get("button") or "").split())[:40]
        if find:
            st["find"] = find
            st["done"] = {"gone": True}
        out.append(st)
        if len(out) >= MAX_STEPS:
            break
    return out


PLAN_SYSTEM = ("You write short step-by-step guides for a desktop computer running OrionOS (Ubuntu with the Sway "
               "window manager, Firefox, Get apps for installing, Settings for system options) or Windows. Answer only "
               "JSON: {\"steps\": [{\"text\": \"one plain sentence\", \"find\": \"exact visible name of the button or "
               "menu item to click, or empty\"}]}. At most 8 steps. Never ask the user to share a password with you.")


# ---------------------------------------------------------------------- the screen: find the target
def ocr_find(words: list[dict[str, Any]], label: str) -> Optional[dict[str, int]]:
    """Box around the first run of OCR words that spells `label` (case and punctuation ignored)."""
    want = [w for w in re.split(r"\W+", label.lower()) if w]
    if not want:
        return None
    norm = [re.sub(r"\W+", "", str(w.get("text", "")).lower()) for w in words]
    for i in range(len(words) - len(want) + 1):
        if all(norm[i + j] == want[j] for j in range(len(want))):
            run = words[i:i + len(want)]
            x0, y0 = min(w["x"] for w in run), min(w["y"] for w in run)
            x1 = max(w["x"] + w["w"] for w in run)
            y1 = max(w["y"] + w["h"] for w in run)
            return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
    return None


ATSPI_ROLES = ("push button", "toggle button", "menu item", "link", "page tab", "list item", "check box",
               "radio button", "text", "entry", "combo box", "icon", "table cell", "label", "menu")


def atspi_find(label: str, max_nodes: int = 4000) -> Optional[dict[str, int]]:
    """Screen box of a showing accessible object named `label` (AT-SPI, Linux). None if not found / unavailable."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        import pyatspi  # python3-pyatspi (os/image/os-everyone.sh); absent off OrionOS
    except Exception:
        return None
    want = label.strip().lower()
    seen = 0
    try:
        desktop = pyatspi.Registry.getDesktop(0)
        stack = [desktop.getChildAtIndex(i) for i in range(desktop.childCount)]
        while stack and seen < max_nodes:
            node = stack.pop()
            seen += 1
            if node is None:
                continue
            try:
                st = node.getState()
                showing = st.contains(pyatspi.STATE_SHOWING) and st.contains(pyatspi.STATE_VISIBLE)
                if (node.name or "").strip().lower() == want and showing and node.getRoleName() in ATSPI_ROLES:
                    e = node.queryComponent().getExtents(pyatspi.WINDOW_COORDS)
                    if e.width > 0 and e.height > 0:
                        try:
                            pid = int(node.get_application().get_process_id())
                        except Exception:
                            pid = 0
                        return {"x": e.x, "y": e.y, "w": e.width, "h": e.height, "pid": pid}
                if showing or node.getRoleName() in ("application", "frame", "window"):
                    stack.extend(node.getChildAtIndex(i) for i in range(min(node.childCount, 400)))
            except Exception:
                continue
    except Exception:
        log.debug("AT-SPI search failed", exc_info=True)
    return None


def to_output_pixels(box: dict[str, int], win_rect: dict[str, int], out_rect: dict[str, int],
                     scale: float) -> dict[str, int]:
    """AT-SPI gives positions inside the app's window on Wayland. Sway knows where that window is (logical, whole
    desktop). The ring and the screenshot use the focused screen's own pixels, so: window + offset - screen, x scale."""
    s = scale or 1.0
    return {"x": round((win_rect["x"] - out_rect["x"] + box["x"]) * s),
            "y": round((win_rect["y"] - out_rect["y"] + box["y"]) * s),
            "w": round(box["w"] * s), "h": round(box["h"] * s)}


def _place(box: dict[str, int]) -> Optional[dict[str, int]]:
    """Accessibility box -> focused-screen pixels (Sway). None when the window isn't on the focused screen."""
    try:
        import linux_desktop
        if not linux_desktop.is_sway():
            return {k: box[k] for k in ("x", "y", "w", "h")}
        out = next((o for o in linux_desktop.outputs() if o.get("focused")), None)
        tree = linux_desktop.swaymsg("-t", "get_tree", json_out=True)
    except Exception:
        return None
    if not out or not isinstance(tree, dict):
        return None
    stack, win = [tree], None
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if box.get("pid") and n.get("pid") == box["pid"] and n.get("visible"):
                win = n
                break
            stack.extend((n.get("nodes") or []) + (n.get("floating_nodes") or []))
    if win is None:
        return None
    wr = dict(win.get("rect") or {})
    inner = win.get("window_rect") or {}
    wr["x"], wr["y"] = wr.get("x", 0) + inner.get("x", 0), wr.get("y", 0) + inner.get("y", 0)
    orect = out.get("rect") or {"x": 0, "y": 0}
    if not (orect["x"] <= wr["x"] < orect["x"] + orect.get("width", 1 << 30)):
        return None
    return to_output_pixels(box, wr, orect, float(out.get("scale") or 1.0))


def _grab():
    import linux_desktop
    if linux_desktop.is_sway():
        return linux_desktop.grab_screen()
    try:
        import mss
        from PIL import Image
        with mss.mss() as s:
            shot = s.grab(s.monitors[1])
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        return None


def _title() -> Optional[str]:
    if sys.platform == "win32":
        try:
            import ctypes
            u = ctypes.windll.user32  # type: ignore[attr-defined]
            h = u.GetForegroundWindow()
            n = u.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            return buf.value or None
        except Exception:
            return None
    try:
        import linux_desktop
        return linux_desktop.active_window_title()
    except Exception:
        return None


def _skip_reason(title: Optional[str]) -> str:
    try:
        import capture_privacy
        return capture_privacy.skip_reason(title)
    except Exception:
        return ""


def _ocr(img) -> list[dict[str, Any]]:
    from guide_cards import _ocr_words
    return _ocr_words(img)


class Screen:
    """Everything Guide Mode reads from the screen, swappable in tests."""

    def __init__(self, grab: Callable[[], Any] = _grab, ocr: Callable[[Any], list[dict[str, Any]]] = _ocr,
                 title: Callable[[], Optional[str]] = _title, atspi: Callable[[str], Optional[dict[str, int]]] = atspi_find,
                 skip: Callable[[Optional[str]], str] = _skip_reason,
                 place: Callable[[dict[str, int]], Optional[dict[str, int]]] = lambda b: {k: b[k] for k in "xywh"}) \
            -> None:
        self.grab, self.ocr, self.title, self.atspi, self.skip, self.place = grab, ocr, title, atspi, skip, place

    def look(self, find: str = "", need_words: bool = False) -> dict[str, Any]:
        """{title, withheld, target, source, words_text, size}"""
        title = self.title() or ""
        out: dict[str, Any] = {"title": title, "withheld": "", "target": None, "source": None, "text": "",
                               "size": None}
        why = self.skip(title)
        if why:
            out["withheld"] = f"Orion doesn't look at {why}. Do this step yourself, then press Done."
            return out
        if find:
            box = self.atspi(find)
            box = self.place(box) if box else None
            if box:
                out["target"], out["source"] = box, "accessibility"
        if (find and out["target"] is None) or need_words:
            img = self.grab()
            if img is not None:
                words = self.ocr(img)
                out["size"] = list(getattr(img, "size", ()) or []) or None
                out["text"] = " ".join(str(w.get("text", "")) for w in words)
                if find and out["target"] is None:
                    box = ocr_find(words, find)
                    if box:
                        out["target"], out["source"] = box, "screen"
        return out


# ---------------------------------------------------------------------- the guide itself
class GuideMode:
    def __init__(self, daemon: Any = None, screen: Optional[Screen] = None,
                 planner: Optional[Callable[[str], Awaitable[Any]]] = None,
                 publish: Optional[Callable[[dict[str, Any]], Awaitable[Any]]] = None,
                 runtime_dir: Optional[Path] = None, poll_s: float = POLL_S,
                 overlay: Optional[Callable[[], Any]] = None) -> None:
        self.d = daemon
        self.screen = screen or Screen(place=_place)
        self._planner = planner
        self._publish = publish
        rd = runtime_dir or (Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get("XDG_RUNTIME_DIR") else None)
        self.state_file = rd / STATE_FILE if rd else None
        self.poll_s = poll_s
        self._overlay = overlay if overlay is not None else start_linux_overlay
        self.guide: Optional[dict[str, Any]] = None
        self.i = 0
        self.view: dict[str, Any] = {}
        self.cards: dict[int, str] = {}
        self.send_to_phone = False
        self._gone = 0
        self._seen_target = False
        self._task: Optional[asyncio.Task] = None

    # --------------------------------------------------------- state
    def active(self) -> bool:
        return self.guide is not None

    def state(self) -> dict[str, Any]:
        if not self.guide:
            return {"active": False}
        step = self.guide["steps"][self.i]
        return {"active": True, "id": self.guide["id"], "title": self.guide["title"], "index": self.i,
                "total": len(self.guide["steps"]), "text": step["text"], "find": step.get("find", ""),
                "auto": bool(step.get("done")), "target": self.view.get("target"), "source": self.view.get("source"),
                "size": self.view.get("size"), "withheld": self.view.get("withheld", ""),
                "phone": self.send_to_phone, "at": time.time()}

    async def _changed(self, why: str = "") -> None:
        st = self.state()
        if why:
            st["why"] = why
        self._write_state(st)
        try:
            if self._publish is not None:
                await self._publish({"event": "GUIDE_STATE", "guide": st})
            elif self.d is not None and getattr(self.d, "bus", None) is not None:
                await self.d.bus.publish({"event": "GUIDE_STATE", "guide": st})
        except Exception:
            log.debug("guide publish failed", exc_info=True)

    def _write_state(self, st: dict[str, Any]) -> None:
        """For the OrionOS ring overlay (os/desktop/orion-guide-ring). Removed when the guide ends."""
        if self.state_file is None:
            return
        try:
            if st.get("active"):
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(st))
                os.replace(tmp, self.state_file)
            elif self.state_file.exists():
                self.state_file.unlink()
        except OSError:
            log.debug("guide state file", exc_info=True)

    # --------------------------------------------------------- start / move / stop
    async def start(self, task: str, send_to_phone: bool = False) -> dict[str, Any]:
        task = " ".join(str(task or "").split())[:200]
        if not task:
            raise ValueError("Tell me what you'd like to do, for example: install Discord.")
        guide = match_guide(task)
        if guide is None:
            steps = clean_plan(await self._plan(task))
            if not steps:
                raise LookupError(f"I don't have a guide for “{task}” yet, and no AI model is set up to make one.")
            guide = {"id": "made", "title": task[:1].upper() + task[1:60], "steps": steps}
        await self.stop("new guide", quiet=True)
        self.guide, self.i, self.send_to_phone, self.cards = guide, 0, bool(send_to_phone), {}
        self._enter_step()
        await self._look()
        await self._changed("started")
        try:
            self._overlay()
        except Exception:
            log.debug("ring overlay didn't start", exc_info=True)
        self._task = asyncio.create_task(self._watch(), name="guide-watch")
        return self.state()

    async def _plan(self, task: str) -> Any:
        if self._planner is not None:
            return await self._planner(task)
        orch = getattr(self.d, "orchestrator", None)
        if orch is None:
            return None
        try:
            text, _ = await orch.frontier_text(PLAN_SYSTEM, f"Task: {task}", json_mode=True, max_tokens=700,
                                               temperature=0.2, timeout=60)
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as exc:
            log.info("guide planner: no model (%s)", exc)
            return None

    def _enter_step(self) -> None:
        self.view, self._gone, self._seen_target = {}, 0, False

    async def next(self, by: str = "button") -> dict[str, Any]:
        if not self.guide:
            return self.state()
        if self.i + 1 >= len(self.guide["steps"]):
            title = self.guide["title"]
            await self.stop("finished", quiet=True)
            await self._changed(f"finished:{title}")
            return {"active": False, "finished": title}
        self.i += 1
        self._enter_step()
        await self._look()
        await self._changed(f"next:{by}")
        return self.state()

    async def back(self) -> dict[str, Any]:
        if self.guide and self.i > 0:
            self.i -= 1
            self._enter_step()
            await self._look()
            await self._changed("back")
        return self.state()

    async def stop(self, why: str = "stopped", quiet: bool = False) -> dict[str, Any]:
        t, self._task = self._task, None
        if t is not None and t is not asyncio.current_task():
            t.cancel()
        was = self.guide is not None
        self.guide, self.view = None, {}
        if was and not quiet:
            await self._changed(why)
        else:
            self._write_state({"active": False})
        return {"active": False}

    # --------------------------------------------------------- watching
    async def _look(self) -> dict[str, Any]:
        step = self.guide["steps"][self.i] if self.guide else {}
        done = step.get("done") or {}
        self.view = await asyncio.to_thread(self.screen.look, step.get("find", ""), "screen" in done)
        if self.view.get("target"):
            self._seen_target, self._gone = True, 0
        if self.send_to_phone and self.i not in self.cards:
            self._phone_card(step)
        return self.view

    def _phone_card(self, step: dict[str, Any]) -> None:
        cards = getattr(self.d, "cards", None)
        if cards is None:
            return
        t = self.view.get("target")
        target = (t["x"] + t["w"] // 2, t["y"] + t["h"] // 2) if t else None
        try:
            self.cards[self.i] = cards.create(step["text"], target=target)["id"]
        except Exception:
            log.debug("phone card failed", exc_info=True)

    def step_done(self) -> bool:
        """Did the user just do the current step? (from the last look)"""
        if not self.guide:
            return False
        step = self.guide["steps"][self.i]
        cid = self.cards.get(self.i)
        if cid is not None:
            try:
                if self.d.cards.get(cid).get("state") != "open":
                    return True       # done (or "Orion does it") on the phone card
            except Exception:
                pass
        done = step.get("done") or {}
        v = self.view
        if not done or v.get("withheld"):
            return False
        if "window" in done and re.search(done["window"], v.get("title") or "", re.I):
            return True
        if "screen" in done and re.search(done["screen"], v.get("text") or "", re.I):
            return True
        if done.get("gone"):
            if not v.get("target") and self._seen_target:
                self._gone += 1
                return self._gone >= GONE_POLLS
        return False

    async def tick(self) -> bool:
        """One check: look again, advance if the step is done, publish if the ring moved. True if it advanced."""
        if not self.guide:
            return False
        before = (self.view.get("target"), self.view.get("withheld"))
        await self._look()
        if self.step_done():
            await self.next("watch")
            return True
        if (self.view.get("target"), self.view.get("withheld")) != before:
            await self._changed("moved")
        return False

    async def _watch(self) -> None:
        while self.guide is not None:
            await asyncio.sleep(self.poll_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("guide watch failed")


def start_linux_overlay() -> bool:
    """OrionOS: start the click-through ring (one per session; it quits when the guide ends)."""
    import linux_desktop
    if not linux_desktop.is_sway():
        return False
    exe = shutil.which("orion-guide-ring") or ("/opt/orion/desktop/orion-guide-ring"
                                               if os.path.exists("/opt/orion/desktop/orion-guide-ring") else "")
    if not exe:
        return False
    try:
        if subprocess.run(["pgrep", "-f", "orion-guide-ring"], capture_output=True, timeout=5).returncode == 0:
            return True
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------- voice / chat
def get(daemon: Any) -> GuideMode:
    g = getattr(daemon, "guide", None)
    if g is None:
        g = GuideMode(daemon)
        daemon.guide = g
    return g


async def chat(daemon: Any, text: str) -> Optional[str]:
    """'Orion, show me how to …', 'next step', 'go back', 'stop the guide'. None if it isn't for Guide Mode."""
    g = get(daemon)
    if g.active():
        if NEXT_RE.match(text):
            st = await g.next("voice")
            return f"All done: {st['finished']}." if st.get("finished") else st["text"]
        if BACK_RE.match(text):
            return (await g.back()).get("text") or "Back."
        if STOP_RE.match(text):
            await g.stop("voice")
            return "Guide stopped."
    m = START_RE.match(text or "")
    if not m:
        return None
    try:
        st = await g.start(m.group("task"))
    except (ValueError, LookupError) as exc:
        return str(exc)
    return f"{st['title']}, {st['total']} steps. Step 1: {st['text']}"


class StartReq(BaseModel):
    task: str
    send_to_phone: bool = False


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.get("/guide")
    async def guide_state() -> dict[str, Any]:
        return get(get_daemon()).state()

    @router.get("/guide/list")
    async def guide_list() -> dict[str, Any]:
        return {"guides": [{"id": g["id"], "title": g["title"], "steps": len(g["steps"])} for g in GUIDES]}

    @router.post("/guide/start")
    async def guide_start(req: StartReq) -> dict[str, Any]:
        try:
            return await get(get_daemon()).start(req.task, req.send_to_phone)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/guide/next")
    async def guide_next() -> dict[str, Any]:
        return await get(get_daemon()).next("button")

    @router.post("/guide/back")
    async def guide_back() -> dict[str, Any]:
        return await get(get_daemon()).back()

    @router.post("/guide/stop")
    async def guide_stop() -> dict[str, Any]:
        return await get(get_daemon()).stop("button")

    return router
