"""Crash counter for the Orion screen (the HUD).

os/lab/bin/orion-hud restarts the HUD whenever it stops. Each time, it calls `orion-rescue record-crash <seconds it
ran>`. A run shorter than QUICK_S counts as a crash; a longer run means the HUD was fine, so the count starts again.
When LIMIT crashes happen inside WINDOW_S, the rescue panel opens instead of another blind restart.

The state is a tiny JSON file in the user's runtime folder, so a fresh login always starts from zero.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

QUICK_S = 60          # a HUD that stops within a minute crashed
WINDOW_S = 600        # ... and LIMIT of those within 10 minutes means it isn't going to get better by itself
LIMIT = 3


def _int_env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, ""))
        return v if v > 0 else default
    except ValueError:
        return default


def state_path(name: str = "hud") -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".cache")
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "hud"
    return Path(base) / f"orion-{safe}-crashes.json"


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"crashes": [], "rescues": 0}
    if not isinstance(data, dict):
        return {"crashes": [], "rescues": 0}
    crashes = [float(t) for t in data.get("crashes", []) if isinstance(t, (int, float))]
    return {"crashes": crashes, "rescues": int(data.get("rescues", 0) or 0)}


def save(path: Path, st: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass   # a full or read-only disk must never stop the HUD restarting


def decide(st: dict[str, Any], ran_for: float, now: float, *, limit: int = LIMIT, window: float = WINDOW_S,
           quick: float = QUICK_S) -> tuple[dict[str, Any], bool]:
    """Pure logic: the new state, and whether the rescue panel should open now."""
    if ran_for >= quick:
        return {"crashes": [], "rescues": st.get("rescues", 0)}, False
    crashes = [t for t in st.get("crashes", []) if now - t < window] + [now]
    rescue = len(crashes) >= limit
    new = {"crashes": [] if rescue else crashes, "rescues": st.get("rescues", 0) + (1 if rescue else 0)}
    return new, rescue


def record(ran_for: float, name: str = "hud", now: Optional[float] = None,
           path: Optional[Path] = None) -> bool:
    """Record one stop of the HUD. True = open the rescue panel."""
    p = path or state_path(name)
    st, rescue = decide(load(p), ran_for, time.time() if now is None else now,
                        limit=_int_env("ORION_RESCUE_AFTER", LIMIT))
    save(p, st)
    return rescue


def reset(name: str = "hud", path: Optional[Path] = None) -> None:
    p = path or state_path(name)
    st = load(p)
    save(p, {"crashes": [], "rescues": st.get("rescues", 0)})


def summary(name: str = "hud", path: Optional[Path] = None) -> dict[str, Any]:
    st = load(path or state_path(name))
    return {"recent_crashes": len(st["crashes"]), "rescues": st["rescues"]}
