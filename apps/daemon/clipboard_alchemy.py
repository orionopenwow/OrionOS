"""Clipboard & Snippet Alchemy (Feature Bible D1).

Watches the clipboard locally (Windows clipboard sequence number — no polling of contents unless it
changed). When you copy something Orion can transform, a pill appears in the HUD:

  JSON payload  -> [Convert to TypeScript Interface] [Save as Reusable Snippet]
  stack trace   -> [Diagnose Stack Trace] [Save as Reusable Snippet]
  SQL query     -> [Explain / Optimise SQL] [Save as Reusable Snippet]
  code block    -> [Explain Code] [Save as Reusable Snippet]

Clipboard text is never stored or sent anywhere unless you click a pill. Anything that looks like a
password, token or key is ignored entirely. Disable with CLIPBOARD_WATCH=false.
"""
from __future__ import annotations

import ctypes
import json
import logging
import re
import sys
import threading
import time
import uuid
from typing import Any, Callable, Optional

from harvester import looks_secret

log = logging.getLogger("orion.clipboard")

STACK_RE = re.compile(r"(Traceback \(most recent call last\)|^\s+at .+\(.+:\d+:\d+\)|Exception in thread|"
                      r"^\s*at [\w$.<>]+\s*\(|panicked at|Error: .+\n\s+at |^\w+(Error|Exception): .+$)", re.M)
SQL_RE = re.compile(r"^\s*(select|insert|update|delete|create|alter|with)\b[\s\S]*\b(from|into|table|set|values|as)\b", re.I)
CODE_RE = re.compile(r"(\bdef \w+\(|\bfunction \w*\(|=>\s*\{|\bclass \w+|\bconst \w+ =|\bimport .+ from|#include|\bfn \w+\()")

ACTIONS = {
    "json": [("to_typescript", "Convert to TypeScript Interface"), ("save_snippet", "Save as Reusable Snippet")],
    "stack_trace": [("diagnose", "Diagnose Stack Trace"), ("save_snippet", "Save as Reusable Snippet")],
    "sql": [("explain_sql", "Explain / Optimise SQL"), ("save_snippet", "Save as Reusable Snippet")],
    "code": [("explain_code", "Explain Code"), ("save_snippet", "Save as Reusable Snippet")],
}

INSTRUCTIONS = {
    "to_typescript": "Convert this JSON payload into precise TypeScript interfaces (nested types, optional fields "
                     "where values are null). Return a code_suggestion with language typescript.",
    "diagnose": "Diagnose this stack trace against the user's projects in memory. Name the root cause, the exact file "
                "and line to look at, and give a code_suggestion with the fix.",
    "explain_sql": "Explain what this SQL does in two sentences, point out performance or correctness problems, and "
                   "return an optimised version as a code_suggestion with language sql.",
    "explain_code": "Explain what this code does in a few sentences and point out any bugs. Return improvements as a "
                    "code_suggestion if there are any.",
}


def classify(text: str) -> Optional[str]:
    t = text.strip()
    if len(t) < 30 or len(t) > 60_000 or looks_secret(t[:2000]):
        return None
    if t[0] in "{[":
        try:
            json.loads(t)
            return "json"
        except ValueError:
            pass
    if STACK_RE.search(t):
        return "stack_trace"
    if SQL_RE.search(t) and len(t) < 20_000:
        return "sql"
    if "\n" in t and CODE_RE.search(t):
        return "code"
    return None


def read_clipboard_windows() -> tuple[int, Optional[str]]:
    """(sequence number, text). Text is only read when the sequence number changes."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    seq = int(user32.GetClipboardSequenceNumber())
    return seq, None


def _windows_text() -> Optional[str]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class ClipboardWatcher(threading.Thread):
    def __init__(self, on_suggestion: Callable[[dict[str, Any]], None], interval: float = 1.0) -> None:
        super().__init__(name="orion-clipboard", daemon=True)
        self.on_suggestion = on_suggestion
        self.interval = interval
        self._halt = threading.Event()
        import linux_desktop
        self._linux = sys.platform != "win32" and linux_desktop.clipboard_available()
        self.enabled = sys.platform == "win32" or self._linux
        self.pending: dict[str, dict[str, Any]] = {}  # id -> {kind, text}

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        if not self.enabled:
            log.info("Clipboard alchemy needs Windows or a Wayland session with wl-clipboard")
            return
        if self._linux:
            self._run_linux()
            return
        last_seq: Optional[int] = None
        while not self._halt.wait(self.interval):
            try:
                seq, _ = read_clipboard_windows()
                if seq == last_seq:
                    continue
                first = last_seq is None
                last_seq = seq
                if first:
                    continue  # don't react to whatever was on the clipboard before Orion started
                text = _windows_text()
                if text:
                    self.consider(text)
            except Exception:
                log.debug("clipboard read failed", exc_info=True)

    def _run_linux(self) -> None:
        """Wayland has no clipboard sequence number; poll wl-paste and react to changes."""
        import hashlib
        import linux_desktop

        last: Optional[str] = None
        first = True
        while not self._halt.wait(self.interval):
            try:
                text = linux_desktop.read_clipboard()
                digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest() if text else None
                if digest == last:
                    continue
                last = digest
                if first:
                    first = False
                    continue  # ignore whatever was on the clipboard before Orion started
                if text:
                    self.consider(text)
            except Exception:
                log.debug("clipboard read failed", exc_info=True)

    def consider(self, text: str) -> Optional[dict[str, Any]]:
        kind = classify(text)
        if not kind:
            return None
        sid = f"clip-{uuid.uuid4().hex[:8]}"
        self.pending[sid] = {"kind": kind, "text": text, "at": time.time()}
        if len(self.pending) > 10:
            self.pending.pop(next(iter(self.pending)))
        suggestion = {
            "event": "CLIPBOARD_SUGGESTION",
            "id": sid,
            "kind": kind,
            "preview": text.strip()[:160],
            "actions": [{"id": a, "label": label} for a, label in ACTIONS[kind]],
        }
        self.on_suggestion(suggestion)
        return suggestion

    def take(self, sid: str) -> Optional[dict[str, Any]]:
        return self.pending.pop(sid, None)
