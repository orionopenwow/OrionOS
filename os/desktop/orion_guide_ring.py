"""Guide Mode's pointer ring on OrionOS (the logic; the window is os/lab/bin/orion-guide-ring).

Orion's daemon (apps/daemon/guide_mode.py) writes the running guide to $XDG_RUNTIME_DIR/orion-guide.json and removes
it when the guide ends. The ring window reads it a few times a second and draws a glowing ring around the target.
Targets are in the focused screen's own pixels (as in a screenshot); the window works in Sway's logical pixels, so
they're divided by that screen's scale.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

STATE_NAME = "orion-guide.json"
MIN_SIZE = 44
PAD = 10


def state_path(environ: Optional[dict[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / STATE_NAME


def read_state(path: Path) -> Optional[dict[str, Any]]:
    """The guide, or None when no guide is running (no file, unreadable, or not active)."""
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) and st.get("active") else None


def ring(st: Optional[dict[str, Any]], scale: float = 1.0) -> Optional[tuple[float, float, float]]:
    """(centre x, centre y, radius) in logical pixels, or None when there's nothing to ring."""
    if not st or st.get("withheld"):
        return None
    t = st.get("target")
    if not isinstance(t, dict) or not all(isinstance(t.get(k), (int, float)) for k in ("x", "y", "w", "h")):
        return None
    s = scale if scale and scale > 0 else 1.0
    cx, cy = (t["x"] + t["w"] / 2) / s, (t["y"] + t["h"] / 2) / s
    r = max(MIN_SIZE, max(t["w"], t["h"]) / s + 2 * PAD) / 2
    return cx, cy, r


def caption(st: Optional[dict[str, Any]]) -> str:
    """Small label under the ring: 'Step 2 of 4: Click Install.'"""
    if not st:
        return ""
    i, n = st.get("index"), st.get("total")
    head = f"Step {int(i) + 1} of {int(n)}: " if isinstance(i, int) and isinstance(n, int) and n else ""
    return (head + str(st.get("text") or ""))[:140]


def focused_scale(outputs: Any) -> float:
    for o in outputs if isinstance(outputs, list) else []:
        if isinstance(o, dict) and o.get("focused"):
            try:
                return float(o.get("scale") or 1.0)
            except (TypeError, ValueError):
                return 1.0
    return 1.0
