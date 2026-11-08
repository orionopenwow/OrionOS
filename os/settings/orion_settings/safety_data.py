"""Data for the Snapshots and Health pages (safety_pages.py). No GTK here, so it can be tested anywhere.

Everything comes from Orion's brain (the daemon) on this computer; when it isn't answering, snapshots still come
from the summary orion-snapshot keeps in /var/lib/orion/snapshots.json.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

API = os.environ.get("ORION_API", "http://127.0.0.1:9120")
INDEX = "/var/lib/orion/snapshots.json"

BACKEND_WORDS = {
    "btrfs": "Snapshots of the system on this drive",
    "vault": "Copies of your files and settings on the vault",
    "none": "Snapshots aren't set up on this computer",
}
STATUS_ICON = {"ok": "emblem-ok-symbolic", "warn": "dialog-warning-symbolic", "fail": "dialog-error-symbolic"}


def snapshots(r: Any, root: str = "") -> dict[str, Any]:
    st = r.http("GET", f"{API}/snapshots")
    if not isinstance(st, dict):
        try:
            st = json.loads(r.read(root + INDEX) or "")
        except ValueError:
            st = None
    if not isinstance(st, dict):
        st = {"backend": "none", "snapshots": [],
              "words": "Snapshots aren't set up here, or Orion's brain isn't answering."}
    snaps = [s for s in st.get("snapshots") or [] if isinstance(s, dict)]
    snaps.sort(key=lambda s: str(s.get("date", "")), reverse=True)
    frz = r.http("GET", f"{API}/freeze")
    return {"backend": st.get("backend", "none"), "title": BACKEND_WORDS.get(st.get("backend", "none"), ""),
            "words": st.get("words", ""), "space": (st.get("space") or {}).get("words", ""),
            "snapshots": snaps[:20], "count": len(snaps), "undo_to": st.get("undo_to"),
            "frozen": bool(isinstance(frz, dict) and (frz.get("freeze") or {}).get("frozen"))}


def changes(r: Any, limit: int = 8) -> Optional[list[dict[str, Any]]]:
    res = r.http("GET", f"{API}/changes?limit={int(limit)}")
    if not isinstance(res, dict):
        return None
    return [c for c in res.get("changes") or [] if isinstance(c, dict)]


def health(r: Any) -> Optional[dict[str, Any]]:
    res = r.http("GET", f"{API}/health/status?refresh=1", timeout=15)
    if not isinstance(res, dict) or not isinstance(res.get("checks"), list):
        return None
    order = {"fail": 0, "warn": 1, "ok": 2}
    res["checks"] = sorted(res["checks"], key=lambda c: order.get(c.get("status"), 3))
    return res


def take(r: Any, why: str = "taken in Settings") -> tuple[bool, str]:
    res = r.http("POST", f"{API}/snapshots", {"why": why}, timeout=900)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering, so no snapshot was taken."
    return bool(res.get("ok")), str(res.get("message") or ("Snapshot taken." if res.get("ok") else "It didn't work."))


def freeze(r: Any) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/freeze", {"by": "Settings"})
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    return True, "Frozen. Unfreeze it from your phone."


def ask_unfreeze(r: Any) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/freeze/unfreeze", {})
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    return bool(res.get("ok")), str(res.get("message") or "")


def undo_change(r: Any, cid: str) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/changes/{cid}/undo", {})
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    return bool(res.get("ok")), str(res.get("message") or "")


def restore_command(sid: str = "", path: str = "") -> str:
    """The terminal command behind the Restore / Undo buttons: always the orion-admin password."""
    import shlex
    if not sid:
        return "orion-ctl undo"
    return "orion-ctl undo " + shlex.quote(sid) + (f" --path {shlex.quote(path)}" if path else "")
