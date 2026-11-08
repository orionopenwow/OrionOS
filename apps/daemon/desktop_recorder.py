"""Desktop Skill Recorder: records WHAT you click in desktop apps (after-1.0 plan §1, Phase 2).

    input source  ->  DesktopRecorder  ->  steps  ->  desktop_skill.build_draft()  ->  skill card

Input sources (all optional, all imports guarded; the daemon starts without any of them):
  PynputSource   Windows and X11 (pip: pynput). Clicks come with their screen position.
  EvdevSource    Wayland fallback (pip: evdev; the user must be in the `input` group). Reads keyboards and mice
                 from /dev/input. Wayland gives no pointer position to other programs, so a click is identified by
                 the element that has focus right after it (AT-SPI) or by the window only. This replaces the
                 Windows-only coordinate macro recorder on Linux.

Identity for every click/typing comes from desktop_access (AT-SPI / UI Automation): app, window title, role +
name, automation id; the screen position is kept only as a backup.

Typed text is saved as text per field (one "type" step per field, the final value), shortcuts as shortcuts
(Ctrl+S), other keys (Enter, Tab, Escape, Delete) as key presses.

Never recorded: password fields, card / one-time code fields, and anything while a password manager, bank, login
or unlock window is in front (desktop_access.private_reason). Recording pauses, says why, and resumes by itself
when you move on. Text typed while paused is dropped, never kept.

"light" mode (the opt-in "notice repeated tasks" watcher) keeps identity only: no typed text, no positions.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable, Optional

import desktop_access
from desktop_access import private_reason, target_from_element

log = logging.getLogger("orion.desktop_recorder")

MAX_SECONDS = 900
MAX_STEPS = 400
MODS = {"ctrl", "alt", "shift", "cmd", "super"}
NAMED_KEYS = {"enter", "tab", "escape", "delete", "up", "down", "left", "right", "home", "end", "page_up",
              "page_down", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"}
OnState = Callable[[str, str], None]   # (state: recording|paused|resumed|stopped, reason)


class DesktopRecorder:
    def __init__(self, backend: Any = None, *, on_state: Optional[OnState] = None, light: bool = False,
                 on_step: Optional[Callable[[dict[str, Any]], None]] = None,
                 clock: Callable[[], float] = time.time, words: Optional[list[str]] = None) -> None:
        self.backend = backend or desktop_access.pick_backend()
        self.on_state = on_state
        self.on_step = on_step          # light mode: each finished step goes here instead of being kept
        self.light = light
        self.clock = clock
        self.words = words
        self.recording = False
        self.paused_reason = ""
        self.started_at = 0.0
        self.steps: list[dict[str, Any]] = []
        self.skipped = 0
        self._lock = threading.RLock()
        self._mods: set[str] = set()
        self._typed = ""
        self._typed_el: Optional[dict[str, Any]] = None
        self._typed_private = False
        self._win: dict[str, Any] = {}
        self.source: Any = None

    # ------------------------------------------------------------------ control
    def start(self, source: Any = None) -> tuple[bool, str]:
        with self._lock:
            if self.recording:
                return False, "Already recording."
            self.steps, self.skipped, self._typed, self._typed_el, self._mods = [], 0, "", None, set()
            self.paused_reason = ""
            self.started_at = self.clock()
            self.recording = True
            self._win = {}
        self.source = source
        if source is not None:
            try:
                source.start(self)
            except Exception as exc:
                self.recording = False
                return False, f"I can't watch the keyboard and mouse here ({exc})."
        self.check_window()
        self._emit("recording", "")
        return True, "Recording. Do the task, then say “stop watching” or press Stop."

    def stop(self) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            if not self.recording:
                return list(self.steps), self.skipped
            self._flush()
            self.recording = False
        if self.source is not None:
            try:
                self.source.stop()
            except Exception:
                pass
            self.source = None
        self._emit("stopped", "")
        return list(self.steps), self.skipped

    def _emit(self, state: str, reason: str) -> None:
        if self.on_state:
            try:
                self.on_state(state, reason)
            except Exception:
                log.debug("recorder state callback failed", exc_info=True)

    def _add(self, step: dict[str, Any]) -> None:
        step["t"] = self.clock()
        step["surface"] = "desktop"
        if self.light:
            from desktop_skill import light_step
            step = {**light_step(step), "t": step["t"]}
            if self.on_step:
                try:
                    self.on_step(step)
                except Exception:
                    log.debug("activity callback failed", exc_info=True)
            return
        if len(self.steps) < MAX_STEPS:
            self.steps.append(step)
        if self.clock() - self.started_at > MAX_SECONDS:
            threading.Thread(target=self.stop, daemon=True).start()

    # ------------------------------------------------------------------ windows / privacy
    def check_window(self) -> dict[str, Any]:
        """Look at the window in front: pause on private ones, add a 'switch to' step on a change."""
        try:
            win = self.backend.active_window() or {}
        except Exception:
            win = {}
        win = {"app": str(win.get("app") or ""), "title": str(win.get("title") or "")}
        with self._lock:
            if not self.recording:
                return win
            why = private_reason(win, None, self.words)
            if why:
                if self.paused_reason != why:
                    self._drop_typed()
                    self.paused_reason = why
                    self._emit("paused", f"Paused: {why} is open. Nothing is recorded until you leave it.")
                self._win = win
                return win
            if self.paused_reason:
                self.paused_reason = ""
                self._emit("resumed", "")
            if (win["app"], win["title"]) != (self._win.get("app"), self._win.get("title")) and (win["app"] or win["title"]):
                self._flush()
                self._win = win
                self._add({"action": "focus", "app": win["app"], "window": win["title"]})
        return win

    def _drop_typed(self) -> None:
        if self._typed:
            self.skipped += 1
        self._typed, self._typed_el, self._typed_private = "", None, False

    def _flush(self) -> None:
        if self._typed_private:
            self.skipped += 1
            self._typed, self._typed_el, self._typed_private = "", None, False
            return
        if not self._typed:
            return
        step: dict[str, Any] = {"action": "type", "value": self._typed}
        if self._typed_el:
            step["target"] = target_from_element(self._typed_el)
        self._typed, self._typed_el = "", None
        self._add(step)

    # ------------------------------------------------------------------ input events (from any thread)
    def click(self, x: Optional[int], y: Optional[int], button: str = "left") -> None:
        if not self.recording:
            return
        win = self.check_window()
        with self._lock:
            if self.paused_reason:
                return
            self._flush()
            el = None
            try:
                el = self.backend.element_at(int(x), int(y)) if x is not None and y is not None else \
                    self.backend.focused_element()
            except Exception:
                el = None
            if el is None:
                el = desktop_access.make_element(app=win.get("app", ""), window=win.get("title", ""))
            why = private_reason(win, el, self.words)
            if why and why != "a password field":
                self.skipped += 1
                return
            if why:  # clicking into a password box is fine; what you type there is never kept
                return
            step: dict[str, Any] = {"action": "click", "target": target_from_element(el, x, y)}
            if self.light:
                step["target"]["selectors"] = [s for s in step["target"]["selectors"] if s["kind"] != "coords"]
            if button == "right":
                step["button"] = "right"
            self._add(step)

    def key(self, name: Optional[str] = None, char: Optional[str] = None, down: bool = True) -> None:
        """One key event. `char` for printable keys, `name` for the rest (ctrl, enter, backspace...)."""
        if not self.recording:
            return
        name = (name or "").lower() or None
        if name in ("control", "ctrl_l", "ctrl_r"):
            name = "ctrl"
        if name in ("alt_l", "alt_r", "alt_gr"):
            name = "alt"
        if name in ("shift_l", "shift_r"):
            name = "shift"
        if name in ("cmd_l", "cmd_r", "super_l", "super_r", "meta", "win"):
            name = "super"
        if name == "return":
            name = "enter"
        if name == "esc":
            name = "escape"
        if not down:
            if name in MODS:
                self._mods.discard(name)
            return
        if name in MODS:
            self._mods.add(name)
            return
        win = self.check_window()
        with self._lock:
            if self.paused_reason:
                return
            held = self._mods - {"shift"}
            if held:
                self._flush()
                k = (char or name or "").lower()
                if not k:
                    return
                keys = sorted(held) + (["shift"] if "shift" in self._mods else []) + [k]
                from desktop_skill import norm_keys
                self._add({"action": "shortcut", "keys": norm_keys(keys),
                           **({"app": win.get("app", "")} if win.get("app") else {})})
                return
            if char is not None or name == "space":
                ch = " " if name == "space" else str(char)
                el = self._focused(win)
                if self._typed and el is not None and self._typed_el is not None and \
                        _same(el, self._typed_el) is False:
                    self._flush()
                if not self._typed:
                    self._typed_el = el
                    self._typed_private = bool(private_reason(win, el, self.words))
                if self._typed_private:
                    self._typed = "•"   # only remember that something was typed, never what
                    return
                self._typed += ch
                return
            if name == "backspace":
                if self._typed and not self._typed_private:
                    self._typed = self._typed[:-1]
                return
            if name in NAMED_KEYS:
                el = self._focused(win) if name in ("enter", "delete") else None
                if self._typed_el is not None and el is None and name == "enter":
                    el = self._typed_el
                was_private = self._typed_private
                self._flush()
                if was_private:
                    return  # Enter after a password: not recorded either
                step: dict[str, Any] = {"action": "press", "key": name}
                if el is not None and not private_reason(win, el, self.words):
                    step["target"] = target_from_element(el)
                    step["target"]["selectors"] = [s for s in step["target"]["selectors"] if s["kind"] != "coords"]
                self._add(step)

    def _focused(self, win: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            return self.backend.focused_element()
        except Exception:
            return None

    def status(self) -> dict[str, Any]:
        return {"recording": self.recording, "paused": bool(self.paused_reason), "reason": self.paused_reason,
                "steps": len(self.steps), "backend": getattr(self.backend, "name", "none"),
                "app": self._win.get("app", ""), "window": self._win.get("title", "")}


def _same(a: dict[str, Any], b: dict[str, Any]) -> Optional[bool]:
    ka = (a.get("app"), a.get("automation_id") or a.get("name"), a.get("role"))
    kb = (b.get("app"), b.get("automation_id") or b.get("name"), b.get("role"))
    return ka == kb


# ====================================================================================== input sources
class PynputSource:
    """Windows / X11 global mouse + keyboard hooks (optional `pynput`)."""
    name = "pynput"

    def __init__(self) -> None:
        self._listeners: list[Any] = []
        self._win_thread: Optional[threading.Thread] = None
        self._rec: Optional[DesktopRecorder] = None
        self._pick: Optional[Callable[[int, int], None]] = None

    @staticmethod
    def available() -> bool:
        if sys.platform.startswith("linux"):
            import os
            if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
                return False
        try:
            import importlib.util
            return importlib.util.find_spec("pynput") is not None
        except Exception:
            return False

    def start(self, rec: Optional[DesktopRecorder], pick: Optional[Callable[[int, int], None]] = None) -> None:
        from pynput import keyboard, mouse  # type: ignore
        self._rec, self._pick = rec, pick

        def on_click(x: int, y: int, button: Any, pressed: bool) -> None:
            if not pressed:
                return
            if self._pick is not None:
                cb, self._pick = self._pick, None
                cb(int(x), int(y))
                return
            if self._rec is not None:
                self._rec.click(int(x), int(y), "right" if getattr(button, "name", "") == "right" else "left")

        def on_key(key: Any, down: bool) -> None:
            if self._rec is None:
                return
            ch = getattr(key, "char", None)
            if ch is not None and len(ch) == 1 and ord(ch) < 32:
                ch = chr(ord(ch) + 96)    # Ctrl+letter arrives as a control character
            nm = None if ch is not None else (getattr(key, "name", None) or str(key))
            self._rec.key(nm, ch, down)

        self._listeners = [mouse.Listener(on_click=on_click)]
        if rec is not None:
            self._listeners.append(keyboard.Listener(on_press=lambda k: on_key(k, True),
                                                     on_release=lambda k: on_key(k, False)))
            self._win_thread = threading.Thread(target=self._watch, daemon=True)
            self._win_thread.start()
        for li in self._listeners:
            li.start()

    def _watch(self) -> None:
        while self._rec is not None and self._rec.recording:
            self._rec.check_window()
            time.sleep(0.4)

    def stop(self) -> None:
        for li in self._listeners:
            try:
                li.stop()
            except Exception:
                pass
        self._listeners, self._rec, self._pick = [], None, None


# US layout for evdev key codes (KEY_A -> "a"); shifted forms for the number row and punctuation.
_EV_CHARS = {f"KEY_{c.upper()}": c for c in "abcdefghijklmnopqrstuvwxyz"}
_EV_CHARS.update({f"KEY_{d}": d for d in "1234567890"})
_EV_CHARS.update({"KEY_MINUS": "-", "KEY_EQUAL": "=", "KEY_LEFTBRACE": "[", "KEY_RIGHTBRACE": "]",
                  "KEY_SEMICOLON": ";", "KEY_APOSTROPHE": "'", "KEY_GRAVE": "`", "KEY_BACKSLASH": "\\",
                  "KEY_COMMA": ",", "KEY_DOT": ".", "KEY_SLASH": "/"})
_EV_SHIFT = dict(zip("1234567890-=[];'`\\,./", "!@#$%^&*()_+{}:\"~|<>?"))
_EV_NAMES = {"KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl", "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt",
             "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift", "KEY_LEFTMETA": "super", "KEY_RIGHTMETA": "super",
             "KEY_ENTER": "enter", "KEY_KPENTER": "enter", "KEY_TAB": "tab", "KEY_ESC": "escape",
             "KEY_BACKSPACE": "backspace", "KEY_DELETE": "delete", "KEY_SPACE": "space", "KEY_UP": "up",
             "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right", "KEY_HOME": "home", "KEY_END": "end",
             "KEY_PAGEUP": "page_up", "KEY_PAGEDOWN": "page_down"}
_EV_NAMES.update({f"KEY_F{i}": f"f{i}" for i in range(1, 13)})


def evdev_key(code_name: str, shift: bool) -> tuple[Optional[str], Optional[str]]:
    """(name, char) for an evdev key name such as KEY_A / KEY_ENTER (US layout)."""
    if code_name in _EV_NAMES:
        return _EV_NAMES[code_name], None
    ch = _EV_CHARS.get(code_name)
    if ch is None:
        return None, None
    if shift:
        ch = ch.upper() if ch.isalpha() else _EV_SHIFT.get(ch, ch)
    return None, ch


class EvdevSource:
    """Wayland fallback: read keyboards and mice from /dev/input (optional `evdev`, `input` group)."""
    name = "evdev"
    FOCUS_DELAY_S = 0.15

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._rec: Optional[DesktopRecorder] = None
        self._pick: Optional[Callable[[Optional[int], Optional[int]], None]] = None
        self._shift = False

    @staticmethod
    def available() -> bool:
        try:
            import evdev  # type: ignore
            return any(True for _ in evdev.list_devices())
        except Exception:
            return False

    def start(self, rec: Optional[DesktopRecorder], pick: Optional[Callable[[Optional[int], Optional[int]], None]] = None) -> None:
        import evdev  # type: ignore
        self._rec, self._pick = rec, pick
        self._stop.clear()
        devices = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
                caps = d.capabilities().get(evdev.ecodes.EV_KEY, [])
                if evdev.ecodes.KEY_A in caps or evdev.ecodes.BTN_LEFT in caps:
                    devices.append(d)
            except Exception:
                continue
        if not devices:
            raise RuntimeError("no keyboard or mouse can be read (add yourself to the 'input' group)")
        for d in devices:
            t = threading.Thread(target=self._read, args=(d,), daemon=True)
            t.start()
            self._threads.append(t)
        if rec is not None:
            w = threading.Thread(target=self._watch, daemon=True)
            w.start()
            self._threads.append(w)

    def _watch(self) -> None:
        while not self._stop.is_set() and self._rec is not None and self._rec.recording:
            self._rec.check_window()
            time.sleep(0.4)

    def _read(self, dev: Any) -> None:
        import select

        import evdev  # type: ignore
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([dev.fd], [], [], 0.5)
                if not r:
                    continue
                for ev in dev.read():
                    if ev.type != evdev.ecodes.EV_KEY:
                        continue
                    name = evdev.ecodes.KEY.get(ev.code) or evdev.ecodes.BTN.get(ev.code)
                    if isinstance(name, list):
                        name = name[0]
                    self.feed(str(name or ""), int(ev.value))
        except Exception:
            log.debug("evdev reader stopped", exc_info=True)

    def feed(self, code_name: str, value: int) -> None:
        """One key/button event: value 1 = down, 0 = up, 2 = repeat. Public so tests can drive it."""
        if code_name in ("BTN_LEFT", "BTN_RIGHT"):
            if value != 1:
                return
            time.sleep(self.FOCUS_DELAY_S)   # let focus move to what was clicked
            if self._pick is not None:
                cb, self._pick = self._pick, None
                cb(None, None)
                return
            if self._rec is not None:
                self._rec.click(None, None, "right" if code_name == "BTN_RIGHT" else "left")
            return
        if code_name in ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"):
            self._shift = value != 0
        name, ch = evdev_key(code_name, self._shift)
        if (name or ch) and self._rec is not None and value in (0, 1, 2):
            self._rec.key(name, ch, value != 0)

    def stop(self) -> None:
        self._stop.set()
        self._rec, self._pick = None, None


def pick_input() -> Any:
    """The input source for this machine, or None (then recording isn't possible here)."""
    if PynputSource.available():
        return PynputSource()
    if sys.platform.startswith("linux") and EvdevSource.available():
        return EvdevSource()
    return None


def input_status() -> dict[str, Any]:
    src = pick_input()
    return {"input": getattr(src, "name", None), **desktop_access.status()}
