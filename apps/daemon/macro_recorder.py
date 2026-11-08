"""Watch-and-Learn Macro Recorder (Feature Bible D1).

"Orion, watch what I do next" starts recording; "stop watching" (or the HUD button) ends it.
While recording, Orion captures mouse clicks, typed text, special keys / hotkeys and window
switches (with the pauses between them), then abstracts them into a replayable workflow in the
same format as screen_workflow, and asks which voice trigger to bind it to.

Privacy: recording only happens between your explicit start/stop, is shown by a red indicator in
the HUD, auto-stops after MAX_SECONDS, and never records while a password-like window title is
focused. Requires the optional `pynput` package (npm run setup:daemon:full).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("orion.macro")

MAX_SECONDS = 600
MIN_WAIT = 0.8  # pauses shorter than this are not recorded as explicit waits
# Also used by capture_privacy (screen reading) and desktop_access (the desktop Skill Recorder).
SENSITIVE_TITLE = re.compile(
    r"(password|passphrase|sign ?in|log ?in|1password|bitwarden|keepass|bank|credential|unlock|authenticat|"
    r"two[- ]factor|2fa|verification code|one[- ]time (code|password)|keyring|polkit|pinentry)", re.I)


class MacroRecorder:
    def __init__(self, window_title_fn: Callable[[], Optional[str]],
                 on_autostop: Optional[Callable[[list[dict[str, Any]]], None]] = None) -> None:
        self.window_title_fn = window_title_fn
        self.on_autostop = on_autostop
        self._autostopping = False
        self.recording = False
        self.started_at = 0.0
        self.events: list[dict[str, Any]] = []
        self._listeners: list[Any] = []
        self._lock = threading.Lock()
        self._typed = ""
        self._last_t = 0.0
        self._last_title: Optional[str] = None
        self._mods: set[str] = set()
        self._title_thread: Optional[threading.Thread] = None
        self.available = self._check()

    @staticmethod
    def _check() -> bool:
        import importlib.util
        try:
            return importlib.util.find_spec("pynput") is not None
        except Exception:
            return False

    # ------------------------------------------------------------ control
    def start(self) -> tuple[bool, str]:
        if self.recording:
            return False, "Already watching."
        if not self.available:
            return False, "Macro recording needs the optional 'pynput' package (npm run setup:daemon:full)."
        from pynput import keyboard, mouse

        with self._lock:
            self.events = []
            self._typed = ""
            self._mods = set()
            self.started_at = self._last_t = time.time()
            self._autostopping = False
            self._last_title = self.window_title_fn()
            self.recording = True
        self._listeners = [
            mouse.Listener(on_click=self._on_click),
            keyboard.Listener(on_press=self._on_press, on_release=self._on_release),
        ]
        for listener in self._listeners:
            listener.start()
        self._title_thread = threading.Thread(target=self._watch_titles, daemon=True)
        self._title_thread.start()
        return True, "Watching. Say “stop watching” when you're done."

    def stop(self) -> list[dict[str, Any]]:
        if not self.recording:
            return []
        self.recording = False
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self._listeners = []
        with self._lock:
            self._flush_typed()
            steps = self._to_steps()
        return steps

    def _auto_stop(self) -> None:
        """Time limit reached: keep what was recorded and hand it to the daemon (it asks for a trigger)."""
        steps = self.stop()
        if self.on_autostop:
            try:
                self.on_autostop(steps)
            except Exception:
                log.exception("macro auto-stop callback failed")

    # ------------------------------------------------------------ capture
    def _sensitive(self) -> bool:
        return bool(self._last_title and SENSITIVE_TITLE.search(self._last_title))

    def _gap(self) -> None:
        now = time.time()
        gap = now - self._last_t
        if gap >= MIN_WAIT and self.events:
            self.events.append({"action": "wait", "seconds": round(min(gap, 5.0), 1)})
        self._last_t = now
        if now - self.started_at > MAX_SECONDS and not self._autostopping:
            self._autostopping = True
            threading.Thread(target=self._auto_stop, daemon=True).start()

    def _flush_typed(self) -> None:
        if self._typed:
            self.events.append({"action": "type", "text": self._typed})
            self._typed = ""

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if not self.recording or not pressed:
            return
        with self._lock:
            self._flush_typed()
            self._gap()
            name = getattr(button, "name", "left")
            self.events.append({"action": "right_click" if name == "right" else "click", "x": int(x), "y": int(y)})

    def _key_name(self, key: Any) -> tuple[Optional[str], Optional[str]]:
        char = getattr(key, "char", None)
        if char is not None:
            if len(char) == 1 and ord(char) < 32:  # Ctrl+letter arrives as a control character
                char = chr(ord(char) + 96)
            return char, None
        name = getattr(key, "name", None) or str(key)
        return None, re.sub(r"_(l|r|gr)$", "", name)

    def _on_press(self, key: Any) -> None:
        if not self.recording:
            return
        char, name = self._key_name(key)
        with self._lock:
            if self._sensitive():
                return
            if name in ("ctrl", "alt", "shift", "cmd"):
                self._mods.add(name)
                return
            if self._mods - {"shift"}:
                self._flush_typed()
                self._gap()
                keys = sorted(self._mods - {"shift"}) + (["shift"] if "shift" in self._mods else [])
                self.events.append({"action": "hotkey", "keys": keys + [char.lower() if char else name]})
                return
            if char is not None:
                if not self._typed:
                    self._gap()
                self._typed += char
                self._last_t = time.time()
                return
            if name == "space":
                self._typed += " "
                return
            if name == "backspace" and self._typed:
                self._typed = self._typed[:-1]
                return
            self._flush_typed()
            self._gap()
            self.events.append({"action": "press", "key": name})

    def _on_release(self, key: Any) -> None:
        _, name = self._key_name(key)
        if name in self._mods:
            self._mods.discard(name)

    def _watch_titles(self) -> None:
        while self.recording:
            title = self.window_title_fn()
            if title and title != self._last_title:
                with self._lock:
                    self._flush_typed()
                    self._gap()
                    self.events.append({"action": "note", "text": f"Switched to: {title[:80]}"})
                    self._last_title = title
            time.sleep(0.5)

    def _to_steps(self) -> list[dict[str, Any]]:
        """Collapse noise: merge consecutive waits, drop trailing waits, keep notes as context only."""
        steps: list[dict[str, Any]] = []
        for e in self.events:
            if e["action"] == "wait" and steps and steps[-1]["action"] == "wait":
                steps[-1]["seconds"] = round(min(steps[-1]["seconds"] + e["seconds"], 8.0), 1)
                continue
            steps.append(e)
        while steps and steps[-1]["action"] in ("wait", "note"):
            steps.pop()
        return steps


def replayable(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Steps the automation engine executes (notes are descriptive only)."""
    return [s for s in steps if s.get("action") != "note"]


def describe(steps: list[dict[str, Any]]) -> str:
    parts = []
    for s in steps:
        a = s.get("action")
        if a == "note":
            parts.append(s["text"])
        elif a == "type":
            parts.append(f"type “{s['text'][:30]}”")
        elif a == "hotkey":
            parts.append("+".join(s["keys"]))
        elif a == "press":
            parts.append(s["key"])
        elif a in ("click", "right_click"):
            parts.append(f"{a.replace('_', ' ')} ({s['x']},{s['y']})")
    return " → ".join(parts)[:400]
