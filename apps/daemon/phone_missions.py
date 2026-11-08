"""Send work to the PC from the phone: Mission Control routes on the lab's phone port (9121).

Every route needs a request signed by a paired phone (phones.py). Registered from phone_api.register, like the
other phone routes, so nothing in main.py changes.

  GET  /phone/missions                 read key   the board: state, cost and budget of each mission
  POST /phone/mission                  read key   {"title","repo"?,"agent"?,"budget_usd"?} -> a queued mission
                                                  (source "phone"). It only queues work: it never merges anything.
  POST /phone/mission/{id}/{op}        pause / stop / resume: read key (pausing and stopping only do less)
                                       approve_review / reject: sign key only (your fingerprint on the phone)
                                       stop on work waiting for review: refused (that's a Reject: fingerprint)

Money rule: with the read key, one mission can have up to PHONE_READ_BUDGET_MAX_USD and all open read-key missions
together (queued, running, waiting for you; created or resumed from the phone) up to PHONE_READ_TOTAL_MAX_USD.
Beyond that the PC answers 401 {"needs": "fingerprint"} and the phone asks for the fingerprint (sign key).

Mission Control itself lives in missions.py. It is imported lazily: a PC without it answers plainly with
NOT_HERE instead of failing to start.

The phone can also hand work over in words, by chat or voice ("Orion, have the PC build a landing page"):
phone_api.py asks from_words() first, before the normal conversation.
"""
from __future__ import annotations

import asyncio
import json
import importlib
import logging
import re
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.phone_missions")

NOT_HERE = "Mission Control isn't on this PC yet. Update Orion on the PC."
READ_OPS = ("pause", "resume", "stop")
SIGN_OPS = ("approve_review", "reject")
TITLE_MAX = 300
REPO_MAX = 300
AGENT_MAX = 60
BUDGET_MAX_USD = 1000.0
# Money rule: the read key can queue a mission with the router's usual budget (a few dollars, routing.json) or up to
# this much. A bigger budget is spending money, so it needs the fingerprint (sign) key, like any other approval.
PHONE_READ_BUDGET_MAX_USD = 5.0
# [review-fix 4] ...and that is also the most all read-key missions may have open together (queued, running or waiting
# for you), so many small missions, resumes or chat requests can't add up to more. Beyond it: the fingerprint key.
PHONE_READ_TOTAL_MAX_USD = 5.0
OPEN_STATES = ("queued", "running", "needs_you")
FINGERPRINT_TOTAL = (f"Your phone already has ${PHONE_READ_TOTAL_MAX_USD:,.0f} of work open on the PC. More needs "
                     "your fingerprint.")
REVIEW_STOP = "This work is waiting for your review. Use Reject with your fingerprint."
REPO_NOT_ALLOWED = "Orion can't check that folder on this PC. Update Orion on the PC, or send it without a folder."
BODY_MAX = 8 * 1024
KEYS = ("id", "title", "repo", "agent", "state", "cost_usd", "budget_usd", "started", "branch", "summary")

_override: Any = None   # tests: a fake missions module


def set_module(mod: Any) -> None:
    """Tests: use this object instead of importing missions.py (None goes back to the real import)."""
    global _override
    _override = mod


def module() -> Optional[Any]:
    """missions.py, or None when this PC doesn't have Mission Control yet."""
    if _override is not None:
        return _override
    try:
        return importlib.import_module("missions")
    except Exception as exc:   # ImportError, or a half-installed module: same plain answer either way
        log.info("Mission Control not available: %s", exc)
        return None


def _redact(text: str) -> str:
    try:
        from phone_pair import redact
        return redact(text)
    except Exception:
        return re.sub(r"[A-Za-z0-9+/_\-]{32,}", "[hidden]", text)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if f == f else None   # NaN -> None


def clean(m: Any) -> Optional[dict[str, Any]]:
    """One mission as the phone shows it: only the known fields, short, nothing secret-looking."""
    if not isinstance(m, dict):
        return None
    out: dict[str, Any] = {}
    for k in KEYS:
        v = m.get(k)
        if k in ("cost_usd", "budget_usd"):
            out[k] = _num(v)
        elif k == "repo":
            # Only the folder's name: full paths usually contain the user's own folder name (privacy).
            s = str(v or "").replace("\\", "/").rstrip("/")
            out[k] = _redact(s.rsplit("/", 1)[-1])[:120]
        elif k == "started":
            out[k] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else (str(v)[:40] if v else None)
        else:
            s = " ".join(str(v if v is not None else "").split())
            out[k] = _redact(s)[:400 if k == "summary" else 200]
    if not out["id"]:
        return None
    return out


