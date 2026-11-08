"""OrionOS desktop routes for the HUD (review H13 + cheap feature 2).

    GET  /linux/windows                the real app windows in the Sway session, for the HUD taskbar
    POST /linux/windows/{id}/focus     bring one to the front (a minimised one comes back)
    GET  /hardware/report              the hardware report card (orion-check-hardware --json), cached for a minute
    POST /hardware/report/send         send it to the paired phone as a card (alerts: type "system")

Off OrionOS (Windows, tests, no Sway) the window list is empty and says so; the report says it isn't available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from typing import Any, Callable, Optional

import alerts
import linux_desktop

log = logging.getLogger("orion.desktop_shell")
REPORT_TTL_S = 60.0


class HardwareReport:
    """Runs orion-check-hardware (os/lab/bin) and keeps the answer for a minute."""

    def __init__(self, run: Optional[Callable[[], Optional[dict]]] = None, clock: Callable[[], float] = time.time):
        self._run = run or self._run_command
        self.clock = clock
        self._cache: Optional[dict] = None
        self._at = 0.0

    @staticmethod
    def _run_command() -> Optional[dict]:
        exe = shutil.which("orion-check-hardware")
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--json"], capture_output=True, text=True, timeout=90).stdout
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
        except (OSError, ValueError, subprocess.SubprocessError, IndexError):
            log.debug("orion-check-hardware failed", exc_info=True)
            return None
        return data if isinstance(data, dict) and isinstance(data.get("rows"), list) else None

    def get(self, fresh: bool = False) -> dict[str, Any]:
        if fresh or self._cache is None or self.clock() - self._at > REPORT_TTL_S:
            rep = self._run()
            self._cache = {"available": True, **rep} if rep else {"available": False}
            self._at = self.clock()
        return self._cache


def card_text(rep: dict[str, Any]) -> tuple[str, str]:
    """(title, body) for the phone card: a green verdict, or the red list with fixes."""
    rows = rep.get("rows") or []
    if rep.get("safe"):
        notes = [f"{r['label']}: {r['text']}" for r in rows if r.get("status") == "warn"]
        body = "Everything important works." + ("\nWorth knowing:\n- " + "\n- ".join(notes) if notes else "")
    else:
        body = "\n".join(f"- {r['label']}: {r['text']} Fix: {r.get('fix', '')}".rstrip()
                         for r in rows if r.get("status") == "fail")
    return str(rep.get("headline") or "Hardware report"), body


def paired_phones(daemon: Any) -> int:
    try:
        return len(daemon.board.phones.list())
    except Exception:
        return 0


def make_router(get_daemon: Callable[[], Any], report: Optional[HardwareReport] = None):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()
    hw = report or HardwareReport()

    @router.get("/linux/windows")
    async def windows() -> dict[str, Any]:
        if not linux_desktop.has_compositor():  # [look] Sway or Hyprland
            return {"available": False, "windows": []}
        return {"available": True, "windows": await asyncio.to_thread(linux_desktop.list_windows)}

    @router.post("/linux/windows/{con_id}/focus")
    async def focus(con_id: int) -> dict[str, Any]:
        if not linux_desktop.has_compositor():  # [look] Sway or Hyprland
            raise HTTPException(409, "No Sway session here.")
        ok = await asyncio.to_thread(linux_desktop.focus_window, con_id)
        if not ok:
            raise HTTPException(404, "That window isn't open any more.")
        return {"ok": True}

    @router.get("/hardware/report")
    async def hardware(fresh: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(hw.get, fresh)

    @router.post("/hardware/report/send")
    async def send() -> dict[str, Any]:
        rep = await asyncio.to_thread(hw.get, True)
        if not rep.get("available"):
            return {"sent": False, "message": "The hardware check isn't on this computer."}
        if not paired_phones(get_daemon()):
            return {"sent": False, "message": "No phone is paired yet (Settings > Orion > Pair my phone)."}
        title, body = card_text(rep)
        alerts.emit("system", title, body, ref="hardware-report")   # the phone shows it as an alert card
        return {"sent": True, "title": title}

    return router
