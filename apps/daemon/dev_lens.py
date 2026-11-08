"""Toolkit, daemon side: AI Blame (4), Context Pack (5), Perf Sentinel (13) and Log Lens (14).

One object (DevLens) builds the four tools, adds their phrases to quick commands, and runs one small loop
(every TICK_S seconds) that:
  * tells AI Blame which agent sessions are working where, and records new commits in watched repos;
  * tells Context Pack which files agents opened (so later packs rank better);
  * runs Perf Sentinel when a repo asks for it (.orion/perf.json "when": commit / proof / nightly).

Phrases: "why did line 42 of app.py change?", "brief Claude on the vault bug", "is the server faster than last
week?", "why did the world server restart at 3 am?".

HTTP routes (local only, like the rest of the daemon; repo paths must be inside the folders agents may use):

    GET  /blame?file=&line=&repo=          "why did this line change?" (the VS Code companion calls this)
    GET  /blame/recent                     the newest recorded commits
    POST /context/pack  {task, repo?, budget_tokens?, copy?}
    GET  /context/stats
    POST /perf/run      {repo, trigger?, wait?}
    GET  /perf          measured benches and recent slowdowns
    GET  /perf/compare?q=&days=            "is the server faster than last week?"
    POST /perf/hook     {repo}             add the optional post-commit hook
    GET  /logs/ask?q=   POST /logs/ask {question, since?, until?}
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import ai_blame
import context_pack
import log_lens
import perf_sentinel

log = logging.getLogger("orion.dev_lens")

TICK_S = 10.0
SCAN_EVERY = 3            # scan repos for new commits every SCAN_EVERY ticks
CARD_KIND = "perf_regression"


class _Plugin:
    def __init__(self, name: str, match: Callable[[str], Optional[str]], run: Callable[[str], Any]):
        self.name, self.match, self.run = name, match, run


def _alert(title: str, body: str) -> None:
    try:
        import alerts
        alerts.emit("system", title, body, ref="perf")
    except Exception:
        pass


class DevLens:
    def __init__(self, daemon: Any, data_dir: Any = None) -> None:
        from config import settings
        self.d = daemon
        data_dir = Path(data_dir or settings.data_dir)
        self.repo_root = Path(settings.repo_root)
        self.port = settings.port
        tk = getattr(daemon, "agent_toolkit", None)
        proof = getattr(tk, "proof", None)
        self.blame = ai_blame.BlameIndex(data_dir, proofs=(lambda: proof.recent(60)) if proof is not None else None)
        ext = getattr(daemon, "ext", None)
        meter = getattr(getattr(daemon, "orchestrator", None), "meter", None)
        self.packer = context_pack.ContextPacker(data_dir, healer=getattr(daemon, "healer", None),
                                                 runner=getattr(ext, "runner", None), meter=meter)
        self.perf = perf_sentinel.PerfSentinel(data_dir, card=self.perf_card)
        self.logs = log_lens.LogLens(data_dir, healer=getattr(daemon, "healer", None), changes_fn=self._changes)
        self._task: Optional[asyncio.Task] = None
        self._jobs: set[asyncio.Task] = set()
        self._ticks = 0
        self._proofs_seen: set[str] = set()
        self.blame.watch(self.repo_root)
        quick = getattr(daemon, "quick", None)
        if quick is not None and hasattr(quick, "plugins"):
            for p in self.plugins():
                if all(x.name != p.name for x in quick.plugins):
                    quick.plugins.append(p)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _changes() -> list[dict[str, Any]]:
        import changes
        return changes.current().list(500)

    def agents(self) -> list[dict[str, Any]]:
        tk = getattr(self.d, "agent_toolkit", None)
        try:
            return tk.agents() if tk is not None else []
        except Exception:
            return []

    def spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def repo_for(self, name: str) -> Path:
        """A project by name ("OpenWoW") or path; Orion's own repo when empty."""
        name = (name or "").strip()
        if not name or name.lower() in ("orion", "orion ai", "orionos", "this", "this repo"):
            return self.repo_root
        ops = getattr(self.d, "ops_toolkit", None)
        if ops is not None:
            try:
                return ops.project(name)
            except Exception:
                pass
        for r in self.blame.repos():
            if Path(r["path"]).name.lower() == name.lower():
                return Path(r["path"])
        raise ValueError(f"I can't find a project called “{name}”.")

    async def perf_card(self, reg: dict[str, Any]) -> None:
        title = f"{reg['bench']} got {reg['pct']:.0f}% {'bigger' if reg.get('unit') == 'bytes' else 'slower'}"
        lines = [reg["text"]]
        if reg.get("commits"):
            lines += ["Commits in that range:", *[f"- {c}" for c in reg["commits"][:10]]]
        _alert(title, reg["text"])
        d = self.d
        if not hasattr(d, "approvals"):
            return
        try:
            item = d.approvals.add(CARD_KIND, platform="Perf Sentinel", channel="Performance", author="Perf Sentinel",
                                   category="normal", title=title, original="\n".join(lines),
                                   draft="Approve = seen. Reject = dismiss.", perf={k: reg.get(k) for k in (
                                       "repo", "bench", "before", "after", "pct", "good_sha", "bad_sha")})
            await d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
        except Exception:
            log.exception("Could not post the Perf Sentinel card")

    # ------------------------------------------------------------ loop
    async def tick(self) -> None:
        self._ticks += 1
        agents = self.agents()
        await asyncio.to_thread(self.blame.observe, agents)
        await asyncio.to_thread(self.packer.observe, agents)
        if self._ticks % SCAN_EVERY == 0:
            for r in self.blame.repos():
                new = await asyncio.to_thread(self.blame.scan, r["path"])
                if new and self.perf.wants(r["path"], "commit"):
                    self.spawn(self.perf.on_commit(r["path"]))
        tk = getattr(self.d, "agent_toolkit", None)
        proof = getattr(tk, "proof", None)
        if proof is not None:
            for res in proof.recent(10):
                rid = str(res.get("id") or "")
                if rid and rid not in self._proofs_seen:
                    self._proofs_seen.add(rid)
                    if self._ticks > 1 and res.get("verdict") == "green":
                        self.spawn(self.perf.on_proof(res))
        repos = [self.repo_root] + [Path(r["path"]) for r in self.blame.repos()]
        done = await self.perf.nightly(list(dict.fromkeys(repos)))
        for run in done:
            log.info("Perf Sentinel nightly run in %s: %d results", run.get("repo"), len(run.get("results", [])))

    def start(self) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(TICK_S)
                try:
                    await self.tick()
                except Exception:
                    log.exception("dev lens tick failed")
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(loop(), name="dev-lens")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        for t in list(self._jobs):
            t.cancel()

    # ------------------------------------------------------------ quick-command phrases
    def blame_phrase(self, t: str) -> str:
        m = re.match(r"^(?:why did|why was|who changed|who wrote|who touched) line (\d+) (?:of|in) (.+?)"
                     r"(?: change| changed| get changed| written| come from)?$", t) or None
        if m:
            line, spoken = int(m.group(1)), m.group(2)
        else:
            m = re.match(r"^(?:why did|why was|who changed|who wrote|who touched) (.+?) line (\d+)"
                         r"(?: change| changed| get changed| written)?$", t)
            if not m:
                return ("Point at the line in VS Code and run “Orion: why did this line change?”, or say "
                        "the file and line, like “why did line 42 of app.py change?”")
            spoken, line = m.group(1), int(m.group(2))
        roots = [self.repo_root] + [Path(r["path"]) for r in self.blame.repos() if Path(r["path"]) != self.repo_root]
        hit = ai_blame.find_file(roots, spoken)
        if hit is None:
            return f"I can't find a file called “{spoken}” in the repos Orion watches."
        top, rel = hit
        try:
            return ai_blame.why(self.blame, str(top / rel), line, agents=self.agents())["answer"]
        except ValueError as exc:
            return str(exc)

    def plugins(self) -> list[_Plugin]:
        def m_blame(t: str) -> Optional[str]:
            if re.search(r"\b(restart|crash|reboot|go down|went down)", t):
                return None
            if re.match(r"^(?:why did|why was|who changed|who wrote|who touched) .*\bline \d+", t):
                return t
            if re.match(r"^why did (?:this|that)(?: line)? change$", t):
                return t
            return None

        async def r_blame(arg: str) -> str:
            return await asyncio.to_thread(self.blame_phrase, arg)

        def m_brief(t: str) -> Optional[str]:
            m = re.match(r"^(?:brief|make a brief for|context pack for) (\w+) (?:on|about|for) (.+)$", t)
            return f"{m.group(1)}|{m.group(2)}" if m else None

        async def r_brief(arg: str) -> str:
            who, _, task = arg.partition("|")
            repo = self.repo_root
            m = re.match(r"^(.+?) in ([\w -]+)$", task)
            if m:
                try:
                    repo, task = self.repo_for(m.group(2)), m.group(1)
                except ValueError:
                    pass
            try:
                pack = await asyncio.to_thread(self.packer.build, task, repo)
            except ValueError as exc:
                return str(exc)
            copied = await asyncio.to_thread(context_pack.copy_to_clipboard, pack["markdown"])
            pct = round(100 * pack["saved_tokens"] / pack["whole_repo_tokens"]) if pack["whole_repo_tokens"] else 0
            where = "It's in your clipboard" if copied else "I couldn't reach the clipboard; get it from /context/pack"
            return (f"Brief for {who.capitalize()} on “{task}”: about {pack['tokens']:,} tokens, "
                    f"{len(pack['files'])} files, {pct}% smaller than the whole repo. {where}.")

        def m_perf(t: str) -> Optional[str]:
            m = re.match(r"^(?:is|are|was|has) (?:the )?(.+?) (?:got |gotten |getting )?(faster|slower|bigger|smaller)"
                         r" than (last week|yesterday|last month)$", t)
            if not m:
                return None
            days = {"last week": 7, "yesterday": 1, "last month": 30}[m.group(3)]
            return f"{days}|{m.group(1)}"

        async def r_perf(arg: str) -> str:
            days, _, what = arg.partition("|")
            return await asyncio.to_thread(self.perf.compare, what, float(days))

        def m_logs(t: str) -> Optional[str]:
            if re.match(r"^(?:why did|why does|why is|why has|what made|what happened)\b", t) and re.search(
                    r"\b(restart\w*|crash\w*|reboot\w*|go down|went down|stop\w*|die|died|fail\w*|happen\w*|freez\w*"
                    r"|hang|hung|slow)\b", t):
                return t
            return None

        async def r_logs(arg: str) -> str:
            q = re.sub(r"\b(\d{1,2}) (\d{2})\b", r"\1:\2", arg)   # quick commands turn "3:00" into "3 00"
            res = await self.logs.ask(q)
            first = res["timeline"][:4]
            tail = "".join(f"\n- {e['time']} {e['unit'] or e['source']}: {e['text'][:140]}" for e in first)
            return res["answer"] + (("\nKey lines:" + tail) if tail else "")

        return [_Plugin("blame", m_blame, r_blame), _Plugin("brief", m_brief, r_brief),
                _Plugin("perf", m_perf, r_perf), _Plugin("loglens", m_logs, r_logs)]