def list_for_phone() -> dict[str, Any]:
    mod = module()
    if mod is None:
        return {"available": False, "missions": [], "message": NOT_HERE}
    try:
        items = mod.list_missions()
    except Exception as exc:
        log.warning("list_missions failed: %s", exc)
        return {"available": True, "missions": [], "message": "Mission Control didn't answer. Try again."}
    missions = [c for c in (clean(m) for m in list(items or [])[:60]) if c is not None]
    return {"available": True, "missions": missions}


def for_home(limit: int = 5) -> list[dict[str, Any]]:
    """Missions still on the board (not finished), for the robots on the phone's home screen. Never raises."""
    mod = module()
    if mod is None:
        return []
    try:
        items = [c for c in (clean(m) for m in list(mod.list_missions() or [])) if c is not None]
    except Exception:
        return []
    done = ("done", "merged", "failed", "stopped", "rejected", "cancelled", "canceled")
    return [m for m in items if m["state"].lower() not in done][:limit]


def _frozen(what: str) -> Optional[str]:
    try:
        import freeze
        return freeze.blocked(what)
    except Exception:
        return None


def _missions(mod: Any) -> list[dict[str, Any]]:
    try:
        return [m for m in list(mod.list_missions() or []) if isinstance(m, dict)]
    except Exception:
        return []


def open_read_total(mod: Any) -> float:
    """Budget of every open mission the phone's read key started or resumed."""
    total = 0.0
    for m in _missions(mod):
        if m.get("state") in OPEN_STATES and m.get("source") == "phone" and m.get("phone_key") == "read":
            total += _num(m.get("budget_usd")) or 0.0
    return round(total, 4)


def _effective_budget(mod: Any, title: str, agent: Any, budget: Optional[float]) -> float:
    """The budget the mission will get: the one asked for, else the router's usual one (assume the most if unknown)."""
    if budget is not None:
        return budget
    fn = getattr(mod, "default_budget", None)
    if fn is not None:
        try:
            return float(fn(title, str(agent).strip()[:AGENT_MAX] if agent else None))
        except Exception as exc:
            log.info("default_budget failed: %s", exc)
    return PHONE_READ_BUDGET_MAX_USD


def create_needs_sign(title: Any, agent: Any = None, budget: Any = None) -> bool:
    """True when this new mission needs the fingerprint key: its own budget is over the read limit, or with the
    read-key missions already open it would go over the read-key total."""
    b = _num(budget) if budget not in (None, "") else None
    if b is not None and b > PHONE_READ_BUDGET_MAX_USD:
        return True
    mod = module()
    if mod is None:
        return False
    t = " ".join(str(title or "").split())[:TITLE_MAX]
    return open_read_total(mod) + _effective_budget(mod, t, agent, b) > PHONE_READ_TOTAL_MAX_USD + 1e-9


def resume_needs_sign(mission_id: str) -> bool:
    """A resume puts a finished mission back in the queue: with the read key it counts toward the same total."""
    mod = module()
    if mod is None:
        return False
    m = next((x for x in _missions(mod) if str(x.get("id")) == mission_id), None)
    if m is None or m.get("state") in OPEN_STATES:
        return False   # already counted (or nothing to resume)
    return open_read_total(mod) + (_num(m.get("budget_usd")) or 0.0) > PHONE_READ_TOTAL_MAX_USD + 1e-9


