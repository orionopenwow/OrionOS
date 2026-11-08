"""Workspace State Shifter — "Battle Stations" (Feature Bible D1).

"Orion, switch to OpenWoW mode" / "battle stations podcast":
  1. closes distracting apps you listed (never system processes),
  2. opens the project folder in your IDE,
  3. boots its dev servers under the self-healing wrapper (crash -> diagnosis -> [Fix & Restart]),
  4. opens URLs / files you listed,
  5. loads that project's memory into the Planetary Context Window.

Stations come from workspace_manifest.json "battle_stations"; every workspace also gets an automatic
"<name> mode" (open in IDE + run its start command). Example station:

  {"name": "Podcast", "aliases": ["recording"], "close_apps": ["Discord.exe", "steam.exe"],
   "open": ["https://riverside.fm/studio", "C:/Podcast/notes.md"], "dev_servers": [],
   "launch": [{"cmd": "obs64.exe", "cwd": "C:/Program Files/obs-studio/bin/64bit"}],
   "producer_mode": true}
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from typing import Any, Optional

from os_automation import open_url
from runner import is_destructive

log = logging.getLogger("orion.battle")

PROTECTED_PROCESSES = {
    "explorer.exe", "svchost.exe", "csrss.exe", "winlogon.exe", "lsass.exe", "services.exe", "smss.exe",
    "dwm.exe", "system", "orion.exe", "python.exe", "pythonw.exe", "node.exe", "code.exe", "msedgewebview2.exe",
}


def default_ide() -> str:
    return os.getenv("ORION_IDE", "code")


class BattleStations:
    def __init__(self, preload: Any, runner: Any) -> None:
        self.preload = preload
        self.runner = runner
        self.active: Optional[str] = None
        self.services: dict[str, list[str]] = {}  # station name -> run ids

    def stations(self) -> list[dict[str, Any]]:
        defined = list(self.preload.battle_stations)
        names = {s["name"].lower() for s in defined}
        for ws in self.preload.workspaces:
            if ws["name"].lower() in names or not ws["exists"]:
                continue
            defined.append({
                "name": ws["name"],
                "aliases": [ws["id"].replace("-", " ")],
                "workspace_id": ws["id"],
                "auto": True,
            })
        return defined

    def find(self, spoken: str) -> Optional[dict[str, Any]]:
        q = " ".join(re.findall(r"[a-z0-9]+", spoken.lower()))
        q = re.sub(r"\b(mode|station|stations|battle|switch|to|into|the|my|orion|please)\b", " ", q)
        q = " ".join(q.split())
        if not q:
            return None
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for st in self.stations():
            labels = [st["name"], *st.get("aliases", [])]
            st_best = 0.0
            for label in labels:
                ln = " ".join(re.findall(r"[a-z0-9]+", label.lower()))
                if not ln:
                    continue
                if q == ln or q.replace(" ", "") == ln.replace(" ", ""):
                    return st
                qs, ls = set(q.split()), set(ln.split())
                # every spoken word must belong to the label (no "dark mode" → "dark-souls-mod" leaps)
                score = len(qs & ls) / max(len(qs | ls), 1) if qs <= ls else 0.0
                qc, lc = q.replace(" ", ""), ln.replace(" ", "")
                if min(len(qc), len(lc)) >= 4 and (qc in lc or lc in qc):
                    score = max(score, 0.75)
                st_best = max(st_best, score)
            if st_best:
                scored.append((st_best, st["name"], st))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0]
        if top[0] < 0.5 or (len(scored) > 1 and scored[1][0] >= top[0] - 0.05):
            return None  # too weak or ambiguous: don't start servers / close apps on a guess
        return top[2]

    async def engage(self, station: dict[str, Any]) -> dict[str, Any]:
        ws = next((w for w in self.preload.workspaces if w["id"] == station.get("workspace_id")), None)
        report: dict[str, Any] = {"station": station["name"], "closed": [], "opened": [], "servers": [], "errors": []}

        # 1. close distractions
        for proc in station.get("close_apps", []) or []:
            name = str(proc).strip()
            if not name or name.lower() in PROTECTED_PROCESSES:
                continue
            if sys.platform == "win32":
                res = await asyncio.create_subprocess_exec("taskkill", "/IM", name, "/F",
                                                           stdout=asyncio.subprocess.DEVNULL,
                                                           stderr=asyncio.subprocess.DEVNULL,
                                                           creationflags=0x08000000)
                if await res.wait() == 0:
                    report["closed"].append(name)

        # 2. stop servers from the previous station
        if self.active and self.active != station["name"]:
            for rid in self.services.pop(self.active, []):
                await self.runner.stop(rid)

        # 3. open the project in the IDE
        if ws:
            ide = station.get("ide") or default_ide()
            await self.runner.start(f'{ide} "{ws["path"]}"', cwd=ws["path"], label=f"Open {ws['name']} in {ide}",
                                    timeout=30)
            report["opened"].append(ws["path"])

        # 4. launch commands / dev servers (long-running => service, self-healing)
        servers = list(station.get("dev_servers", []) or [])
        if ws and ws.get("start_cmd") and (station.get("auto") or station.get("start_workspace", True)) and not servers:
            servers.append({"cmd": ws["start_cmd"], "cwd": ws["path"]})
        run_ids = []
        for srv in servers:
            cmd = srv.get("cmd") if isinstance(srv, dict) else str(srv)
            if not cmd:
                continue
            cwd = srv.get("cwd") if isinstance(srv, dict) else None
            if is_destructive(cmd):
                report.setdefault("skipped", []).append(cmd)
                continue
            run = await self.runner.start(cmd, cwd=cwd or (ws or {}).get("path"), label=f"{station['name']}: {cmd[:40]}",
                                          kind="service")
            run_ids.append(run.id)
            report["servers"].append(cmd)
        for item in station.get("launch", []) or []:
            cmd = item.get("cmd") if isinstance(item, dict) else str(item)
            if cmd and is_destructive(cmd):
                report.setdefault("skipped", []).append(cmd)
            elif cmd:
                await self.runner.start(cmd, cwd=item.get("cwd") if isinstance(item, dict) else None,
                                        label=f"Launch {cmd[:40]}", kind="service")
        if run_ids:
            self.services[station["name"]] = run_ids

        # 5. URLs / files
        for target in station.get("open", []) or []:
            t = str(target)
            try:
                if t.startswith(("http://", "https://")):
                    open_url(t)
                elif sys.platform == "win32":
                    os.startfile(t)  # type: ignore[attr-defined]
                report["opened"].append(t)
            except Exception as exc:
                report["errors"].append(f"{t}: {exc}")

        self.active = station["name"]
        report["workspace"] = ws
        report["producer_mode"] = bool(station.get("producer_mode"))
        return report
