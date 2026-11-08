"""Toolkit, daemon side: Project Runbook (10), Release Butler (11), DB Time Machine (12), Raid Night Autopilot (24).

One object (OpsToolkit) builds the four tools, registers their boardroom task types and executors, adds their
phrases to quick commands ("Orion, run OpenWoW", "release Orion 1.1", "what changed in the world DB today?",
"raid night is Fridays at 7"), and runs Raid Night's clock. Every decision that is public, pushes, overwrites data
or runs something risky is sensitive (always asks, capped at autonomy level 1).

HTTP routes (local only, like the rest of the daemon):

    GET  /runbook/projects                     projects Orion knows and their steps
    POST /runbook/learn    {project}           what Orion would write (diff); nothing is written
    POST /runbook/propose  {project}           write/update justfile + .mise.toml (asks first; see runbook.py)
    POST /runbook/run      {project, step}     run / test / build / deploy / lint through Orion's runner
    POST /release/start    {project, version, dry_run?, wait?}
    GET  /release, /release/{id}               recent releases / one release with its steps
    POST /release/{id}/publish                 ask to make the drafted GitHub release public
    GET  /dbtm/connections, /dbtm/snapshots?connection=
    POST /dbtm/snapshot    {connection?, tables?, reason?}
    POST /dbtm/compare     {snapshot}          row-level changes since a snapshot, in plain words
    POST /dbtm/changes     {connection?}       "what changed today?"
    POST /dbtm/rollback    {snapshot, table?}  asks you first (one tap on the phone)
    GET  /raid                                 schedule, next raid, last checklist, tonight's log
    POST /raid/config      {schedule?: "Fridays at 7 pm", enabled?, discord_channel_id?, ...}
    POST /raid/check                           run the checklist now (drafts the post, asks before posting)
    POST /raid/log         {text}              add a line to tonight's log
    POST /raid/notes                           draft the post-raid notes now
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import boardroom
from boardroom import TaskType

import db_time_machine as dbtm_mod
import raid_night as raid_mod
import release_butler as rel_mod
import runbook as rb_mod

log = logging.getLogger("orion.ops_toolkit")

# Each module registers its own task types when imported (above); these are the keys.
TASK_KEYS = (rb_mod.TASK_WRITE, rb_mod.TASK_RUN, rel_mod.TASK_PUSH, rel_mod.TASK_PUBLISH, dbtm_mod.TASK_ROLLBACK,
             raid_mod.TASK_ANNOUNCE, raid_mod.TASK_NOTES)
TASK_TYPES: tuple[TaskType, ...] = tuple(boardroom.TASK_TYPES[k] for k in TASK_KEYS)


def _notify(kind: str) -> Callable[[str, str], None]:
    def notify(title: str, body: str) -> None:
        try:
            import alerts
            alerts.emit(kind, title, body)
        except Exception:
            pass
        try:
            import linux_desktop
            linux_desktop.notify(title, body)
        except Exception:
            pass
    return notify


# =====================================================================================  quick-command plugins
class _Plugin:
    def __init__(self, name: str, match: Callable[[str], Optional[str]], run: Callable[[str], Awaitable[str]]):
        self.name, self.match, self.run = name, match, run


class OpsToolkit:
    def __init__(self, daemon: Any, data_dir: Any = None) -> None:
        from config import settings
        self.d = daemon
        data_dir = Path(data_dir or settings.data_dir)
        self.repo_root = Path(settings.repo_root)
        board = getattr(daemon, "board", None)
        ext = getattr(daemon, "ext", None)
        runner = getattr(ext, "runner", None)
        self._jobs: set[asyncio.Task] = set()
        self.runbook = rb_mod.Runbook(data_dir, board=board, runner=runner, grimoire=self._grimoire,
                                      notify=_notify("system"))
        self.butler = rel_mod.ReleaseButler(data_dir, board=board, proof=self._proof(), notify=_notify("system"))
        self.dbtm = dbtm_mod.TimeMachine(data_dir, board=board, notify=_notify("system"))
        dbtm_mod.CURRENT = self.dbtm
        wow = getattr(daemon, "wow", None)
        self.raid = raid_mod.RaidNight(
            data_dir, board=board,
            server=raid_mod.default_server_check(wow),
            backup=raid_mod.default_backup_check(daemon, lambda: self.raid.config()["backup_target"]),
            patch=raid_mod.default_patch_check(self._proof(), self._raid_project,
                                               lambda: self.raid.config()["patch_branch"]),
            poster=self._post_discord, restart=self._propose_restart, lag_probe=self._lag_probe,
            notify=_notify("openwow"))
        if wow is not None and self.raid.crash_seen not in wow.on_crash:
            wow.on_crash.append(self.raid.crash_seen)
        if board is not None:
            for t in TASK_TYPES:
                board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (t.key,))
            board.executors[rb_mod.TASK_WRITE] = self.runbook.execute_write
            board.executors[rb_mod.TASK_RUN] = lambda dd: self.runbook.execute_run(dd, self.spawn)
            board.executors[rel_mod.TASK_PUSH] = lambda dd: self.butler.execute_push(dd, self.spawn)
            board.executors[rel_mod.TASK_PUBLISH] = lambda dd: self.butler.execute_publish(dd, self.spawn)
            board.executors[dbtm_mod.TASK_ROLLBACK] = lambda dd: self.dbtm.execute_rollback(dd, self.spawn)
            board.executors[raid_mod.TASK_ANNOUNCE] = lambda dd: self.raid.execute_post(dd, self.spawn, "announce")
            board.executors[raid_mod.TASK_NOTES] = lambda dd: self.raid.execute_post(dd, self.spawn, "notes")
            if self.butler.on_decided not in board.on_decided:
                board.on_decided.append(self.butler.on_decided)
        quick = getattr(daemon, "quick", None)
        if quick is not None and hasattr(quick, "plugins"):
            for p in self.plugins():
                if all(x.name != p.name for x in quick.plugins):
                    quick.plugins.append(p)

    # ------------------------------------------------------------ wiring helpers
    def spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)   # type: ignore[arg-type]
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def _proof(self) -> Any:
        tk = getattr(self.d, "agent_toolkit", None)
        return getattr(tk, "proof", None)

    def _grimoire(self) -> list[dict[str, Any]]:
        g = getattr(getattr(self.d, "ext", None), "grimoire", None)
        try:
            return list(g.entries()) if g is not None else []
        except Exception:
            return []

    def _raid_project(self) -> Optional[Path]:
        return self.runbook.find(self.raid.config().get("project") or "openwow")

    async def _post_discord(self, channel_id: str, text: str) -> bool:
        bot = getattr(self.d, "discord_bot", None)
        client = getattr(bot, "client", None)
        if client is None:
            return False
        ch = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
        for i in range(0, len(text), 1990):
            await ch.send(text[i:i + 1990])
        return True

    async def _propose_restart(self, why: str) -> None:
        board = getattr(self.d, "board", None)
        if board is None or "wow.restart" not in boardroom.TASK_TYPES:
            return
        if any(x["task_type"] == "wow.restart" for x in board.pending()):
            return
        await board.propose("wow.restart", "OpenWoW went down during raid night. Restart it?",
                            {"project": "openwow", "during": "raid night", "state": why[:200]},
                            proposer="Raid Night Autopilot", risk="low", simulated=False)

    async def _lag_probe(self) -> Optional[float]:
        perf = getattr(self.d, "perf", None) or getattr(self.d, "perf_sentinel", None)
        for name in ("server_lag_ms", "tick_ms", "lag_ms"):
            fn = getattr(perf, name, None) if perf is not None else None
            if fn is not None:
                try:
                    v = fn()
                    v = await v if asyncio.iscoroutine(v) else v
                    return float(v) if v is not None else None
                except Exception:
                    return None
        wow = getattr(self.d, "wow", None)
        if wow is None or not getattr(wow, "enabled", False):
            return None
        _, out = await wow._run(wow.wow, "status", timeout=15)
        m = re.search(r"\b(?:tick|lag|latency)\b[^\d\n]{0,20}(\d+(?:\.\d+)?)\s*ms", out, re.I)
        return float(m.group(1)) if m else None

    def project(self, raw: str) -> Path:
        """A project by name ("OpenWoW") or path, from the folders Orion knows."""
        raw = (raw or "").strip()
        if raw.lower() in ("orion", "orion ai", "orionos"):
            return self.repo_root
        p = self.runbook.find(raw)
        if p is not None:
            return p
        if raw:
            from mcp_tools import McpConfig, ToolError, resolve_project
            try:
                return resolve_project(raw, McpConfig.load())
            except ToolError:
                pass
        raise rb_mod.RunbookError(f"I can't find a project called “{raw}”. Put it in ~/projects, or add its folder "
                                  "to the allowed roots in ~/.config/orion/mcp.json." if raw else
                                  "Say which project.")

    def start(self) -> None:
        self.raid.start()

    async def stop(self) -> None:
        await self.raid.stop()
        for t in list(self._jobs):
            t.cancel()
        if dbtm_mod.CURRENT is self.dbtm:
            dbtm_mod.CURRENT = None

    # ------------------------------------------------------------ phrases
    def plugins(self) -> list[_Plugin]:
        def m_runbook(t: str) -> Optional[str]:
            hit = self.runbook.match_phrase(t)
            if hit:
                return f"{hit[0]}|{hit[1]}"
            m = re.match(r"^(?:learn|make a runbook for|set up the runbook for|runbook for) (.+)$", t)
            if m and self.runbook.find(m.group(1)):
                return f"learn|{self.runbook.find(m.group(1))}"
            return None

        async def r_runbook(arg: str) -> str:
            verb, _, path = arg.partition("|")
            project = Path(path)
            try:
                if verb == "learn":
                    return (await self.runbook.propose(project, "you"))["message"]
                res = await self.runbook.run(project, verb)
                if justfile_missing(project):
                    await self.runbook.propose(project, "you")   # first time: offer the justfile (asks first)
                    res["message"] += " I've also drafted a justfile for it; approve it on your phone."
                return res["message"]
            except rb_mod.RunbookError as exc:
                return str(exc)

        def m_release(t: str) -> Optional[str]:
            # quick commands turn "1.1" into "1 1" (punctuation becomes spaces), so accept both
            m = re.match(r"^(dry run )?release (.+?) v?(\d+(?:[ .]\d+){1,2})( dry run)?$", t)
            if m:
                version = m.group(3).replace(" ", ".")
                return f"{'dry' if (m.group(1) or m.group(4)) else 'real'}|{m.group(2)}|{version}"
            m = re.match(r"^publish the (.+?) release$", t)
            if m:
                return f"publish|{m.group(1)}|"
            return None

        async def r_release(arg: str) -> str:
            mode, name, version = arg.split("|")
            try:
                if mode == "publish":
                    rec = self.butler.latest_drafted("" if name in ("latest", "new") else name)
                    if rec is None:
                        return f"There's no drafted {name} release waiting."
                    await self.butler.ask_publish(rec["id"])
                    return f"Asked on your phone: publish {rec['name']} {rec['version']}?"
                project = self.project(name)
                rec = await self.butler.start(project, version, dry_run=mode == "dry", wait=mode == "dry")
                if mode == "dry":
                    return rel_mod.ReleaseButler.describe(rec)
                return (f"Getting {rec['name']} {rec['version']} ready: changelog, version bump, full tests, signing. "
                        "Nothing leaves this computer until you approve it on your phone.")
            except (rel_mod.ReleaseError, rb_mod.RunbookError) as exc:
                return str(exc)

        def m_db(t: str) -> Optional[str]:
            m = re.match(r"^what (?:has )?changed in (?:the )?(.+?) (?:db|database)(?: today)?$", t)
            if m:
                return f"changes|{m.group(1)}"
            m = re.match(r"^(?:snapshot|back up|take a snapshot of) (?:the )?(.+?) (?:db|database)$", t)
            if m:
                return f"snapshot|{m.group(1)}"
            return None

        async def r_db(arg: str) -> str:
            what, _, word = arg.partition("|")
            conn = self.dbtm.find_connection(word)
            try:
                if what == "changes":
                    return await self.dbtm.changes_today(conn)
                if conn is None:
                    return "I couldn't match that to one of your staging databases."
                meta = await self.dbtm.snapshot(conn, reason="asked by you", kind="manual")
                return f"Snapshot {meta['id']} of {meta['database']} taken ({len(meta['tables'])} tables)."
            except dbtm_mod.DbtmError as exc:
                return str(exc)
            except Exception as exc:
                return f"The database didn't answer ({type(exc).__name__})."

        def m_raid(t: str) -> Optional[str]:
            m = re.match(r"^raid night is (.+)$", t)
            if m:
                return f"set|{m.group(1)}"
            if re.match(r"^(raid (night )?(check|checklist)|is everything ready for (the )?raid( night)?|"
                        r"check (for )?raid night)$", t):
                return "check|"
            if re.match(r"^(raid (night )?status|when is raid night)$", t):
                return "status|"
            if re.match(r"^(raid night is off|stop raid night|no raid night)$", t):
                return "off|"
            return None

        async def r_raid(arg: str) -> str:
            what, _, rest = arg.partition("|")
            try:
                if what == "set":
                    cfg = self.raid.set_config({"schedule": rest})
                    return (f"Raid night is {raid_mod.describe_schedule(cfg)}. I'll run the checks "
                            f"{int(cfg['lead_minutes']) // 60} hours before and draft the Discord post for your OK.")
                if what == "off":
                    self.raid.set_config({"enabled": False})
                    return "Raid night autopilot is off."
                if what == "check":
                    res = await self.raid.run_checklist(force=False)
                    return res["summary"] + " The announcement is waiting for your OK on your phone."
                return self.raid.describe()
            except ValueError as exc:
                return str(exc)

        return [_Plugin("runbook", m_runbook, r_runbook), _Plugin("release", m_release, r_release),
                _Plugin("dbtm", m_db, r_db), _Plugin("raid", m_raid, r_raid)]


def justfile_missing(project: Path) -> bool:
    return rb_mod.justfile_path(project) is None


# =====================================================================================  routes
def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["toolkit"])

    def tk() -> OpsToolkit:
        t = getattr(get_daemon(), "ops_toolkit", None)
        if t is None:
            raise RuntimeError("starting")
        return t

    def err(msg: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=status)

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    async def guarded(fn: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await fn()
        except RuntimeError as exc:
            if str(exc) == "starting":
                return err("Orion is still starting.", 503)
            raise
        except (rb_mod.RunbookError, rel_mod.ReleaseError, dbtm_mod.DbtmError, ValueError) as exc:
            return err(str(exc))

    # ---------------------------------------------------------- runbook
    @router.get("/runbook/projects")
    async def rb_projects() -> Any:
        async def go() -> Any:
            t = tk()
            out = []
            for p in t.runbook.projects():
                cmds = rb_mod.detect_commands(p, t._grimoire())
                out.append({"name": p.name, "path": str(p), "has_justfile": not justfile_missing(p),
                            "steps": {v: c["command"] for v, c in cmds.items()}})
            return {"projects": out}
        return await guarded(go)

    @router.post("/runbook/learn")
    async def rb_learn(request: Request) -> Any:
        b = await body_of(request)
        return await guarded(lambda: _async(tk().runbook.learn(tk().project(str(b.get("project") or "")))))

    @router.post("/runbook/propose")
    async def rb_propose(request: Request) -> Any:
        b = await body_of(request)
        return await guarded(lambda: tk().runbook.propose(tk().project(str(b.get("project") or "")),
                                                          str(b.get("requested_by") or "you")[:60]))

    @router.post("/runbook/run")
    async def rb_run(request: Request) -> Any:
        b = await body_of(request)
        step = str(b.get("step") or b.get("recipe") or "run").lower()
        if step not in rb_mod.VERBS:
            return err(f"Step must be one of: {', '.join(rb_mod.VERBS)}.")
        return await guarded(lambda: tk().runbook.run(tk().project(str(b.get("project") or "")), step,
                                                      str(b.get("requested_by") or "you")[:60]))

    # ---------------------------------------------------------- release
    @router.post("/release/start")
    async def rel_start(request: Request) -> Any:
        b = await body_of(request)

        async def go() -> Any:
            t = tk()
            project = t.project(str(b.get("project") or ""))
            dry = bool(b.get("dry_run"))
            rec = await t.butler.start(project, str(b.get("version") or ""), dry_run=dry,
                                       wait=bool(b.get("wait", dry)))
            return {"release": rec, "text": rel_mod.ReleaseButler.describe(rec)}
        return await guarded(go)

    @router.get("/release")
    async def rel_list(limit: int = 10) -> Any:
        return await guarded(lambda: _async({"releases": tk().butler.recent(max(1, min(limit, 50)))}))

    @router.get("/release/{rid}")
    async def rel_one(rid: str) -> Any:
        async def go() -> Any:
            rec = tk().butler.get(rid)
            if rec is None:
                return err("No such release.", 404)
            return {"release": rec, "text": rel_mod.ReleaseButler.describe(rec)}
        return await guarded(go)

    @router.post("/release/{rid}/publish")
    async def rel_publish(rid: str) -> Any:
        async def go() -> Any:
            rec = await tk().butler.ask_publish(rid)
            return {"release": rec, "decision_id": rec.get("publish_decision")}
        return await guarded(go)

    # ---------------------------------------------------------- DB time machine
    @router.get("/dbtm/connections")
    async def db_conns() -> Any:
        return await guarded(lambda: _async({"connections": tk().dbtm.connections()}))

    @router.get("/dbtm/snapshots")
    async def db_snaps(connection: str = "", limit: int = 30) -> Any:
        return await guarded(lambda: _async({"snapshots": tk().dbtm.snapshots(connection or None)[:max(1, min(limit, 200))]}))

    @router.post("/dbtm/snapshot")
    async def db_snapshot(request: Request) -> Any:
        b = await body_of(request)
        tables = b.get("tables")
        tables = [str(x) for x in tables][:200] if isinstance(tables, list) else None
        return await guarded(lambda: _snapshot(tk(), str(b.get("connection") or "") or None, tables,
                                               str(b.get("reason") or "")))

    @router.post("/dbtm/compare")
    async def db_compare(request: Request) -> Any:
        b = await body_of(request)
        return await guarded(lambda: tk().dbtm.compare(str(b.get("snapshot") or "")))

    @router.post("/dbtm/changes")
    async def db_changes(request: Request) -> Any:
        b = await body_of(request)

        async def go() -> Any:
            t = tk()
            conn = t.dbtm.find_connection(str(b.get("connection") or ""))
            return {"connection": conn, "text": await t.dbtm.changes_today(conn)}
        return await guarded(go)

    @router.post("/dbtm/rollback")
    async def db_rollback(request: Request) -> Any:
        b = await body_of(request)
        return await guarded(lambda: tk().dbtm.request_rollback(str(b.get("snapshot") or ""),
                                                                str(b.get("table") or "") or None,
                                                                str(b.get("requested_by") or "you")[:60]))

    # ---------------------------------------------------------- raid night
    @router.get("/raid")
    async def raid_status() -> Any:
        return await guarded(lambda: _async({**tk().raid.status(), "text": tk().raid.describe()}))

    @router.post("/raid/config")
    async def raid_config(request: Request) -> Any:
        b = await body_of(request)
        return await guarded(lambda: _async({"config": tk().raid.set_config(b), "text": tk().raid.describe()}))

    @router.post("/raid/check")
    async def raid_check() -> Any:
        return await guarded(lambda: tk().raid.run_checklist())

    @router.post("/raid/log")
    async def raid_log(request: Request) -> Any:
        b = await body_of(request)
        text = " ".join(str(b.get("text") or "").split())[:300]
        if not text:
            return err("text is required")

        async def go() -> Any:
            st = tk().raid.status()
            tk().raid.log(st["raid"], text)
            return {"ok": True}
        return await guarded(go)

    @router.post("/raid/notes")
    async def raid_notes() -> Any:
        return await guarded(lambda: tk().raid.draft_notes(tk().raid.status()["raid"]))

    return router


async def _async(v: Any) -> Any:
    return v


async def _snapshot(t: OpsToolkit, conn: Optional[str], tables: Optional[list[str]], reason: str) -> Any:
    ok, why = t.dbtm.available()
    if not ok:
        raise dbtm_mod.DbtmError(f"Can't take a snapshot: {why}.")
    meta = await t.dbtm.snapshot(conn, tables, reason=reason or "asked for", kind="manual")
    return {"snapshot": meta}


# =====================================================================================  MCP tools
def mcp_tools(tools: Any) -> list[Any]:
    """Tool definitions for mcp_tools.OrionTools (imported lazily there)."""
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    S = {"type": "string"}
    B = {"type": "boolean"}
    L = {"type": "array", "items": {"type": "string"}}
    PROJECT = {**S, "description": "Project folder (absolute, or relative to the first allowed root)"}

    async def call(method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: float = 30) -> dict:
        try:
            status, data = await tools.backend.call(method, path, body, timeout=timeout)
        except DaemonUnavailable:
            raise ToolError("Orion isn't running, so this can't be done right now.")
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        return data

    async def runbook_list(a: dict[str, Any]) -> str:
        project = tools.project(a)
        lines = [f"{project.name}:"]
        cmds = rb_mod.detect_commands(project)
        for v in rb_mod.VERBS:
            if v in cmds:
                lines.append(f"- {v}: `{cmds[v]['command']}` (from {cmds[v]['source']})")
        if len(lines) == 1:
            lines.append("No start/test/build/deploy steps found yet.")
        tv = rb_mod.detect_tools(project)
        if tv:
            lines.append("Tool versions: " + ", ".join(f"{k} {v['version']}" for k, v in sorted(tv.items())))
        lines.append("Run one with runbook_run(step=...).")
        return tools.out("\n".join(lines))

    async def runbook_run(a: dict[str, Any]) -> str:
        project = tools.project(a)
        step = (a.get("step") or "test").lower()
        if step not in rb_mod.VERBS:
            raise ToolError(f"step must be one of: {', '.join(rb_mod.VERBS)}")
        try:
            data = await call("POST", "/runbook/run", {"project": str(project), "step": step,
                                                       "requested_by": a.get("requested_by") or "Coding agent (MCP)"})
        except ToolError as exc:
            if "isn't running" not in str(exc):
                raise
            from config import settings
            book = rb_mod.Runbook(Path(settings.data_dir))
            try:
                command, note = book.command_for(project, step)
            except rb_mod.RunbookError as e:
                raise ToolError(str(e))
            from runner import is_destructive
            if step == "deploy" or is_destructive(command) or is_destructive(note):
                raise ToolError("That step needs the user's approval, and Orion isn't running to ask. Nothing ran.")
            run = await tools.backend.local_runner().start(command, cwd=str(project), label=f"{project.name}: {step}",
                                                           kind="task")
            tools._runs_local.add(run.id)
            return f"Started `{command}` here (run {run.id}). Follow it with runner_status / runner_logs."
        if data.get("state") == "waiting":
            return (f"{data.get('message')} Decision {data.get('decision_id')}: check it with "
                    f"approval_status(decision_id=\"{data.get('decision_id')}\").")
        run = data.get("run") or {}
        return f"{data.get('message')} Run {run.get('id')}: follow it with runner_status / runner_logs."

    async def release_plan(a: dict[str, Any]) -> str:
        project = tools.project(a)
        data = await call("POST", "/release/start", {"project": str(project), "version": a["version"],
                                                     "dry_run": True}, timeout=60)
        return tools.out(data.get("text") or "")

    async def release_start(a: dict[str, Any]) -> str:
        project = tools.project(a)
        data = await call("POST", "/release/start", {"project": str(project), "version": a["version"],
                                                     "wait": False}, timeout=60)
        r = data.get("release") or {}
        return (f"Release {r.get('id')} started for {r.get('name')} {r.get('version')} in a separate release branch. "
                "It runs the full tests, then asks the user before anything is pushed or published. Nothing has "
                "left this computer.")

    async def db_snapshot(a: dict[str, Any]) -> str:
        data = await call("POST", "/dbtm/snapshot", {"connection": a.get("connection") or "",
                                                     "tables": a.get("tables") or None,
                                                     "reason": a.get("reason") or "before an agent's change"},
                          timeout=600)
        m = data.get("snapshot") or {}
        return (f"Snapshot {m.get('id')} of {m.get('database')} ({len(m.get('tables') or [])} tables). After your "
                f"change, call db_changes(snapshot=\"{m.get('id')}\") to see what changed.")

    async def db_changes(a: dict[str, Any]) -> str:
        if a.get("snapshot"):
            data = await call("POST", "/dbtm/compare", {"snapshot": a["snapshot"]}, timeout=600)
            return tools.out(f"Since snapshot {a['snapshot']}: {data.get('text')}.")
        data = await call("POST", "/dbtm/changes", {"connection": a.get("connection") or ""}, timeout=600)
        return tools.out(data.get("text") or "")

    async def db_rollback(a: dict[str, Any]) -> str:
        data = await call("POST", "/dbtm/rollback", {"snapshot": a["snapshot"], "table": a.get("table") or "",
                                                     "requested_by": a.get("requested_by") or "Coding agent (MCP)"})
        return (f"{data.get('message')} Nothing changes until the user approves. Check it with "
                f"approval_status(decision_id=\"{data.get('decision_id')}\").")

    async def raid_status(a: dict[str, Any]) -> str:
        data = await call("GET", "/raid")
        return tools.out(data.get("text") or "")

    return [
        Tool("runbook_list", "Project steps",
             "Project Runbook: how this project runs, tests, builds and deploys (from its justfile, Makefile, "
             "package.json, Cargo.toml, pyproject.toml...), and its pinned tool versions.", "read",
             _schema({"project": PROJECT}), runbook_list),
        Tool("runbook_run", "Run a project step",
             "Project Runbook: run the project's run/test/build/lint step (`just <step>` when there's a justfile) in "
             "Orion's runner. Deploy, and anything that looks destructive, is sent to the user for approval.", "run",
             _schema({"project": PROJECT, "step": {**S, "description": "run, test, build, deploy or lint"},
                      "requested_by": S}, ("step",)), runbook_run),
        Tool("release_plan", "Plan a release (dry run)",
             "Release Butler dry run: the changelog, version bumps and steps a release would take. Writes nothing.",
             "read", _schema({"project": PROJECT, "version": {**S, "description": "e.g. 1.1 or 1.1.0"}},
                             ("version",)), release_plan),
        Tool("release_start", "Prepare a release",
             "Release Butler: prepare a release on a separate branch (changelog, version bump, full tests, signing) "
             "and ask the user to approve pushing it. Nothing is pushed or published without that approval.",
             "approval", _schema({"project": PROJECT, "version": S}, ("version",)), release_start),
        Tool("db_snapshot", "Snapshot a staging database",
             "DB Time Machine: take a consistent snapshot of a staging MySQL database (or some tables) before a "
             "migration or bulk change. Only hosts listed as staging work.", "run",
             _schema({"connection": S, "tables": {**L, "description": "Only these tables (default: all)"},
                      "reason": S}), db_snapshot),
        Tool("db_changes", "What changed in the database",
             "DB Time Machine: row-level changes since a snapshot (or since this morning's first snapshot), in "
             "plain words.", "read", _schema({"snapshot": S, "connection": S}), db_changes),
        Tool("db_rollback", "Ask to roll a database back",
             "DB Time Machine: ask the user to put a table (or the whole staging database) back to a snapshot. Only "
             "an approval card; nothing changes unless they approve.", "approval",
             _schema({"snapshot": S, "table": S, "requested_by": S}, ("snapshot",)), db_rollback),
        Tool("raid_status", "Raid night status",
             "Raid Night Autopilot: the schedule, the next raid and the last checklist.", "read", _schema({}),
             raid_status),
    ]

