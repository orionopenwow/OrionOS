"""Toolkit, daemon side: Dependency Autopilot (17), Supply-Chain Scan (19), Exposure Map (20), Host Health (21).

One object (SecurityToolkit) holds the four tools, runs their clock, posts their cards and answers their phrases:

    hourly   Exposure Map of this computer (a new listener others can reach -> card; internet -> phone too)
    daily    Supply-Chain Scan of your projects (a new serious hole -> phone + Dependency Autopilot right away)
    weekly   Dependency Autopilot per project (one card per project, approve = a tested branch)
             Supply-Chain summary card; the polite home-network scan (only if you turned it on)
    Sunday   Host Health check-up card

Phrases: "what's open on the server?", "how healthy is the laptop?", "is the USB image safe to ship?",
"check the dependencies for OpenWoW".

HTTP routes (local only, like the rest of the daemon):
    GET  /security/status                 everything at a glance (Settings > Security check-up)
    GET  /security/config, POST /security/config   {deps:{enabled,projects}, exposure:{lan_scan}, ...}
    GET  /deps, POST /deps/run {project?}
    GET  /supply, POST /supply/scan, GET /supply/image
    GET  /exposure?machine=, POST /exposure/scan, POST /exposure/lan-scan
    POST /exposure/report                 another machine's listeners (os/windows/Report-Listeners.ps1)
    GET  /host-health, POST /host-health/check
"""
from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import dep_autopilot as deps_mod
import exposure_map as expo
import host_health as hh
import supply_scan as supply
from sec_common import Shell, alert, machine_name, plural, read_json, write_json

log = logging.getLogger("orion.security_toolkit")

try:   # routes resolve their annotations from module globals (this file uses `from __future__ import annotations`)
    from fastapi import Request
except ImportError:   # pragma: no cover
    Request = Any  # type: ignore

