"""Permission levels for self-changes (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 4 and its table).

Every path where Orion changes itself or the computer on its own calls ONE function first:

    decide(kind) -> "ask" | "tell" | "do"

  ask   Level 1: always ask. The change becomes a card (or a phone decision); nothing runs until you say yes.
  tell  Level 2: tell me after. It runs, then you get a note (and it's in the change history with an undo).
  do    Level 3: just do it. It runs and is only written in the change history.

While Orion is frozen (kill switch) decide() answers "ask" for everything, and the automatic loops don't start
at all, so nothing runs by itself.

Where the levels live:
  - data_dir/self_levels.json       what you lowered, or raised with your phone's OK (and the approval streaks)
  - /etc/orion/self-levels.json     what you set with the orion-admin password (orion-self-helper level ...)
  For each kind the newest of the two wins; anything missing uses the plan's default. Levels are always clamped
  to what a kind allows: the protected core, Orion's own brain (daemon code), firmware and new system versions
  can't be raised at all.

Changing a level:
  - lowering (safer) applies at once, from Settings or here;
  - raising waits for your phone (a boardroom decision, fingerprint) or is done with the admin password.
The autonomy learner counts your yeses: after a run of STREAK_TO_SUGGEST approvals in a row it *suggests*
raising that kind one step (as a phone card). It never raises anything itself.

HTTP (local):
    GET  /self/levels                      the table (kind, label, level, default, max, raisable, where it's set)
    POST /self/levels {kind, level}        lower now, or ask your phone to raise
    GET  /self/decide/{kind}               {"decision", "frozen", "level"}  (used by orion-apps and the gate)
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.self_levels")

LEVEL_TASK = "self.level"
STREAK_TO_SUGGEST = 20
WORDS = {1: "ask", 2: "tell", 3: "do"}
LEVEL_NAMES = {1: "Always ask", 2: "Tell me after", 3: "Just do it"}


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    default: int
    max: int
    note: str = ""
    fingerprint: bool = False     # asking means your phone's fingerprint, not just a tap
    shown: bool = True            # a row on the Settings page

    @property
    def raisable(self) -> bool:
        return self.max > 1


KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("security_update", "Security updates", 3, 3, "With a snapshot first and a health check after"),
    Kind("app_update", "App and Flatpak updates", 2, 3, "Each app is checked after; a broken one goes back"),
    Kind("routine_fix", "Restarting a stuck service, clearing caches", 3, 3,
         "Checked again after; undone if it made things worse"),
    Kind("system_version", "New system version", 1, 1,
         "Tested first, then into the spare system copy with automatic rollback"),
    Kind("ai_model", "AI model updates", 2, 3, "Tried with a small question before and after; put back if broken"),
    Kind("ui_code", "Orion changing its own screens", 1, 2, ""),
    Kind("daemon_code", "Orion changing its own brain", 1, 1, "Needs your fingerprint", fingerprint=True),
    Kind("firmware", "Drivers and firmware", 1, 1, "Orion only tells you; it never flashes firmware by itself"),
    Kind("unknown_fix", "A fix Orion worked out itself", 1, 1, "Always shown to you first", shown=False),
    Kind("protected_core", "Protected core", 1, 1, "Approvals, the signer, the firewall, privacy rules and the "
         "self-change gate. Always asks, with your fingerprint. Never automatic. Can't be raised.",
         fingerprint=True),
)}


def admin_path() -> Path:
    return Path(os.environ.get("ORION_SELF_LEVELS_ADMIN", "/etc/orion/self-levels.json"))


def _read_json(p: Path) -> dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _frozen() -> bool:
    try:
        import freeze
        return freeze.is_frozen()
    except Exception:
        return False


class SelfLevels:
    def __init__(self, data_dir: Path, admin: Optional[Path] = None, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(data_dir) / "self_levels.json"
        self.admin = admin
        self.clock = clock
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ storage
    def _state(self) -> dict[str, Any]:
        st = _read_json(self.path)
        st.setdefault("levels", {})
        st.setdefault("streaks", {})
        st.setdefault("suggested", {})
        return st

    def _save(self, st: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _admin(self) -> dict[str, Any]:
        d = _read_json(self.admin or admin_path())
        return d.get("levels", d) if isinstance(d.get("levels", d), dict) else {}

    # ------------------------------------------------------------------ reading
    def entry(self, kind: str) -> dict[str, Any]:
        """{"level", "by", "at", "where"} for one kind."""
        k = KINDS.get(kind)
        if k is None:
            raise KeyError(kind)
        best: dict[str, Any] = {"level": k.default, "by": "default", "at": 0.0, "where": "default"}
        for where, src in (("orion", self._state()["levels"]), ("admin", self._admin())):
            e = src.get(kind)
            if not isinstance(e, dict):
                continue
            try:
                lvl, at = int(e.get("level")), float(e.get("at") or 0)
            except (TypeError, ValueError):
                continue
            if at >= best["at"]:
                best = {"level": lvl, "by": str(e.get("by") or where)[:80], "at": at, "where": where}
        best["level"] = max(1, min(int(best["level"]), k.max))
        return best

    def level(self, kind: str) -> int:
        return self.entry(kind)["level"]

    def decide(self, kind: str) -> str:
        if _frozen():
            return "ask"
        if kind not in KINDS:
            return "ask"        # anything we don't know is asked about
        return WORDS[self.level(kind)]

    def table(self) -> list[dict[str, Any]]:
        st = self._state()
        out = []
        for k in KINDS.values():
            e = self.entry(k.key)
            out.append({"kind": k.key, "label": k.label, "note": k.note, "level": e["level"],
                        "level_name": LEVEL_NAMES[e["level"]], "decision": WORDS[e["level"]], "default": k.default,
                        "max": k.max, "raisable": k.raisable, "fingerprint": k.fingerprint, "shown": k.shown,
                        "set_by": e["by"], "set_at": e["at"], "where": e["where"],
                        "streak": int(st["streaks"].get(k.key, 0))})
        return out

    # ------------------------------------------------------------------ changing
    def set_level(self, kind: str, level: int, by: str) -> int:
        """Store a level. Callers make sure raising was approved; this only clamps."""
        k = KINDS[kind]
        level = max(1, min(int(level), k.max))
        with self.lock:
            st = self._state()
            st["levels"][kind] = {"level": level, "by": " ".join(str(by).split())[:80], "at": self.clock()}
            st["streaks"][kind] = 0
            st["suggested"].pop(kind, None)
            self._save(st)
        return level

    def record_verdict(self, kind: str, approved: bool) -> Optional[int]:
        """A yes or no on a card of this kind. Returns the level worth suggesting, when a streak earned one."""
        if kind not in KINDS:
            return None
        with self.lock:
            st = self._state()
            streak = int(st["streaks"].get(kind, 0)) + 1 if approved else 0
            st["streaks"][kind] = streak
            if not approved:
                st["suggested"].pop(kind, None)
            self._save(st)
        k = KINDS[kind]
        lvl = self.level(kind)
        if approved and streak >= STREAK_TO_SUGGEST and lvl < k.max and not k.fingerprint \
                and not self._state()["suggested"].get(kind):
            return lvl + 1
        return None

    def mark_suggested(self, kind: str, decision_id: Optional[str]) -> None:
        with self.lock:
            st = self._state()
            if decision_id:
                st["suggested"][kind] = decision_id
            else:
                st["suggested"].pop(kind, None)
            self._save(st)


# =====================================================================================  module helpers
_cur: Optional[SelfLevels] = None
_cur_lock = threading.Lock()


def install(levels: Optional[SelfLevels]) -> None:
    global _cur
    _cur = levels


def current() -> SelfLevels:
    global _cur
    with _cur_lock:
        if _cur is None:
            from config import settings
            _cur = SelfLevels(Path(settings.data_dir))
        return _cur


def decide(kind: str) -> str:
    """The one gate every self-change path calls: "ask", "tell" or "do". Frozen -> "ask"."""
    try:
        return current().decide(kind)
    except Exception:
        log.exception("couldn't read the permission level for %s; asking instead", kind)
        return "ask"


def _register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    # sensitive: the boardroom's own learner never lets this run by itself (raising always needs your OK)
    boardroom.TASK_TYPES.setdefault(LEVEL_TASK, TaskType(LEVEL_TASK, "Let Orion do more on its own", "system",
                                                         reversible=True, money=False, sensitive=True))


async def request_change(daemon: Any, kind: str, level: int, by: str = "you",
                         why: str = "") -> dict[str, Any]:
    """Lower now; raise only after your phone says yes. Never raises by itself."""
    k = KINDS.get(kind)
    if k is None:
        raise KeyError(kind)
    try:
        level = int(level)
    except (TypeError, ValueError):
        raise ValueError("The level is 1 (always ask), 2 (tell me after) or 3 (just do it).")
    if level < 1 or level > 3:
        raise ValueError("The level is 1 (always ask), 2 (tell me after) or 3 (just do it).")
    lv = current()
    now = lv.level(kind)
    if level > k.max:
        return {"ok": False, "level": now,
                "message": f"“{k.label}” can't go above {LEVEL_NAMES[k.max].lower()}."}
    if level == now:
        return {"ok": True, "level": now, "message": f"“{k.label}” is already set to {LEVEL_NAMES[now].lower()}."}
    if level < now:
        lv.set_level(kind, level, by)
        _note(f"Lowered “{k.label}” to {LEVEL_NAMES[level].lower()}", why or "you lowered it", by)
        return {"ok": True, "level": level, "message": f"Done: “{k.label}” is now {LEVEL_NAMES[level].lower()}."}
    board = getattr(daemon, "board", None)
    if board is None:
        return {"ok": False, "level": now, "message": "Orion is still starting. Try again in a moment."}
    _register_task_type()
    board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (LEVEL_TASK,))
    for d in board.pending():
        det = d["packet"]["details"]
        if d["task_type"] == LEVEL_TASK and det.get("kind") == kind and int(det.get("to_level", 0)) == level:
            return {"ok": True, "pending": True, "decision": d["id"], "level": now,
                    "message": "Already waiting for your OK on your phone."}
    dec = await board.propose(
        LEVEL_TASK, f"Let Orion {LEVEL_NAMES[level].lower()} for “{k.label}”"[:200],
        {"kind": kind, "label": k.label, "from_level": now, "to_level": level, "asked_by": str(by)[:60],
         "why": " ".join(str(why or "").split())[:300],
         "what_changes": ("Orion will do these on its own and tell you afterwards. You can undo them."
                          if level == 2 else "Orion will do these on its own and only write them in the change "
                          "history.")},
        proposer=str(by)[:60] or "you", risk="medium", simulated=False)
    return {"ok": True, "pending": True, "decision": dec["id"], "level": now,
            "message": "Asked your phone. The level changes when you approve it there."}


async def suggest_raise(daemon: Any, kind: str, level: int) -> Optional[dict[str, Any]]:
    """The autonomy learner's suggestion after a streak: a phone card, never a change."""
    k = KINDS[kind]
    streak = current()._state()["streaks"].get(kind, 0)
    res = await request_change(daemon, kind, level, by="Autonomy learner",
                               why=f"You approved {streak} of these in a row. Should Orion just "
                                   f"{'tell you after' if level == 2 else 'do them'} from now on?")
    if res.get("decision"):
        current().mark_suggested(kind, res["decision"])
    return res


