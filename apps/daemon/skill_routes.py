"""Skill Recorder wiring: WebSocket events, voice commands, approvals and HTTP routes.

main.py hooks (one line each):
    app.include_router(skill_routes.attach(daemon))                 # creates daemon.skills (SkillHub)
    dispatch():        elif str(kind).startswith("SKILL_") and await self.skills.dispatch(client, kind, msg): pass
    USER_PROMPT:       if await self.skills.handle_text(text, source): return
    decide_approval(): skill_step items -> await self.skills.decide(current, decision)

WebSocket events
  extension -> daemon   SKILL_REC_STATE {state: started|stopped|paused|resumed, rec_id, url, title, reason}
                        SKILL_REC_EVENT {rec_id, ev}          one raw DOM action (private fields never sent)
                        SKILL_STEP_RESULT {run_id, nonce, ...}  result of SKILL_EXEC_STEP
                        SKILL_PICKED {run_id, nonce, target}    the element the user clicked to fix a step
                        SKILL_ACTIVITY {steps}                  opt-in "notice repeated tasks" (no values)
                        SKILL_RUN_CONTROL {run_id, action: start|next|run_all|stop}   from the page pill
  desktop -> daemon     SKILL_LIST, SKILL_RECORD_START, SKILL_RECORD_STOP, SKILL_SAVE {draft_id, name,
                        voice_triggers, variables}, SKILL_DISCARD, SKILL_DELETE, SKILL_RUN {id, values, mode},
                        SKILL_RUN_CONTROL, SKILL_EXPORT {id, lang}, SKILL_IMPORT {code, name},
                        SKILL_SUGGESTION_DISMISS {id}
  daemon -> extension   SKILL_RECORD_START/STOP, SKILL_PREVIEW, SKILL_EXEC_STEP, SKILL_PICK, SKILL_RUN_STATE
  daemon -> HUD         SKILL_RECORDING, SKILL_DRAFT, SKILLS, SKILL_RUN_STATE, SKILL_SUGGESTION, SKILL_EXPORTED,
                        SKILL_IMPORTED, APPROVAL_REQUIRED (kind skill_step)

Desktop skills (desktop_skills_hub.py, `hub.desktop`): "watch this on the desktop" / "record a desktop skill"
(and on Linux "watch what I do next", replacing the coordinate macro recorder; plain "watch this" too when Chrome
isn't connected), SKILL_RECORD_START/STOP {surface: "desktop"}, the same drafts, runs, approvals and fixes.
SKILL_IMPORT takes Playwright or plain Python (desktop_skill.import_python); SKILL_EXPORT {lang: "python"} of a
desktop skill is a plain Python script.

Approving a risky step is only possible from the desktop (HUD approval card, SKILL_STEP_DECISION or the HTTP
API, which refuses browser-extension origins): a web page or another extension can never approve it.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from skill_replay import SkillRun, SkillRunner
from skills_recorder import (SkillStore, apply_fix, build_draft, card_lines, export_playwright, finalize,
                             import_playwright, spot_patterns, step_signature, suggestion_card)
import module_guard

desktop_skill = module_guard.load("desktop_skill")   # [stability] a broken desktop part switches only itself off

log = logging.getLogger("orion.skills")

EXTENSION_EVENTS = {"SKILL_REC_STATE", "SKILL_REC_EVENT", "SKILL_STEP_RESULT", "SKILL_PICKED", "SKILL_ACTIVITY",
                    "SKILL_RUN_CONTROL"}
MAX_EVENTS = 3000
STEP_TIMEOUT_S = 60.0
MANUAL_TIMEOUT_S = 300.0
PICK_TIMEOUT_S = 300.0

START_RE = re.compile(r"^(?:watch this|watch me do this|record (?:a |this |my )?(?:browser )?skill|"
                      r"learn this(?: skill)?|record (?:this|what i do) in (?:the )?browser)\b", re.I)
STOP_RE = re.compile(r"^(?:stop (?:watching|recording)|done(?: watching| recording)?|that'?s it|finish (?:the )?skill|"
                     r"end (?:the )?(?:skill|recording))\b", re.I)
GO_RE = re.compile(r"^(?:go(?: ahead)?|start|run it|do it|run all)\b", re.I)
DESK_START_RE = re.compile(r"^(?:watch (?:this|me) on (?:the |my )?(?:desktop|computer|pc|screen)|"
                           r"record (?:a |this |my )?(?:desktop|app) skill|record (?:this|what i do) on (?:the )?desktop)\b",
                           re.I)
WATCH_NEXT_RE = re.compile(r"^watch (?:what i do|me)(?: next)?$", re.I)
RUN_RE = re.compile(r"^(?:run|replay|do) (?:the |my )?(?:skill )?(.+)$", re.I)


def _norm(text: str) -> str:
    t = " ".join(re.findall(r"[a-z0-9']+", text.lower()))
    return re.sub(r"^(hey )?(orion )?(please )?(can you |could you )?", "", t).strip()


class _NoDesktop:
    """Stands in for desktop skills when desktop_skills_hub failed to load (GET /health/full says why)."""
    OFF = "Desktop skills are switched off because that part of Orion failed to start. Browser skills still work."

    def __init__(self) -> None:
        self.runner = type("_R", (), {"runs": {}})()
        self.recorder = None

    def recording_view(self) -> None:
        return None

    def input_factory(self) -> None:
        return None

    async def start_recording(self) -> tuple[bool, str]:
        return False, self.OFF

    async def stop_recording(self) -> None:
        return None

    async def run(self, *a: Any, **k: Any) -> Any:
        raise RuntimeError(self.OFF)

    def control(self, run_id: str, action: str) -> None:
        return None


class SkillHub:
    def __init__(self, bus: Any, approvals: Any, data_dir: Path, *, daemon: Any = None) -> None:
        self._bus = bus
        self._approvals = approvals
        self.d = daemon
        self.store = SkillStore(data_dir)
        self.recordings: dict[str, dict[str, Any]] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.activity: deque[str] = deque(maxlen=400)
        self.suggested: set[str] = set()
        self.dismissed: set[str] = set()
        self.suggestions: list[dict[str, Any]] = []
        self.ext_client: Any = None
        self._waiters: dict[str, asyncio.Future] = {}
        self._approval_runs: dict[str, str] = {}
        self.runner = SkillRunner(exec_step=self._exec_step, ask_approval=self._ask_approval, ask_pick=self._ask_pick,
                                  save_fix=self._save_fix, on_update=self._on_run_update)
        hub_mod = module_guard.load("desktop_skills_hub")   # [stability] guarded: browser skills work without it
        self.desktop = (module_guard.call("desktop_skills_hub", lambda: hub_mod.DesktopSkills(self, data_dir), "load")
                        if hub_mod is not None else None) or _NoDesktop()

    # ================================================================ transport helpers
    @property
    def bus(self) -> Any:  # follow the daemon's current objects (tests and restarts may swap them)
        return getattr(self.d, "bus", None) or self._bus

    @property
    def approvals(self) -> Any:
        return getattr(self.d, "approvals", None) or self._approvals

    def _extension(self) -> Any:
        clients = [c for c in list(self.bus.clients.values()) if c.kind == "extension"]
        if self.ext_client in clients:
            return self.ext_client
        return clients[0] if clients else None

    async def _to_extension(self, event: dict[str, Any]) -> bool:
        ext = self._extension()
        if ext is None:
            return False
        return await self.bus.send(ext, event)

    async def _say(self, text: str, source: str = "user") -> None:
        ext = getattr(self.d, "ext", None) if self.d is not None else None
        if ext is not None and hasattr(ext, "_ack"):
            try:
                await ext._ack(text, source)
                return
            except Exception:
                log.exception("ack failed")
        await self.bus.log(text)

    def _future(self, key: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._waiters[key] = fut
        return fut

    def _resolve(self, key: str, value: Any) -> bool:
        fut = self._waiters.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(value)
            return True
        return False

    # ================================================================ state for UIs
    def state(self) -> dict[str, Any]:
        return {
            "skills": self.store.summaries(),
            "drafts": list(self.drafts.values()),
            "recording": next(({"rec_id": k, **{x: v.get(x) for x in ("url", "title", "paused", "reason")},
                                "events": len(v["events"]), "surface": "browser"}
                               for k, v in self.recordings.items() if v.get("active")),
                              None) or self.desktop.recording_view(),
            "runs": [r.view() for r in self.runner.runs.values() if r.status not in ("done", "failed", "stopped")] +
                    [{**r.view(), "surface": "desktop", "highlights": next((x["highlights"] for x in r.log
                                                                            if x.get("event") == "preview"), [])}
                     for r in self.desktop.runner.runs.values() if r.status not in ("done", "failed", "stopped")],
            "suggestions": [s for s in self.suggestions if s["id"] not in self.dismissed],
        }

    async def publish_list(self) -> None:
        await self.bus.publish({"event": "SKILLS", **self.state()})

    # ================================================================ recording
    async def start_recording(self) -> bool:
        ok = await self._to_extension({"event": "SKILL_RECORD_START"})
        if not ok:
            await self.bus.log("Open Chrome with the Orion Bridge extension to record a browser skill.", "warn")
        return ok

    async def stop_recording(self) -> bool:
        return await self._to_extension({"event": "SKILL_RECORD_STOP"})

    def _rec(self, rec_id: str) -> dict[str, Any]:
        rec_id = str(rec_id or "rec")[:40]
        if rec_id not in self.recordings:
            self.recordings[rec_id] = {"events": [], "url": "", "title": "", "active": True, "started_at": time.time()}
        return self.recordings[rec_id]

    async def _on_rec_state(self, msg: dict[str, Any]) -> None:
        state = str(msg.get("state", ""))
        rec_id = str(msg.get("rec_id") or "rec")[:40]
        rec = self._rec(rec_id)
        if state == "started":
            rec.update(active=True, url=str(msg.get("url", ""))[:2048], title=str(msg.get("title", ""))[:300],
                       paused=False)
        elif state in ("paused", "resumed"):
            rec.update(paused=state == "paused", reason=str(msg.get("reason") or ""))
        elif state == "stopped":
            rec["active"] = False
            await self.bus.publish({"event": "SKILL_RECORDING", "recording": False, "rec_id": rec_id})
            await self._finish_recording(rec_id)
            return
        await self.bus.publish({"event": "SKILL_RECORDING", "recording": rec.get("active", False), "rec_id": rec_id,
                                "paused": rec.get("paused", False), "reason": rec.get("reason", ""),
                                "url": rec.get("url"), "events": len(rec["events"])})

    async def _finish_recording(self, rec_id: str) -> Optional[dict[str, Any]]:
        rec = self.recordings.pop(rec_id, None)
        if rec is None:
            return None
        events = list(rec["events"])
        if rec.get("url") and not any(e.get("action") == "navigate" for e in events[:1]):
            first_t = min((float(e.get("t") or 0) for e in events), default=0.0)
            events.insert(0, {"action": "navigate", "url": rec["url"], "title": rec.get("title", ""), "t": first_t - 1})
        draft = build_draft(events, start_url=rec.get("url", ""), title=rec.get("title", ""))
        if not draft["steps"]:
            await self._say("I didn't catch any steps to save." +
                            (" Private fields and sign-in pages are never recorded." if draft["skipped_private"] else ""))
            return None
        self.drafts[draft["draft_id"]] = draft
        await self.bus.publish({"event": "SKILL_DRAFT", "draft": draft})
        await self._to_extension({"event": "SKILL_DRAFT_READY", "draft_id": draft["draft_id"],
                                  "steps": len(draft["steps"])})
        msg = f"Got it — {len(draft['steps'])} steps. Name the skill and check its variables in the card."
        if draft["skipped_private"]:
            msg += f" I left out {draft['skipped_private']} private thing(s) like passwords or codes."
        await self._say(msg)
        await self._note_activity(draft["steps"], draft.get("start_url", ""))
        return draft

    async def save_draft(self, draft_id: str, name: str, triggers: list[str],
                         variables: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise KeyError("draft not found")
        skill = finalize(draft, name, triggers, variables)
        saved = self.store.save(skill)
        self.drafts.pop(draft_id, None)
        await self.publish_list()
        await self._refresh_grimoire()
        return saved

    async def _refresh_grimoire(self) -> None:
        ext = getattr(self.d, "ext", None) if self.d is not None else None
        view = getattr(ext, "_grimoire_view", None)
        if callable(view):
            try:
                await self.bus.publish({"event": "GRIMOIRE", "grimoire": view()})
            except Exception:
                log.debug("grimoire refresh failed", exc_info=True)

    # ================================================================ patterns
    async def _note_activity(self, steps: list[dict[str, Any]], url: str = "") -> None:
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0] if url else ""
        for s in steps:
            if s.get("action") == "open":
                host = re.sub(r"^https?://(www\.)?", "", str(s.get("url", ""))).split("/")[0]
            self.activity.append(step_signature(s, host))
        for pattern in spot_patterns(list(self.activity)):
            card = suggestion_card(pattern)
            if card["id"] in self.suggested or card["id"] in self.dismissed:
                continue
            self.suggested.add(card["id"])
            self.suggestions.append(card)
            await self.bus.publish({"event": "SKILL_SUGGESTION", "card": card})

    # ================================================================ replay
    async def run_skill(self, skill_id: str, values: Optional[dict[str, str]] = None, *, mode: str = "step",
                        preview: bool = True, source: str = "user") -> SkillRun:
        skill = self.store.load(skill_id)
        if skill is None:
            raise KeyError("skill not found")
        if (frozen := __import__("freeze").blocked("replay skills")):  # kill switch (freeze.py)
            raise PermissionError(frozen)
        for r in list(self.runner.runs.values()) + list(self.desktop.runner.runs.values()):  # one run at a time
            if r.status not in ("done", "failed", "stopped"):
                self.control(r.id, "stop")
        if skill.get("surface") == "desktop":
            if not skill.get("trusted"):
                raise PermissionError("This skill was changed outside Orion (its signature doesn't match), "
                                      "so I won't run it. Record it again or re-save it from Orion.")
            run = await self.desktop.run(skill, values, mode=mode, preview=preview, source=source)
            await self._say(f"Ready to run “{skill.get('name')}” ({len(skill.get('steps') or [])} steps). Check the "
                            "steps in the Skills card, then press Start." if preview else
                            f"Running “{skill.get('name')}”.", source)
            return run
        run = self.runner.start(skill, values, mode=mode, preview=preview)
        if preview:
            await self._to_extension({"event": "SKILL_PREVIEW", "run_id": run.id, "name": skill.get("name"),
                                      "steps": run.steps()})
        await self._say(f"Ready to run “{skill.get('name')}” ({len(skill.get('steps') or [])} steps). "
                        "Check the highlighted steps, then press Start." if preview else
                        f"Running “{skill.get('name')}”.", source)
        return run

    async def run_from_entry(self, entry: dict[str, Any], source: str) -> None:
        try:
            await self.run_skill(str(entry["id"]).removeprefix("skill:"), source=source)
        except PermissionError as exc:
            await self._say(str(exc), source)
        except KeyError:
            await self._say("I couldn't find that skill.", source)

    def _cancel_waiters(self, run_id: str) -> None:
        for key in [k for k in self._waiters if k.startswith(run_id + ":")]:
            self._resolve(key, None)
        for apr_id, rid in list(self._approval_runs.items()):
            if rid == run_id:
                self._resolve("approval:" + apr_id, False)
                self.approvals.update(apr_id, status="rejected", error="Run stopped")

    def control(self, run_id: str, action: str) -> Optional[SkillRun]:
        if run_id in self.desktop.runner.runs:
            return self.desktop.control(run_id, action)
        run = self.runner.control(run_id, action)
        if action == "stop" and run is not None:
            self._cancel_waiters(run_id)
        return run

    async def _exec_step(self, run: SkillRun, i: int, step: dict[str, Any], approved: bool) -> dict[str, Any]:
        nonce = uuid.uuid4().hex[:8]
        key = f"{run.id}:{nonce}"
        fut = self._future(key)
        sent = await self._to_extension({"event": "SKILL_EXEC_STEP", "run_id": run.id, "index": i, "nonce": nonce,
                                         "step": step, "approved": approved, "total": len(run.skill["steps"]),
                                         "tab_id": run.tab_id})
        if not sent:
            self._waiters.pop(key, None)
            return {"ok": False, "error": "the Orion Bridge extension isn't connected"}
        manual = step.get("action") == "upload" or step.get("ask")
        try:
            res = await asyncio.wait_for(fut, MANUAL_TIMEOUT_S if manual else STEP_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._waiters.pop(key, None)
            return {"ok": False, "error": "the page didn't answer in time"}
        if res is None:
            return {"ok": False, "error": "stopped"}
        if res.get("tab_id") and run.tab_id is None:
            run.tab_id = res["tab_id"]
        return res

    async def _ask_approval(self, run: SkillRun, i: int, step: dict[str, Any], reason: str) -> bool:
        total = len(run.skill.get("steps") or [])
        item = self.approvals.add("skill_step", platform="orion", category="grimoire", author="Skill",
                                  channel=str(run.skill.get("name", "skill")),
                                  original=f"{run.skill.get('name')} · step {i + 1} of {total}",
                                  draft=f"{step.get('description')} — {reason}",
                                  run_id=run.id, step_index=i, skill_id=run.skill.get("id"))
        self._approval_runs[item["id"]] = run.id
        fut = self._future("approval:" + item["id"])
        await self.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
        try:
            return bool(await fut)
        finally:
            self._approval_runs.pop(item["id"], None)

    async def decide(self, item: dict[str, Any], decision: str) -> bool:
        """Approval decision for a skill_step item (HUD card / SKILL_STEP_DECISION / HTTP)."""
        claimed = self.approvals.claim(item["id"], "approved" if decision in ("approve", "edit") else "rejected")
        if claimed is None:
            return False
        claimed = self.approvals.update(item["id"], status=claimed["status"])
        await self.bus.publish({"event": "APPROVAL_RESOLVED", "item": claimed})
        return self._resolve("approval:" + item["id"], decision in ("approve", "edit"))

    async def _ask_pick(self, run: SkillRun, i: int, step: dict[str, Any]) -> Optional[dict[str, Any]]:
        nonce = uuid.uuid4().hex[:8]
        key = f"{run.id}:pick:{nonce}"
        fut = self._future(key)
        if not await self._to_extension({"event": "SKILL_PICK", "run_id": run.id, "index": i, "nonce": nonce,
                                         "step": step, "tab_id": run.tab_id}):
            self._waiters.pop(key, None)
            return None
        try:
            return await asyncio.wait_for(fut, PICK_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._waiters.pop(key, None)
            return None

    async def _save_fix(self, run: SkillRun, i: int, target: dict[str, Any], how: str) -> Optional[dict[str, Any]]:
        current = self.store.load(str(run.skill.get("id")))
        if current is None or not current.get("trusted"):
            return None
        saved = self.store.save(apply_fix(current, i, target, healed_kind=how))
        await self.bus.log(f"Skill “{saved.get('name')}”: saved a fix for step {i + 1} (now version {saved['version']}).")
        return saved

    async def _on_run_update(self, run: SkillRun) -> None:
        view = run.view()
        await self.bus.publish({"event": "SKILL_RUN_STATE", "run": view})
        await self._to_extension({"event": "SKILL_RUN_STATE", "run": view, "tab_id": run.tab_id})

    # ================================================================ commands
    async def handle_text(self, text: str, source: str) -> bool:
        if source not in ("user", "voice"):
            return False
        norm = _norm(text)
        recording = any(r.get("active") for r in self.recordings.values())
        desk_rec = self.desktop.recorder is not None and self.desktop.recorder.recording
        import sys
        if not recording and not desk_rec and (DESK_START_RE.match(norm) or (
                sys.platform.startswith("linux") and WATCH_NEXT_RE.match(norm)) or (
                START_RE.match(norm) and self._extension() is None and self.desktop.input_factory() is not None)):
            ok, msg = await self.desktop.start_recording()
            await self._say(msg + (" Passwords and private windows are never recorded." if ok else ""), source)
            return True
        if (STOP_RE.match(norm) or re.match(r"^stop watching", norm)) and desk_rec:
            await self.desktop.stop_recording()
            return True
        if START_RE.match(norm) and not recording:
            if await self.start_recording():
                await self._say("Watching — do the task in Chrome. Say “stop watching” or press Stop on the page "
                                "when you're done. Passwords, card numbers and codes are never recorded.", source)
            else:
                await self._say("I can't see Chrome — open it with the Orion Bridge extension first.", source)
            return True
        if STOP_RE.match(norm) and recording:
            await self.stop_recording()
            return True
        active = [r for r in self.runner.runs.values() if r.status in ("preview", "waiting_step")]
        if active and GO_RE.match(norm):
            self.control(active[-1].id, "run_all")
            return True
        skill = self._match_skill(norm)
        if skill is not None:
            try:
                await self.run_skill(skill["id"], source=source)
            except PermissionError as exc:
                await self._say(str(exc), source)
            return True
        return False

    def _match_skill(self, norm: str) -> Optional[dict[str, Any]]:
        candidates = [norm]
        m = RUN_RE.match(norm)
        if m:
            candidates.append(m.group(1).strip())
        for s in self.store.all():
            names = [_norm(t) for t in (s.get("voice_triggers") or [])] + [_norm(str(s.get("name", "")))]
            if any(c and c in names for c in candidates):
                return s
        return None

    async def dispatch(self, client: Any, kind: str, msg: dict[str, Any]) -> bool:
        send = lambda ev: self.bus.send(client, ev)  # noqa: E731
        if client.kind == "extension":
            if kind not in EXTENSION_EVENTS:
                await send({"event": "LOG", "level": "warn", "message": f"{kind} not allowed from extension"})
                return True
            self.ext_client = client
        if kind == "SKILL_REC_STATE":
            await self._on_rec_state(msg)
        elif kind == "SKILL_REC_EVENT":
            rec = self._rec(str(msg.get("rec_id") or "rec"))
            ev = msg.get("ev")
            if isinstance(ev, dict) and len(rec["events"]) < MAX_EVENTS:
                rec["events"].append(ev)
        elif kind == "SKILL_STEP_RESULT":
            self._resolve(f"{msg.get('run_id')}:{msg.get('nonce')}", {k: v for k, v in msg.items() if k != "event"})
        elif kind == "SKILL_PICKED":
            target = msg.get("target") if isinstance(msg.get("target"), dict) else None
            self._resolve(f"{msg.get('run_id')}:pick:{msg.get('nonce')}", target)
        elif kind == "SKILL_ACTIVITY":
            steps = [s for s in (msg.get("steps") or []) if isinstance(s, dict)][:50]
            for s in steps:
                s.pop("value", None)
            await self._note_activity(steps, str(msg.get("url") or ""))
        elif kind == "SKILL_RUN_CONTROL":
            self.control(str(msg.get("run_id", "")), str(msg.get("action", "")))
        elif kind == "SKILL_LIST":
            await send({"event": "SKILLS", **self.state()})
        elif kind == "SKILL_RECORD_START":
            if msg.get("surface") == "desktop":
                ok, text = await self.desktop.start_recording()
                await send({"event": "LOG", "level": "info" if ok else "warn", "message": text})
            else:
                await self.start_recording()
        elif kind == "SKILL_RECORD_STOP":
            if msg.get("surface") == "desktop" or (self.desktop.recorder and self.desktop.recorder.recording):
                await self.desktop.stop_recording()
            else:
                await self.stop_recording()
        elif kind == "SKILL_SAVE":
            try:
                saved = await self.save_draft(str(msg.get("draft_id", "")), str(msg.get("name", "")),
                                              [str(t) for t in msg.get("voice_triggers") or []],
                                              msg.get("variables") or [])
                await send({"event": "SKILL_SAVED", "skill": saved, "card": card_lines(saved)})
            except KeyError:
                await send({"event": "LOG", "level": "warn", "message": "That skill draft is gone."})
        elif kind == "SKILL_DISCARD":
            self.drafts.pop(str(msg.get("draft_id", "")), None)
            await self.publish_list()
        elif kind == "SKILL_DELETE":
            self.store.delete(str(msg.get("id", "")))
            await self.publish_list()
        elif kind == "SKILL_RUN":
            try:
                await self.run_skill(str(msg.get("id", "")), msg.get("values") or {}, mode=str(msg.get("mode", "step")),
                                     preview=msg.get("preview", True) is not False)
            except (KeyError, PermissionError) as exc:
                await send({"event": "LOG", "level": "warn", "message": str(exc).strip("'")})
        elif kind == "SKILL_STEP_DECISION":
            item = self.approvals.get(str(msg.get("id", "")))
            if item and item.get("kind") == "skill_step":
                await self.decide(item, str(msg.get("decision", "")))
        elif kind == "SKILL_EXPORT":
            skill = self.store.load(str(msg.get("id", "")))
            if skill:
                lang = str(msg.get("lang", "python"))
                await send({"event": "SKILL_EXPORTED", "id": skill["id"], "lang": lang,
                            "code": export_code(skill, lang)})
        elif kind == "SKILL_IMPORT":
            draft, skipped = import_code(str(msg.get("code", ""))[:200_000], str(msg.get("name") or "Imported skill"))
            self.drafts[draft["draft_id"]] = draft
            await send({"event": "SKILL_IMPORTED", "draft": draft, "skipped": skipped})
        elif kind == "SKILL_SUGGESTION_DISMISS":
            self.dismissed.add(str(msg.get("id", "")))
        else:
            return False
        return True


def export_code(skill: dict[str, Any], lang: str = "python") -> str:
    """Browser skills: a Playwright script (Python or JS). Desktop skills: a plain Python script."""
    if skill.get("surface") == "desktop":
        if desktop_skill is None:
            raise ValueError(_NoDesktop.OFF)
        return desktop_skill.export_python(skill)
    return export_playwright(skill, lang)


def import_code(code: str, name: str = "Imported skill") -> tuple[dict[str, Any], list[str]]:
    """Playwright scripts become browser skills; any other Python script becomes a desktop skill (parsed, never run)."""
    if desktop_skill is None or desktop_skill.looks_like_playwright(code):
        return import_playwright(code, name)
    return desktop_skill.import_python(code, name)


# ==================================================================== HTTP
class SaveDraftRequest(BaseModel):
    name: str
    voice_triggers: list[str] = []
    variables: list[dict[str, Any]] = []


class RunRequest(BaseModel):
    values: dict[str, str] = {}
    mode: str = "step"
    preview: bool = True


class ControlRequest(BaseModel):
    action: str


class ImportRequest(BaseModel):
    code: str
    name: str = "Imported skill"


def make_router(hub: SkillHub) -> APIRouter:
    r = APIRouter(prefix="/browser-skills", tags=["skills"])

    @r.get("")
    async def list_skills() -> dict[str, Any]:
        return hub.state()

    @r.post("/record/start")
    async def record_start() -> dict[str, Any]:
        return {"ok": await hub.start_recording()}

    @r.post("/record/stop")
    async def record_stop() -> dict[str, Any]:
        return {"ok": await hub.stop_recording()}

    @r.post("/drafts/{draft_id}/save")
    async def save(draft_id: str, req: SaveDraftRequest) -> dict[str, Any]:
        try:
            return await hub.save_draft(draft_id, req.name, req.voice_triggers, req.variables)
        except KeyError:
            raise HTTPException(404, "draft not found")

    @r.post("/drafts/{draft_id}/discard")
    async def discard(draft_id: str) -> dict[str, Any]:
        return {"ok": hub.drafts.pop(draft_id, None) is not None}

    @r.post("/import")
    async def import_script(req: ImportRequest) -> dict[str, Any]:
        draft, skipped = import_code(req.code[:200_000], req.name)
        hub.drafts[draft["draft_id"]] = draft
        return {"draft": draft, "skipped": skipped}

    @r.post("/suggestions/{suggestion_id}/dismiss")
    async def dismiss(suggestion_id: str) -> dict[str, Any]:
        hub.dismissed.add(suggestion_id)
        return {"ok": True}

    @r.post("/runs/{run_id}/control")
    async def control(run_id: str, req: ControlRequest) -> dict[str, Any]:
        run = hub.control(run_id, req.action)
        if run is None:
            raise HTTPException(404, "run not found")
        return run.view()

    @r.get("/{skill_id}")
    async def get_skill(skill_id: str) -> dict[str, Any]:
        skill = hub.store.load(skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        return {**skill, "card": card_lines(skill)}

    @r.post("/{skill_id}/delete")
    async def delete(skill_id: str) -> dict[str, Any]:
        ok = hub.store.delete(skill_id)
        await hub.publish_list()
        return {"ok": ok}

    @r.get("/{skill_id}/export")
    async def export(skill_id: str, lang: str = "python") -> PlainTextResponse:
        skill = hub.store.load(skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        return PlainTextResponse(export_code(skill, lang))

    @r.post("/{skill_id}/run")
    async def run(skill_id: str, req: RunRequest) -> dict[str, Any]:
        try:
            started = await hub.run_skill(skill_id, req.values, mode=req.mode, preview=req.preview)
        except KeyError:
            raise HTTPException(404, "skill not found")
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        return started.view()

    return r


def attach(daemon: Any, data_dir: Optional[Path] = None) -> APIRouter:
    """Create the hub on the daemon (daemon.skills) and return its HTTP router."""
    if data_dir is None:
        from config import settings
        data_dir = settings.data_dir
    hub = SkillHub(daemon.bus, daemon.approvals, Path(data_dir), daemon=daemon)
    daemon.skills = hub
    router = APIRouter()
    router.include_router(make_router(hub))
    if not isinstance(hub.desktop, _NoDesktop):
        module_guard.router(router, "desktop_skills_hub",
                            lambda: __import__("desktop_skills_hub").make_router(hub.desktop))
    return router