CARD_KIND = "security_report"
KINDS = (CARD_KIND,)
HOUR, DAY = 3600, 86400
DEFAULTS: dict[str, Any] = {
    "deps": {"enabled": True, "projects": [], "every_days": 7},
    "supply": {"enabled": True, "call_analysis": True},
    "exposure": {"enabled": True, "lan_scan": False},
    "host_health": {"enabled": True},
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


class _Plugin:
    def __init__(self, name: str, match: Callable[[str], Optional[str]], run: Callable[[str], Any]):
        self.name, self.match, self.run = name, match, run


class SecurityToolkit:
    def __init__(self, daemon: Any, data_dir: Any = None, sh: Optional[Shell] = None,
                 repo_root: Optional[Path] = None) -> None:
        from config import settings
        self.d = daemon
        self.data_dir = Path(data_dir or settings.data_dir)
        self.repo_root = Path(repo_root or settings.repo_root)
        self.path = self.data_dir / "security_toolkit.json"
        self.sh = sh or Shell()
        board = getattr(daemon, "board", None)
        self.deps = deps_mod.Autopilot(self.data_dir, sh=self.sh, board=board, notify=self._notify)
        self._task: Optional[asyncio.Task] = None
        self._jobs: set[asyncio.Task] = set()
        self._locks = {k: asyncio.Lock() for k in ("deps", "supply", "exposure", "health", "lan")}
        self.running: dict[str, str] = {}
        if board is not None:
            try:
                board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (deps_mod.TASK_APPLY,))
            except Exception:
                pass
            board.executors[deps_mod.TASK_APPLY] = self.deps.execute_apply
        quick = getattr(daemon, "quick", None)
        if quick is not None and hasattr(quick, "plugins"):
            for p in self.plugins():
                if all(x.name != p.name for x in quick.plugins):
                    quick.plugins.append(p)

    # ------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        st = read_json(self.path, {}) or {}
        st["config"] = _merge(DEFAULTS, st.get("config") or {})
        for k in ("maps", "health", "at"):
            st.setdefault(k, {})
        st.setdefault("notified", [])
        st.setdefault("changes", [])
        return st

    def _save(self, st: dict[str, Any]) -> None:
        st["notified"] = st["notified"][-3000:]
        st["changes"] = st["changes"][-100:]
        write_json(self.path, st)

    def config(self) -> dict[str, Any]:
        return self.state()["config"]

    def set_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        st = self.state()
        clean: dict[str, Any] = {}
        for section, vals in (changes or {}).items():
            if section not in DEFAULTS or not isinstance(vals, dict):
                raise ValueError(f"unknown setting {section!r}")
            for k, v in vals.items():
                if k not in DEFAULTS[section]:
                    raise ValueError(f"unknown setting {section}.{k}")
                want = type(DEFAULTS[section][k])
                if want is list:
                    if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                        raise ValueError(f"{section}.{k} must be a list of folders")
                    v = [str(self._project(x)) for x in v]
                elif want is bool:
                    v = bool(v)
                elif want is int:
                    v = max(1, min(60, int(v)))
                clean.setdefault(section, {})[k] = v
        st["config"] = _merge(st["config"], clean)
        self._save(st)
        return st["config"]

    def projects(self) -> list[Path]:
        cfg = self.config()["deps"]
        if cfg.get("projects"):
            return [Path(p) for p in cfg["projects"] if Path(p).is_dir()]
        try:
            from mcp_tools import McpConfig
            from secret_shield import default_repos
            return default_repos(McpConfig.load().resolved_roots())
        except Exception:
            return []

    def _project(self, raw: str) -> Path:
        raw = (raw or "").strip()
        for p in self.projects():
            if p.name.lower() == raw.lower():
                return p
        if raw.lower() in ("orion", "orionos", "orion ai"):
            return self.repo_root
        from mcp_tools import McpConfig, resolve_project
        return resolve_project(raw, McpConfig.load())

    # ------------------------------------------------------------ clock
    def start(self) -> None:
        if self._task is None and os.environ.get("ORION_SECURITY_SCHEDULE", "1") != "0":
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="security-toolkit")

    async def stop(self) -> None:
        for t in [self._task, *self._jobs]:
            if t is None:
                continue
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._jobs.clear()

    async def _loop(self) -> None:
        await asyncio.sleep(600)
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("security check failed")
            await asyncio.sleep(HOUR)

    def spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def tick(self, now: Optional[float] = None) -> dict[str, Any]:
        now = now or time.time()
        st = self.state()
        cfg, at = st["config"], st["at"]
        did: dict[str, Any] = {}
        if cfg["exposure"]["enabled"]:
            did["exposure"] = len((await self.exposure_scan())["changes"])
            if cfg["exposure"]["lan_scan"] and now - at.get("lan", 0) >= 7 * DAY:
                did["lan"] = (await self.lan_scan()).get("ok")
        if cfg["supply"]["enabled"] and now - at.get("supply", 0) >= DAY:
            did["supply"] = (await self.supply_scan("daily"))["serious"]
            self._stamp("supply", now)
        if cfg["supply"]["enabled"] and now - at.get("supply_card", 0) >= 7 * DAY:
            last = read_json(self.data_dir / "supply_scan.json")
            if last:
                await self._supply_card(last)
                self._stamp("supply_card", now)
                did["supply_card"] = True
        if cfg["host_health"]["enabled"] and _dt.datetime.fromtimestamp(now).weekday() == 6 \
                and now - at.get("health_card", 0) >= 6 * DAY:
            await self.host_check(card=True)
            self._stamp("health_card", now)
            did["health"] = True
        if cfg["deps"]["enabled"]:
            for p in self.projects():
                if self.deps.due(p, now, cfg["deps"]["every_days"]):
                    did["deps"] = (await self.deps_run(p, "weekly"))["state"]
                    break          # one project per hour keeps the computer usable
        return did

    def _stamp(self, key: str, now: Optional[float] = None) -> None:
        st = self.state()
        st["at"][key] = now or time.time()
        self._save(st)

    # ------------------------------------------------------------ 17. Dependency Autopilot
    async def deps_run(self, project: Path, reason: str = "you asked") -> dict[str, Any]:
        lock = self._locks["deps"]
        async with lock:
            self.running["deps"] = Path(project).name
            try:
                findings = (read_json(self.data_dir / "supply_scan.json", {}) or {}).get("findings") or []
                findings = [f for f in findings if f.get("project_path") == str(project)]
                rec = await asyncio.to_thread(self.deps.run, Path(project), findings, reason)
            finally:
                self.running.pop("deps", None)
        if rec["state"] == "ready":
            try:
                await self.deps.propose(rec)
            except Exception:
                log.exception("Could not propose the dependency branch")
            if any(u.get("security") for u in rec["ready"]):
                self._notify(f"Security updates ready for {rec['name']}", rec["text"].splitlines()[0] +
                             ". Approve on your phone to make the branch.")
        elif rec["state"] in ("tests_fail", "nothing_ready") and reason != "weekly":
            await self._card(f"Dependency updates for {rec['name']}", rec["text"], "normal")
        await self._log(f"Dependency Autopilot, {rec['name']}: {rec['text'].splitlines()[0]}")
        return rec

    def deps_status(self) -> dict[str, Any]:
        return {"running": self.running.get("deps"), "runs": [
            {k: r.get(k) for k in ("id", "name", "project", "at", "state", "text", "found", "trials", "decision_id",
                                   "reason")} for r in self.deps.last_runs()],
            "projects": [str(p) for p in self.projects()]}

    # ------------------------------------------------------------ 19. Supply-Chain Scan
    async def supply_scan(self, why: str = "you asked") -> dict[str, Any]:
        async with self._locks["supply"]:
            cfg = self.config()["supply"]
            report = await asyncio.to_thread(supply.scan, self.projects(), self.sh, cfg.get("call_analysis", True))
            write_json(self.data_dir / "supply_scan.json", report)
            st = self.state()
            st["at"]["supply"] = report["at"]
            new = [f for f in report["findings"] if f["key"] not in st["notified"]
                   and f["severity"] in supply.SERIOUS]
            st["notified"] += [f["key"] for f in report["findings"] if f["key"] not in st["notified"]]
            self._save(st)
        if new:
            first = new[0]
            self._notify(f"{plural(len(new), 'new serious hole')} in your dependencies", supply.explain(first)[:300])
            for proj in sorted({f["project_path"] for f in new if f.get("fixed")}):
                if Path(proj).is_dir() and self.config()["deps"]["enabled"]:
                    self.spawn(self.deps_run(Path(proj), "security fix"))
        return report

    async def _supply_card(self, report: dict[str, Any]) -> None:
        self._stamp("supply_card")
        n = len(report.get("findings") or [])
        title = "Weekly dependency check: no known holes" if not n else \
            f"Weekly dependency check: {plural(n, 'known hole')} ({report.get('serious', 0)} serious)"
        img = supply.image_verdict(supply.newest_image_report(supply.image_dirs(self.repo_root)))
        text = report["summary"] + ("\n\nThe USB image: " + img["answer"] if img["safe"] is not None else "")
        await self._card(title, text, "urgent" if report.get("serious") else "normal")

    def image_answer(self) -> dict[str, Any]:
        return supply.image_verdict(supply.newest_image_report(supply.image_dirs(self.repo_root)))

    # ------------------------------------------------------------ 20. Exposure Map
    async def exposure_scan(self) -> dict[str, Any]:
        async with self._locks["exposure"]:
            m = await asyncio.to_thread(expo.build_map, self.sh)
            return await self._store_map(m)

    async def exposure_report(self, report: dict[str, Any]) -> dict[str, Any]:
        m = expo.map_from_report(report)
        if m["machine"].lower() == machine_name().lower():
            m["machine"] += " (reported)"
        return await self._store_map(m)

    async def _store_map(self, m: dict[str, Any]) -> dict[str, Any]:
        st = self.state()
        old = st["maps"].get(m["machine"])
        changes = expo.diff(old, m)
        st["maps"][m["machine"]] = m
        st["at"]["exposure"] = m["at"]
        if changes:
            st["changes"].append({"at": m["at"], "machine": m["machine"], "changes": changes})
        self._save(st)
        if changes:
            text = expo.describe_changes(m["machine"], changes)
            internet = any(c["row"]["widest"] == "internet" for c in changes)
            title = f"{'Now open to the internet' if internet else 'Newly reachable'} on {m['machine']}"
            await self._card(title, text + "\n\nIf you didn't expect this, stop that program or close the port "
                             "(sudo ufw deny <port>).", "urgent" if internet else "normal")
            if internet:
                self._notify(title, text[:300])
        return {**m, "changes": changes}

    def exposure_maps(self) -> dict[str, Any]:
        return self.state()["maps"]

    def find_machine(self, name: str) -> tuple[Optional[dict[str, Any]], str]:
        """(map, note) for a spoken machine name ("the server", "the PC", "this computer")."""
        maps = self.exposure_maps()
        local = machine_name()
        n = (name or "").lower().strip().rstrip("?")
        n = re.sub(r"^(the|my|this|that)\s+", "", n)
        if n in ("", "computer", "machine", "here", "laptop", "pc here", local.lower()):
            return maps.get(local), ""
        for k in maps:
            kl = k.lower()
            if n == kl or n in kl or kl.split(" (")[0] in n:
                return maps[k], ""
        if n in ("pc", "windows", "windows pc", "desktop"):
            win = next((maps[k] for k in maps if (maps[k].get("source") or "").lower().startswith("windows")), None)
            if win:
                return win, ""
            return None, ("The Windows PC hasn't reported yet. Run os/windows/Report-Listeners.ps1 there while "
                          "Orion runs on it.")
        return maps.get(local), (f"I don't have a map of “{name}” yet, so here is this computer ({local}). Other "
                                 "machines appear once Orion runs there or they report in.")

    async def lan_scan(self) -> dict[str, Any]:
        async with self._locks["lan"]:
            old = read_json(self.data_dir / "lan_scan.json")
            r = await asyncio.to_thread(expo.lan_scan, self.sh)
            self._stamp("lan")
            if r.get("hosts") is not None and r.get("ok"):
                write_json(self.data_dir / "lan_scan.json", r)
                new = expo.lan_diff(old, r)
                if new:
                    await self._card("New open ports on your home network",
                                     "Since the last weekly scan:\n" + "\n".join(f"- {x}" for x in new[:30]) +
                                     "\n\n" + r["text"], "normal")
            return r

    # ------------------------------------------------------------ 21. Host Health
    async def host_check(self, card: bool = False) -> dict[str, Any]:
        async with self._locks["health"]:
            r = await asyncio.to_thread(hh.check, self.sh)
            st = self.state()
            st["health"][r["machine"]] = {k: v for k, v in r.items() if k != "info"} | {
                "battery": (r["info"] or {}).get("battery"), "disks": (r["info"] or {}).get("disks"),
                "bruteforce": (r["info"] or {}).get("bruteforce")}
            st["at"]["health"] = r["at"]
            if card:
                st["at"]["health_card"] = r["at"]
            self._save(st)
        if card:
            await self._card(f"Weekly check-up: {r['machine']} scores {r['score']}/100", r["text"],
                             "urgent" if r["score"] < 50 else "normal")
        return r

    def health_status(self) -> dict[str, Any]:
        return self.state()["health"]

    # ------------------------------------------------------------ cards, alerts, log
    async def _card(self, title: str, text: str, category: str = "normal") -> None:
        d = self.d
        if d is None or not hasattr(d, "approvals"):
            return
        try:
            item = d.approvals.add(CARD_KIND, platform="Security", channel="Security check-up",
                                   author="Orion security", category=category, title=title,
                                   original=text[:4000], draft="Approve = seen. Reject = dismiss.")
            await d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
        except Exception:
            log.exception("Could not post the security card")

    def _notify(self, title: str, body: str) -> None:
        alert(title, body, "security")

    async def _log(self, msg: str, level: str = "info") -> None:
        try:
            await self.d.bus.log(msg, level)
        except Exception:
            pass

    async def decide(self, item: dict[str, Any], decision: str, text: str = "") -> None:
        """Security cards are information: approve = seen, reject = dismissed."""
        updated = self.d.approvals.update(item["id"], status="rejected" if decision == "reject" else "handled")
        await self.d.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})

    # ------------------------------------------------------------ overview
    def status(self) -> dict[str, Any]:
        st = self.state()
        sup = read_json(self.data_dir / "supply_scan.json") or {}
        local = machine_name()
        return {"config": st["config"], "machine": local, "running": dict(self.running),
                "exposure": {"maps": st["maps"], "changes": st["changes"][-10:],
                             "lan": read_json(self.data_dir / "lan_scan.json"),
                             "nmap": bool(self.sh.which("nmap"))},
                "supply": {"at": sup.get("at"), "summary": sup.get("summary"), "serious": sup.get("serious", 0),
                           "count": len(sup.get("findings") or []), "tools": sup.get("tools") or [],
                           "image": self.image_answer()},
                "health": st["health"], "deps": self.deps_status(), "at": st["at"]}

    # ------------------------------------------------------------ phrases
    def plugins(self) -> list[_Plugin]:
        def m_open(t: str) -> Optional[str]:
            m = re.match(r"^what(?:'s| s| is)? (?:open|listening|reachable) on (.+?)$", t)
            return m.group(1) if m else None

        async def r_open(arg: str) -> str:
            m, note = self.find_machine(arg)
            if m is None and not note:
                m = await self.exposure_scan()
            return (note + "\n" if note else "") + (expo.answer(m) if m else "")

        def m_health(t: str) -> Optional[str]:
            m = re.match(r"^how (?:healthy|secure|safe) is (.+?)$", t)
            return m.group(1) if m else None

        async def r_health(arg: str) -> str:
            r = await self.host_check()
            n = re.sub(r"^(the|my|this)\s+", "", arg.lower())
            note = "" if n in ("laptop", "computer", "machine", "pc", machine_name().lower()) else \
                f"I can only check the machine Orion runs on ({r['machine']}). "
            return note + r["text"]

        def m_image(t: str) -> Optional[str]:
            return "image" if re.match(r"^is (?:the )?(?:usb |orionos |os )*image (?:safe|ok|okay|ready) to ship$", t) \
                else None

        async def r_image(_arg: str) -> str:
            return self.image_answer()["answer"]

        def m_deps(t: str) -> Optional[str]:
            m = re.match(r"^(?:check|update|test) (?:the |my )?(?:dependencies|deps|packages)"
                         r"(?: (?:for|of|in) (.+))?$", t)
            return (m.group(1) or "*") if m else None

        async def r_deps(arg: str) -> str:
            try:
                targets = self.projects() if arg == "*" else [self._project(arg)]
            except Exception:
                return f"I can't find a project called “{arg}”."
            if not targets:
                return "I don't know any projects yet. Add their folders in Settings > Security check-up."
            for p in targets:
                self.spawn(self.deps_run(p, "you asked"))
            return (f"Checking updates for {', '.join(p.name for p in targets)}. Each one is tested in a throwaway "
                    "copy first; you'll get one card per project.")

        return [_Plugin("exposure", m_open, r_open), _Plugin("hosthealth", m_health, r_health),
                _Plugin("imagesafe", m_image, r_image), _Plugin("depsauto", m_deps, r_deps)]