def _note(what: str, why: str, by: str) -> None:
    try:
        import changes
        changes.record_change(what, why=why, kind="permission_level", source="permission levels", approved_by=by)
    except Exception:
        pass


def install_executor(daemon: Any) -> None:
    _register_task_type()
    board = getattr(daemon, "board", None)
    if board is None:
        return
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (LEVEL_TASK,))
    except Exception:
        pass

    def execute(d: dict[str, Any]) -> dict[str, Any]:
        det = d["packet"]["details"]
        kind = str(det.get("kind", ""))
        if kind not in KINDS:
            raise ValueError("That kind of change doesn't exist any more.")
        if current().level(kind) != int(det.get("from_level", 0)):
            raise ValueError("This is out of date: the level changed since it was asked.")
        lvl = current().set_level(kind, int(det.get("to_level", 1)), "your phone (fingerprint)")
        _note(f"Raised “{KINDS[kind].label}” to {LEVEL_NAMES[lvl].lower()}", det.get("why") or "you approved it",
              "your phone (fingerprint)")
        return {"kind": kind, "level": lvl}

    board.executors[LEVEL_TASK] = execute

    def decided(d: dict[str, Any]) -> None:   # a "no" to the learner's suggestion: start counting again
        if d.get("task_type") == LEVEL_TASK and d.get("status") in ("rejected", "failed", "cancelled"):
            kind = str(d["packet"]["details"].get("kind", ""))
            if kind in KINDS:
                current().mark_suggested(kind, None)
                current().record_verdict(kind, False)
    if decided not in board.on_decided:
        board.on_decided.append(decided)


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/self", tags=["self-running"])

    @router.get("/levels")
    async def levels() -> Any:
        return {"levels": current().table(), "frozen": _frozen(), "streak_to_suggest": STREAK_TO_SUGGEST}

    @router.post("/levels")
    async def set_levels(request: Request) -> Any:
        try:
            b = await request.json()
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return JSONResponse({"ok": False, "message": "bad request"}, status_code=400)
        try:
            return await request_change(get_daemon(), str(b.get("kind") or ""), b.get("level"),
                                        by=str(b.get("by") or "you")[:40], why=str(b.get("why") or ""))
        except KeyError:
            return JSONResponse({"ok": False, "message": "There's no such kind of change."}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    @router.get("/decide/{kind}")
    async def decide_route(kind: str) -> Any:
        lvl = current().level(kind) if kind in KINDS else 1
        return {"kind": kind, "decision": decide(kind), "frozen": _frozen(), "level": lvl}

    return router
