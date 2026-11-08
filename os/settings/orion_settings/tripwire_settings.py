"""Settings data for Tripwires (docs/ORION_TOOLKIT_PLAN.md, tool 22). No GTK here, so it can be tested anywhere.

The daemon side is apps/daemon/tripwires.py: GET /tripwires, POST /tripwires/plant (asks your phone first),
POST /tripwires/remove, POST /tripwires/config.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .backend import API

KIND_NAMES = {
    "passwords": "Old passwords file",
    "env_backup": "Fake database login (.env.backup)",
    "cloud_key": "Fake cloud key",
    "desktop": "Server logins note on the desktop",
    "git_branch": "Old git branch",
}
DEFAULT_KINDS = ("passwords", "env_backup", "cloud_key", "desktop")
CALLBACK_OK = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(:\d{1,5})?$")


def state(runner: Any) -> Optional[dict[str, Any]]:
    st = runner.http("GET", f"{API}/tripwires")
    return st if isinstance(st, dict) and "config" in st else None


def bait_rows(st: dict[str, Any]) -> list[tuple[str, str, str]]:
    """[(title, where, pill)] for what is planted now."""
    out = []
    for p in st.get("planted") or []:
        hits = int(p.get("hits") or 0)
        pill = f"tripped {hits}x" if hits else "set"
        out.append((KIND_NAMES.get(p.get("kind"), str(p.get("label") or p.get("kind"))), str(p.get("where") or ""),
                    pill))
    return out


def hit_rows(st: dict[str, Any], n: int = 8) -> list[tuple[str, str]]:
    out = []
    for h in (st.get("hits") or [])[:n]:
        try:
            at = datetime.fromtimestamp(float(h["at"])).strftime("%a %d %b %H:%M")
        except (KeyError, TypeError, ValueError):
            at = ""
        who = f"from {h.get('ip', '?')}" + (f" · {h['user_agent'][:60]}" if h.get("user_agent") else "")
        out.append((f"{at}  ·  {h.get('label', '')}".strip(" ·"), who))
    return out


def chosen(st: dict[str, Any]) -> set[str]:
    return {b.get("kind") for b in (st.get("config") or {}).get("baits") or []}


def set_kind(runner: Any, st: dict[str, Any], kind: str, on: bool) -> tuple[bool, str]:
    baits = [b for b in (st.get("config") or {}).get("baits") or [] if b.get("kind") != kind]
    if on:
        baits.append({"kind": kind})
    return save(runner, {"baits": baits})


def save(runner: Any, changes: dict[str, Any]) -> tuple[bool, str]:
    if "callback" in changes:
        cb = str(changes["callback"] or "").strip().rstrip("/")
        if cb and not CALLBACK_OK.match(cb):
            return False, "Use Orion's own address, like http://192.168.1.20:9135."
        changes = {**changes, "callback": cb}
    if "repo" in changes:
        changes = {**changes, "repo": str(changes["repo"] or "").strip()}
    res = runner.http("POST", f"{API}/tripwires/config", changes)
    if not isinstance(res, dict) or "config" not in res:
        return False, str((res or {}).get("error") or "Couldn't save it. Is Orion running?")
    return True, "Saved."


def listener(runner: Any, on: bool, port: Optional[int] = None) -> tuple[bool, str]:
    lis: dict[str, Any] = {"enabled": bool(on)}
    if port is not None:
        lis["port"] = int(port)
    ok, msg = save(runner, {"listener": lis})
    return ok, ("The listener starts with Orion's brain (restart it, or it starts the next time)." if ok and on
                else msg)


def plant(runner: Any, target: str = "this computer") -> tuple[bool, str]:
    res = runner.http("POST", f"{API}/tripwires/plant", {"target": target})
    if not isinstance(res, dict) or "message" not in res:
        return False, "Couldn't ask. Is Orion running?"
    return bool(res.get("ok")), str(res["message"])


def remove(runner: Any) -> tuple[bool, str]:
    res = runner.http("POST", f"{API}/tripwires/remove", {}, timeout=30)
    if not isinstance(res, dict) or "message" not in res:
        return False, "Couldn't remove them. Is Orion running?"
    return True, str(res["message"])
