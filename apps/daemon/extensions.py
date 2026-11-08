"""Feature Bible wiring: everything from the Day-One Preload Kit and the 7 domains, attached to the
daemon as one object so main.py stays readable.

  Preload kit ............ preload.py / harvester.py
  D1 Living Grimoire ..... grimoire.py, runner.py (self-healing), clipboard_alchemy.py, macro_recorder.py,
                           battle_stations.py
  D2 Comms ............... VIP satellites, muted threads + twice-daily digest, voice-to-reply
  D3 Deep work ........... devlog.py (git radar, devlog), cross-repo transplant (self_evolve generic mode)
  D4 Creative ............ creative.py (ideas, asset pipeline, producer HUD)
  D5 Health .............. pacing bands / pause-aware / roulette (pacing_tracker.py), hydration, wind-down
  D6 Life ................ runway.py, knowledge_vault.py
  D7 Training ............ learned preference rules (memory_core user_rules) injected into every prompt
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from agent_watch import AgentWatcher
from alignment_drafter import suggest_replies
from audio_cues import audio_cues
from media_session import MediaSession
from battle_stations import BattleStations
from capture_engine import active_window_title
from clipboard_alchemy import INSTRUCTIONS, ClipboardWatcher
from commands import Command, parse as parse_command
from config import settings
from discord_bridge import _norm_channel
from creative import MAX_UPLOAD, AssetPipeline, IdeaCatcher, Producer
from devlog import Devlog, radar
from grimoire import Grimoire
from harvester import harvest
from knowledge_vault import KnowledgeVault
from macro_recorder import MacroRecorder, describe, replayable
from pacing_tracker import Hydration, circadian_state
from preload import Preload, ingest_preload
from runner import ProcessRunner, is_destructive
from runway import Runway
from xp import XPLedger, momentum
from schemas import OrchestratorInput
from self_evolve import PatchError, SelfEvolveEngine

log = logging.getLogger("orion.ext")


def reveal_folder(path: Path) -> None:
    """Open one of Orion's own output folders in the OS file manager."""
    import os
    import subprocess
    import sys

    path = Path(path).resolve()
    if not str(path).startswith(str(Path(settings.data_dir).resolve())) or not path.is_dir():
        return
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

APPROVAL_KINDS = {"grimoire_run", "devlog"}
REPLY_TTL_S = 180.0
DEVLOG_MIN_SESSION_MIN = 45
DEVLOG_MIN_COMMITS = 3


