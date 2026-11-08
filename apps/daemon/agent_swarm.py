"""Agent swarm controls: the robots orbiting the core (see agent_watch.SwarmMixin for how agents are found).

Pause and Stop only work on coding agents Orion started itself through the process runner. Agents you
started yourself (in a terminal, an editor or the cloud) are only watched: Orion never signals or kills
a process it did not start. Open shows the agent's project folder in the file manager.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.swarm")

# A runner command counts as a coding agent when its first program is one of these.
AGENT_CLIS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "cursor-agent": "Cursor", "aider": "Aider",
              "antigravity": "Antigravity"}
_FIRST_PROGRAM = re.compile(r"^\s*(?:(?:npx|pnpm\s+dlx|bunx)\s+(?:-y\s+)?)?(?:@[\w.-]+/)?([\w.-]+)")

EXTERNAL_NOTE = "Orion did not start this agent, so it will not pause or stop it. Use the app it runs in."

# Toolkit hooks (Collision Guard, Proof Gate): each gets the snapshot and may add to it before the HUD sees it.
DECORATORS: list[Callable[[dict[str, Any]], None]] = []
_LIST_LOCK = threading.Lock()   # the HUD poll and the toolkit loop both read the watcher


def agent_tool(command: str) -> Optional[str]:
    m = _FIRST_PROGRAM.match(command or "")
    if not m:
        return None
    prog = Path(m.group(1)).name.lower()
    prog = re.sub(r"\.(exe|cmd|bat)$", "", prog)
    if prog.endswith("-cli"):
        prog = prog[:-4]
    return AGENT_CLIS.get(prog)


def _descendants(pid: int) -> list[int]:
    """Child process ids (Linux /proc first, then pgrep). Best effort."""
    out: list[int] = []
    todo = [pid]
    while todo and len(out) < 200:
        p = todo.pop()
        kids: list[int] = []
        proc = Path(f"/proc/{p}/task")
        if proc.is_dir():
            for task in proc.iterdir():
                try:
                    kids += [int(x) for x in (task / "children").read_text().split()]
                except (OSError, ValueError):
                    continue
        else:
            try:
                res = subprocess.run(["pgrep", "-P", str(p)], capture_output=True, text=True, timeout=2)
                kids = [int(x) for x in res.stdout.split()]
            except (OSError, ValueError, subprocess.SubprocessError):
                kids = []
        out += kids
        todo += kids
    return out


class AgentSwarm:
    def __init__(self, watcher: Any, runner: Any = None) -> None:
        self.watcher = watcher
        self.runner = runner
        self.paused: set[str] = set()

    # ------------------------------------------------------------ list
    def launched(self) -> list[dict[str, Any]]:
        runs = getattr(self.runner, "runs", None) or {}
        out = []
        for run in list(runs.values()):
            tool = agent_tool(getattr(run, "command", ""))
            if not tool:
                continue
            running = run.ended_at is None
            if not running:
                self.paused.discard(run.id)
            out.append({"id": run.id, "tool": tool, "cwd": run.cwd or "", "label": run.label, "running": running,
                        "paused": run.id in self.paused, "started_at": run.started_at, "ended_at": run.ended_at,
                        "exit_code": run.exit_code})
        return out

    def list(self, now: Optional[float] = None, decorate: bool = True) -> dict[str, Any]:
        with _LIST_LOCK:
            agents = self.watcher.swarm(now, launched=self.launched())
        for a in agents:
            a["control_note"] = "" if a["controllable"] else EXTERNAL_NOTE
        snap = {"agents": agents, "at": now or time.time(), "watching": self.watcher.watching()}
        if decorate:
            for fn in list(DECORATORS):
                try:
                    fn(snap)
                except Exception:
                    log.exception("swarm decorator failed")
        return snap

    def find(self, agent_id: str) -> Optional[dict[str, Any]]:
        return next((a for a in self.list()["agents"] if a["id"] == agent_id), None)

    # ------------------------------------------------------------ actions
    async def act(self, agent_id: str, action: str) -> tuple[bool, str]:
        agent = self.find(agent_id)
        if not agent:
            return False, "That agent is gone."
        if action == "open":
            return self._open(agent)
        if action not in ("pause", "resume", "stop"):
            return False, "Unknown action."
        if not agent["controllable"] or not agent.get("run_id"):
            return False, EXTERNAL_NOTE
        run = (getattr(self.runner, "runs", None) or {}).get(agent["run_id"])
        if run is None or run.ended_at is not None or run.proc is None:
            return False, "It already stopped."
        if action == "stop":
            if run.id in self.paused:   # a stopped process can't handle SIGTERM until it is woken up
                self._signal(run.proc.pid, "resume")
                self.paused.discard(run.id)
            await self.runner.stop(run.id)
            return True, "Stopping it."
        if sys.platform == "win32":
            return False, "Pause works on Linux and macOS only."
        self._signal(run.proc.pid, action)
        if action == "pause":
            self.paused.add(run.id)
            return True, "Paused."
        self.paused.discard(run.id)
        return True, "Running again."

    @staticmethod
    def _signal(pid: int, action: str) -> None:
        sig = signal.SIGSTOP if action == "pause" else signal.SIGCONT  # type: ignore[attr-defined]
        for p in [pid, *_descendants(pid)]:
            try:
                os.kill(p, sig)
            except (ProcessLookupError, PermissionError):
                continue

    @staticmethod
    def _open(agent: dict[str, Any]) -> tuple[bool, str]:
        folder = Path(agent.get("cwd") or "")
        if not agent.get("cwd") or not folder.is_dir():
            return False, "No project folder is known for this agent."
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return False, f"Could not open the folder: {exc}"
        return True, f"Opened {folder.name}."
