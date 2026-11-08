"""Data for Settings > Orion's permission levels and Settings > Updates (self-running, complete plan step 4).
No GTK here, so it can be tested anywhere. Everything comes from Orion's brain (apps/daemon/self_levels.py,
update_channels.py); when it isn't answering, the plan's defaults are shown read-only.

Changing a level: lowering applies at once; raising asks your phone, or is done in a terminal with the
orion-admin password. Switching the update channel always uses the admin password.
"""
from __future__ import annotations

import os
import shlex
from typing import Any, Optional

API = os.environ.get("ORION_API", "http://127.0.0.1:9120")
HELPER = "/opt/orion/safety/orion-self-helper"
CHANNEL_FILE = "/etc/orion/channel"
CHANNELS = [("stable", "Stable"), ("beta", "Beta"), ("mine", "My builds")]
LEVEL_NAMES = ["Always ask", "Tell me after", "Just do it"]

# The plan's table, for when Orion's brain isn't answering (kind, label, default, max).
DEFAULTS = [("security_update", "Security updates", 3, 3), ("app_update", "App and Flatpak updates", 2, 3),
            ("routine_fix", "Restarting a stuck service, clearing caches", 3, 3),
            ("system_version", "New system version", 1, 1), ("ai_model", "AI model updates", 2, 3),
            ("ui_code", "Orion changing its own screens", 1, 2),
            ("daemon_code", "Orion changing its own brain", 1, 1), ("firmware", "Drivers and firmware", 1, 1)]


def levels(r: Any) -> dict[str, Any]:
    """{"live": bool, "frozen": bool, "rows": [...]} with only the rows Settings shows (not the protected core)."""
    res = r.http("GET", f"{API}/self/levels")
    if isinstance(res, dict) and isinstance(res.get("levels"), list):
        rows = [x for x in res["levels"] if isinstance(x, dict) and x.get("shown") and x.get("kind") != "protected_core"]
        return {"live": True, "frozen": bool(res.get("frozen")), "rows": rows}
    return {"live": False, "frozen": False,
            "rows": [{"kind": k, "label": lab, "level": d, "default": d, "max": m, "raisable": m > 1, "note": "",
                      "streak": 0, "where": "default"} for k, lab, d, m in DEFAULTS]}


def set_level(r: Any, kind: str, level: int) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/self/levels", {"kind": kind, "level": int(level), "by": "Settings"}, timeout=15)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering, so nothing changed."
    return bool(res.get("ok")), str(res.get("message") or "")


def admin_level_command(kind: str, level: int) -> str:
    if not kind.replace("_", "").isalnum() or int(level) not in (1, 2, 3):
        raise ValueError("bad level")
    inner = f"sudo {HELPER} level {kind} {int(level)}"
    return f"su -P - orion-admin -c {shlex.quote(inner)}"


def channel_command(channel: str) -> str:
    if channel not in dict(CHANNELS):
        raise ValueError("bad channel")
    return f"su -P - orion-admin -c {shlex.quote(f'sudo {HELPER} channel {channel}')}"


def updates(r: Any, root: str = "") -> dict[str, Any]:
    res = r.http("GET", f"{API}/self/updates")
    ch = (res or {}).get("channel") if isinstance(res, dict) else None
    if isinstance(ch, dict):
        return {"live": True, "channel": ch.get("channel", "stable"), "vm_test": bool(ch.get("vm_test", True)),
                "offer": ch.get("offer") or None, "firmware": res.get("firmware") or [],
                "security_waiting": int(res.get("security_waiting") or 0)}
    word = (r.read(root + CHANNEL_FILE) or "").strip()
    return {"live": False, "channel": word if word in dict(CHANNELS) else "stable", "vm_test": True, "offer": None,
            "firmware": [], "security_waiting": 0}


def set_vm_test(r: Any, on: bool) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/self/updates/settings", {"vm_test": bool(on)})
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering, so nothing changed."
    return True, "New versions are tested in a VM first (when this computer can run one)." if on else \
        "New versions won't be tried in a VM first; only this computer's own checks run."


def check_now(r: Any) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/self/updates/check", {}, timeout=4 * 3600)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    if res.get("skipped") == "frozen":
        return False, "Orion is frozen, so it won't look for updates. Unfreeze it from your phone first."
    if res.get("error"):
        return False, str(res["error"])
    if not res.get("newer"):
        return True, f"Up to date ({res.get('current') or 'this version'})."
    if res.get("offered"):
        return True, f"OrionOS {res.get('latest')} passed the self-test. Its card is in your queue."
    return True, f"OrionOS {res.get('latest')} didn't pass the self-test, so it isn't offered."


def offer_words(offer: Optional[dict[str, Any]]) -> str:
    if not offer or not offer.get("version"):
        return ""
    if offer.get("passed"):
        vm = "tested in a VM" if offer.get("vm_ran") else "VM run skipped: " + str(offer.get("vm_skipped") or "")
        return f"OrionOS {offer['version']} is waiting for your OK ({vm})."
    return f"OrionOS {offer['version']} was held back: it failed the self-test."