def create(title: Any, repo: Any = None, agent: Any = None, budget: Any = None,
           key: str = "read") -> tuple[int, dict[str, Any]]:
    """Queues a mission from the phone. Returns (HTTP status, body). key: which phone key signed it."""
    title = " ".join(str(title or "").split())[:TITLE_MAX]
    if not title:
        return 400, {"error": "Say what the PC should do."}
    repo_s = str(repo).strip()[:REPO_MAX] if repo else None
    agent_s = str(agent).strip()[:AGENT_MAX] if agent else None
    budget_f: Optional[float] = None
    if budget not in (None, ""):
        budget_f = _num(budget)
        if budget_f is None or budget_f <= 0 or budget_f > BUDGET_MAX_USD:
            return 400, {"error": f"The budget must be between $0 and ${BUDGET_MAX_USD:,.0f}."}
    why = _frozen("start new work")
    if why:
        return 409, {"error": why}
    mod = module()
    if mod is None:
        return 503, {"error": NOT_HERE}
    if repo_s:   # [review-fix 3] same folder rule as the HUD: Orion itself or a folder inside the mcp.json roots
        check = getattr(mod, "allowed_repo", None)
        if check is None:
            return 409, {"error": REPO_NOT_ALLOWED}
        try:
            repo_s = check(repo_s)
        except Exception as exc:   # MissionError / mcp_tools.ToolError: a plain reason
            return 409, {"error": str(exc)[:300] or "That folder isn't one Orion lets agents use."}
    if key != "sign":   # [review-fix 4] the read key: per-mission and total money caps
        if budget_f is not None and budget_f > PHONE_READ_BUDGET_MAX_USD:
            return 401, {"error": f"A budget over ${PHONE_READ_BUDGET_MAX_USD:,.0f} needs your fingerprint.",
                         "needs": "fingerprint"}
        eff = _effective_budget(mod, title, agent_s, budget_f)
        if open_read_total(mod) + eff > PHONE_READ_TOTAL_MAX_USD + 1e-9:
            return 401, {"error": FINGERPRINT_TOTAL, "needs": "fingerprint"}
        budget_f = eff   # pin it: the mission gets exactly the budget that was counted
    try:
        m = mod.create_mission(title, repo=repo_s, agent=agent_s, budget_usd=budget_f, race=1, source="phone",
                               phone_key="sign" if key == "sign" else "read")
    except (ValueError, PermissionError, LookupError) as exc:
        return 409, {"error": str(exc) or "Mission Control said no."}
    except Exception as exc:
        log.warning("create_mission failed: %s", exc)
        return 500, {"error": "Mission Control couldn't take it. Try again."}
    mission = clean(m) or {"title": title}
    return 200, {"ok": True, "mission": mission,
                 "message": f"Sent to your PC: \"{mission.get('title') or title}\". It's queued."}


def act(mission_id: str, op: str, key: str = "read") -> tuple[int, dict[str, Any]]:
    if op not in READ_OPS + SIGN_OPS:
        return 400, {"error": "Use pause, resume, stop, approve_review or reject."}
    if op == "resume":
        why = _frozen("start work again")
        if why:
            return 409, {"error": why}
    mod = module()
    if mod is None:
        return 503, {"error": NOT_HERE}
    mission_id = mission_id[:120]
    if key != "sign" and op in ("stop", "resume"):
        cur = next((x for x in _missions(mod) if str(x.get("id")) == mission_id), None)
        # [review-fix 2] stopping finished work throws it away: that's a Reject, and Reject needs the fingerprint
        if op == "stop" and cur is not None and cur.get("state") == "review":
            return 409, {"error": REVIEW_STOP, "needs": "fingerprint"}
        # [review-fix 4] a resume with the read key counts toward the read-key total
        if op == "resume" and cur is not None and cur.get("state") not in OPEN_STATES and \
                open_read_total(mod) + (_num(cur.get("budget_usd")) or 0.0) > PHONE_READ_TOTAL_MAX_USD + 1e-9:
            return 401, {"error": FINGERPRINT_TOTAL, "needs": "fingerprint"}
    try:
        if op == "resume":
            r = mod.act(mission_id, op, phone_key="sign" if key == "sign" else "read")
        else:
            r = mod.act(mission_id, op)
    except KeyError:
        return 404, {"error": "That mission isn't on the board any more."}
    except (ValueError, PermissionError, LookupError) as exc:
        return 409, {"error": str(exc) or "Mission Control said no."}
    except Exception as exc:
        log.warning("missions.act(%s) failed: %s", op, exc)
        return 500, {"error": "Mission Control couldn't do that. Try again."}
    body: dict[str, Any] = {"ok": True}
    if isinstance(r, dict):
        if r.get("ok") is False or r.get("error"):
            return 409, {"error": str(r.get("error") or r.get("message") or "Mission Control said no.")}
        c = clean(r.get("mission") if isinstance(r.get("mission"), dict) else r)
        if c is not None:
            body["mission"] = c
        if r.get("message"):
            body["message"] = str(r["message"])[:300]
    body.setdefault("message", {"pause": "Paused.", "resume": "Running again.", "stop": "Stopped.",
                                "approve_review": "Review approved.", "reject": "Rejected."}[op])
    return 200, body


