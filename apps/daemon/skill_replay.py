"""Skill replay: walks a signed skill step by step through the browser extension.

Rules that always hold (tests: tests/test_skill_recorder.py):
  * A skill whose signature does not match is never replayed.
  * Every risky step (sends, buys, pays, deletes, posts, publishes, submits a form) asks for the user's OK,
    every time, before the extension touches the page. If the page reveals at run time that a step is risky
    (the extension checks the real element), the step is held and the OK is asked then.
  * Selectors are tried in order by the extension; if none match it tries a fuzzy text match; if it is still
    unsure the run stops and asks the user to click the right element. That fix is saved into the skill.

The engine is transport-agnostic: skill_routes.py wires it to the WebSocket extension and the approval queue.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from skills_recorder import classify_risk, describe_step, render_step

log = logging.getLogger("orion.skills.replay")

ExecStep = Callable[["SkillRun", int, dict[str, Any], bool], Awaitable[dict[str, Any]]]
AskApproval = Callable[["SkillRun", int, dict[str, Any], str], Awaitable[bool]]
AskPick = Callable[["SkillRun", int, dict[str, Any]], Awaitable[Optional[dict[str, Any]]]]
SaveFix = Callable[["SkillRun", int, dict[str, Any], str], Awaitable[Optional[dict[str, Any]]]]
OnUpdate = Callable[["SkillRun"], Awaitable[None]]

MAX_FIX_TRIES = 2


@dataclass
class SkillRun:
    skill: dict[str, Any]
    values: dict[str, str]
    mode: str = "step"                     # step | all
    id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}")
    index: int = 0
    status: str = "preview"                # preview | running | waiting_step | waiting_approval | waiting_fix | done | failed | stopped
    message: str = ""
    log: list[dict[str, Any]] = field(default_factory=list)
    tab_id: Optional[int] = None
    started_at: float = field(default_factory=time.time)
    stop_requested: bool = False
    go: asyncio.Event = field(default_factory=asyncio.Event)

    def steps(self) -> list[dict[str, Any]]:
        return [render_step(s, self.values) for s in self.skill.get("steps") or []]

    def view(self) -> dict[str, Any]:
        steps = self.steps()
        return {"run_id": self.id, "skill_id": self.skill.get("id"), "name": self.skill.get("name"),
                "mode": self.mode, "index": self.index, "total": len(steps), "status": self.status,
                "message": self.message, "log": self.log[-50:],
                "steps": [{"description": s["description"], "risky": bool(s.get("risky")),
                           "risk_reason": s.get("risk_reason")} for s in steps]}


class SkillRunner:
    def __init__(self, *, exec_step: ExecStep, ask_approval: AskApproval, ask_pick: AskPick, save_fix: SaveFix,
                 on_update: OnUpdate) -> None:
        self.exec_step = exec_step
        self.ask_approval = ask_approval
        self.ask_pick = ask_pick
        self.save_fix = save_fix
        self.on_update = on_update
        self.runs: dict[str, SkillRun] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------ control
    def start(self, skill: dict[str, Any], values: Optional[dict[str, str]] = None, *, mode: str = "step",
              preview: bool = True, tab_id: Optional[int] = None) -> SkillRun:
        if not skill.get("trusted"):
            raise PermissionError("This skill was changed outside Orion (its signature doesn't match), "
                                  "so I won't run it. Record it again or re-save it from Orion.")
        vals = {v["name"]: str(v.get("example", "")) for v in skill.get("variables") or []}
        vals.update({str(k): str(v) for k, v in (values or {}).items()})
        run = SkillRun(skill=skill, values=vals, mode="all" if mode == "all" else "step", tab_id=tab_id)
        run.status = "preview" if preview else "running"
        if not preview:
            run.go.set()
        self.runs[run.id] = run
        self._tasks[run.id] = asyncio.create_task(self._drive(run), name=f"skill-{run.id}")
        return run

    def control(self, run_id: str, action: str) -> Optional[SkillRun]:
        run = self.runs.get(run_id)
        if run is None or run.status in ("done", "failed", "stopped"):
            return run
        if action == "stop":
            run.stop_requested = True
            run.go.set()
        elif action in ("start", "next"):
            run.go.set()
        elif action == "run_all":
            run.mode = "all"
            run.go.set()
        return run

    async def wait(self, run_id: str) -> SkillRun:
        task = self._tasks.get(run_id)
        if task:
            await task
        return self.runs[run_id]

    # ------------------------------------------------------------ engine
    async def _set(self, run: SkillRun, status: str, message: str = "") -> None:
        run.status = status
        run.message = message
        try:
            await self.on_update(run)
        except Exception:
            log.exception("skill run update failed")

    async def _wait_go(self, run: SkillRun, status: str, message: str) -> bool:
        await self._set(run, status, message)
        await run.go.wait()
        run.go.clear()
        return not run.stop_requested

    async def _drive(self, run: SkillRun) -> None:
        try:
            if run.status == "preview":
                if not await self._wait_go(run, "preview", "Check the highlighted steps, then start."):
                    return await self._set(run, "stopped", "Stopped before the first step.")
            run.go.clear()
            while run.index < len(run.skill.get("steps") or []):
                if run.stop_requested:
                    return await self._set(run, "stopped", f"Stopped at step {run.index + 1}.")
                i = run.index
                step = render_step(run.skill["steps"][i], run.values)
                if run.mode == "step" and i > 0:
                    if not await self._wait_go(run, "waiting_step", f"Next: {step['description']}"):
                        return await self._set(run, "stopped", f"Stopped at step {i + 1}.")
                ok = await self._run_step(run, i, step)
                if not ok:
                    return
                run.index += 1
            await self._set(run, "done", "All steps done.")
        except asyncio.CancelledError:
            run.status = "stopped"
            raise
        except Exception as exc:
            log.exception("skill run %s crashed", run.id)
            await self._set(run, "failed", f"Something went wrong: {exc}")

    async def _approve(self, run: SkillRun, i: int, step: dict[str, Any], reason: str) -> bool:
        await self._set(run, "waiting_approval", f"Waiting for your OK: {step['description']} ({reason})")
        ok = await self.ask_approval(run, i, step, reason)
        run.log.append({"index": i, "event": "approved" if ok else "declined", "at": time.time()})
        if not ok:
            await self._set(run, "stopped", f"You said no to step {i + 1}, so I stopped.")
        return ok and not run.stop_requested

    async def _run_step(self, run: SkillRun, i: int, step: dict[str, Any]) -> bool:
        approved = False
        reason = step.get("risk_reason") or classify_risk(step) or ""
        if step.get("risky") or reason:
            if not await self._approve(run, i, step, reason or "risky step"):
                return False
            approved = True
        await self._set(run, "running", f"Step {i + 1}: {step['description']}")
        tries = 0
        while True:
            res = await self.exec_step(run, i, step, approved)
            if res.get("needs_approval") and not approved:
                # the live page shows this is a submit/send/delete etc. even though the recording didn't
                if not await self._approve(run, i, step, str(res.get("reason") or "it looks risky on the page")):
                    return False
                approved = True
                continue
            if res.get("need_pick") and tries < MAX_FIX_TRIES:
                tries += 1
                await self._set(run, "waiting_fix",
                                f"I can't find the target for step {i + 1} ({step['description']}). "
                                "Click the right element on the page.")
                target = await self.ask_pick(run, i, step)
                if not target or run.stop_requested:
                    await self._set(run, "stopped", f"No element picked for step {i + 1}, so I stopped.")
                    return False
                updated = await self.save_fix(run, i, target, "picked")
                if updated:
                    run.skill = updated
                step = render_step(run.skill["steps"][i], run.values)
                run.log.append({"index": i, "event": "fixed", "at": time.time()})
                new_reason = step.get("risk_reason") or classify_risk(step)
                if new_reason and not approved:
                    if not await self._approve(run, i, step, new_reason):
                        return False
                    approved = True
                await self._set(run, "running", f"Step {i + 1}: {step['description']}")
                continue
            break
        if not res.get("ok"):
            await self._set(run, "failed", f"Step {i + 1} didn't work: {res.get('error') or 'unknown problem'}")
            run.log.append({"index": i, "event": "failed", "error": res.get("error"), "at": time.time()})
            return False
        if res.get("healed") and res.get("target"):
            updated = await self.save_fix(run, i, res["target"], f"healed:{res.get('matched', '')}")
            if updated:
                run.skill = updated
        run.log.append({"index": i, "event": "done", "matched": res.get("matched"), "healed": bool(res.get("healed")),
                        "at": time.time(), "description": describe_step(step)})
        return True