# =====================================================================================  routes
def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(tags=["toolkit"])

    def tk() -> SecurityToolkit:
        d = get_daemon()
        t = getattr(d, "security_toolkit", None)
        if t is None:
            t = SecurityToolkit(d)
            try:
                d.security_toolkit = t
            except Exception:
                pass
        return t

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    @router.get("/security/status")
    async def sec_status() -> Any:
        return tk().status()

    @router.get("/security/config")
    async def get_cfg() -> Any:
        return tk().config()

    @router.post("/security/config")
    async def post_cfg(request: Request) -> Any:
        try:
            return {"ok": True, "config": tk().set_config(await body_of(request))}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.get("/deps")
    async def get_deps() -> Any:
        return tk().deps_status()

    @router.post("/deps/run")
    async def post_deps(request: Request) -> Any:
        b = await body_of(request)
        t = tk()
        try:
            targets = [t._project(str(b["project"]))] if b.get("project") else t.projects()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not targets:
            return JSONResponse({"error": "No projects to check. Add folders in Settings."}, status_code=404)
        if b.get("wait") and len(targets) == 1:
            return await t.deps_run(targets[0], "you asked")
        for p in targets:
            t.spawn(t.deps_run(p, "you asked"))
        return {"ok": True, "started": [p.name for p in targets],
                "text": "Checking; the result arrives as a card (tests can take a while)."}

    @router.get("/supply")
    async def get_supply() -> Any:
        return {"report": read_json(tk().data_dir / "supply_scan.json")}

    @router.post("/supply/scan")
    async def post_supply() -> Any:
        return await tk().supply_scan("you asked")

    @router.get("/supply/image")
    async def get_image() -> Any:
        return tk().image_answer()

    @router.get("/exposure")
    async def get_exposure(machine: str = "") -> Any:
        t = tk()
        if machine:
            m, note = t.find_machine(machine)
            return {"map": m, "note": note, "text": (note + "\n" if note else "") + expo.answer(m)}
        return {"maps": t.exposure_maps()}

    @router.post("/exposure/scan")
    async def post_exposure() -> Any:
        m = await tk().exposure_scan()
        return {**m, "text": expo.answer(m)}

    @router.post("/exposure/report")
    async def post_report(request: Request) -> Any:
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse({"error": "Reports are only taken from this computer."}, status_code=403)
        b = await body_of(request)
        if not isinstance(b.get("listeners"), list) or len(b["listeners"]) > 5000:
            return JSONResponse({"error": "listeners must be a list"}, status_code=400)
        m = await tk().exposure_report(b)
        return {"ok": True, "machine": m["machine"], "changes": len(m["changes"]), "text": expo.answer(m)}

    @router.post("/exposure/lan-scan")
    async def post_lan() -> Any:
        t = tk()
        if not t.config()["exposure"]["lan_scan"]:
            return JSONResponse({"error": "The home network scan is off. Turn it on in Settings > Security check-up."},
                                status_code=400)
        return await t.lan_scan()

    @router.get("/host-health")
    async def get_health() -> Any:
        return {"machines": tk().health_status()}

    @router.post("/host-health/check")
    async def post_health() -> Any:
        r = await tk().host_check()
        return {k: v for k, v in r.items() if k != "info"}

    return router


