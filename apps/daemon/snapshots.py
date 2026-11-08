"""Snapshots, daemon side (docs/ORIONOS_COMPLETE_PLAN.md Part 1 "Undo for the whole system").

The real work is the root command `orion-snapshot` (os/lab/bin/orion-snapshot): btrfs + snapper on an installed
OrionOS, copies on the vault when running from the USB stick. This module shows what's there, takes a snapshot
when asked (polkit lets the session user take one, nothing more), and turns "put this back" into a decision you
sign, because a restore replaces newer work.

After you approve a restore it still needs the admin password: Orion opens a terminal that asks for it (the same
two locks as `orion-ctl undo`). Orion never holds that password.

HTTP (local):
    GET  /snapshots                          {backend, snapshots, space, words, undo_to}
    POST /snapshots              {why?}      take one now
    POST /snapshots/{id}/restore {path?}     ask to put back one file/folder, or everything (approval first)
    POST /snapshots/undo                     ask to go back to before the last update (approval first)
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.snapshots")

RESTORE_TASK = "safety.restore"
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def cli() -> str:
    env = os.environ.get("ORION_SNAPSHOT_CLI")
    if env:
        return env
    for p in ("/usr/local/bin/orion-snapshot", "/opt/orion/bin/orion-snapshot"):
        if os.access(p, os.X_OK):
            return p
    return shutil.which("orion-snapshot") or "orion-snapshot"


def index_path() -> Path:
    return Path(os.environ.get("ORION_SNAP_INDEX", "/var/lib/orion/snapshots.json"))


Run = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str], timeout: float = 600) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]} isn't installed")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "it took too long")


class Snapshots:
    def __init__(self, run: Run = _run, as_root: Optional[bool] = None) -> None:
        self.run = run
        self.as_root = (os.name != "nt" and os.geteuid() == 0) if as_root is None else as_root

    def _root_argv(self, *args: str) -> list[str]:
        """The session user goes through polkit (allowed for take/list/status only)."""
        return [cli(), *args] if self.as_root else ["pkexec", cli(), *args]

    def status(self) -> dict[str, Any]:
        """Read the world-readable index orion-snapshot keeps; ask the command only if there's none."""
        try:
            st = json.loads(index_path().read_text(encoding="utf-8"))
            if isinstance(st, dict) and "backend" in st:
                return st
        except (OSError, ValueError):
            pass
        r = self.run([cli(), "status", "--json"], 60)
        try:
            st = json.loads(r.stdout or "")
            if isinstance(st, dict):
                return st
        except ValueError:
            pass
        return {"backend": "none", "snapshots": [], "count": 0,
                "words": "Snapshots aren't available here (orion-snapshot isn't installed)."}

    def take(self, why: str = "", kind: str = "manual") -> dict[str, Any]:
        why = " ".join((why or "you asked for one").split())[:200]
        r = self.run(self._root_argv("take", "--kind", kind, "--why", why, "--json"), 3600)
        try:
            res = json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            res = {"ok": r.returncode == 0,
                   "message": (r.stderr or r.stdout or "").strip()[-300:] or "No answer from orion-snapshot."}
        if r.returncode != 0 and res.get("ok") is not False:
            res = {"ok": False, "message": (r.stderr or "").strip()[-300:] or "The snapshot couldn't be taken."}
        return res

    def find(self, sid: str) -> Optional[dict[str, Any]]:
        return next((s for s in self.status().get("snapshots") or [] if str(s.get("id")) == sid), None)

    def restore_command(self, sid: str, path: str = "") -> str:
        """What runs after your approval, in a terminal that asks for the admin password."""
        import shlex
        inner = f"sudo orion-snapshot restore {shlex.quote(sid)} --yes" + (f" --path {shlex.quote(path)}" if path else "")
        return f"su -P - orion-admin -c {shlex.quote(inner)}"


