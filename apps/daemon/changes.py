"""Change history (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 6 "A readable change history").

Every change Orion makes to itself or to the computer goes in one append-only list: what changed, why, who
approved it, which checks it passed, and how to undo it when that's possible. One tap undoes a change; undoing a
sensitive one (putting back a snapshot, removing an app) first waits for your approval on the phone or the
boardroom.

Where entries come from:
  - the daemon itself: `record_change(...)` (snapshots, freeze, health, approvals...), kept in data_dir/changes.db
  - Get apps (os/getapps/orion_apps.py) writes ~/.local/state/orion/changes/apps.jsonl
  - root tools (orion-snapshot, the apt hook) write /var/lib/orion/changes/system.jsonl
The two JSON-lines files are read as they are (read-only here) and shown in the same list.

Rows are never edited or deleted; an undo is a new row that points at the one it undid.

HTTP (local only):
    GET  /changes?limit=100          newest first, all sources
    GET  /changes/{id}
    POST /changes                    {what, why?, kind?, source?, approved_by?, tests?, undo?}  (local tools)
    POST /changes/{id}/undo          runs the undo now, or asks for approval first when it's sensitive
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.changes")

UNDO_TASK = "safety.undo"
# Programs an undo from the Get apps history may run (its entries carry an argv); nothing else.
APP_UNDO_PROGRAMS = {"orion-apps", "flatpak"}


def apps_history_path() -> Path:
    env = os.environ.get("ORION_APPS_HISTORY")
    if env:
        return Path(env)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "orion" / "changes" / "apps.jsonl"


def system_history_path() -> Path:
    return Path(os.environ.get("ORION_SYSTEM_CHANGES", "/var/lib/orion/changes/system.jsonl"))


def _read_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _plain_apps_entry(e: dict[str, Any]) -> dict[str, Any]:
    """A Get apps history line, in the same shape as our own rows."""
    kind = str(e.get("kind") or "app_change")
    name = e.get("name") or e.get("app") or ", ".join(e.get("refs") or []) or "an app"
    what = {
        "app_update": f"Updated {name}", "app_update_failed": f"Tried to update {name} (it failed)",
        "app_rollback": f"Put back the previous version of {name}", "app_install": f"Installed {name}",
        "app_remove": f"Removed {name}", "app_resume": f"Let {name} update again",
        "runtime_update": "Updated shared app parts (runtimes)", "runtime_rollback": "Put back shared app parts",
    }.get(kind, f"{kind.replace('_', ' ').capitalize()}: {name}")
    undo = e.get("undo")
    undo_action = None
    if isinstance(undo, list) and undo:
        argvs = undo if isinstance(undo[0], list) else [undo]
        if all(isinstance(a, list) and a and a[0] in APP_UNDO_PROGRAMS for a in argvs):
            sensitive = any(("remove" in a or "install" in a) for a in argvs)
            undo_action = {"kind": "argv", "argv": argvs, "label": "Undo this app change", "sensitive": sensitive}
    checks = e.get("checks") or []
    return {"id": str(e.get("id") or f"apps-{int(float(e.get('ts') or 0) * 1000)}"),
            "ts": float(e.get("ts") or 0), "kind": kind, "source": "get-apps", "what": what,
            "why": str(e.get("why") or ""), "approved_by": str(e.get("approved_by") or ""),
            "tests": checks if isinstance(checks, list) else [], "result": str(e.get("result") or ""),
            "undo": undo_action, "undone_by": None, "undoes": None, "details": {}}


def _plain_system_entry(e: dict[str, Any]) -> dict[str, Any]:
    undo = e.get("undo") if isinstance(e.get("undo"), dict) else None
    return {"id": str(e.get("id") or f"sys-{int(float(e.get('ts') or 0) * 1000)}"),
            "ts": float(e.get("ts") or 0), "kind": str(e.get("kind") or "system"),
            "source": str(e.get("source") or "system"), "what": str(e.get("what") or ""),
            "why": str(e.get("why") or ""), "approved_by": str(e.get("approved_by") or ""),
            "tests": e.get("tests") if isinstance(e.get("tests"), list) else [],
            "result": str(e.get("result") or ""), "undo": undo, "undone_by": None, "undoes": None,
            "details": e.get("details") if isinstance(e.get("details"), dict) else {}}


class ChangeLog:
    """Append-only SQLite list of what Orion changed. Thread-safe; rows are never updated except the
    `undone_by` link, which is set once."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS changes(
            id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, what TEXT NOT NULL,
            why TEXT, approved_by TEXT, tests TEXT, result TEXT, undo TEXT, undone_by TEXT, undoes TEXT,
            details TEXT)""")
        # append-only: a trigger refuses deletes, and updates may only fill in undone_by once
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS changes_no_delete BEFORE DELETE ON changes
            BEGIN SELECT RAISE(ABORT, 'the change history is append-only'); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS changes_no_edit BEFORE UPDATE ON changes
            WHEN OLD.undone_by IS NOT NULL OR NEW.id != OLD.id OR NEW.what != OLD.what OR NEW.ts != OLD.ts
              OR NEW.kind != OLD.kind OR IFNULL(NEW.undo, '') != IFNULL(OLD.undo, '')
            BEGIN SELECT RAISE(ABORT, 'the change history is append-only'); END""")
        self.db.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        def j(v: Optional[str], default: Any) -> Any:
            try:
                return json.loads(v) if v else default
            except ValueError:
                return default
        return {"id": r["id"], "ts": r["ts"], "kind": r["kind"], "source": r["source"], "what": r["what"],
                "why": r["why"] or "", "approved_by": r["approved_by"] or "", "tests": j(r["tests"], []),
                "result": r["result"] or "", "undo": j(r["undo"], None), "undone_by": r["undone_by"],
                "undoes": r["undoes"], "details": j(r["details"], {})}

    def add(self, what: str, *, why: str = "", kind: str = "change", source: str = "orion", approved_by: str = "",
            tests: Optional[list[Any]] = None, result: str = "", undo: Optional[dict[str, Any]] = None,
            undoes: Optional[str] = None, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        what = " ".join(str(what or "").split())[:300]
        if not what:
            raise ValueError("a change needs a short description of what changed")
        if undo is not None and (not isinstance(undo, dict) or not undo.get("kind")):
            raise ValueError("undo must be an object with a kind")
        cid = f"chg-{uuid.uuid4().hex[:12]}"
        with self.lock:
            self.db.execute(
                "INSERT INTO changes(id, ts, kind, source, what, why, approved_by, tests, result, undo, undoes, details)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, time.time(), str(kind)[:60], str(source)[:60], what, " ".join(str(why or "").split())[:600],
                 str(approved_by or "")[:120], json.dumps(tests or []), str(result or "")[:300],
                 json.dumps(undo) if undo else None, undoes, json.dumps(details or {}, default=str)))
            self.db.commit()
        return self.get(cid) or {}

    def get(self, cid: str) -> Optional[dict[str, Any]]:
        with self.lock:
            r = self.db.execute("SELECT * FROM changes WHERE id=?", (cid,)).fetchone()
        if r is not None:
            return self._row(r)
        for e in self.external(1000):
            if e["id"] == cid:
                return e
        return None

    def mark_undone(self, cid: str, by: str) -> None:
        with self.lock:
            self.db.execute("UPDATE changes SET undone_by=? WHERE id=? AND undone_by IS NULL", (by, cid))
            self.db.commit()

    def own(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM changes ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def external(self, limit: int = 200) -> list[dict[str, Any]]:
        out = [_plain_apps_entry(e) for e in _read_jsonl(apps_history_path(), limit)]
        out += [_plain_system_entry(e) for e in _read_jsonl(system_history_path(), limit)]
        undone = {}
        with self.lock:
            for r in self.db.execute("SELECT id, undoes FROM changes WHERE undoes IS NOT NULL").fetchall():
                undone[r["undoes"]] = r["id"]
        for e in out:
            e["undone_by"] = undone.get(e["id"])
        return out

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        items = self.own(limit) + self.external(limit)
        items.sort(key=lambda e: e["ts"], reverse=True)
        return items[:limit]


# =====================================================================================  module helpers
_log: Optional[ChangeLog] = None
_lock = threading.Lock()


def install(log_: ChangeLog) -> None:
    global _log
    _log = log_


def current() -> ChangeLog:
    """The daemon's change log (made on first use in its data folder)."""
    global _log
    with _lock:
        if _log is None:
            from config import settings
            _log = ChangeLog(Path(settings.data_dir) / "changes.db")
        return _log


def record_change(what: str, *, why: str = "", kind: str = "change", source: str = "orion", approved_by: str = "",
                  tests: Optional[list[Any]] = None, result: str = "", undo: Optional[dict[str, Any]] = None,
                  details: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Note a change. Never raises: a broken history must not break the thing that changed.

    undo, when there is a way back: {"kind": <handler>, "label": "Put back ...", "sensitive": bool, ...args}
    """
    try:
        return current().add(what, why=why, kind=kind, source=source, approved_by=approved_by, tests=tests,
                             result=result, undo=undo, details=details)
    except Exception:
        log.exception("couldn't record a change")
        return None


# Undo handlers: kind -> fn(daemon, change, undo) -> {"ok": bool, "message": str}. Modules register theirs.
UndoFn = Callable[[Any, dict[str, Any], dict[str, Any]], Any]
UNDO_HANDLERS: dict[str, UndoFn] = {}


def register_undo(kind: str, fn: UndoFn) -> None:
    UNDO_HANDLERS[kind] = fn


def _undo_argv(daemon: Any, change: dict[str, Any], undo: dict[str, Any]) -> dict[str, Any]:
    import shutil
    import subprocess
    msgs = []
    for argv in undo.get("argv") or []:
        if not (isinstance(argv, list) and argv and argv[0] in APP_UNDO_PROGRAMS):
            return {"ok": False, "message": "That undo isn't one Orion is allowed to run."}
        exe = shutil.which(argv[0])
        if not exe:
            return {"ok": False, "message": f"{argv[0]} isn't installed here, so this can't be undone."}
        try:
            r = subprocess.run([exe, *map(str, argv[1:])], capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "message": f"The undo didn't finish: {exc}"}
        if r.returncode != 0:
            return {"ok": False, "message": ((r.stderr or r.stdout or "").strip()[-300:]) or "The undo failed."}
        msgs.append((r.stdout or "").strip()[-200:])
    return {"ok": True, "message": "Undone." + (" " + msgs[-1] if msgs and msgs[-1] else "")}


register_undo("argv", _undo_argv)


def _register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(UNDO_TASK, TaskType(UNDO_TASK, "Undo a change Orion made", "system",
                                                        reversible=False, money=False, sensitive=True))


async def _run_undo(daemon: Any, change: dict[str, Any], approved_by: str) -> dict[str, Any]:
    import asyncio
    import inspect
    undo = change.get("undo") or {}
    fn = UNDO_HANDLERS.get(str(undo.get("kind")))
    if fn is None:
        return {"ok": False, "message": "Orion doesn't know how to undo this one."}
    if inspect.iscoroutinefunction(fn):
        res = await fn(daemon, change, undo)
    else:   # handlers that run programs block: keep them off the event loop
        res = await asyncio.to_thread(fn, daemon, change, undo)
        if inspect.isawaitable(res):
            res = await res
    res = res if isinstance(res, dict) else {"ok": bool(res), "message": ""}
    if res.get("ok") and not res.get("pending"):
        row = current().add(f"Undid: {change['what']}"[:300], why="you asked to undo it", kind="undo",
                            source="change-history", approved_by=approved_by, result=res.get("message", ""),
                            undoes=change["id"])
        current().mark_undone(change["id"], row["id"])   # rows from the history files are linked by `undoes`
    return res


async def undo(daemon: Any, cid: str) -> dict[str, Any]:
    """One tap. Sensitive undos become a decision you sign; the rest run now."""
    change = current().get(cid)
    if change is None:
        raise KeyError(cid)
    if change.get("undone_by"):
        return {"ok": False, "message": "This change was already undone."}
    u = change.get("undo")
    if not u:
        return {"ok": False, "message": "There's no automatic way back for this change."}
    try:
        import freeze
        if freeze.is_frozen() and u.get("kind") != "unfreeze":
            return {"ok": False, "message": freeze.MESSAGE}
    except ImportError:
        pass
    if u.get("sensitive"):
        board = getattr(daemon, "board", None)
        if board is None:
            return {"ok": False, "message": "Orion is still starting."}
        _register_task_type()
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (UNDO_TASK,))
        dec = await board.propose(UNDO_TASK, f"Undo: {change['what'][:140]}",
                                  {"change_id": cid, "what": change["what"], "how": u.get("label", ""),
                                   "why_it_asks": "putting things back can remove newer work"},
                                  proposer="Change history", risk="medium", simulated=False)
        return {"ok": True, "pending": True, "decision": dec["id"],
                "message": "Waiting for your approval on your phone or the boardroom."}
    return await _run_undo(daemon, change, "you (one tap)")


def install_executor(daemon: Any) -> None:
    """Approved undo decisions run here (boardroom executors are sync; the work runs in the background)."""
    _register_task_type()
    board = getattr(daemon, "board", None)
    if board is None:
        return
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (UNDO_TASK,))
    except Exception:
        pass

    def execute(d: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        change = current().get(d["packet"]["details"].get("change_id", ""))
        if change is None or change.get("undone_by"):
            raise ValueError("This change is gone or was already undone.")
        signer = "your approval"
        asyncio.get_running_loop().create_task(_run_undo(daemon, change, signer))
        return {"started": True}

    board.executors[UNDO_TASK] = execute


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/changes", tags=["safety"])
    try:
        install_executor(get_daemon())
    except Exception:
        log.debug("change history executor not installed yet", exc_info=True)

    @router.get("")
    async def changes_list(limit: int = 100) -> Any:
        return {"changes": current().list(limit)}

    @router.get("/{cid}")
    async def changes_get(cid: str) -> Any:
        c = current().get(cid)
        if c is None:
            return JSONResponse({"error": "No such change."}, status_code=404)
        return {"change": c}

    @router.post("")
    async def changes_add(request: Request) -> Any:
        try:
            b = await request.json()
        except ValueError:
            b = None
        if not isinstance(b, dict):
            return JSONResponse({"error": "bad request"}, status_code=400)
        undo = b.get("undo") if isinstance(b.get("undo"), dict) else None
        if undo is not None and undo.get("kind") not in UNDO_HANDLERS:
            undo = None    # outside tools can describe a change, not invent new ways to run things
        try:
            c = current().add(str(b.get("what") or ""), why=str(b.get("why") or ""),
                              kind=str(b.get("kind") or "change"), source=str(b.get("source") or "local tool"),
                              approved_by=str(b.get("approved_by") or ""),
                              tests=b.get("tests") if isinstance(b.get("tests"), list) else None,
                              result=str(b.get("result") or ""), undo=undo)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"change": c}

    @router.post("/{cid}/undo")
    async def changes_undo(cid: str) -> Any:
        try:
            return await undo(get_daemon(), cid)
        except KeyError:
            return JSONResponse({"error": "No such change."}, status_code=404)

    return router
