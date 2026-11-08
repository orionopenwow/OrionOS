"""Toolkit tools 22, 23 and 25 wired into the daemon in one place: Tripwires (tripwires.py), Share Scrubber
(share_scrubber.py) and Promise Keeper (promise_keeper.py), plus the phone's health line and two read-only MCP
tools (health_status, snapshots_list).

main.py needs only: `app.include_router(trust_kit.make_router(lambda: daemon))`, `trust_kit.start(self)` in start()
and `await trust_kit.stop(self)` in stop().

What it adds:
  * quick phrases: "set tripwires (on the server)", "remove the tripwires", "are my tripwires ok",
    "what did I promise this week", "share clean" (cleans the clipboard)
  * the "Promises" part of the morning brief (quick.brief_parts)
  * the boardroom executor for "safety.tripwires" (sensitive: always asks)
  * phone: GET /phone/health (read key): the health monitor's summary and the kill switch state
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

import promise_keeper as pk_mod
import share_scrubber
import tripwires as tw_mod

log = logging.getLogger("orion.trust_kit")

TRIP_SET_RE = re.compile(r"^(set|plant|lay|arm|put)( up)?( the| some| my)? tripwires?"
                         r"( on( the| this)? (?P<target>server|computer|machine|pc|laptop|vms?|lab))?$")
TRIP_REMOVE_RE = re.compile(r"^(remove|clear|take (away|down)|disarm)( the| my| all( the)?)? tripwires?$")
TRIP_STATUS_RE = re.compile(r"^(are (my|the) tripwires (ok|set|on)|tripwires?( status)?|"
                            r"(did|has) (anyone|anything|someone) (trip|tripped|set off) (a|my|the) tripwires?)$")
SHARE_RE = re.compile(r"^(share clean|clean (my|the) clipboard|scrub (my|the) clipboard)$")


class _Plugin:
    def __init__(self, name: str, match: Callable[[str], Optional[str]], run: Callable[[str], Any]) -> None:
        self.name, self.match, self.run = name, match, run


def _rx(rx: re.Pattern, value: Callable[[re.Match], str] = lambda m: "") -> Callable[[str], Optional[str]]:
    def match(t: str) -> Optional[str]:
        m = rx.match(t)
        return value(m) if m else None
    return match


class TrustKit:
    def __init__(self, daemon: Any, data_dir: Optional[Path] = None, repo_root: Optional[Path] = None,
                 port: Optional[int] = None) -> None:
        from config import settings
        self.d = daemon
        data_dir = Path(data_dir or settings.data_dir)
        self.tripwires = tw_mod.Tripwires(data_dir, repo_root=Path(repo_root or settings.repo_root),
                                          default_callback=f"http://127.0.0.1:{port or settings.port}",
                                          publish=self._publish)
        self.promises = pk_mod.PromiseKeeper(data_dir, comms=getattr(daemon, "comms", None),
                                             llm=getattr(daemon, "orchestrator", None))
        self.scrubber = share_scrubber.default()
        self._task: Optional[asyncio.Task] = None
        self._install()

    # ------------------------------------------------------------------ wiring
    def _publish(self, event: dict[str, Any]) -> None:
        bus = getattr(self.d, "bus", None)
        loop = getattr(self.d, "loop", None)
        if bus is None or loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(bus.publish(event), loop)
        except Exception:
            pass

    def _install(self) -> None:
        board = getattr(self.d, "board", None)
        tw_mod.register_task_type()
        if board is not None:
            try:
                board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (tw_mod.TASK,))
            except Exception:
                pass
            board.executors[tw_mod.TASK] = tw_mod.make_executor(self.tripwires, self._listen_now)
            if self.phone_hook not in board.remote_hooks:
                board.remote_hooks.append(self.phone_hook)
        quick = getattr(self.d, "quick", None)
        if quick is not None and hasattr(quick, "plugins"):
            for p in self.plugins():
                if all(x.name != p.name for x in quick.plugins):
                    quick.plugins.append(p)
            parts = getattr(quick, "brief_parts", None)
            if isinstance(parts, list) and self.brief_promises not in parts:
                parts.append(self.brief_promises)

    def _listen_now(self) -> None:
        self.tripwires.set_config({"listener": {"enabled": True}})
        self.tripwires.start_listener()

    def plugins(self) -> list[_Plugin]:
        return [
            _Plugin("tripwires", _rx(TRIP_SET_RE, lambda m: "set:" + (m.group("target") or "")), self._trip_quick),
            _Plugin("tripwires-off", _rx(TRIP_REMOVE_RE, lambda m: "remove"), self._trip_quick),
            _Plugin("tripwires-status", _rx(TRIP_STATUS_RE, lambda m: "status"), self._trip_quick),
            _Plugin("promises", _rx(pk_mod.WEEK_RE, lambda m: "week"), self._promise_quick),
            _Plugin("share-clean", _rx(SHARE_RE, lambda m: "clipboard"), self._share_quick),
        ]

    async def _trip_quick(self, arg: str) -> str:
        if arg.startswith("set:"):
            target = arg[4:] or "computer"
            words = {"server": "the server", "lab": "the lab", "vm": "the VM", "vms": "the VMs"}.get(
                target, "this computer")
            board = getattr(self.d, "board", None)
            if board is None:
                return "Orion is still starting."
            return (await tw_mod.propose(board, self.tripwires, words, "you"))["message"]
        if arg == "remove":
            return (await asyncio.to_thread(self.tripwires.remove))["message"]
        return self.tripwires.public()["words"]

    async def _promise_quick(self, arg: str) -> str:
        await self.promises.scan()
        return self.promises.week_words()

    async def _share_quick(self, arg: str) -> str:
        return await asyncio.to_thread(self.scrubber.clean_clipboard)

    async def brief_promises(self) -> str:
        try:
            await self.promises.scan()
        except Exception:
            log.exception("promise scan for the brief failed")
        return self.promises.brief_section()

    # ------------------------------------------------------------------ phone
    def health_summary(self) -> dict[str, Any]:
        mon = getattr(self.d, "health", None)
        last = getattr(mon, "last", None) or {"status": "ok", "words": "Not checked yet.", "checks": []}
        try:
            import freeze
            fz = freeze.public()
        except Exception:
            fz = {"frozen": False}
        return {"status": last.get("status", "ok"), "words": last.get("words", ""), "at": last.get("at", 0),
                "turbulence": last.get("turbulence", 0.0),
                "problems": [{"label": c.get("label"), "status": c.get("status"), "reason": c.get("reason")}
                             for c in last.get("checks", []) if c.get("status") != "ok"][:6],
                "frozen": bool(fz.get("frozen")), "freeze": fz}

    def phone_hook(self, app: Any, phone_auth: Any, json_body: Any) -> None:
        from fastapi import Request

        @app.get("/phone/health")
        async def phone_health(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return {"health": self.health_summary()}

    # ------------------------------------------------------------------ lifecycle
    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tripwires.ingest_inbox)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tripwire inbox failed")
            await asyncio.sleep(30)

    def start(self) -> None:
        self.promises.start()
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="tripwire-inbox")
        if self.tripwires.config()["listener"]["enabled"]:
            try:
                self.tripwires.start_listener()
            except OSError as exc:
                log.warning("Tripwire listener not started: %s", exc)

    async def stop(self) -> None:
        await self.promises.stop()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.tripwires.stop_listener()


_kits: dict[int, TrustKit] = {}


def kit(daemon: Any) -> TrustKit:
    k = _kits.get(id(daemon))
    if k is None:
        _kits.clear()
        k = _kits[id(daemon)] = TrustKit(daemon)
        daemon.trust_kit = k
    return k


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter

    router = APIRouter()
    try:
        kit(get_daemon())      # early, so the phone route is in place before the boardroom serves
    except Exception:
        log.exception("trust kit not installed yet")
    router.include_router(tw_mod.make_router(lambda: kit(get_daemon()).tripwires,
                                             lambda: getattr(get_daemon(), "board", None)))
    router.include_router(share_scrubber.make_router(lambda: kit(get_daemon()).scrubber))
    router.include_router(pk_mod.make_router(lambda: kit(get_daemon()).promises))
    return router


def start(daemon: Any) -> None:
    kit(daemon).start()


async def stop(daemon: Any) -> None:
    await kit(daemon).stop()


# =====================================================================================  MCP tools (read-only)
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    async def get(path: str) -> dict[str, Any]:
        try:
            status, data = await tools.backend.call("GET", path)
        except DaemonUnavailable:
            raise ToolError("Orion isn't running, so this can't be read right now.")
        if status >= 400:
            raise ToolError(str(data.get("error") or data.get("detail") or f"Orion refused it ({status})."))
        return data

    async def health_status(_a: dict[str, Any]) -> str:
        h = await get("/health/status")
        lines = [h.get("words") or "Not checked yet."]
        for c in h.get("checks") or []:
            if c.get("status") != "ok":
                lines.append(f"- {c.get('label')}: {c.get('status')}. {c.get('reason')}"
                             + (f" Fix: {c['fix_words']}" if c.get("fix_words") else ""))
        return tools.out("\n".join(lines))

    async def snapshots_list(a: dict[str, Any]) -> str:
        s = await get("/snapshots")
        snaps = list(s.get("snapshots") or [])[-max(1, min(int(a.get("limit") or 20), 100)):]
        lines = [s.get("words") or f"Snapshots: {s.get('backend', 'none')}."]
        for sn in reversed(snaps):
            lines.append(f"- {sn.get('id')}: {sn.get('date', '')} {sn.get('kind_words') or sn.get('kind', '')}"
                         + (f" ({sn.get('why')})" if sn.get("why") else ""))
        if s.get("undo_to"):
            lines.append(f"Undo point before the last update: {s['undo_to']}.")
        return tools.out("\n".join(lines))

    return [
        Tool("health_status", "Computer health",
             "Orion's health monitor: whether the screen, Orion's brain, AI model, voice, phone link, network, disk, "
             "memory, temperature, battery, services and desktop are fine, with the fix for anything that isn't. "
             "Reads only.", "read", _schema({}), health_status),
        Tool("snapshots_list", "List snapshots",
             "The system snapshots Orion can go back to (newest first), and the undo point before the last update. "
             "Reads only: restoring always needs the user's approval.", "read",
             _schema({"limit": {"type": "integer", "description": "How many to list (1-100, default 20)"}}),
             snapshots_list),
    ]
