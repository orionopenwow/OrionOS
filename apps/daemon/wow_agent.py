"""OpenWoW agent: watches the OpenWoW server and runs update/build/restart after a signed approval.

The `wow` command (os/lab/bin/wow) does the work; this agent decides when to ask.
- `wow update` / `wow build` → POST /wow/request → a boardroom decision (phone or boardroom signs it).
  Both are marked sensitive, so they always ask (autonomy capped at level 1).
- Every 5 minutes: if you started OpenWoW (`wow start`) and it's no longer running, propose a restart.
  Restarts are reversible, so after enough clean approvals the learner may offer to let Orion restart it
  by itself and tell you (level 2).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.wow")

ACTIONS = {
    "update": ("wow.update", "Get the newest OpenWoW code and reinstall its packages",
               "git pull (fast-forward only), then npm install; restarts the server if it was running"),
    "build": ("wow.build", "Build an OpenWoW release", "runs the build command chosen in `wow setup`"),
}
WATCH_EVERY_S = 300.0


def _notify(title: str, message: str) -> None:
    try:
        import alerts
        alerts.emit("openwow", title, message)   # your phone (alerts.py decides how loudly)
    except Exception:
        pass
    try:
        import linux_desktop
        linux_desktop.notify(title, message)
    except Exception:
        pass


class WowAgent:
    def __init__(self, board: Any, wow_bin: Optional[str] = None, state_dir: Optional[Path] = None,
                 enabled: Optional[bool] = None) -> None:
        self.board = board
        self.wow = wow_bin or shutil.which("wow") or "/usr/local/bin/wow"
        self.state_dir = state_dir or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "orion"
        self.enabled = sys.platform.startswith("linux") if enabled is None else enabled
        self.notify = _notify
        self.on_crash: list[Callable[[str], Any]] = []   # e.g. crash_healer.observe_wow(status text)
        self._task: Optional[asyncio.Task] = None
        self._jobs: set[asyncio.Task] = set()
        board.executors["wow.update"] = lambda d: self._spawn_cmd("_do-update", "OpenWoW updated")
        board.executors["wow.build"] = lambda d: self._spawn_cmd("_do-build", "OpenWoW build finished")
        board.executors["wow.restart"] = lambda d: self._spawn_cmd("restart", "OpenWoW restarted")

    # ------------------------------------------------------------------ requests from `wow update|build`
    def _pending(self, task_type: str) -> bool:
        return any(d["task_type"] == task_type for d in self.board.pending())

    async def request(self, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError("unknown action")
        task_type, summary, what = ACTIONS[action]
        if self._pending(task_type):
            return {"summary": summary, "already_waiting": True}
        await self.board.propose(task_type, summary, {"project": "openwow", "does": what},
                                 proposer="OpenWoW agent", risk="low", simulated=False)
        self.notify("Orion needs your OK", summary)
        return {"summary": summary, "already_waiting": False}

    # ------------------------------------------------------------------ watcher
    async def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        for t in [self._task, *self._jobs]:
            if t:
                t.cancel()

    async def _run(self, *args: str, timeout: float = 30) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "timed out"
        return proc.returncode or 0, out.decode(errors="replace")

    async def check_once(self) -> Optional[str]:
        """Propose a restart if OpenWoW should be running but isn't. Returns what was done (for tests)."""
        if not (self.state_dir / "wow-wanted").exists() or self._pending("wow.restart"):
            return None
        rc, out = await self._run("systemctl", "--user", "is-active", "openwow")
        if out.strip() == "active":
            return None
        _, status = await self._run(self.wow, "status")
        last_err = next((l for l in status.splitlines() if l.startswith("Last error:")), "")
        await self.board.propose(
            "wow.restart", "OpenWoW stopped. Restart it?",
            {"project": "openwow", "state": out.strip() or "stopped", "last_error": last_err[12:][:200]},
            proposer="OpenWoW agent", risk="low", simulated=False)
        self.notify("OpenWoW stopped", (last_err or "It isn't running.") + " Approve a restart on your phone.")
        for hook in self.on_crash:
            try:
                hook(status)
            except Exception:
                log.exception("OpenWoW crash hook failed")
        return "proposed"

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(WATCH_EVERY_S)
            try:
                await self.check_once()
            except Exception:
                log.exception("OpenWoW watch failed")

    # ------------------------------------------------------------------ executors (after a valid signature)
    def _spawn_cmd(self, arg: str, done_title: str) -> dict[str, Any]:
        async def job() -> None:
            rc, out = await self._run(self.wow, arg, timeout=1800)
            tail = out.strip().splitlines()[-1][:200] if out.strip() else ""
            if rc == 0:
                self.notify(done_title, tail)
            else:
                self.notify(f"OpenWoW: '{arg.lstrip('_')}' failed", tail or f"exit {rc}")
        task = asyncio.get_running_loop().create_task(job())
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        return {"started": True}
