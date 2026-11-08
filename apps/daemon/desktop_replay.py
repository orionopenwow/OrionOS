"""Desktop skill replay: one step at a time through the accessibility tree and os_automation.

Plugs into skill_replay.SkillRunner (the same engine as browser skills), so the same rules hold:
  * a skill whose signature doesn't match never runs;
  * every step that sends, buys, deletes, posts or submits waits for the user's OK, every time, and a step that
    only turns out to be risky at run time (the live element is called "Send") is held for the OK then.

Finding the target of a step (self-healing), in this order:
  1. the recorded selectors: automation id, role + name, visible name (accessibility tree, retried for a few
     seconds while the window settles);
  2. a fuzzy match on the element names in that window;
  3. a visual search: OCR of the screen with Tesseract (optional; never on a private window), used only when there
     is exactly one confident match;
  4. otherwise the run stops and asks the user to point at it (the next click is captured) and the fix is saved
     into the skill (skill_routes.SkillHub._save_fix).
The screen position is used directly only when there is no accessibility on this machine at all (it's the
backup), and then the step is marked as found "by position".

Acting: the accessibility action (Invoke / AT-SPI "click", set the text) when the element offers it, else a
mouse click at the element's centre / typing through os_automation (pyautogui on Windows/X11, Sway seat + wtype on
OrionOS) and, on other Wayland desktops, ydotool.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Optional

import desktop_access
from desktop_access import center, ratio, similar, target_from_element
from desktop_skill import TEXT_ROLES, classify_risk, norm_keys

log = logging.getLogger("orion.desktop_replay")

FIND_TIMEOUT_S = 5.0
FIND_POLL_S = 0.4
FUZZY_AT_LEAST = 0.82
OCR_AT_LEAST = 0.85
OCR_MIN_CONF = 60.0


# ====================================================================================== OCR (optional)
def match_ocr(data: dict[str, list[Any]], text: str, *, at_least: float = OCR_AT_LEAST,
              min_conf: float = OCR_MIN_CONF) -> list[dict[str, Any]]:
    """Find `text` in pytesseract.image_to_data(..., output_type=DICT) output. Returns boxes {x,y,w,h,score}
    for every run of words on one line that matches. Pure function (tests feed it fake data)."""
    want = re.sub(r"\s+", " ", (text or "")).strip().lower()
    if not want:
        return []
    n_words = len(want.split())
    rows: dict[tuple, list[int]] = {}
    for i, word in enumerate(data.get("text") or []):
        w = str(word or "").strip()
        try:
            conf = float((data.get("conf") or [])[i])
        except (TypeError, ValueError, IndexError):
            conf = -1.0
        if not w or conf < min_conf:
            continue
        key = tuple((data.get(k) or [0] * (i + 1))[i] for k in ("block_num", "par_num", "line_num"))
        rows.setdefault(key, []).append(i)
    hits = []
    for idxs in rows.values():
        for start in range(len(idxs)):
            for span in {n_words, n_words - 1, n_words + 1}:
                if span < 1 or start + span > len(idxs):
                    continue
                pick = idxs[start:start + span]
                phrase = " ".join(str(data["text"][j]).strip() for j in pick).lower().strip(":.…")
                score = ratio(phrase, want)
                if score >= at_least:
                    x0 = min(int(data["left"][j]) for j in pick)
                    y0 = min(int(data["top"][j]) for j in pick)
                    x1 = max(int(data["left"][j]) + int(data["width"][j]) for j in pick)
                    y1 = max(int(data["top"][j]) + int(data["height"][j]) for j in pick)
                    hits.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "score": round(score, 3)})
    # one hit per place (overlapping spans collapse to the best one)
    hits.sort(key=lambda h: -h["score"])
    out: list[dict[str, Any]] = []
    for h in hits:
        if not any(abs(h["x"] - o["x"]) < 12 and abs(h["y"] - o["y"]) < 8 for o in out):
            out.append(h)
    return out


class OcrFinder:
    """Visual search with Tesseract, reusing the screen-reading code's setup (capture_engine). Optional."""

    def __init__(self) -> None:
        self._ok: Optional[bool] = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                import capture_engine
                self._ok = bool(capture_engine.configure_tesseract())
            except Exception:
                self._ok = False
        return bool(self._ok)

    def _grab(self) -> Any:
        try:
            import linux_desktop
            if linux_desktop.is_wayland():
                return linux_desktop.grab_screen()
        except Exception:
            pass
        import mss  # type: ignore
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def data(self) -> Optional[dict[str, list[Any]]]:
        import pytesseract  # type: ignore
        img = self._grab()
        if img is None:
            return None
        return pytesseract.image_to_data(img.convert("L"), output_type=pytesseract.Output.DICT, timeout=30)

    async def find(self, text: str, window_title: str = "") -> list[dict[str, Any]]:
        if not text or not self.available():
            return []
        try:
            import capture_privacy
            if capture_privacy.skip_reason(window_title):
                return []          # never read a private screen
        except Exception:
            return []
        try:
            data = await asyncio.to_thread(self.data)
        except Exception:
            log.debug("OCR failed", exc_info=True)
            return []
        return match_ocr(data or {}, text)