# ------------------------------------------------------------------ words: "Orion, have the PC build ..."
_WORDS = re.compile(
    r"^\s*(?:hey\s+)?(?:orion[\s,:]+)?(?:please\s+)?"
    r"(?:(?:have|get|ask|tell)\s+(?:the|my)\s+(?:pc|computer|lab)\s+(?:to\s+)?"
    r"|send\s+(?:this\s+)?(?:work\s+)?to\s+(?:the|my)\s+(?:pc|computer|lab)\s*[:,\-]?\s*(?:to\s+)?)"
    r"(?P<task>.+?)\s*[.!]?\s*$", re.I | re.S)


def parse_words(text: str) -> Optional[str]:
    """The mission title in "have the PC build a landing page" ("Build a landing page"), else None."""
    m = _WORDS.match(text or "")
    if not m:
        return None
    task = " ".join(m.group("task").split()).strip(" ,.:;-")
    if len(task) < 3:
        return None
    return (task[0].upper() + task[1:])[:TITLE_MAX]


async def from_words(text: str) -> Optional[str]:
    """If [text] hands work to the PC, queue it and return the reply; otherwise None (a normal conversation)."""
    title = parse_words(text)
    if title is None:
        return None
    status, body = await asyncio.to_thread(create, title)
    if status == 200:
        return f"Sent to your PC's Mission Control: \"{body['mission'].get('title') or title}\". It's queued. " \
               "Reviews still need your fingerprint."
    return str(body.get("error") or "Mission Control couldn't take it.")


# ------------------------------------------------------------------ routes (via phone_api.register)
def hook(app: Any, phone_auth: Any, json_body: Any) -> None:
    @app.get("/phone/missions")
    async def phone_missions(request: Request):
        who, err = await phone_auth(request, "read")
        if err is not None:
            return err
        return await asyncio.to_thread(list_for_phone)

    @app.post("/phone/mission")
    async def phone_mission_new(request: Request):
        # Peek at the budget first: up to PHONE_READ_BUDGET_MAX_USD the read key is enough (queueing work);
        # more than that is spending money, so only the fingerprint (sign) key can ask for it.
        # [review-fix 4] ...and so is going over the read-key total of open phone work.
        try:
            peek = json.loads((await request.body()) or b"{}")
            if not isinstance(peek, dict):
                peek = {}
        except Exception:
            peek = {}
        try:
            big = await asyncio.to_thread(create_needs_sign, peek.get("title"), peek.get("agent"),
                                          peek.get("budget_usd"))
        except Exception:
            big = False
        who, err = await phone_auth(request, "sign" if big else "read", BODY_MAX)
        if err is not None:
            if big:
                b = _num(peek.get("budget_usd"))
                msg = (f"A budget over ${PHONE_READ_BUDGET_MAX_USD:,.0f} needs your fingerprint."
                       if b is not None and b > PHONE_READ_BUDGET_MAX_USD else FINGERPRINT_TOTAL)
                return JSONResponse({"error": msg, "needs": "fingerprint"}, status_code=401)
            return err
        data = await json_body(request)
        if data is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        status, body = await asyncio.to_thread(create, data.get("title"), data.get("repo"), data.get("agent"),
                                               data.get("budget_usd"), "sign" if big else "read")
        return body if status == 200 else JSONResponse(body, status_code=status)

    @app.post("/phone/mission/{mission_id}/{op}")
    async def phone_mission_act(mission_id: str, op: str, request: Request):
        # Pausing, stopping and resuming: the read key. Approving or rejecting a review: the fingerprint key only.
        # [review-fix 4] A resume over the read-key money total needs the fingerprint key too.
        need = "sign" if op in SIGN_OPS else "read"
        if op == "resume" and await asyncio.to_thread(resume_needs_sign, mission_id[:120]):
            need = "sign"
        who, err = await phone_auth(request, need, BODY_MAX)
        if err is not None:
            if op == "resume" and need == "sign":
                return JSONResponse({"error": FINGERPRINT_TOTAL, "needs": "fingerprint"}, status_code=401)
            return err
        status, body = await asyncio.to_thread(act, mission_id, op, need)
        return body if status == 200 else JSONResponse(body, status_code=status)
