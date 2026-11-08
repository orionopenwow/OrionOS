"""Self-running, wired into the daemon in one place (docs/ORIONOS_COMPLETE_PLAN.md order of work, step 4:
"Update channels with VM test run, self-repair loop, permission levels, weekly review").

  self_levels.py      permission levels; decide(kind) -> "ask" | "tell" | "do" (every self-change path calls it)
  self_cards.py       "ask" cards and the actions behind them; the autonomy learner's streak counting
  self_repair.py      the self-repair loop on top of the health monitor
  update_channels.py  Stable / Beta / My builds, the self-test (VM when possible), offering a passing version
  self_updates.py     keeping everything current (security, apps, AI models, firmware), hourly scheduler
  weekly_review.py    the Sunday card, and "how did this week go?"

main.py needs only: `app.include_router(self_running.make_router(lambda: daemon))`, `self_running.start(self)` in
start(), `await self_running.stop(self)` in stop(), the two approval kinds in decide_approval, and the chat hook.
Nothing automatic runs while Orion is frozen.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Optional

import self_cards
import self_levels
import self_repair
import self_updates
import skill_dreams  # Skills: nightly review ("dreaming"), opt-in
import update_channels
import weekly_review

log = logging.getLogger("orion.self_running")

KINDS = (self_cards.CARD_KIND, weekly_review.CARD_KIND, skill_dreams.CARD_KIND)
_parts: dict[str, Any] = {}


def _data_dir() -> Path:
    from config import settings
    return Path(settings.data_dir)


def parts(daemon: Any) -> dict[str, Any]:
    if _parts.get("daemon") is not daemon:
        _parts.clear()
        repair = self_repair.SelfRepair(daemon, _data_dir())
        _parts.update({"daemon": daemon, "repair": repair, "updates": self_updates.SelfUpdates(daemon),
                       "review": weekly_review.WeeklyReview(daemon, _data_dir(), repair),
                       "dreams": skill_dreams.NightlyReview(daemon, _data_dir())})  # Skills: nightly review
    return _parts


def repair_or_none() -> Optional[self_repair.SelfRepair]:
    return _parts.get("repair")


def install(daemon: Any) -> None:
    for fn in (self_levels.install_executor, self_cards.install_executor):
        try:
            fn(daemon)
        except Exception:
            log.debug("self-running executor not installed yet", exc_info=True)


def on_orionos() -> bool:
    """Repairs and updates only run by themselves on OrionOS (or when ORION_SELF_RUNNING=1), never on a dev box."""
    import os
    flag = os.environ.get("ORION_SELF_RUNNING", "")
    return flag == "1" or (flag != "0" and Path("/etc/orion").is_dir())


def start(daemon: Any) -> None:
    install(daemon)
    p = parts(daemon)
    if on_orionos():
        p["repair"].start()
        p["updates"].start()
    else:
        log.info("Not OrionOS: self-repair and automatic updates stay off (set ORION_SELF_RUNNING=1 to try them)")
    p["review"].start()
    p["dreams"].start()  # Skills: nightly review (does nothing unless turned on)


async def stop(daemon: Any) -> None:
    if _parts.get("daemon") is not daemon:
        return
    for k in ("repair", "updates", "review", "dreams"):
        try:
            await _parts[k].stop()
        except Exception:
            pass


async def decide_card(daemon: Any, item: dict[str, Any], decision: str) -> None:
    if item.get("kind") == skill_dreams.CARD_KIND:  # Skills: nightly review card
        await skill_dreams.decide_card(daemon, item, decision, parts(daemon)["dreams"].state)
        return
    if item.get("kind") == weekly_review.CARD_KIND:
        p = parts(daemon)
        await weekly_review.decide_card(daemon, item, decision, p["review"].reviews, p["repair"])
        return
    await self_cards.decide_card(daemon, item, decision)


async def chat(daemon: Any, text: str) -> Optional[str]:
    """"How did this week go?" Returns the reply, or None when it isn't that question."""
    try:
        return parts(daemon)["review"].chat(text)
    except Exception:
        log.exception("weekly review chat failed")
        return None


def status(daemon: Any) -> dict[str, Any]:
    import freeze
    p = parts(daemon)
    review = p["review"].reviews.latest()
    return {"frozen": freeze.is_frozen(), "levels": self_levels.current().table(),
            "channel": update_channels.status(), "updates": self_updates.status(),
            "repair": p["repair"].summary(p["repair"].host.now() - 7 * 86400),
            "review": {"latest": review.get("week") if review else None,
                       "words": weekly_review.words(review) if review else ""}}


