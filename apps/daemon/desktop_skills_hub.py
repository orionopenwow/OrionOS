"""Desktop skills wired into the Skill Recorder hub (skill_routes.SkillHub): record, replay with approval,
point-to-fix, the light activity log, pattern spotting and the opt-in settings.

skill_routes.attach() creates this as `daemon.skills.desktop`; nothing in main.py.

HTTP (local, desktop only; browser extensions can't call the daemon's HTTP API):
    GET  /desktop-skills                 what this machine can do (accessibility, input), recording, settings
    POST /desktop-skills/record/start    start recording the desktop
    POST /desktop-skills/record/stop     stop: the draft card shows up in the HUD (SKILL_DRAFT)
    POST /desktop-skills/settings        {"notice_patterns": bool, "nightly_review": bool}  (both off by default)
    POST /desktop-skills/runs/{run_id}/pick/cancel
Runs, drafts, saving, export and import go through /browser-skills (one list of skills for both kinds).

Events to the HUD: SKILL_RECORDING {surface: "desktop", recording, paused, reason}, SKILL_DRAFT,
SKILL_HIGHLIGHT {run_id, highlights: [{index, found, how, rect}]}, SKILL_RUN_STATE, SKILL_PICK {surface: desktop}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

import desktop_recorder
import desktop_replay
from desktop_access import target_from_element
from desktop_skill import build_draft
from skill_replay import SkillRun, SkillRunner

log = logging.getLogger("orion.desktop_skills")

PICK_TIMEOUT_S = 180.0
KEEP_DAYS = 14
DEFAULT_SETTINGS = {"notice_patterns": False, "nightly_review": False, "review_hour": 3}


class Settings:
    """skills/settings.json: the opt-in switches for learning on its own."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "skills" / "settings.json"

    def get(self) -> dict[str, Any]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **{k: d[k] for k in DEFAULT_SETTINGS if k in d}}
        except (OSError, ValueError):
            return dict(DEFAULT_SETTINGS)

    def update(self, **changes: Any) -> dict[str, Any]:
        cur = self.get()
        for k, v in changes.items():
            if k in ("notice_patterns", "nightly_review"):
                cur[k] = bool(v)
            elif k == "review_hour":
                try:
                    cur[k] = max(0, min(23, int(v)))
                except (TypeError, ValueError):
                    pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        return cur