# =====================================================================================  MCP tools
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    S = {"type": "string"}

    async def call(method: str, path: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        try:
            status, data = await tools.backend.call(method, path, body, timeout=1800)
        except DaemonUnavailable:
            raise ToolError("Orion isn't running, so this can't be checked right now.")
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        return data

    async def exposure(a: dict[str, Any]) -> str:
        if a.get("refresh"):
            await call("POST", "/exposure/scan", {})
        from urllib.parse import quote
        data = await call("GET", f"/exposure?machine={quote(a.get('machine') or 'this computer')}")
        return tools.out(data["text"])

    async def health(_a: dict[str, Any]) -> str:
        return tools.out((await call("POST", "/host-health/check", {}))["text"])

    async def supply_tool(_a: dict[str, Any]) -> str:
        return tools.out((await call("POST", "/supply/scan", {}))["summary"])

    async def image(_a: dict[str, Any]) -> str:
        return tools.out((await call("GET", "/supply/image"))["answer"])

    async def deps(a: dict[str, Any]) -> str:
        data = await call("POST", "/deps/run", {"project": a["project"]} if a.get("project") else {})
        return tools.out(data.get("text") or "Started.")

    return [
        Tool("exposure_map", "What's open on a machine?",
             "Exposure Map: what is listening on which port, and who can reach it (this computer only, the home "
             "network, Tailscale or the internet), checked against the firewall and Tailscale.", "read",
             _schema({"machine": {**S, "description": "Machine name (default: this computer)"},
                      "refresh": {"type": "boolean", "description": "Re-check this computer first"}}), exposure),
        Tool("host_health", "How healthy is this computer?",
             "Host Health: a security check-up of this computer (Lynis score, file watch, disks, battery, brute-force "
             "protection) with the three most useful fixes in plain words.", "read", _schema({}), health),
        Tool("supply_chain_scan", "Known holes in my dependencies",
             "Supply-Chain Scan: check every project's Rust, Python and npm packages for known security holes, "
             "explained in plain words with the fixing version.", "read", _schema({}), supply_tool),
        Tool("image_safe_to_ship", "Is the USB image safe to ship?",
             "Reads the newest OrionOS image scan (syft + grype) and answers whether it has serious holes that "
             "already have fixes.", "read", _schema({}), image),
        Tool("dependency_autopilot", "Test dependency updates",
             "Dependency Autopilot: find updates for a project, try them in a throwaway copy, run the tests, and "
             "send the user one card. Approving it makes a branch; nothing is pushed or merged.", "run",
             _schema({"project": {**S, "description": "Project name or folder (default: all)"}}), deps),
    ]
