"""Quick commands and the morning brief (release plan, Step 3).

A quick command is one word you say or type ("stream", "done", "status") that runs a short list of steps.
They live in ~/.config/orion/quick.json, which you can edit:

  {"stream": ["app:discord", "wow:start"],
   "done":   ["wow:stop"],
   "brief_at": "08:00"}

Step kinds:
  app:<name>      open an app (its launcher name, e.g. discord, code, firefox, thunar)
  wow:<action>    start, stop, restart, status or logs (update and build always go to your phone instead)
  station:<name>  a Battle Station
  say:status      a one-paragraph status: OpenWoW, approvals waiting, alerts

Quick commands can never approve anything or touch money; approvals stay on your phone's fingerprint.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

WOW_SAFE = {"start", "stop", "restart", "status", "logs"}
DEFAULTS: dict[str, Any] = {
    "status": ["say:status"],
    "stream": ["app:discord", "wow:start"],
    "done": ["wow:stop"],
    "brief_at": "08:00",
}
# Plain-language phrasings for the OpenWoW commands, so "Orion, start the server" works.
WOW_PHRASES = [
    (re.compile(r"^(start|run|launch|boot)( up)?( the)? (server|openwow|wow)( server)?$"), "start"),
    (re.compile(r"^(stop|shut down|kill|close)( the)? (server|openwow|wow)( server)?$"), "stop"),
    (re.compile(r"^restart( the)? (server|openwow|wow)( server)?$"), "restart"),
    (re.compile(r"^(is the server (up|running)|server status|wow status|openwow status|how'?s the server)$"), "status"),
    (re.compile(r"^(server|wow|openwow) logs$|^show( me)?( the)? (server )?logs$"), "logs"),
]
Run = Callable[..., Awaitable[tuple[int, str]]]


def normalize(text: str) -> str:
    t = re.sub(r"[^\w\s']", " ", text.lower()).strip()
    t = re.sub(r"^(hey |ok |okay )?orion\b[\s,]*", "", t)
    t = re.sub(r"^(please |can you |could you )", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r" please$", "", t)


async def _run(*args: str, timeout: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace").strip()


class QuickCommands:
    def __init__(self, config: Optional[Path] = None, run: Run = _run, board: Any = None,
                 stations: Any = None, alerts: Any = None) -> None:
        self.path = config or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "orion" / "quick.json"
        self.run = run
        self.board, self.stations, self.alerts = board, stations, alerts
        # Toolkit phrases (ops_toolkit.py): objects with .name, .match(text) -> arg or None, async .run(arg) -> str
        self.plugins: list[Any] = []
        # Extra parts of the morning brief: async () -> str, "" to say nothing (Promise Keeper, trust_kit.py)
        self.brief_parts: list[Callable[[], Awaitable[str]]] = []

    def config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                return {**DEFAULTS, **data}
        except (OSError, ValueError):
            pass
        return dict(DEFAULTS)

    def commands(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.config().items() if isinstance(v, list)}

    def save_command(self, name: str, steps: list[str]) -> None:
        name = normalize(name)
        if not re.fullmatch(r"[a-z0-9' ]{1,30}", name):
            raise ValueError("a quick command is a short word or two")
        for s in steps:
            self._check_step(s)
        cfg = self.config()
        cfg[name] = steps
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(cfg, indent=2))

    def _check_step(self, step: str) -> tuple[str, str]:
        kind, _, arg = step.partition(":")
        if kind == "toolkit" and any(p.name == arg.partition(":")[0] for p in self.plugins):
            return kind, arg
        if kind == "wow" and arg not in WOW_SAFE:
            raise ValueError(f"wow:{arg} isn't allowed in a quick command (update and build ask your phone)")
        if kind == "app" and not re.fullmatch(r"[\w.+-]{1,60}", arg):
            raise ValueError("bad app name")
        if kind not in ("app", "wow", "station", "say"):
            raise ValueError(f"unknown step '{step}'")
        return kind, arg

    # ------------------------------------------------------------------ matching
    def match(self, text: str) -> Optional[tuple[str, list[str]]]:
        t = normalize(text)
        if not t:
            return None
        cmds = self.commands()
        if t in cmds:
            return t, cmds[t]
        m = re.match(r"^(run|do|go|start) (.+)$", t)
        if m and m.group(2) in cmds:
            return m.group(2), cmds[m.group(2)]
        for p in self.plugins:   # toolkit phrases ("run openwow", "release orion 1.1") before the wow shortcuts
            arg = p.match(t)
            if arg is not None:
                return f"{p.name} {t}", [f"toolkit:{p.name}:{arg}"]
        for rx, action in WOW_PHRASES:
            if rx.match(t):
                return f"wow {action}", [f"wow:{action}"]
        m = re.match(r"^wow (\w+)$", t)
        if m and m.group(1) in WOW_SAFE:
            return t, [f"wow:{m.group(1)}"]
        return None

    # ------------------------------------------------------------------ running
    async def run_steps(self, steps: list[str]) -> str:
        said = []
        for step in steps:
            try:
                kind, arg = self._check_step(step)
            except ValueError as exc:
                said.append(str(exc))
                continue
            if kind == "toolkit":
                name, _, parg = arg.partition(":")
                plugin = next(p for p in self.plugins if p.name == name)
                said.append(await plugin.run(parg))
            elif kind == "wow":
                rc, out = await self.run(shutil.which("wow") or "wow", arg, timeout=60)
                said.append(out.splitlines()[-1] if arg != "logs" and out else out[-800:] or f"wow {arg}: exit {rc}")
            elif kind == "app":
                rc, out = await self.run("swaymsg", "exec", f"gtk-launch {arg} || {arg}")
                said.append(f"Opening {arg}." if rc == 0 else f"Couldn't open {arg}.")
            elif kind == "station" and self.stations is not None:
                st = self.stations.find(arg)
                if st:
                    await self.stations.engage(st)
                    said.append(f"Battle Station {arg} engaged.")
                else:
                    said.append(f"No Battle Station called {arg}.")
            elif kind == "say" and arg == "status":
                said.append(await self.status())
        return " ".join(s for s in said if s)

    async def status(self) -> str:
        parts = []
        wow_bin = shutil.which("wow")
        if wow_bin:
            rc, out = await self.run(wow_bin, "status", timeout=20)
            parts.append(out.splitlines()[0] if out else "OpenWoW status unknown.")
        else:
            parts.append("OpenWoW standby.")
        if self.board is not None:
            n = len(self.board.pending())
            parts.append("Nothing waiting for your approval." if n == 0 else
                         f"{n} decision{'s' if n != 1 else ''} waiting for your approval.")
        return " ".join(p if p.endswith(".") else p + "." for p in parts)

    # ------------------------------------------------------------------ morning brief
    def brief_due(self, now: float, last_sent: float) -> bool:
        hh, _, mm = str(self.config().get("brief_at", "08:00")).partition(":")
        try:
            h, m = int(hh), int(mm or 0)
        except ValueError:
            h, m = 8, 0
        lt = time.localtime(now)
        due = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        return now >= due and last_sent < due

    async def morning_brief(self) -> str:
        parts = ["Good morning. " + await self.status()]
        for part in self.brief_parts:
            try:
                said = await part()
            except Exception:
                said = ""
            if said:
                parts.append(said)
        return " ".join(parts)