def _clean_path(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    if "\x00" in path or not path.startswith("/") or ".." in Path(path).parts or len(path) > 1024:
        raise ValueError("Give the full path of the file or folder to put back, like /home/orion/notes.txt.")
    return os.path.normpath(path)


def _register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(RESTORE_TASK, TaskType(RESTORE_TASK, "Put back a snapshot", "system",
                                                           reversible=False, money=False, sensitive=True))


async def request_restore(daemon: Any, snaps: Snapshots, sid: str, path: str = "", why: str = "",
                          asked_by: str = "you") -> dict[str, Any]:
    if not ID_RE.match(sid or ""):
        raise ValueError("That isn't a snapshot name.")
    path = _clean_path(path)
    s = await asyncio.to_thread(snaps.find, sid)
    if s is None:
        raise LookupError(f"There's no snapshot {sid}.")
    board = getattr(daemon, "board", None)
    if board is None:
        raise RuntimeError("Orion is still starting.")
    _register_task_type()
    board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (RESTORE_TASK,))
    what = f"{path} from" if path else "everything from"
    summary = f"Put back {what} the snapshot of {str(s.get('date', ''))[:16]} ({s.get('kind_words', '')})"
    dec = await board.propose(RESTORE_TASK, summary[:200],
                              {"snapshot": sid, "path": path, "date": s.get("date", ""), "kind": s.get("kind", ""),
                               "why": " ".join(str(why or "").split())[:300], "asked_by": asked_by[:60],
                               "what_happens": ("Newer changes to that file or folder are replaced." if path else
                                                "The system goes back to that moment at the next restart. What you "
                                                "have now is kept too, so this can be undone."),
                               "after_approval": "A terminal opens and asks for the admin password."},
                              proposer="Snapshots", risk="high", simulated=False)
    return {"ok": True, "pending": True, "decision": dec["id"],
            "message": "Waiting for your approval on your phone or the boardroom. Then it asks for the admin "
                       "password."}


def _open_terminal(command: str) -> bool:
    try:
        from linux_desktop import is_wayland  # noqa: F401  (OrionOS desktop present)
    except Exception:
        pass
    for term in ("foot", "x-terminal-emulator"):
        exe = shutil.which(term)
        if exe:
            try:
                subprocess.Popen([exe, "--title=orion-tool", "sh", "-c",
                                  f"{command}; echo; printf 'Press Enter to close. '; read _"] if term == "foot" else
                                 [exe, "-e", "sh", "-c", f"{command}; read _"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                continue
    return False


def install(daemon: Any, snaps: Snapshots, open_terminal: Callable[[str], bool] = _open_terminal) -> None:
    _register_task_type()
    board = getattr(daemon, "board", None)
    if board is None:
        return
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (RESTORE_TASK,))
    except Exception:
        pass

    def execute(d: dict[str, Any]) -> dict[str, Any]:
        det = d["packet"]["details"]
        sid, path = str(det.get("snapshot", "")), str(det.get("path") or "")
        if not ID_RE.match(sid):
            raise ValueError("This restore no longer makes sense.")
        cmd = snaps.restore_command(sid, path)
        opened = open_terminal(cmd)
        try:
            import changes
            changes.record_change(f"Approved putting back {path or 'everything'} from snapshot {sid}",
                                  why=det.get("why") or "you asked to go back", kind="restore", source="snapshots",
                                  approved_by="your approval",
                                  result="terminal opened for the admin password" if opened else
                                  "waiting for you to run it", details={"snapshot": sid, "path": path})
        except Exception:
            pass
        if opened:
            return {"state": "terminal", "note": "A terminal asks for the admin password to finish the restore."}
        return {"state": "manual", "note": f"Run this in a terminal to finish: {cmd}"}

    board.executors[RESTORE_TASK] = execute

    try:
        import changes

        async def undo_restore(dmn: Any, change: dict[str, Any], undo: dict[str, Any]) -> dict[str, Any]:
            return await request_restore(dmn, snaps, str(undo.get("snapshot", "")), str(undo.get("path") or ""),
                                         why=f"undo: {change.get('what', '')}"[:200], asked_by="change history")
        changes.register_undo("snapshot_restore", undo_restore)
    except Exception:
        pass


def make_router(get_daemon: Callable[[], Any], snaps: Optional[Snapshots] = None):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    snaps = snaps or Snapshots()
    router = APIRouter(prefix="/snapshots", tags=["safety"])
    try:
        install(get_daemon(), snaps)
    except Exception:
        log.debug("snapshots executor not installed yet", exc_info=True)

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    @router.get("")
    async def snapshots_status() -> Any:
        return await asyncio.to_thread(snaps.status)

    @router.post("")
    async def snapshots_take(request: Request) -> Any:
        b = await body_of(request)
        res = await asyncio.to_thread(snaps.take, str(b.get("why") or ""))   # orion-snapshot notes it in the history
        return res if res.get("ok") else JSONResponse(res, status_code=500)

    @router.post("/undo")
    async def snapshots_undo(request: Request) -> Any:
        st = await asyncio.to_thread(snaps.status)
        sid = st.get("undo_to")
        if not sid:
            return JSONResponse({"ok": False, "message": "There's no snapshot from before an update to go back to."},
                                status_code=404)
        try:
            return await request_restore(get_daemon(), snaps, str(sid), why="undo the last update")
        except (ValueError, LookupError, RuntimeError) as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    @router.post("/{sid}/restore")
    async def snapshots_restore(sid: str, request: Request) -> Any:
        b = await body_of(request)
        try:
            return await request_restore(get_daemon(), snaps, sid, str(b.get("path") or ""), str(b.get("why") or ""))
        except LookupError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except (ValueError, RuntimeError) as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    return router