# ====================================================================================== input
class YdotoolInput:
    """Wayland desktops other than Sway: ydotool (needs ydotoold running)."""

    @staticmethod
    def available() -> bool:
        return sys.platform.startswith("linux") and shutil.which("ydotool") is not None

    def _run(self, *args: str) -> bool:
        try:
            return subprocess.run(["ydotool", *args], capture_output=True, timeout=20).returncode == 0
        except Exception:
            return False

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1) -> bool:
        if x is not None and y is not None and not self._run("mousemove", "--absolute", "-x", str(x), "-y", str(y)):
            return False
        code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
        return all(self._run("click", code) for _ in range(max(1, clicks)))

    def type_text(self, text: str, interval: float = 0.02) -> bool:
        return self._run("type", "--", text)

    def hotkey(self, *keys: str) -> bool:
        return self._run("key", "+".join(keys))

    def press_key(self, key: str) -> bool:
        return self._run("key", key)


def pick_input() -> Any:
    try:
        from os_automation import desktop_automation
        if desktop_automation.is_available:
            return desktop_automation
    except Exception:
        log.debug("os_automation unavailable", exc_info=True)
    if YdotoolInput.available():
        return YdotoolInput()
    return None


# ====================================================================================== executor
class DesktopExecutor:
    def __init__(self, backend: Any = None, *, automation: Any = None, ocr: Any = None,
                 launcher: Optional[Callable[[str], bool]] = None, sleep: Callable[[float], Any] = asyncio.sleep,
                 find_timeout: float = FIND_TIMEOUT_S, poll: float = FIND_POLL_S) -> None:
        self.backend = backend or desktop_access.pick_backend()
        self.automation = automation if automation is not None else pick_input()
        self.ocr = ocr if ocr is not None else OcrFinder()
        self.launcher = launcher or launch_app
        self.sleep = sleep
        self.find_timeout = find_timeout
        self.poll = poll

    # ------------------------------------------------------------------ finding
    def _by_selectors(self, t: dict[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
        els = self.backend.elements(t.get("app", ""), t.get("window", ""))
        if not els and t.get("window"):
            els = self.backend.elements(t.get("app", ""), "")
        for s in t.get("selectors") or []:
            k = s.get("kind")
            if k == "automation_id" and s.get("value"):
                hit = [e for e in els if e.get("automation_id") == s["value"]]
            elif k == "role" and s.get("name"):
                hit = [e for e in els if e.get("role") == s.get("role") and e.get("name") == s["name"]]
            elif k == "text" and s.get("value"):
                hit = [e for e in els if e.get("name") == s["value"]]
            else:
                continue
            if hit:
                return hit[0], k
        name = t.get("name") or ""
        if name and els:
            scored = sorted(((ratio(e.get("name", ""), name) + (0.05 if e.get("role") == t.get("role") else 0), i)
                             for i, e in enumerate(els)), reverse=True)
            if scored and scored[0][0] >= FUZZY_AT_LEAST:
                # only when the best is clearly better than the runner-up
                if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.04:
                    return els[scored[0][1]], "fuzzy"
        return None, ""

    async def locate(self, step: dict[str, Any], *, use_ocr: bool = True) -> tuple[Optional[dict[str, Any]], str]:
        t = step.get("target") or {}
        if not t:
            return None, "none"
        waited = 0.0
        identity = getattr(self.backend, "identity", False)
        if identity:
            while True:
                el, how = self._by_selectors(t)
                if el is not None:
                    return el, how
                if waited >= self.find_timeout:
                    break
                await self.sleep(self.poll)
                waited += self.poll
        label = t.get("name") or next((s.get("value") for s in t.get("selectors") or [] if s.get("kind") == "ocr"), "")
        if use_ocr and label and self.ocr is not None:
            hits = await self.ocr.find(label, t.get("window", ""))
            if len(hits) == 1:
                h = hits[0]
                return desktop_access.make_element(app=t.get("app", ""), window=t.get("window", ""),
                                                   role=t.get("role", ""), name=label,
                                                   rect=(h["x"], h["y"], h["w"], h["h"])), "ocr"
        if not identity:
            xy = next(((c.get("x"), c.get("y")) for c in t.get("selectors") or [] if c.get("kind") == "coords"), None)
            if xy and xy[0] is not None:
                return {**{k: t.get(k, "") for k in ("app", "window", "role", "name")}, "x": int(xy[0]),
                        "y": int(xy[1])}, "coords"
        return None, ""

    async def find_window(self, app: str, title: str) -> Optional[dict[str, Any]]:
        wins = self.backend.windows() or []
        for w in wins:
            if title and w.get("title") == title:
                return w
        best = None
        for w in wins:
            if title and similar(w.get("title", ""), title, 0.6):
                return w
            if app and str(w.get("app", "")).lower().endswith(app.lower()) and best is None:
                best = w
        return best

    # ------------------------------------------------------------------ preview
    async def preview(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Where each step's target is on screen right now (for the highlights). OCR is skipped here."""
        out = []
        for i, s in enumerate(steps):
            if not s.get("target"):
                out.append({"index": i, "found": None})
                continue
            saved, self.find_timeout = self.find_timeout, 0.0
            try:
                el, how = await self.locate(s, use_ocr=False)
            finally:
                self.find_timeout = saved
            out.append({"index": i, "found": el is not None, "how": how,
                        "rect": (el or {}).get("rect") or ([el["x"] - 20, el["y"] - 12, 40, 24]
                                                         if el and el.get("x") is not None else None)})
        return out

    # ------------------------------------------------------------------ acting
    def _click(self, el: dict[str, Any], step: dict[str, Any]) -> bool:
        plain = step.get("button") != "right" and not step.get("double")
        if plain and el.get("_ref") is not None and self.backend.do(el, "click"):
            return True
        pos = center(el)
        if pos is None or self.automation is None:
            return False
        return bool(self.automation.click(x=pos[0], y=pos[1], button=step.get("button") or "left",
                                          clicks=2 if step.get("double") else 1))

    async def exec_step(self, run: Any, i: int, step: dict[str, Any], approved: bool,
                        on_found: Optional[Callable[[dict[str, Any], str], Any]] = None) -> dict[str, Any]:
        a = step.get("action")
        try:
            if a == "wait":
                await self.sleep(min(float(step.get("seconds") or 1), 30.0))
                return {"ok": True}
            if a == "open_url":
                from os_automation import open_url
                return {"ok": bool(open_url(str(step.get("url", ""))))}
            if a == "focus":
                win = await self.find_window(str(step.get("app") or ""), str(step.get("window") or ""))
                if win is None:
                    if not step.get("app") or not self.launcher(str(step["app"])):
                        return {"ok": False, "error": f"{step.get('app') or 'that app'} isn't open and I can't start it"}
                    waited = 0.0
                    while win is None and waited < self.find_timeout * 3:
                        await self.sleep(self.poll)
                        waited += self.poll
                        win = await self.find_window(str(step.get("app") or ""), str(step.get("window") or ""))
                    if win is None:
                        return {"ok": False, "error": f"{step['app']} didn't open a window"}
                self.backend.activate(win)
                return {"ok": True, "matched": "window"}
            if a == "shortcut":
                if self.automation is None:
                    return {"ok": False, "error": "keyboard control isn't available here"}
                return {"ok": bool(self.automation.hotkey(*norm_keys(step.get("keys") or [])))}

            el, how = (None, "none")
            if step.get("target"):
                el, how = await self.locate(step)
                if el is None and a == "click":
                    return {"ok": False, "need_pick": True, "error": "target not found"}
                if el is None and a == "type" and step["target"].get("name"):
                    return {"ok": False, "need_pick": True, "error": "field not found"}
                if el is not None and on_found is not None:
                    try:
                        await on_found(el, how)
                    except Exception:
                        pass
            if el is not None and not approved:
                live = {**step, "target": {**(step.get("target") or {}), "name": el.get("name", ""),
                                           "role": el.get("role") or (step.get("target") or {}).get("role", "")}}
                reason = classify_risk(live)
                if reason:
                    return {"ok": False, "needs_approval": True, "reason": reason}
            if a == "click":
                ok = self._click(el or {}, step)
            elif a == "type":
                value = str(step.get("value") if step.get("value") is not None else "")
                if el is not None and (el.get("password") or el.get("role") == "password"):
                    return {"ok": False, "error": "that's a password field; Orion never types into those"}
                ok = False
                if el is not None and el.get("_ref") is not None and el.get("role") in TEXT_ROLES:
                    ok = self.backend.set_text(el, value)
                if not ok:
                    if el is not None and not self.backend.do(el, "focus"):
                        self._click(el, {"action": "click"})
                    ok = bool(self.automation and self.automation.type_text(value))
            elif a == "press":
                if el is not None:
                    self.backend.do(el, "focus")
                ok = bool(self.automation and self.automation.press_key(str(step.get("key") or "")))
            else:
                return {"ok": False, "error": f"unknown step {a}"}
            res: dict[str, Any] = {"ok": bool(ok), "matched": how}
            if not ok:
                res["error"] = "the action didn't go through"
            first = next((s.get("kind") for s in (step.get("target") or {}).get("selectors") or []
                          if s.get("kind") != "coords"), None)
            if ok and el is not None and how in ("fuzzy", "ocr", "automation_id", "role", "text") and how != first:
                res["healed"] = True
                res["target"] = healed_target(el, how)
            return res
        except Exception as exc:
            log.exception("desktop step failed")
            return {"ok": False, "error": str(exc)[:200]}


def healed_target(el: dict[str, Any], how: str) -> dict[str, Any]:
    t = target_from_element(el)
    if how == "ocr" and el.get("name"):
        t["selectors"].insert(0, {"kind": "ocr", "value": el["name"]})
        t["selectors"] = [s for s in t["selectors"] if s["kind"] in ("ocr", "coords")]
    return t


def launch_app(app: str) -> bool:
    """Start an app by its launcher name (no shell)."""
    if not re.fullmatch(r"[\w.\- ]{1,80}", app or ""):
        return False
    try:
        if sys.platform == "win32":
            import os
            os.startfile(app)  # type: ignore[attr-defined]
            return True
        exe = shutil.which(app) or shutil.which(app.lower()) or shutil.which(app.split(".")[-1].lower())
        if exe is None and shutil.which("gtk-launch"):
            exe = shutil.which("gtk-launch")
            subprocess.Popen([exe, app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        if exe is None:
            return False
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False