class Extensions:
    def __init__(self, daemon: Any) -> None:
        self.d = daemon
        data = settings.data_dir
        self.preload = Preload(data, Path(__file__).resolve().parent, settings.repo_root)
        self.runner = ProcessRunner(daemon._publish_ui, daemon.orchestrator)
        self.grimoire = Grimoire(self.preload, data, daemon.memory.embedder)
        self.battle = BattleStations(self.preload, self.runner)
        self.recorder = MacroRecorder(active_window_title, on_autostop=self._on_macro_autostop)
        self.clipboard = ClipboardWatcher(self._on_clip_threadsafe)
        self.devlog = Devlog(data, daemon.orchestrator)
        self.ideas = IdeaCatcher(data, self.preload)
        self.assets = AssetPipeline(data, self.grimoire, self.runner)
        self.producer = Producer(data)
        self.hydration = Hydration(data / "hydration.json")
        self.runway = Runway(data)
        self.vault = KnowledgeVault(data)
        self.staged_reply: Optional[dict[str, Any]] = None
        self.pending_macro: Optional[dict[str, Any]] = None
        self.harvesting = False
        self.active_project: Optional[dict[str, Any]] = None
        self._digest_done: set[str] = set()
        self._last_digest_at = time.time()
        self._wind_down_date: Optional[str] = None
        self._last_vault_sync = 0.0
        self._session_peak_min = 0.0
        self._tasks: list[asyncio.Task] = []
        self.media = MediaSession()
        self.now_playing: Optional[dict[str, Any]] = None
        self.agent = AgentWatcher(workspaces=lambda: self.preload.workspaces)
        self.suggestions: dict[str, dict[str, Any]] = {}  # thread_key -> {for_id, replies, model}
        self.xp = XPLedger(data / "xp.json")
        self.momentum: dict[str, Any] = {"score": 0.0, "swarm": 0, "debris": []}
        self._focus_today: dict[str, Any] = {"date": "", "minutes": 0.0}
        self._commits_today: dict[str, Any] = {"date": "", "count": 0, "next": 0.0}

    # ================================================================ lifecycle
    async def start(self) -> None:
        self.d.pacing.log_path = settings.data_dir / "conditioning_log.json"
        self.apply_preload()
        try:
            await ingest_preload(self.preload, self.d.memory)
        except Exception:
            log.exception("Preload ingestion failed")
        if settings.clipboard_watch:
            self.clipboard.start()
        self._tasks = [asyncio.create_task(self._loop(), name="ext-loop"),
                       asyncio.create_task(self._fast_loop(), name="ext-fast")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self.clipboard.stop()
        self.recorder.stop()
        for run in list(self.runner.runs.values()):
            if run.ended_at is None:
                await self.runner.stop(run.id)

    def apply_preload(self) -> None:
        """Push the (possibly edited) seed files into the live subsystems."""
        deck = self.preload.conditioning
        self.d.pacing.configure(deck)
        self.hydration.goal = int(deck.get("hydration_goal_glasses") or 8)
        routing = self.preload.comms_routing
        self.d.comms.set_routing(routing)
        discord = routing.get("discord", {}) if isinstance(routing, dict) else {}
        if discord.get("triage_channels"):
            self.d.triage.channel_names |= {_norm_channel(str(c)) for c in discord["triage_channels"]}
        ids = discord.get("channel_ids") or {}
        if isinstance(ids, dict):
            self.d.triage.channel_ids |= {int(v) for v in ids.values() if str(v).isdigit()}

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                if self.preload.reload():
                    self.apply_preload()
                    await ingest_preload(self.preload, self.d.memory)
                    await self.d.bus.publish({"event": "PRELOAD_UPDATED", "summary": self.preload.summary(),
                                              "workspaces": self.preload.workspaces})
                if time.time() - self._last_vault_sync > 600:
                    self._last_vault_sync = time.time()
                    await self.vault.sync(self.d.memory)
                await self._update_momentum()
                await self._check_digest()
                await self._check_wind_down()
                await self._check_session_devlog()
                if self.staged_reply and time.time() - self.staged_reply["at"] > REPLY_TTL_S:
                    self.staged_reply = None
                    await self.d.bus.publish({"event": "VOICE_REPLY_CLEARED"})
            except Exception:
                log.exception("extension loop tick failed")

    async def _fast_loop(self) -> None:
        """Every 2.5 s: now-playing music and coding-agent activity (both cheap, both only publish on change)."""
        while True:
            await asyncio.sleep(2.5)
            try:
                if self.media.available:
                    np_ = await self.media.now_playing()
                    def key(d: Optional[dict[str, Any]]) -> tuple:
                        d = d or {}
                        return d.get("title"), d.get("playing"), d.get("artist")
                    if key(np_) != key(self.now_playing) or (np_ and self.now_playing and abs(
                            (np_.get("position") or 0) - (self.now_playing.get("position") or 0)) > 8):
                        self.now_playing = np_
                        await self.d.bus.publish({"event": "MEDIA_STATE", "media": np_}, record=False)
                changed = await asyncio.to_thread(self.agent.poll)
                if changed:
                    await self.d.bus.publish({"event": "AGENT_STATUS", "agent": changed}, record=False)
            except Exception:
                log.exception("fast loop tick failed")

    # ---------------------------------------------------------------- XP & momentum
    async def award(self, kind: str, count: int = 1, note: str = "") -> None:
        ev = self.xp.award(kind, count, note)
        if not ev:
            return
        await self.d.bus.publish({"event": "XP", "award": ev, "xp": self.xp.snapshot()}, record=False)
        if ev.get("level_up"):
            snap = self.xp.snapshot()
            unlocked = [s["name"] for s in snap["skins"] if s["level"] == ev["level_up"]]
            line = f"Level {ev['level_up']}!" + (f" You unlocked the {unlocked[0]} skin." if unlocked else "")
            await self._ack(line, "voice")

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def add_focus_minutes(self, minutes: float) -> None:
        if self._focus_today["date"] != self._today():
            self._focus_today = {"date": self._today(), "minutes": 0.0}
        self._focus_today["minutes"] += max(0.0, minutes)

    async def _commits_since_midnight(self) -> int:
        from devlog import git

        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        total = 0
        for ws in self.preload.workspaces:
            if ws.get("exists") and (Path(ws["path"]) / ".git").exists():
                out = await asyncio.to_thread(git, ws["path"], "rev-list", "--count", f"--since={midnight}", "HEAD")
                total += int(out) if str(out).strip().isdigit() else 0
        return total

    async def _update_momentum(self) -> None:
        now = time.time()
        if self._commits_today["date"] != self._today():
            self._commits_today = {"date": self._today(), "count": 0, "next": 0.0}
        if now >= self._commits_today["next"]:
            self._commits_today["next"] = now + 300
            count = await self._commits_since_midnight()
            new = count - self._commits_today["count"]
            # the first count after start-up (or midnight) is a baseline, so restarts never re-award old commits
            if new > 0 and self._commits_today.get("baselined"):
                await self.award("commit", new, f"{new} commit{'s' if new != 1 else ''}")
            self._commits_today["count"] = max(count, self._commits_today["count"])
            self._commits_today["baselined"] = True
        pacing = self.d.pacing.snapshot()
        live = pacing["session_minutes"] if pacing["phase"] in ("focus", "warming", "break_due") else 0
        if self._focus_today["date"] != self._today():
            self._focus_today = {"date": self._today(), "minutes": 0.0}
        failed = sum(1 for r in self.runner.runs.values()
                     if r.ended_at and r.exit_code not in (0, None) and now - r.ended_at < 86400)
        night = self.circadian()["phase"] == "night" and pacing["phase"] in ("focus", "warming", "break_due")
        nxt = momentum(focus_minutes_today=self._focus_today["minutes"] + live,
                       commits_today=self._commits_today["count"], hydration=self.hydration.snapshot(),
                       approvals_pending=len(self.d.approvals.pending()), failed_runs=failed, night_activity=night)
        if nxt != self.momentum:
            self.momentum = nxt

    # ---------------------------------------------------------------- Sanctuary
    async def on_break(self, routine: Optional[str], session_minutes: float) -> None:
        self.add_focus_minutes(session_minutes)
        if session_minutes >= 60:
            await self.award("focus_block", 1, f"{round(session_minutes)} min focus")

    async def sanctuary_done(self, routine: str, completed: bool) -> None:
        if completed:
            await self.award("recovery", 1, routine)
        else:
            self.d.pacing.mark_last_skipped()

    # ---------------------------------------------------------------- dropped documents -> vault + briefing
    async def vault_drop(self, filename: str, data: bytes) -> dict[str, Any]:
        import re as _re

        from knowledge_vault import _read

        safe = _re.sub(r"[^\w.\- ]+", "_", Path(filename).name)[:120] or "document.txt"
        dest = self.vault.dir / "dropped" / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, data)
        await self.vault.sync(self.d.memory)
        text = (await asyncio.to_thread(_read, dest)) or ""
        briefing = ""
        if text.strip():
            try:
                briefing, _ = await self.d.orchestrator.frontier_text(
                    "Give a spoken 20-second briefing (about 50 words) of this document for a busy maker: what it "
                    "is, the key points, anything to act on. Plain sentences, no lists or markdown.",
                    text[:14000], max_tokens=160, temperature=0.3, timeout=45.0)
            except Exception:
                sentences = _re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
                briefing = " ".join(sentences[:3])[:400]
        briefing = briefing.strip() or f"I saved {safe} to your knowledge vault."
        await self.d.bus.publish({"event": "BRIEFING", "name": safe, "text": briefing})
        asyncio.create_task(self.d.speak_and_stream(briefing))
        return {"name": safe, "briefing": briefing}

    async def suggest_for(self, thread_key: str, client: Any = None) -> Optional[dict[str, Any]]:
        """Quick replies in the user's own style for the latest incoming message (cached per message)."""
        thread = self.d.comms.threads.get(thread_key)
        last = self.d.comms.last_inbound(thread_key)
        if not thread or not last:
            return None
        cached = self.suggestions.get(thread_key)
        if cached and cached.get("for_id") == last["id"]:
            result = cached
        else:
            out = await suggest_replies(self.d.drafter, platform=thread["platform"], author=last["author"],
                                        text=last["text"], thread_context=self.d.comms.context_for(thread_key)[:-1],
                                        style_samples=self.d.comms.style_samples())
            result = {"for_id": last["id"], "replies": out["replies"], "model": out["model"]}
            self.suggestions[thread_key] = result
            if len(self.suggestions) > 60:
                self.suggestions.pop(next(iter(self.suggestions)))
        ev = {"event": "REPLY_SUGGESTIONS", "thread_key": thread_key, **result}
        if client is not None:
            await self.d.bus.send(client, ev)
        else:
            await self.d.bus.publish(ev, record=False)
        return result

    # ================================================================ snapshots
    def telemetry(self) -> dict[str, Any]:
        return {
            "preload": self.preload.summary(),
            "hydration": self.hydration.snapshot(),
            "circadian": self.circadian(),
            "recording_macro": self.recorder.recording,
            "producer": self._producer_state(),
            "active_station": self.battle.active,
            "runs_active": sum(1 for r in self.runner.runs.values() if r.ended_at is None),
            "momentum": self.momentum,
        }

    async def hello(self) -> dict[str, Any]:
        return {
            "workspaces": self.preload.workspaces,
            "stations": [{"name": s["name"], "auto": bool(s.get("auto"))} for s in self.battle.stations()],
            "grimoire": self._grimoire_view(),
            "runs": self.runner.list(),
            "ideas": self.ideas.recent(),
            "rules": self.d.memory.list_rules(),
            "staged_reply": self.staged_reply,
            "producer": self._producer_state(),
            "assets": list(self.assets.assets.values())[-20:],
            "media": self.now_playing,
            "agent": self.agent.state,
            "xp": self.xp.snapshot(),
            "location": self.location(),
            "active_venture": (self.active_project or {}).get("id"),
        }

    def _grimoire_view(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in e.items() if k not in ("steps", "snippet")} | {"has_steps": bool(e.get("steps"))}
                for e in self.grimoire.entries()]

    def _producer_state(self) -> Optional[dict[str, Any]]:
        s = self.producer.session
        if not s:
            return None
        return {**s, "elapsed": round(self.producer.elapsed(), 1)}

    # ---------------------------------------------------------------- Sun & Moon
    def _location_path(self) -> Path:
        return settings.data_dir / "location.json"

    def location(self) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(self._location_path().read_text(encoding="utf-8"))
            return {"lat": float(data["lat"]), "lon": float(data["lon"]), "label": str(data.get("label", ""))[:60]}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def set_location(self, lat: float, lon: float, label: str = "") -> None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("latitude/longitude out of range")
        self._location_path().write_text(json.dumps({"lat": round(lat, 3), "lon": round(lon, 3), "label": label[:60]}),
                                         encoding="utf-8")

    def circadian(self) -> dict[str, Any]:
        deck = self.preload.conditioning
        return circadian_state(str(deck.get("bedtime") or "23:00"), int(deck.get("wind_down_minutes") or 90),
                               location=self.location())

    def pause_signal(self) -> bool:
        cap = self.d.capture
        t = getattr(cap, "last_focus_change_ts", None) if cap else None
        return bool(t and time.time() - t < 20)

    async def rules_for(self, query: str) -> list[str]:
        try:
            return await self.d.memory.rules_for(query)
        except Exception:
            return []

    async def _say(self, text: str, source: str) -> None:
        if source == "voice" and text:
            asyncio.create_task(self.d.speak_and_stream(text))

    async def _ack(self, text: str, source: str, *, level: str = "info") -> None:
        """Short confirmation in the chat stream (and spoken when the request was spoken)."""
        await self.d.bus.publish({
            "event": "ORCHESTRATOR_RESPONSE", "request_id": uuid.uuid4().hex[:12], "trigger": source,
            "prompt": "", "response": {"intent": "automation", "tier": "local", "summary": text, "actions": [],
                                       "model": "orion-command", "latency_ms": 0},
        })
        await self._say(text, source)
        if level != "info":
            await self.d.bus.log(text, level)

    # ================================================================ command router
    async def handle_text(self, text: str, source: str) -> bool:
        """Deterministic commands first. Returns True when handled (skip the LLM)."""
        if source not in ("user", "voice"):
            return False
        skills = getattr(self.d, "skills", None)  # browser Skill Recorder: "watch this", skill voice triggers
        if skills is not None and await skills.handle_text(text, source):
            return True
        # exact Grimoire voice trigger ("clear stuck ports")
        hit = self.grimoire.match_trigger(text)
        if hit:
            await self.run_entry(hit, source)
            return True
        cmd = parse_command(text, reply_pending=self.staged_reply is not None, recording=self.recorder.recording)
        if cmd is None:
            return False
        try:
            return await self._execute(cmd, source)
        except Exception as exc:
            log.exception("command %s failed", cmd.kind)
            await self._ack(f"That didn't work: {exc}", source, level="warn")
            return True

    def _on_macro_autostop(self, steps: list[dict[str, Any]]) -> None:
        loop = self.d.loop
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._macro_finished(steps, "voice"), loop)

    async def _macro_finished(self, steps: list[dict[str, Any]], source: str) -> None:
        await self.d.bus.publish({"event": "MACRO_STATE", "recording": False})
        if not steps:
            await self._ack("I didn't catch any actions to save.", source)
            return
        self.pending_macro = {"id": f"macro-{uuid.uuid4().hex[:6]}", "steps": steps, "summary": describe(steps)}
        await self.d.bus.publish({"event": "MACRO_RECORDED", **self.pending_macro})
        await self._ack(f"Got it — {len(replayable(steps))} steps. What voice trigger should I bind it to? "
                        "Name it in the card.", source)

    async def _execute(self, cmd: Command, source: str) -> bool:
        a = cmd.args
        k = cmd.kind
        if k == "macro_start":
            ok, msg = self.recorder.start()
            await self.d.bus.publish({"event": "MACRO_STATE", "recording": self.recorder.recording, "message": msg})
            await self._ack(msg, source)
            return True
        if k == "macro_stop":
            if not self.recorder.recording and self.pending_macro:
                await self._ack("Already stopped (10-minute limit) — name the macro in the card.", source)
                return True
            await self._macro_finished(self.recorder.stop(), source)
            return True
        if k == "idea":
            rec = self.ideas.log(a)
            await self.award("idea", 1, rec["text"][:60])
            await self.d.commit("idea", f"[{rec['kind']} idea · {rec['project']}] {rec['text']}")
            await self.d.bus.publish({"event": "IDEA_LOGGED", "idea": rec})
            await self._ack(f"Logged to {rec['project']}: {rec['text'][:80]}", source)
            return True
        if k == "battle":
            station = self.battle.find(a["name"])
            if station is None and a["name"].lower() in {"podcast", "recording", "show", "stream", "producer"}:
                return await self._execute(Command("producer_start", {"title": ""}), source)
            if station is None:
                return False  # "switch to dark mode" etc. — let the orchestrator handle it
            await self.engage_station(station, source)
            return True
        if k == "grimoire":
            entry = await self.grimoire.resolve(a["query"])
            if entry is None:
                return False  # not a known script: let the orchestrator answer normally
            await self.run_entry(entry, source)
            return True
        if k == "voice_reply":
            await self.stage_reply(a["name"], a.get("platform"), a["text"], source)
            return True
        if k == "reply_send":
            await self.send_staged(source)
            return True
        if k == "reply_cancel":
            self.staged_reply = None
            await self.d.bus.publish({"event": "VOICE_REPLY_CLEARED"})
            await self._ack("Cancelled — nothing sent.", source)
            return True
        if k == "devlog":
            asyncio.create_task(self.draft_devlog(source, manual=True))
            await self._ack("Reading your commits across all projects — the devlog draft will land in the queue.", source)
            return True
        if k == "harvest_code":
            asyncio.create_task(self.cross_repo(a, source))
            return True
        if k == "producer_start":
            s = self.producer.start(a.get("title", ""))
            await self.d.bus.publish({"event": "PRODUCER_STATE", "producer": self._producer_state()})
            await self._ack(f"Producer mode on — {s['title']}. Say “mark that” for highlights.", source)
            return True
        if k == "producer_stop":
            path = self.producer.stop()
            await self.d.bus.publish({"event": "PRODUCER_STATE", "producer": None, "notes_path": str(path) if path else None})
            await self._ack(f"Producer mode off. Notes saved to {path}" if path else "Producer mode wasn't on.", source)
            return True
        if k == "highlight":
            if self.producer.highlight(a.get("note", "")):
                await self.d.bus.publish({"event": "PRODUCER_STATE", "producer": self._producer_state()})
                await audio_cues.emit("command_detected")
                return True
            return False
        if k == "topic":
            if self.producer.topic(a["title"]):
                await self.d.bus.publish({"event": "PRODUCER_STATE", "producer": self._producer_state()})
                return True
            return False
        if k == "water":
            n = self.hydration.log_glass()
            await self.award("water")
            await self.d.bus.publish({"event": "HYDRATION", "hydration": self.hydration.snapshot()})
            await self._ack(f"Logged — {n} of {self.hydration.goal} glasses today.", source)
            return True
        if k == "harvest":
            asyncio.create_task(self.run_harvest())
            await self._ack("Harvesting your projects, scripts and docs — about 30 seconds.", source)
            return True
        if k == "rule":
            rec = await self.d.memory.add_rule(a["text"], source="learned", tier=1)
            await self.d.bus.publish({"event": "RULES", "rules": self.d.memory.list_rules()})
            await self._ack("Noted — I'll follow that from now on." if rec else "I already have that rule.", source)
            return True
        return False

    # ================================================================ D1 Grimoire
    async def run_entry(self, entry: dict[str, Any], source: str, *, approved: bool = False) -> None:
        kind = entry["kind"]
        if kind == "snippet":
            await self.d.bus.publish({"event": "SNIPPET", "entry": entry})
            await self._ack(f"Snippet “{entry['description']}” is ready to copy.", source)
            return
        if kind == "userscript":
            await self.d.bus.publish({"event": "SNIPPET", "entry": {**entry, "snippet": Path(entry["path"]).read_text(
                encoding="utf-8", errors="replace")[:20000]}})
            await self._ack("Userscripts run in your browser — I've put it on the copy card.", source)
            return
        if kind == "skill":  # browser skill: risky steps ask for approval one by one during the run
            await self.d.skills.run_from_entry(entry, source)
            return
        needs = entry.get("requires_approval") or is_destructive(entry.get("command", ""))
        if needs and not approved:
            item = self.d.approvals.add("grimoire_run", platform="orion", category="grimoire", author="Scripts",
                                        entry_id=entry["id"], original=entry["description"],
                                        draft=entry.get("command") or describe(entry.get("steps") or []),
                                        destructive=is_destructive(entry.get("command", "")))
            await self.d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
            await self._ack(f"“{entry['description'][:60]}” needs your OK — it's in the queue.", source)
            return
        if kind == "internal":
            action = entry.get("internal_action", "")
            if "consolidate" in action:
                await self._ack("Consolidating memories and exporting training data…", source)
                asyncio.create_task(self.d.consolidate())
            return
        if kind == "workflow":
            steps = replayable(entry.get("steps") or [])
            await self._ack(f"Running macro “{entry['description'][:60]}” ({len(steps)} steps).", source)
            asyncio.create_task(self.d._execute_screen_workflow(steps, uuid.uuid4().hex[:8]))
            return
        cwd = entry.get("cwd") or (self.active_project or {}).get("path") or str(settings.repo_root)
        run = await self.runner.start(entry["command"], cwd=cwd, label=entry["description"][:60])
        await self._ack(f"Running {entry['description'][:70]}…", source)
        if source == "voice":
            asyncio.create_task(self._announce_when_done(run.id))

    async def _announce_when_done(self, run_id: str) -> None:
        for _ in range(1800):
            run = self.runner.runs.get(run_id)
            if not run or run.ended_at:
                break
            await asyncio.sleep(0.5)
        run = self.runner.runs.get(run_id)
        if run and run.ended_at:
            if run.exit_code == 0:
                await self._say("Done.", "voice")
            else:
                diag = (run.diagnosis or {}).get("diagnosis", "It failed.")
                await self._say(f"That failed. {diag} Check the Tools tab for a fix.", "voice")

    async def engage_station(self, station: dict[str, Any], source: str) -> None:
        report = await self.battle.engage(station)
        ws = report.get("workspace")
        if ws:
            self.active_project = ws
            hits = await self.d.memory.query_memory(f"{ws['name']} project status plans todo", top_k=8)
            radar_info = await radar([ws])
            await self.d.bus.publish({"event": "PROJECT_CONTEXT", "workspace": ws, "memories": hits,
                                      "git": radar_info[0] if radar_info else None})
        if report.get("producer_mode") and not self.producer.session:
            self.producer.start(station["name"])
            await self.d.bus.publish({"event": "PRODUCER_STATE", "producer": self._producer_state()})
        await self.d.bus.publish({"event": "BATTLE_STATION", "report": {k: v for k, v in report.items() if k != "workspace"},
                                  "station": station["name"]})
        bits = []
        if report["closed"]:
            bits.append(f"closed {len(report['closed'])} apps")
        if report["servers"]:
            bits.append(f"started {len(report['servers'])} server{'s' if len(report['servers']) != 1 else ''}")
        if ws:
            bits.append(f"opened {ws['name']}")
        if report.get("skipped"):
            bits.append(f"skipped {len(report['skipped'])} destructive command(s) — run those from Scripts")
        await self._ack(f"{station['name']} mode engaged" + (": " + ", ".join(bits) if bits else "") + ".", source)

    # ---------------------------------------------------------------- clipboard
    def _on_clip_threadsafe(self, suggestion: dict[str, Any]) -> None:
        loop = self.d.loop
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.d.bus.publish(suggestion), loop)

    async def clipboard_action(self, sid: str, action: str, description: str = "") -> None:
        item = self.clipboard.take(sid)
        if not item:
            await self.d.bus.log("That clipboard suggestion expired.", "warn")
            return
        if action == "save_snippet":
            lang = {"json": "json", "sql": "sql", "stack_trace": "text"}.get(item["kind"], "")
            entry = self.grimoire.save_snippet(item["text"], description or f"{item['kind']} snippet "
                                               f"{datetime.now():%b %d %H:%M}", lang)
            await self.d.bus.publish({"event": "GRIMOIRE", "grimoire": self._grimoire_view()})
            await self._ack(f"Saved snippet “{entry['description']}” to Scripts.", "user")
            return
        instruction = INSTRUCTIONS.get(action)
        if instruction:
            await self.d.run_orchestrator(OrchestratorInput(source="user", content=item["text"][:12000],
                                                            instruction=instruction), trigger="user")

    # ---------------------------------------------------------------- macro save
    async def save_macro(self, name: str, triggers: list[str]) -> None:
        if not self.pending_macro:
            return
        entry = self.grimoire.save_workflow(name, self.pending_macro["steps"], triggers,
                                            f"Recorded macro: {self.pending_macro['summary'][:120]}")
        self.pending_macro = None
        await self.d.bus.publish({"event": "GRIMOIRE", "grimoire": self._grimoire_view()})
        await self._ack(f"Macro saved. Say “{entry['voice_triggers'][0]}” to replay it.", "user")

    # ================================================================ D2 comms
    async def stage_reply(self, name: str, platform: Optional[str], text: str, source: str) -> None:
        thread = self.d.comms.find_thread(name, platform)
        if not thread:
            where = f" on {platform.title()}" if platform else ""
            await self._ack(f"I couldn't find a conversation with {name}{where} yet.", source)
            return
        self.staged_reply = {"id": f"vr-{uuid.uuid4().hex[:6]}", "thread_key": thread["key"],
                             "name": thread["thread_name"], "platform": thread["platform"], "text": text,
                             "at": time.time()}
        await self.d.bus.publish({"event": "VOICE_REPLY_STAGED", "reply": self.staged_reply})
        await self._say(f"Message to {thread['thread_name']} on {thread['platform'].title()}: {text}. "
                        "Say send it, or cancel.", "voice")

    async def send_staged(self, source: str) -> None:
        staged, self.staged_reply = self.staged_reply, None
        if not staged:
            return
        ok, detail, _ = await self.d.comms.reply(staged["thread_key"], staged["text"])
        await self.d.bus.publish({"event": "VOICE_REPLY_CLEARED", "sent": ok, "detail": detail})
        await self._ack(f"Sent to {staged['name']}." if ok else f"Couldn't send: {detail}", source,
                        level="info" if ok else "warn")

    async def _check_digest(self) -> None:
        """Fire each digest slot once per day, even if a slow loop tick skipped the exact minute."""
        now = datetime.now()
        times = self.preload.comms_routing.get("digest_times", ["12:00", "18:00"]) or []
        for slot in times:
            try:
                h, m = (int(x) for x in str(slot).split(":", 1))
            except ValueError:
                continue
            due = now.replace(hour=h % 24, minute=m % 60, second=0, microsecond=0)
            key = f"{now:%Y-%m-%d} {slot}"
            # only within 2 h of the slot, so opening the app at night doesn't replay the whole day's digests
            if due <= now < due + timedelta(hours=2) and key not in self._digest_done:
                self._digest_done.add(key)
                await self.compile_digest()

    async def compile_digest(self) -> Optional[dict[str, Any]]:
        since, self._last_digest_at = self._last_digest_at, time.time()
        msgs = self.d.comms.messages_since(since, muted_only=True)
        if not msgs:
            return None
        lines = [f"[{thread}] {m['author']}: {m['text'][:200]}" for thread, ms in msgs.items() for m in ms[-40:]]
        summary = ""
        try:
            summary, _ = await self.d.orchestrator.frontier_text(
                "Summarise these low-priority group-chat messages for a busy developer as exactly 3 short bullets "
                "(what matters, anything needing a reply, anything time-sensitive). Plain text bullets only.",
                "\n".join(lines)[:12000], max_tokens=300, temperature=0.3, timeout=60.0)
        except Exception:
            summary = "\n".join(f"• {t}: {len(ms)} new message{'s' if len(ms) != 1 else ''}" for t, ms in msgs.items())
        digest = {"at": time.time(), "threads": list(msgs.keys()), "count": sum(len(v) for v in msgs.values()),
                  "summary": summary.strip()}
        await self.d.bus.publish({"event": "COMMS_DIGEST", "digest": digest})
        await self.d.commit("consolidated", f"Comms digest {datetime.now():%H:%M}: {digest['summary'][:1500]}")
        return digest

    # ================================================================ D3 deep work
    async def draft_devlog(self, source: str = "user", manual: bool = False) -> None:
        try:
            draft = await self.devlog.draft(self.preload.workspaces, await self.rules_for("devlog community update"))
        except Exception as exc:
            await self._ack(f"Couldn't draft the devlog: {exc}", source, level="warn")
            return
        if not draft:
            if manual:
                await self._ack("No new commits since the last devlog.", source)
            return
        item = self.d.approvals.add("devlog", platform="discord_server", category="devlog", author="Orion",
                                    original=f"{draft['commit_count']} commits across {', '.join(draft['projects'])}",
                                    draft=draft["discord_update"], changelog=draft["changelog"],
                                    title=draft["title"], channel="#devlog")
        self.devlog.mark()
        await self.d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
        await self.d.commit("devlog", f"{draft['title']}\n{draft['changelog'][:3000]}")
        await audio_cues.emit("approval_required", detail="devlog draft")

    async def _check_session_devlog(self) -> None:
        if not settings.devlog_auto:
            return
        snap = self.d.pacing.snapshot()
        if snap["phase"] in ("focus", "warming", "break_due"):
            self._session_peak_min = max(self._session_peak_min, snap["session_minutes"])
            return
        if self._session_peak_min >= DEVLOG_MIN_SESSION_MIN:
            self._session_peak_min = 0.0
            commits = await self.devlog.collect(self.preload.workspaces)
            if sum(len(v) for v in commits.values()) >= DEVLOG_MIN_COMMITS:
                await self.draft_devlog("routine")
        else:
            self._session_peak_min = 0.0

    async def cross_repo(self, args: dict[str, Any], source: str) -> None:
        def find(name: str) -> Optional[dict[str, Any]]:
            n = name.lower()
            for ws in self.preload.workspaces:
                labels = [ws["name"].lower(), ws["id"].lower(), ws["id"].replace("-", " ")]
                if ws["exists"] and any(n == lab or n in lab or lab in n for lab in labels):
                    return ws
            return None

        src, dst = find(args["source"]), find(args["target"])
        if not src or not dst:
            missing = args["source"] if not src else args["target"]
            await self._ack(f"I don't know a project called “{missing}” — add it to workspace_manifest.json or run "
                            "a harvest.", source)
            return
        if src["id"] == dst["id"]:
            await self._ack("Source and target are the same project.", source)
            return
        await self.d.bus.publish({"event": "SELF_EVOLVE_STATUS", "stage": "planning", "request": args["request"]})
        await self._ack(f"Pulling {args['what']} from {src['name']} into {dst['name']} — the patch will be in the "
                        "queue.", source)
        engine = SelfEvolveEngine(self.d.orchestrator, Path(dst["path"]), settings.data_dir,
                                  source_roots={src["id"]: Path(src["path"])}, label=dst["name"])
        engine.proposals = self.d.evolver.proposals
        try:
            proposal = await engine.propose(args["request"], trigger=source)
        except (PatchError, Exception) as exc:
            await self.d._set_evolve_status({"event": "SELF_EVOLVE_STATUS", "stage": "failed",
                                             "request": args["request"], "error": str(exc)[:500]})
            return
        item = self.d.approvals.add("self_evolve", id=proposal["id"], request=args["request"], patch=proposal,
                                    author="Orion", category="self_evolve", original=args["request"],
                                    draft=proposal["summary"], platform="orion")
        await self.d._set_evolve_status({"event": "SELF_EVOLVE_STATUS", "stage": "ready", "request": args["request"],
                                         "patch_id": proposal["id"]})
        await self.d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})

    # ================================================================ D5 health
    async def _check_wind_down(self) -> None:
        state = self.circadian()
        today = datetime.now().strftime("%Y-%m-%d")
        if state["phase"] == "wind_down" and self._wind_down_date != today:
            self._wind_down_date = today
            await self.d.bus.publish({"event": "WIND_DOWN", "circadian": state})
            asyncio.create_task(self.d.speak_and_stream(
                "The sun's down. I'm warming the screen and holding non-urgent alerts until morning."
                if state.get("trigger") == "sunset" else
                f"Heads up — bedtime is {state['bedtime']}. I'm warming the screen down; good time to wrap up."))

    # ================================================================ preload / harvest
    async def run_harvest(self) -> None:
        if self.harvesting:
            return
        self.harvesting = True

        async def progress(msg: str, frac: float) -> None:
            await self.d.bus.publish({"event": "HARVEST_PROGRESS", "message": msg, "progress": frac})

        try:
            result = await harvest(self.preload, self.d.memory, self.d.orchestrator, progress=progress)
            self.apply_preload()
            await self.d.bus.publish({"event": "HARVEST_RESULT", "result": result, "workspaces": self.preload.workspaces,
                                      "grimoire": self._grimoire_view()})
        except Exception as exc:
            log.exception("harvest failed")
            await self.d.bus.publish({"event": "HARVEST_RESULT", "error": str(exc)})
        finally:
            self.harvesting = False

    # ================================================================ approvals
    async def decide(self, item: dict[str, Any], decision: str, text: str) -> None:
        if decision == "reject":
            item = self.d.approvals.update(item["id"], status="rejected")
            await self.d.bus.publish({"event": "APPROVAL_RESOLVED", "item": item})
            return
        claimed = self.d.approvals.claim(item["id"], "sending")
        if claimed is None:
            return
        if item["kind"] == "grimoire_run":
            entry = self.grimoire.get(item.get("entry_id", ""))
            if entry is None:
                item = self.d.approvals.update(item["id"], status="pending", error="That saved script no longer exists.")
            else:
                if decision == "edit" and text.strip() and entry["kind"] not in ("workflow", "internal"):
                    entry = {**entry, "command": text.strip()}
                await self.run_entry(entry, "user", approved=True)
                item = self.d.approvals.update(item["id"], status="sent", final=entry.get("command"))
        elif item["kind"] == "devlog":
            final = (text.strip() if decision == "edit" and text.strip() else item.get("draft", "")).strip()
            channel_id = (self.preload.comms_routing.get("discord", {}) or {}).get("devlog_channel_id")
            posted = False
            if channel_id and self.d.triage.client:
                posted = await self.d.triage.send_reply({"channel_id": int(channel_id), "message_id": 0}, final)
            item = self.d.approvals.update(item["id"], status="sent", final=final, posted=posted,
                                           error=None if posted or not channel_id else "Discord post failed")
            await self.d.drafter.record(platform="discord_server", category="devlog", system="devlog",
                                        user_prompt=item.get("original", ""), draft=item.get("draft", ""),
                                        final=final, decision="approved")
        await self.d.bus.publish({"event": "APPROVAL_RESOLVED", "item": item})

    # ================================================================ websocket events
    async def dispatch(self, client: Any, kind: str, msg: dict[str, Any]) -> bool:
        send = lambda ev: self.d.bus.send(client, ev)  # noqa: E731
        if kind == "GRIMOIRE_LIST":
            await send({"event": "GRIMOIRE", "grimoire": self._grimoire_view()})
        elif kind == "GRIMOIRE_SEARCH":
            await send({"event": "GRIMOIRE_RESULTS", "query": msg.get("query", ""),
                        "results": await self.grimoire.search(str(msg.get("query", "")), 8)})
        elif kind == "GRIMOIRE_RUN":
            entry = self.grimoire.get(str(msg.get("id", "")))
            if entry:
                await self.run_entry(entry, "user")
        elif kind == "RUN_STOP":
            await self.runner.stop(str(msg.get("run_id", "")))
        elif kind == "RUN_FIX":
            await self.runner.fix_and_restart(str(msg.get("run_id", "")))
        elif kind == "RUNS_LIST":
            await send({"event": "RUNS", "runs": self.runner.list()})
        elif kind == "CLIPBOARD_ACTION":
            await self.clipboard_action(str(msg.get("id", "")), str(msg.get("action", "")), str(msg.get("description", "")))
        elif kind == "MACRO_START":
            await self._execute(Command("macro_start"), "user")
        elif kind == "MACRO_STOP":
            await self._execute(Command("macro_stop"), "user")
        elif kind == "MACRO_SAVE":
            triggers = [str(t) for t in msg.get("triggers", []) if str(t).strip()]
            await self.save_macro(str(msg.get("name", "macro")), triggers)
        elif kind == "MACRO_DISCARD":
            self.pending_macro = None
        elif kind == "BATTLE_STATION":
            station = self.battle.find(str(msg.get("name", "")))
            if station:
                await self.engage_station(station, "user")
        elif kind == "WORKSPACE_LAUNCH":
            ws = next((w for w in self.preload.workspaces if w["id"] == msg.get("id")), None)
            if ws:
                station = next((s for s in self.battle.stations() if s.get("workspace_id") == ws["id"]), None)
                await self.engage_station(station or {"name": ws["name"], "workspace_id": ws["id"], "auto": True}, "user")
        elif kind == "GIT_RADAR":
            await send({"event": "GIT_RADAR", "repos": await radar(self.preload.workspaces)})
        elif kind == "DEVLOG_REQUEST":
            asyncio.create_task(self.draft_devlog("user", manual=True))
        elif kind == "VOICE_REPLY_SEND":
            await self.send_staged("user")
        elif kind == "VOICE_REPLY_CANCEL":
            self.staged_reply = None
            await self.d.bus.publish({"event": "VOICE_REPLY_CLEARED"})
        elif kind == "COMMS_MUTE":
            routing = self.d.comms.set_muted(str(msg.get("thread_key", "")), bool(msg.get("muted")))
            self.preload.save_comms_routing(routing)
            await self.d.bus.publish({"event": "COMMS_ROUTING", "routing": routing,
                                      "threads": self.d.comms.snapshot(per_thread=1)})
        elif kind == "DIGEST_REQUEST":
            digest = await self.compile_digest()
            if not digest:
                await send({"event": "COMMS_DIGEST", "digest": {"at": time.time(), "threads": [], "count": 0,
                                                                "summary": "Nothing new in muted threads."}})
        elif kind == "IDEAS_LIST":
            await send({"event": "IDEAS", "ideas": self.ideas.recent()})
        elif kind == "ASSET_ACTION":
            try:
                result = await self.assets.run(str(msg.get("asset_id", "")), str(msg.get("action", "")))
                await send({"event": "ASSET_RESULT", "asset_id": msg.get("asset_id"), "result": result})
            except Exception as exc:
                await send({"event": "ASSET_RESULT", "asset_id": msg.get("asset_id"), "error": str(exc)[:300]})
        elif kind == "ASSET_REVEAL":
            asset = self.assets.assets.get(str(msg.get("asset_id", "")))
            if asset:
                target = Path(asset["outputs"][-1]["path"]).parent if asset["outputs"] else Path(asset["path"]).parent
                reveal_folder(target)
        elif kind == "PRODUCER":
            action = str(msg.get("action", ""))
            mapping = {"start": Command("producer_start", {"title": str(msg.get("title", ""))}),
                       "stop": Command("producer_stop"), "highlight": Command("highlight", {"note": str(msg.get("note", ""))}),
                       "topic": Command("topic", {"title": str(msg.get("title", ""))})}
            if action in mapping:
                await self._execute(mapping[action], "user")
        elif kind == "HYDRATION_LOG":
            self.hydration.log_glass(int(msg.get("delta", 1)) or 1)
            await self.award("water")
            await self.d.bus.publish({"event": "HYDRATION", "hydration": self.hydration.snapshot()})
        elif kind == "RUNWAY_GET":
            await send({"event": "RUNWAY", "runway": await self.runway.summary()})
        elif kind == "RUNWAY_SAVE":
            self.runway.save(msg.get("runway") or {})
            await send({"event": "RUNWAY", "runway": await self.runway.summary()})
        elif kind == "VAULT_LIST":
            await send({"event": "VAULT", "dir": str(self.vault.dir), "files": self.vault.files()})
        elif kind == "VAULT_SYNC":
            result = await self.vault.sync(self.d.memory)
            await send({"event": "VAULT", "dir": str(self.vault.dir), "files": self.vault.files(), "synced": result})
        elif kind == "RULES_LIST":
            await send({"event": "RULES", "rules": self.d.memory.list_rules()})
        elif kind == "RULE_ADD":
            await self.d.memory.add_rule(str(msg.get("text", "")), source="learned", tier=1)
            await self.d.bus.publish({"event": "RULES", "rules": self.d.memory.list_rules()})
        elif kind == "RULE_DELETE":
            await self.d.memory.delete_rule(str(msg.get("id", "")))
            await self.d.bus.publish({"event": "RULES", "rules": self.d.memory.list_rules()})
        elif kind == "HARVEST_REQUEST":
            asyncio.create_task(self.run_harvest())
        elif kind == "MEDIA_CONTROL":
            action = str(msg.get("action", ""))
            if action in ("toggle", "next", "previous") and await self.media.control(action):
                await asyncio.sleep(0.4)
                self.now_playing = await self.media.now_playing()
                await self.d.bus.publish({"event": "MEDIA_STATE", "media": self.now_playing}, record=False)
        elif kind == "SUGGEST_REPLIES":
            await self.suggest_for(str(msg.get("thread_key", "")), client)
        elif kind == "LOCATION_SET":
            try:
                self.set_location(float(msg.get("lat")), float(msg.get("lon")), str(msg.get("label", "")))
            except (TypeError, ValueError) as exc:
                await send({"event": "LOG", "level": "warn", "message": f"Couldn't save location: {exc}"})
            await self.d.bus.publish({"event": "CIRCADIAN", "circadian": self.circadian(), "location": self.location()})
        elif kind == "SANCTUARY_DONE":
            await self.sanctuary_done(str(msg.get("routine", "")), bool(msg.get("completed")))
        elif kind == "XP_SKIN":
            if self.xp.set_skin(str(msg.get("skin", ""))):
                await self.d.bus.publish({"event": "XP", "xp": self.xp.snapshot()}, record=False)
        elif kind == "ACTIVE_VENTURE":
            ws = next((w for w in self.preload.workspaces if w["id"] == msg.get("id")), None)
            if ws:
                self.active_project = ws
                hits = await self.d.memory.query_memory(f"{ws['name']} project status plans todo", top_k=8)
                radar_info = await radar([ws])
                await self.d.bus.publish({"event": "PROJECT_CONTEXT", "workspace": ws, "memories": hits,
                                          "git": radar_info[0] if radar_info else None})
        elif kind == "PRELOAD_INFO":
            await send({"event": "PRELOAD_UPDATED", "summary": self.preload.summary(),
                        "workspaces": self.preload.workspaces})
        else:
            return False
        return True

    # ================================================================ producer silent cards
    async def on_transcript(self, text: str) -> None:
        if not self.producer.may_show_card() or len(text) < 12:
            return
        hits = await self.d.memory.query_memory(text, top_k=3)
        hits = [h for h in hits if h["source"] in ("docs", "vault", "idea", "preload") and h["score"] > 0.35]
        if hits:
            title = hits[0]["content"].splitlines()[0][:100]
            self.producer.card_shown(title)
            await self.d.bus.publish({"event": "PRODUCER_CARD", "title": title, "hits": hits})

    # ================================================================ HTTP upload helper
    def store_asset(self, filename: str, data: bytes) -> dict[str, Any]:
        if len(data) > MAX_UPLOAD:
            raise ValueError("file too large (300 MB max)")
        return self.assets.store(filename, data)


__all__ = ["Extensions", "APPROVAL_KINDS"]
