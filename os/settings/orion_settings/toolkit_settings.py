"""Settings data for Orion's Toolkit pages (no GTK here, so it can be tested anywhere).

Raid Night Autopilot (docs/ORION_TOOLKIT_PLAN.md, tool 24) lives in the daemon: GET /raid, POST /raid/config,
POST /raid/check. This module reads and writes it through the local daemon, in plain words.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .backend import API

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CHANNEL_OK = re.compile(r"^\d{5,25}$")


def raid(runner: Any) -> Optional[dict[str, Any]]:
    """The daemon's raid night status, or None when Orion isn't answering."""
    st = runner.http("GET", f"{API}/raid")
    return st if isinstance(st, dict) and "config" in st else None


def when_text(st: dict[str, Any]) -> str:
    if not st.get("enabled"):
        return "Off. Set a day and time to turn it on."
    try:
        nxt = datetime.fromisoformat(st["next_start"]).strftime("%a %d %b, %H:%M")
    except (KeyError, ValueError):
        nxt = "soon"
    return f"{st.get('schedule', '')}. Next one: {nxt}."


def phase_pill(st: dict[str, Any]) -> tuple[str, str]:
    return {"off": ("off", "off"), "idle": ("scheduled", ""), "checklist": ("checking", "warn"),
            "raid": ("raid on", "ok"), "after": ("done", "")}.get(st.get("phase", ""), ("", ""))


def checks(st: dict[str, Any]) -> list[tuple[str, str, str]]:
    """[(title, text, pill kind)] for the last checklist."""
    chk = (st.get("state") or {}).get("checklist") or {}
    out = []
    names = {"server": "Server", "backup": "Last night's backup", "patch": "Staged patch"}
    for c in chk.get("checks") or []:
        kind = "ok" if c.get("ok") else ("warn" if c.get("ok") is False else "off")
        out.append((names.get(c.get("name"), str(c.get("name"))), str(c.get("text") or ""), kind))
    return out


def log_lines(st: dict[str, Any], n: int = 6) -> list[str]:
    out = []
    for e in ((st.get("state") or {}).get("log") or [])[-n:]:
        try:
            at = datetime.fromtimestamp(float(e["at"])).strftime("%H:%M")
        except (KeyError, TypeError, ValueError):
            at = ""
        out.append(f"{at} {e.get('text', '')}".strip())
    return out


def save(runner: Any, changes: dict[str, Any]) -> tuple[bool, str]:
    if "discord_channel_id" in changes:
        v = str(changes["discord_channel_id"] or "").strip()
        if v and not CHANNEL_OK.match(v):
            return False, "The channel id is a long number (in Discord: right-click the channel, Copy Channel ID)."
        changes = {**changes, "discord_channel_id": v}
    if "schedule" in changes and not re.search(r"(mon|tue|wed|thu|fri|sat|sun)", str(changes["schedule"]).lower()):
        return False, "Say a day and a time, like “Fridays at 7 pm”."
    res = runner.http("POST", f"{API}/raid/config", changes)
    if not isinstance(res, dict) or "config" not in res:
        return False, "Couldn't save it. Is Orion running? (Or the time wasn't understood: try “Fridays at 7 pm”.)"
    return True, str(res.get("text") or "Saved.")


def check_now(runner: Any) -> tuple[bool, str]:
    res = runner.http("POST", f"{API}/raid/check", {}, timeout=120)
    if not isinstance(res, dict) or "summary" not in res:
        return False, "Couldn't run the checks. Is Orion running?"
    return True, res["summary"] + " The post is waiting for your OK on your phone."
