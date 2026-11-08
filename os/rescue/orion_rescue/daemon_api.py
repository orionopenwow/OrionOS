"""Tiny client for Orion's daemon on 127.0.0.1:9120, used by the rescue panel and Settings' "Undo the last change".
The daemon may well be the thing that's broken, so every call has a short timeout and returns None on failure."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

BASE = os.environ.get("ORION_DAEMON_URL", "http://127.0.0.1:9120")


def _token() -> str:
    # Review B1: every daemon call carries its per-start key, read from the owner-only file it writes
    # (none present = no header, e.g. an older daemon).
    run = os.environ.get("XDG_RUNTIME_DIR", "")
    # The daemon's per-start key (apps/daemon/api_token.py): ORION_TOKEN_FILE, else $XDG_RUNTIME_DIR/orion/daemon.token
    for p in (os.environ.get("ORION_TOKEN_FILE", ""), f"{run}/orion/daemon.token" if run else "",
              str(Path.home() / ".orion" / "daemon.token")):
        if p:
            try:
                t = Path(p).read_text(encoding="utf-8").strip()
                if t:
                    return t
            except OSError:
                continue
    return ""


def request(method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: float = 4.0) -> Optional[Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed local URL)
            raw = r.read(2_000_000)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None


def alive() -> bool:
    h = request("GET", "/health", timeout=2.0)
    return isinstance(h, dict) and h.get("status") == "ok"
