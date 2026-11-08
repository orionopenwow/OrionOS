"""Architect XP, levels, singularity skins, and the momentum / entropy score behind the living ecosystem.

XP comes from things that move life and work forward: finished focus blocks, recovery routines, commits, ideas,
water, cleared approvals. Levels unlock singularity skins. Momentum (0..1) grows the Dyson swarm around the core;
entropy items (piled-up approvals, failed builds, skipped sleep) become red asteroids you clear by dealing with them.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

AWARDS = {
    "focus_block": 50,     # a full focus session followed by a break
    "recovery": 20,        # completed a Sanctuary / conditioning routine
    "commit": 10,
    "idea": 5,
    "water": 2,
    "approval": 3,
    "devlog": 15,
}
DAILY_CAP = {"commit": 120, "water": 20, "approval": 45}

SKINS = [
    {"id": "default", "name": "Cosmic Violet", "level": 1},
    {"id": "emerald", "name": "Emerald Nebula", "level": 3},
    {"id": "supernova", "name": "Supernova Gold", "level": 5},
    {"id": "bloodmoon", "name": "Blood Moon Eclipse", "level": 8},
    {"id": "runic", "name": "Arcane Runic", "level": 12},
]


def level_for(xp: int) -> tuple[int, int, int]:
    """(level, xp at start of level, xp needed for next level). Level n starts at 100 * (n-1)^2."""
    level = int(math.sqrt(max(0, xp) / 100)) + 1
    return level, 100 * (level - 1) ** 2, 100 * level ** 2


class XPLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"xp": 0, "skin": "default", "days": {}, "recent": []}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            pass

    def award(self, kind: str, count: int = 1, note: str = "") -> Optional[dict[str, Any]]:
        pts = AWARDS.get(kind, 0) * max(1, count)
        if pts <= 0:
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        day = self.data.setdefault("days", {}).setdefault(today, {})
        cap = DAILY_CAP.get(kind)
        if cap is not None:
            pts = max(0, min(pts, cap - day.get(kind, 0)))
            if pts == 0:
                return None
        before = level_for(self.data["xp"])[0]
        self.data["xp"] += pts
        day[kind] = day.get(kind, 0) + pts
        self.data["days"] = dict(sorted(self.data["days"].items())[-60:])
        event = {"at": time.time(), "kind": kind, "xp": pts, "note": note[:80]}
        self.data["recent"] = (self.data.get("recent", []) + [event])[-30:]
        self._save()
        after = level_for(self.data["xp"])[0]
        return {**event, "level_up": after if after > before else None}

    def set_skin(self, skin: str) -> bool:
        level = level_for(self.data["xp"])[0]
        if any(s["id"] == skin and s["level"] <= level for s in SKINS):
            self.data["skin"] = skin
            self._save()
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        xp = int(self.data.get("xp", 0))
        level, start, nxt = level_for(xp)
        today = self.data.get("days", {}).get(datetime.now().strftime("%Y-%m-%d"), {})
        return {
            "xp": xp, "level": level, "level_start": start, "next_level": nxt,
            "today": sum(today.values()), "skin": self.data.get("skin", "default"),
            "skins": [{**s, "unlocked": s["level"] <= level} for s in SKINS],
            "recent": list(reversed(self.data.get("recent", [])[-8:])),
        }


def momentum(*, focus_minutes_today: float, commits_today: int, hydration: dict[str, Any],
             approvals_pending: int, failed_runs: int, night_activity: bool) -> dict[str, Any]:
    """0..1 momentum plus a list of entropy 'asteroids' (each a concrete thing to clear)."""
    goal = max(1, int(hydration.get("goal") or 8))
    score = (
        0.35 * min(1.0, focus_minutes_today / 180)
        + 0.25 * min(1.0, commits_today / 5)
        + 0.15 * min(1.0, (hydration.get("today") or 0) / goal)
        + 0.1 * (1.0 if approvals_pending <= 2 else 0.4 if approvals_pending <= 5 else 0.0)
        + 0.15 * (1.0 if failed_runs == 0 else 0.0)
    )
    debris: list[dict[str, Any]] = []
    if approvals_pending > 5:
        debris.append({"id": "approvals", "label": f"{approvals_pending} items waiting in the Queue", "tab": "queue",
                       "count": min(6, approvals_pending // 3)})
    if failed_runs:
        debris.append({"id": "runs", "label": f"{failed_runs} failed command{'s' if failed_runs != 1 else ''}",
                       "tab": "tools", "count": min(4, failed_runs)})
    if night_activity:
        debris.append({"id": "sleep", "label": "Working past bedtime", "tab": "life", "count": 2})
    return {"score": round(score, 3), "swarm": int(round(score * 12)), "debris": debris}