class ActivityLog:
    """The light event log: one JSON line per step (identity only: action, app, element role + name; never typed
    text, never positions, never screen images). skills/activity/YYYY-MM-DD.jsonl, kept KEEP_DAYS days."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "skills" / "activity"
        self._lock = threading.Lock()

    def append(self, step: dict[str, Any], sig: str, when: Optional[float] = None) -> None:
        when = time.time() if when is None else when
        from desktop_skill import light_step
        row = {"t": round(when, 1), "sig": sig,
               "step": light_step(step) if step.get("surface") == "desktop" else
               {k: step.get(k) for k in ("action", "url") if step.get(k)}}
        day = date.fromtimestamp(when).isoformat()
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with (self.dir / f"{day}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._prune()

    def _prune(self) -> None:
        files = sorted(self.dir.glob("*.jsonl"))
        for p in files[:-KEEP_DAYS]:
            try:
                p.unlink()
            except OSError:
                pass

    def since(self, t0: float) -> list[dict[str, Any]]:
        out = []
        if not self.dir.exists():
            return out
        first = date.fromtimestamp(t0).isoformat()
        for p in sorted(self.dir.glob("*.jsonl")):
            if p.stem < first:
                continue
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if float(row.get("t") or 0) >= t0:
                        out.append(row)
            except OSError:
                continue
        return out


class DesktopSkills:
    def __init__(self, hub: Any, data_dir: Path, *, backend: Any = None, executor: Any = None,
                 input_factory: Any = None) -> None:
        self.hub = hub
        self.settings = Settings(data_dir)
        self.log = ActivityLog(data_dir)
        self._backend = backend
        self._executor = executor
        self.input_factory = input_factory or desktop_recorder.pick_input
        self.recorder: Optional[desktop_recorder.DesktopRecorder] = None
        self.watcher: Optional[desktop_recorder.DesktopRecorder] = None
        self._light: deque[dict[str, Any]] = deque(maxlen=200)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._picks: dict[str, Any] = {}
        self.runner = SkillRunner(exec_step=self._exec_step, ask_approval=hub._ask_approval, ask_pick=self._ask_pick,
                                  save_fix=hub._save_fix, on_update=self._on_update)

    # ------------------------------------------------------------------ parts (built on first use)
    @property
    def backend(self) -> Any:
        if self._backend is None:
            import desktop_access
            self._backend = desktop_access.pick_backend()
        return self._backend

    @property
    def executor(self) -> Any:
        if self._executor is None:
            self._executor = desktop_replay.DesktopExecutor(self.backend)
        return self._executor

    def _call_soon(self, coro_fn: Any, *args: Any) -> None:
        """Run an async callback on the daemon loop from a recorder thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro_fn(*args))
        else:
            asyncio.run_coroutine_threadsafe(coro_fn(*args), loop)

    # ------------------------------------------------------------------ status
    def recording_view(self) -> Optional[dict[str, Any]]:
        r = self.recorder
        if r is None or not r.recording:
            return None
        st = r.status()
        return {"rec_id": "desktop", "surface": "desktop", "url": "", "title": st["window"], "app": st["app"],
                "paused": st["paused"], "reason": st["reason"], "events": st["steps"]}

    def status(self) -> dict[str, Any]:
        src = None
        try:
            src = self.input_factory()
        except Exception:
            pass
        return {"backend": getattr(self.backend, "name", "none"), "identity": bool(getattr(self.backend, "identity", False)),
                "input": getattr(src, "name", None), "can_record": src is not None,
                "ocr": bool(getattr(self.executor.ocr, "available", lambda: False)()),
                "recording": self.recording_view(), "settings": self.settings.get(),
                "watching": bool(self.watcher and self.watcher.recording)}

    # ------------------------------------------------------------------ recording
    async def start_recording(self) -> tuple[bool, str]:
        self._loop = asyncio.get_running_loop()
        if self.recorder is not None and self.recorder.recording:
            return False, "Already recording."
        if (frozen := __import__("freeze").blocked("record skills")):
            return False, frozen
        src = self.input_factory()
        if src is None:
            return False, ("I can't watch the keyboard and mouse on this desktop. On Windows or X11 install pynput; "
                           "on Wayland install evdev and add yourself to the 'input' group.")
        self.recorder = desktop_recorder.DesktopRecorder(self.backend, on_state=self._on_rec_state)
        ok, msg = self.recorder.start(src)
        if ok and not getattr(self.backend, "identity", False):
            msg += " (No accessibility here, so clicks keep only their window and position.)"
        return ok, msg

    def _on_rec_state(self, state: str, reason: str) -> None:
        self._call_soon(self._publish_rec, state, reason)

    async def _publish_rec(self, state: str, reason: str) -> None:
        r = self.recorder
        await self.hub.bus.publish({"event": "SKILL_RECORDING", "surface": "desktop", "rec_id": "desktop",
                                    "recording": bool(r and r.recording), "paused": state == "paused",
                                    "reason": reason, "events": len(r.steps) if r else 0})
        if state == "paused":
            await self.hub._say(reason)

    async def stop_recording(self) -> Optional[dict[str, Any]]:
        r = self.recorder
        if r is None:
            return None
        steps, skipped = r.stop()
        self.recorder = None
        draft = build_draft(steps, skipped=skipped, backend=getattr(self.backend, "name", ""))
        if not draft["steps"]:
            await self.hub._say("I didn't catch any steps to save." +
                                (" Password fields and private windows are never recorded." if skipped else ""))
            return None
        self.hub.drafts[draft["draft_id"]] = draft
        await self.hub.bus.publish({"event": "SKILL_DRAFT", "draft": draft})
        msg = f"Got it — {len(draft['steps'])} steps. Name the skill and check its values in the card."
        if skipped:
            msg += f" I left out {skipped} private thing(s) like passwords."
        await self.hub._say(msg)
        await self.note(draft["steps"])
        return draft

    # ------------------------------------------------------------------ activity / patterns
    async def note(self, steps: list[dict[str, Any]]) -> None:
        from skills_recorder import step_signature
        for s in steps:
            try:
                self.log.append(s, step_signature(s))
            except OSError:
                log.debug("activity log write failed", exc_info=True)
        await self.hub._note_activity(steps)

    def start_watcher(self) -> bool:
        """The opt-in "notice repeated tasks" watcher (light mode: identity only)."""
        if not self.settings.get()["notice_patterns"] or (self.watcher and self.watcher.recording):
            return False
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        src = self.input_factory()
        if src is None:
            return False
        self.watcher = desktop_recorder.DesktopRecorder(self.backend, light=True, on_step=self._on_light_step)
        ok, _ = self.watcher.start(src)
        return ok

    def stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    def _on_light_step(self, step: dict[str, Any]) -> None:
        self._light.append(step)
        if len(self._light) >= 6 or step.get("action") == "focus":
            batch = list(self._light)
            self._light.clear()
            self._call_soon(self.note, batch)

    async def set_settings(self, **changes: Any) -> dict[str, Any]:
        cur = self.settings.update(**changes)
        import changes as change_log
        for k, label in (("notice_patterns", "noticing repeated tasks"), ("nightly_review", "the nightly review")):
            if k in changes:
                try:
                    change_log.record_change(("Turned on " if cur[k] else "Turned off ") + label, kind="setting",
                                             source="Skills", approved_by="you")
                except Exception:
                    pass
        if "notice_patterns" in changes:
            if cur["notice_patterns"]:
                self.start_watcher()
            else:
                self.stop_watcher()
        return cur

    # ------------------------------------------------------------------ replay
    async def run(self, skill: dict[str, Any], values: Optional[dict[str, str]] = None, *, mode: str = "step",
                  preview: bool = True, source: str = "user") -> SkillRun:
        self._loop = asyncio.get_running_loop()
        run = self.runner.start(skill, values, mode=mode, preview=preview)
        if preview:
            try:
                highlights = await self.executor.preview(run.steps())
            except Exception:
                log.exception("desktop preview failed")
                highlights = []
            run.log.append({"event": "preview", "highlights": highlights, "at": time.time()})
            await self.hub.bus.publish({"event": "SKILL_HIGHLIGHT", "run_id": run.id, "highlights": highlights})
        return run

    async def _exec_step(self, run: SkillRun, i: int, step: dict[str, Any], approved: bool) -> dict[str, Any]:
        async def found(el: dict[str, Any], how: str) -> None:  # highlight the target before acting on it
            hl = [{"index": i, "found": True, "how": how, "rect": el.get("rect")}]
            await self.hub.bus.publish({"event": "SKILL_HIGHLIGHT", "run_id": run.id, "highlights": hl})
        return await self.executor.exec_step(run, i, step, approved, on_found=found)

    async def _ask_pick(self, run: SkillRun, i: int, step: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Ask the user to point at the right element: their next click is captured (not passed on as a step)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._picks[run.id] = fut
        src = self.input_factory()
        if src is None:
            self._picks.pop(run.id, None)
            return None

        def picked(x: Optional[int], y: Optional[int]) -> None:
            try:
                el = self.backend.element_at(x, y) if x is not None and y is not None else self.backend.focused_element()
            except Exception:
                el = None
            target = target_from_element(el, x, y) if el else (
                {"selectors": [{"kind": "coords", "x": int(x), "y": int(y)}]} if x is not None and y is not None else None)
            if not fut.done():
                loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(target))

        try:
            src.start(None, pick=picked)
        except Exception:
            self._picks.pop(run.id, None)
            return None
        await self.hub.bus.publish({"event": "SKILL_PICK", "surface": "desktop", "run_id": run.id, "index": i,
                                    "description": step.get("description", "")})
        await self.hub._say(f"I can't find the target for step {i + 1}. Click the right spot on screen and I'll "
                            "remember it.")
        try:
            return await asyncio.wait_for(fut, PICK_TIMEOUT_S)
        except asyncio.TimeoutError:
            return None
        finally:
            self._picks.pop(run.id, None)
            try:
                src.stop()
            except Exception:
                pass

    def cancel_pick(self, run_id: str) -> bool:
        fut = self._picks.pop(run_id, None)
        if fut is not None and not fut.done():
            fut.set_result(None)
            return True
        return False

    async def _on_update(self, run: SkillRun) -> None:
        await self.hub.bus.publish({"event": "SKILL_RUN_STATE", "run": {**run.view(), "surface": "desktop"}})

    def control(self, run_id: str, action: str) -> Optional[SkillRun]:
        run = self.runner.control(run_id, action)
        if run is not None and action == "stop":
            self.cancel_pick(run_id)
            self.hub._cancel_waiters(run_id)
        return run


# ====================================================================================== HTTP
def make_router(desk: DesktopSkills) -> Any:
    r = APIRouter(prefix="/desktop-skills", tags=["skills"])

    @r.get("")
    async def status() -> dict[str, Any]:
        return desk.status()

    @r.post("/record/start")
    async def record_start() -> dict[str, Any]:
        ok, msg = await desk.start_recording()
        return {"ok": ok, "message": msg}

    @r.post("/record/stop")
    async def record_stop() -> dict[str, Any]:
        draft = await desk.stop_recording()
        return {"ok": draft is not None, "draft": draft}

    @r.post("/settings")
    async def settings(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, "send a JSON object")
        return await desk.set_settings(**{k: v for k, v in body.items() if k in DEFAULT_SETTINGS})

    @r.post("/runs/{run_id}/pick/cancel")
    async def pick_cancel(run_id: str) -> dict[str, Any]:
        return {"ok": desk.cancel_pick(run_id)}

    return r


def today_iso() -> str:
    return datetime.now().date().isoformat()