async def decide_card(daemon: Any, item: dict[str, Any], decision: str) -> None:
    """The Perf Sentinel card is information only: approve = seen, reject = dismissed."""
    updated = daemon.approvals.update(item["id"], status="rejected" if decision == "reject" else "handled")
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})


# =====================================================================================  routes
def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _allowed(path: str) -> Path:
    from mcp_tools import McpConfig, resolve_project
    return resolve_project(path, McpConfig.load())


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    return body


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    from mcp_tools import ToolError
    router = APIRouter(tags=["toolkit"])

    def lens() -> DevLens:
        d = get_daemon()
        dl = getattr(d, "dev_lens", None) if d is not None else None
        if dl is None:
            raise RuntimeError("Orion is still starting.")
        return dl

    async def guarded(fn: Callable[[], Any]) -> Any:
        try:
            return await fn()
        except (ToolError, ValueError) as exc:
            return _err(str(exc))
        except RuntimeError as exc:
            return _err(str(exc), 503)

    # ------------------------------------------------------------ AI Blame
    @router.get("/blame")
    async def blame(file: str = "", line: int = 0, repo: str = "") -> Any:
        async def go() -> Any:
            dl = lens()
            if not file:
                raise ValueError("Say which file (file=...).")
            p = Path(file).expanduser()
            if not p.is_absolute():
                if not repo:
                    raise ValueError("Give the full path of the file, or the repo it's in.")
                p = _allowed(repo) / p
            top = ai_blame.toplevel(p.parent)
            if top is None:
                raise ValueError(f"{p.name} isn't inside a git repository.")
            _allowed(str(top))
            return await asyncio.to_thread(ai_blame.why, dl.blame, str(p), int(line), str(top), dl.agents())
        return await guarded(go)

    @router.get("/blame/recent")
    async def blame_recent(limit: int = 30) -> Any:
        async def go() -> Any:
            recs = lens().blame.recent(max(1, min(limit, 200)))
            return {"commits": [{**r, "answer": ai_blame.plain_answer(r)} for r in recs]}
        return await guarded(go)

    # ------------------------------------------------------------ Context Pack
    @router.post("/context/pack")
    async def pack(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            dl = lens()
            repo = _allowed(str(b["repo"])) if b.get("repo") else dl.repo_root
            res = await asyncio.to_thread(dl.packer.build, str(b.get("task") or ""), repo,
                                          int(b.get("budget_tokens") or context_pack.DEFAULT_BUDGET))
            if b.get("copy"):
                res["copied"] = await asyncio.to_thread(context_pack.copy_to_clipboard, res["markdown"])
            return res
        return await guarded(go)

    @router.get("/context/stats")
    async def pack_stats() -> Any:
        async def go() -> Any:
            return lens().packer.stats()
        return await guarded(go)

    # ------------------------------------------------------------ Perf Sentinel
    @router.post("/perf/run")
    async def perf_run(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed(str(b.get("repo") or ""))
            dl = lens()
            trigger = str(b.get("trigger") or "manual")
            if trigger == "commit":   # from the optional git hook: only if the repo asked for it
                if not dl.perf.wants(repo, "commit"):
                    return {"skipped": True, "message": "This repo's perf.json doesn't ask for runs on commit."}
                dl.spawn(dl.perf.on_commit(repo))
                return {"started": True}
            if b.get("wait") is False:
                dl.spawn(dl.perf.run(repo, "manual"))
                return {"started": True}
            return await dl.perf.run(repo, "manual")
        return await guarded(go)

    @router.get("/perf")
    async def perf_list() -> Any:
        async def go() -> Any:
            dl = lens()
            return {"benches": dl.perf.benches(), "regressions": dl.perf.regressions()}
        return await guarded(go)

    @router.get("/perf/compare")
    async def perf_compare(q: str = "", days: float = 7) -> Any:
        async def go() -> Any:
            return {"answer": await asyncio.to_thread(lens().perf.compare, q, max(1.0, min(days, 365.0)))}
        return await guarded(go)

    @router.post("/perf/hook")
    async def perf_hook(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed(str(b.get("repo") or ""))
            path = await asyncio.to_thread(perf_sentinel.install_hook, repo, lens().port)
            return {"hook": path, "message": "Added. Commits now ping Orion; benches run if perf.json says \"commit\"."}
        return await guarded(go)

    # ------------------------------------------------------------ Log Lens
    @router.get("/logs/ask")
    async def logs_ask_get(q: str = "") -> Any:
        async def go() -> Any:
            return await lens().logs.ask(q)
        return await guarded(go)

    @router.post("/logs/ask")
    async def logs_ask(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            since = float(b["since"]) if b.get("since") is not None else None
            until = float(b["until"]) if b.get("until") is not None else None
            return await lens().logs.ask(str(b.get("question") or b.get("q") or ""), since, until)
        return await guarded(go)

    return router


# =====================================================================================  MCP tools
def mcp_tools(tools: Any) -> list[Any]:
    """Tool definitions for mcp_tools.OrionTools (imported lazily there). All three are read-only."""
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    S = {"type": "string"}
    I = {"type": "integer"}
    PROJECT = {**S, "description": "Project folder (absolute, or relative to the first allowed root)"}

    async def call(method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: float = 60
                   ) -> Optional[dict]:
        try:
            status, data = await tools.backend.call(method, path, body, timeout=timeout)
        except DaemonUnavailable:
            return None
        if status == 404 and data.get("detail") == "Not Found":
            return None
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        return data

    def local_dir() -> Path:
        from config import settings
        return Path(settings.data_dir)

    async def ai_blame_tool(a: dict[str, Any]) -> str:
        project = tools.project(a)
        f = Path(str(a["file"])).expanduser()
        f = f if f.is_absolute() else project / f
        try:
            f.resolve().relative_to(project)
        except ValueError:
            raise ToolError("That file isn't inside the project.")
        line = int(a["line"])
        from urllib.parse import urlencode
        data = await call("GET", "/blame?" + urlencode({"file": str(f), "line": line}))
        if data is None:
            try:
                data = await asyncio.to_thread(ai_blame.why, ai_blame.BlameIndex(local_dir()), str(f), line,
                                               str(project))
            except ValueError as exc:
                raise ToolError(str(exc))
        return tools.out(f"{data['file']}:{data['line']} ({data['sha'][:7]}): {data['answer']}")

    async def context_pack_tool(a: dict[str, Any]) -> str:
        project = tools.project(a)
        budget = int(a.get("budget_tokens") or context_pack.DEFAULT_BUDGET)
        data = await call("POST", "/context/pack", {"task": a["task"], "repo": str(project), "budget_tokens": budget})
        if data is None:
            try:
                data = await asyncio.to_thread(context_pack.ContextPacker(local_dir()).build, a["task"], project,
                                               budget)
            except ValueError as exc:
                raise ToolError(str(exc))
        return tools.out(data["markdown"])

    async def log_lens_tool(a: dict[str, Any]) -> str:
        from urllib.parse import urlencode
        data = await call("GET", "/logs/ask?" + urlencode({"q": a["question"]}), timeout=150)
        if data is None:
            try:
                data = await log_lens.LogLens(local_dir()).ask(a["question"])
            except ValueError as exc:
                raise ToolError(str(exc))
        return tools.out(log_lens.describe(data))

    return [
        Tool("ai_blame", "Why did this line change?",
             "AI Blame: for a file and line, which commit last changed it, whether an agent made it (which tool, "
             "the user's request, the session's estimated cost, the Proof Gate result) or the user did by hand.",
             "read", _schema({"file": {**S, "description": "File path, relative to the project or absolute"},
                              "line": {**I, "description": "1-based line number"}, "project": PROJECT},
                             ("file", "line")), ai_blame_tool),
        Tool("context_pack", "Context Pack",
             "Context Pack: a short markdown brief for a task in this repo, within a token budget: the project "
             "rules, the files that look relevant (with their main symbols and short excerpts), recent errors, "
             "matching decisions from the plan docs and Baton hand-off notes.", "read",
             _schema({"task": {**S, "description": "What you're about to do, e.g. 'fix the vault sync bug'"},
                      "project": PROJECT, "budget_tokens": {**I, "description": "800-60000, default 6000"}},
                     ("task",)), context_pack_tool),
        Tool("log_lens", "Ask the logs",
             "Log Lens: answer a question about what happened on this computer (e.g. 'why did the world server "
             "restart at 3 am?') from the system and user journals, crash-healer records, Orion's change history "
             "and log files, as a short timeline with the key lines. Secrets are taken out.", "read",
             _schema({"question": S}, ("question",)), log_lens_tool),
    ]
