"""Settings > Backups, the part without GTK (so it can be tested): reads and changes Orion's backup settings
through the daemon's /backup routes (apps/daemon/backup.py), plus the "Try with Craft" switches (/craft).
Keys and the backup password are never read or shown here."""
from __future__ import annotations

import time
from typing import Any, Optional

from .backend import API, Runner


def load(r: Runner) -> Optional[dict[str, Any]]:
    st = r.http("GET", f"{API}/backup/status")
    if not isinstance(st, dict):
        return None
    craft = r.http("GET", f"{API}/craft/status")
    st["try_with_craft"] = (craft.get("try_with_craft") or {}) if isinstance(craft, dict) else {}
    return st


def ago(ts: Optional[float], now: Optional[float] = None) -> str:
    if not ts:
        return "never"
    s = max(0.0, (now or time.time()) - float(ts))
    if s < 3600:
        return "just now" if s < 120 else f"{int(s // 60)} minutes ago"
    if s < 48 * 3600:
        return f"{int(s // 3600)} hours ago"
    return f"{int(s // 86400)} days ago"


def rows(st: dict[str, Any], now: Optional[float] = None) -> list[tuple[str, str, str]]:
    """(title, subtitle, pill kind: '' | 'ok' | 'warn') for the status group."""
    out = []
    last = st.get("last_backup") or {}
    if not st.get("configured"):
        out.append(("Not set up yet", "Choose a folder on a USB drive below. Backups are encrypted.", "warn"))
    else:
        where = ", ".join(r.get("target", "") for r in last.get("results") or []) or "nowhere yet"
        out.append(("Last backup", f"{ago(last.get('at'), now)} ({where})" if last else "No backup yet",
                    "warn" if st.get("stale") else "ok"))
    d = st.get("last_drill") or {}
    if d:
        sub = (f"{ago(d.get('at'), now)}: {d.get('checked', 0)} random files restored and opened"
               + (f" in a {d['where']}" if d.get("where") else ""))
        if not d.get("ok"):
            sub += ". Problem: " + "; ".join(d.get("problems") or [])[:160]
        out.append(("Restore test", sub, "ok" if d.get("ok") else "warn"))
    else:
        out.append(("Restore test", "Once a month Orion restores a few files and checks they open.", ""))
    if not st.get("restic", True):
        out.append(("restic isn't installed", "Install it with: sudo apt install restic", "warn"))
    return out


def what_is_saved(st: dict[str, Any]) -> str:
    labels: list[str] = []
    for s in st.get("sources") or []:
        if s.get("label") and s["label"] not in labels:
            labels.append(s["label"])
    return ", ".join(labels + ["database copies (when DB Time Machine is set up)"])


def _answer(res: Any, ok_msg: str) -> tuple[bool, str]:
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering, so nothing changed."
    if res.get("error"):
        return False, str(res["error"])
    return True, str(res.get("message") or ok_msg)


def set_target(r: Runner, usb: Optional[str] = None, s3: Optional[str] = None) -> tuple[bool, str]:
    body: dict[str, Any] = {}
    if usb is not None:
        body["usb"] = usb.strip()
    if s3 is not None:
        body["s3"] = s3.strip()
    return _answer(r.http("POST", f"{API}/backup/config", body), "Saved.")


def back_up_now(r: Runner) -> tuple[bool, str]:
    return _answer(r.http("POST", f"{API}/backup/now", {}, timeout=6 * 3600), "Backup done.")


def drill_now(r: Runner) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/backup/drill", {}, timeout=3 * 3600)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    if res.get("ok"):
        return True, "Restore test passed."
    return False, "Restore test failed: " + "; ".join((res.get("drill") or {}).get("problems") or [])[:200]


def try_with_craft(r: Runner, kind: str, on: bool) -> tuple[bool, str]:
    return _answer(r.http("POST", f"{API}/craft/try-with-craft", {"kind": kind, "on": on}), "Saved.")