def describe(st: dict[str, Any]) -> str:
    """The status in plain words (MCP self_status, chat)."""
    lines = ["Orion is FROZEN: nothing changes by itself until you unfreeze it." if st["frozen"] else
             "Orion is running normally (not frozen)."]
    lines.append("Permission levels: " + "; ".join(f"{r['label']}: {r['level_name'].lower()}"
                                                   for r in st["levels"] if r["shown"]))
    ch = st["channel"]
    lines.append(f"Update channel: {ch['channel_name']}. Test new versions in a VM first: "
                 f"{'on' if ch['vm_test'] else 'off'}.")
    offer = ch.get("offer") or {}
    if offer.get("version"):
        lines.append(f"OrionOS {offer['version']}: " + ("passed the self-test, waiting for your OK." if offer.get("passed")
                                                       else "held back (self-test failed)."))
    rep = st["repair"]
    if rep["tried"]:
        lines.append("Self-repair this week: " + "; ".join(f"{t['fix']} ({t['result']})" for t in rep["tried"][-6:]))
    if st["review"]["latest"]:
        lines.append(f"Latest weekly review ({st['review']['latest']}):\n{st['review']['words']}")
    return "\n".join(lines)


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter()
    try:
        install(get_daemon())
    except Exception:
        log.debug("self-running not installed yet", exc_info=True)
    router.include_router(self_levels.make_router(get_daemon))

    async def body(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    @router.get("/self/status")
    async def self_status() -> Any:
        st = status(get_daemon())
        return {**st, "words": describe(st)}

    @router.get("/self/repair")
    async def repair_state() -> Any:
        r = parts(get_daemon())["repair"]
        return {**r.summary(0), "fixes": {k: {"kind": f.kind, "words": f.words, "auto": f.auto}
                                          for k, f in self_repair.FIXES.items()}}

    @router.post("/self/repair/run")
    async def repair_run() -> Any:
        return {"done": await parts(get_daemon())["repair"].run_once()}

    @router.get("/self/channel")
    async def channel() -> Any:
        return update_channels.status()

    @router.get("/self/updates")
    async def updates() -> Any:
        return {**self_updates.status(), "channel": update_channels.status()}

    @router.post("/self/updates/check")
    async def updates_check() -> Any:
        return await parts(get_daemon())["updates"].run_job("system")

    @router.post("/self/updates/run")
    async def updates_run(request: Request) -> Any:
        job = str((await body(request)).get("job") or "")
        if job not in self_updates.JOBS:
            return JSONResponse({"ok": False, "message": "job is one of: " + ", ".join(self_updates.JOBS)},
                                status_code=400)
        return await parts(get_daemon())["updates"].run_job(job)

    @router.post("/self/updates/settings")
    async def updates_settings(request: Request) -> Any:
        b = await body(request)
        if "vm_test" in b:
            on = bool(b["vm_test"])
            self_updates.update_state(lambda s: s.__setitem__("vm_test", on))
            import changes
            changes.record_change(("Turned on" if on else "Turned off") + " testing new versions in a VM first",
                                  kind="setting", source="Settings", approved_by="you")
        return update_channels.status()

    @router.post("/self/after-update")
    async def after_update(request: Request) -> Any:
        return await self_updates.after_update(get_daemon(), str((await body(request)).get("kind") or "security_update"))

    @router.get("/self/review")
    async def review() -> Any:
        return parts(get_daemon())["review"].current()

    @router.post("/self/review/now")
    async def review_now() -> Any:
        p = parts(get_daemon())
        r = await weekly_review.make_review(get_daemon(), p["review"].reviews, p["repair"])
        return {**r, "words": weekly_review.words(r)}

    @router.post("/self/review/{rid}/items/{iid}")
    async def review_item(rid: str, iid: str, request: Request) -> Any:
        p = parts(get_daemon())
        try:
            return await weekly_review.decide_item(get_daemon(), p["review"].reviews, rid, iid,
                                                   bool((await body(request)).get("accept")), p["repair"])
        except KeyError:
            return JSONResponse({"ok": False, "message": "No such review or item."}, status_code=404)

    return router


# =====================================================================================  MCP (read only)
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import Tool, _schema

    async def self_status(a: dict[str, Any]) -> str:
        from mcp_tools import DaemonUnavailable
        try:
            status_code, data = await tools.backend.call("GET", "/self/status")
        except DaemonUnavailable:
            status_code, data = 0, {}
        if status_code != 200 or not isinstance(data, dict):
            return "Orion isn't running, so its self-running status can't be read."
        return tools.out(str(data.get("words") or ""))

    return [Tool("self_status", "Orion's self-running status",
                 "How Orion keeps itself running: the permission levels for self-changes (ask / tell after / just "
                 "do it), whether it's frozen, the update channel and any tested version waiting, what self-repair "
                 "did this week and the latest weekly review.", "read", _schema({}, ()), self_status)]
