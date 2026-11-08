"""Living Orion: Orion reprograms itself, live (docs/ORIONOS_AFTER_1_0_PLAN.md §7, Phase 8).

self_evolve.py turns a request ("Orion, make the robots bigger") into a patch. This module takes that patch through
the safe path the plan asks for:

  1. Sandboxed change   The patch is written into a separate copy of Orion, never the running one:
                        - checkout mode: a git worktree of the running checkout on branch orion-self/<id> (your
                          uncommitted edits are copied in first and committed as the base, so the test sees what
                          is really running);
                        - layer mode (the read-only OrionOS image): a copy of the current Orion layer generation
                          (orion_layer.py), made into a small git repo.
  2. Build and test     Python compile, a fresh-interpreter import of the daemon, the daemon tests that cover the
                        changed modules plus a smoke test (SELFPROG_FULL_TESTS=1 runs them all), the HUD type check
                        and build when screen files changed, and a visual check: screenshots of the HUD before and
                        after when a headless Chromium/Chrome/Edge is installed (skipped, and said so, otherwise).
  3. Ghost preview      GET /self/changes serves the summary, the reason, the diff, the checks, the screenshots and
                        a live preview address of the built screen (served on 127.0.0.1 under a random path).
                        The HUD shows it in a corner (apps/desktop/src/components/selfprog/GhostPreview.tsx).
  4. You approve        The change hot-swaps in: screen changes reload in place; daemon changes restart the daemon
                        through the exit-75 path (about a second with the dev launcher, a few on systemd).
  5. Rollback           "Orion, undo that" (or the Undo button, or POST /self/undo) puts the last swap back.
                        Automatic: the start-up guard (orion_layer.boot_guard) undoes a swap if the daemon starts
                        again before it ever answered; ProbationWatch undoes it if the authenticated /health fails
                        during the 2 minutes after a swap, or if the screen doesn't come back after a screen change.
                        The last swaps are kept in <data>/selfprog/history.json.
  6. Protected core     protected_core.py: a change touching the approval system and signer, the firewall, the
                        recorder's privacy rules or the self-change gate can only be applied with a boardroom
                        decision signed by your phone (fingerprint key) or the boardroom key, never automatically.
                        Permission levels: self_levels.py kinds ui_code / daemon_code / protected_core, read the way
                        os/safety/orion-self-gate reads them (the admin file wins; your own file can only lower).
  7. Upstream           POST /self/changes/{id}/upstream makes a pull-request-ready branch orion-self/<id>-pr on top
                        of your current commit (plus PULL_REQUEST.md). Nothing is ever pushed.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import http.server
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import orion_layer
import protected_core
from fastapi import Request  # module level: the routes below use postponed annotations

log = logging.getLogger("orion.self_program")

SELF_PROTECTED_TASK = "self.protected_change"
CHANGE_ID = re.compile(r"^patch-[0-9a-f]{8}$")
TEST_TIMEOUT_S = 900
CHECK_TIMEOUT_S = 300
MAX_COPY_DIRTY = 400
HEALTH_EVERY_S = 5.0
HEALTH_FAILS_TO_ROLLBACK = 3
SMOKE_TESTS = ("tests/test_windows_imports.py",)
SKIP_NAMES = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "target"}

UNDO_PHRASE = re.compile(
    r"^\s*(?:hey\s+)?(?:orion[,:!.]?\s*)?(?:please\s+)?(?:undo|revert|roll\s*back|roll\s+(?:it|that)\s+back|put\s+(?:it|that)\s+back)"
    r"(?:\s+(?:that|this|it|the\s+last\s+(?:change|swap|update)|your\s+(?:last\s+)?change|what\s+you\s+(?:just\s+)?"
    r"(?:did|changed)))?(?:\s+please)?[\s.!]*$",
    re.I,
)

GATE_DEFAULTS = {"ui_code": 1, "daemon_code": 1, "protected_core": 1}
GATE_MAX = {"ui_code": 2, "daemon_code": 1, "protected_core": 1}



def needs_fingerprint(*kinds: str) -> bool:
    """True when the permission levels say this kind of self-change needs the phone's fingerprint
    (self_levels.KINDS, e.g. daemon_code = "Orion changing its own brain"). Unknown -> ask (safe side)."""
    try:
        import self_levels
        return any(bool(getattr(self_levels.KINDS.get(k), "fingerprint", False)) for k in kinds if k)
    except Exception:
        return any(k == "daemon_code" for k in kinds)


def _ui_only(path: str) -> bool:
    """[review-fix 6] Only the HUD's web code counts as ui_code. The Tauri shell (apps/desktop/src-tauri: Rust, its
    capabilities and tauri.conf.json) has full access to the PC and holds the daemon's key, and any package.json
    can run scripts at build time: both are daemon_code (fingerprint)."""
    p = path.replace("\\", "/").lower()
    if not p.startswith(("apps/desktop/", "packages/")):
        return False
    if p.startswith("apps/desktop/src-tauri/") or p.rsplit("/", 1)[-1] == "package.json":
        return False
    return True


def _clean_rel(path: str) -> str:
    """A repo-relative path in one spelling; anything that could point outside the repo is refused."""
    rel = protected_core.norm_path(path)
    first = rel.split("/", 1)[0]
    if not rel or first == ".." or ":" in first or rel.startswith("~"):
        raise SelfProgramError(f"That change names a file outside Orion's code: {path!r}")
    return rel

class SelfProgramError(RuntimeError):
    pass


def detect_undo(text: str) -> bool:
    """'Orion, undo that' and friends. Short, whole-sentence phrases only."""
    return bool(text) and len(text) <= 60 and bool(UNDO_PHRASE.match(text))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(p: Path) -> Optional[bytes]:
    try:
        return p.read_bytes()
    except OSError:
        return None


# =====================================================================================  permission level
def gate_level(kind: str, data_dir: Path, admin: Optional[Path] = None) -> int:
    """The level for a self-change kind, read like os/safety/orion-self-gate (review: the admin file wins).

    allowed = the admin file's level, else the plan's default; your own data file can only lower it."""
    try:
        import freeze
        if freeze.is_frozen():
            return 1
    except Exception:
        pass

    def level_in(path: Path) -> Optional[int]:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        d = d.get("levels", d) if isinstance(d, dict) else {}
        e = d.get(kind) if isinstance(d, dict) else None
        if not isinstance(e, dict):
            return None
        try:
            return int(e.get("level"))
        except (TypeError, ValueError):
            return None

    admin_p = admin or Path(os.environ.get("ORION_SELF_LEVELS_ADMIN", "/etc/orion/self-levels.json"))
    a = level_in(admin_p)
    best = a if a is not None else GATE_DEFAULTS.get(kind, 1)
    mine = level_in(Path(data_dir) / "self_levels.json")
    if mine is not None:
        best = min(best, mine)
    return max(1, min(best, GATE_MAX.get(kind, 1)))


# =====================================================================================  checks
def default_runner(cmd: list[str], cwd: Path, timeout: float, env: Optional[dict[str, str]] = None) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, **(env or {})})
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, f"stopped after {int(timeout)} seconds"
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def find_renderer() -> Optional[str]:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def default_screenshot(url: str, out: Path) -> bool:
    """Screenshot `url` with a headless browser. False when there is none (the visual check is then skipped)."""
    browser = find_renderer()
    if not browser:
        return False
    code, _ = default_runner([browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                              "--window-size=1280,800", "--virtual-time-budget=4000", f"--screenshot={out}", url],
                             out.parent, 60)
    return code == 0 and out.exists()


def pixel_change(before: Path, after: Path) -> Optional[float]:
    """Share of pixels (0-100) that differ, or None if images can't be compared."""
    try:
        from PIL import Image, ImageChops
        a = Image.open(before).convert("RGB")
        b = Image.open(after).convert("RGB").resize(a.size)
        diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 16 else 0)
        hist = diff.histogram()
        changed = hist[255]
        return round(100.0 * changed / max(1, a.size[0] * a.size[1]), 2)
    except Exception:
        return None


# =====================================================================================  preview server
class _PreviewHandler(http.server.SimpleHTTPRequestHandler):
    prefix = "/"

    def log_message(self, *a: Any) -> None:
        pass

    def translate_path(self, path: str) -> str:
        if not path.startswith(self.prefix):
            return str(Path(self.directory) / "__nothing__")
        return super().translate_path("/" + path[len(self.prefix):])

    def do_GET(self) -> None:  # noqa: N802
        if not self.path.startswith(self.prefix):
            self.send_error(404)
            return
        if self.path.rstrip("/") + "/" == self.prefix:
            self.path = self.prefix + "index.html"
        super().do_GET()

    def send_head(self):  # type: ignore[override]
        import local_static   # [review-fix 9] only 127.0.0.1/localhost:<port>, and no symlinks out of the folder
        if local_static.refuse(self):
            return None
        return super().send_head()


class PreviewServer:
    """The built screen of a change, on 127.0.0.1 under a random path (an iframe can't send the daemon's key)."""

    def __init__(self, directory: Path) -> None:
        prefix = f"/ghost-{secrets.token_urlsafe(12)}/"
        handler = type("Handler", (_PreviewHandler,), {"prefix": prefix})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler,
                                                                                          directory=str(directory)))
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}{prefix}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="selfprog-preview")
        self.thread.start()

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


# =====================================================================================  the flow
class SelfProgram:
    def __init__(self, *, repo_root: Path, data_dir: Path, daemon_dir: Optional[Path] = None,
                 evolver: Any = None, board: Any = None, approvals: Any = None,
                 publish: Optional[Callable[[dict[str, Any]], Any]] = None,
                 restart: Optional[Callable[[], None]] = None,
                 runner: Callable[..., tuple[int, str]] = default_runner,
                 screenshot: Callable[[str, Path], bool] = default_screenshot,
                 health: Optional[Callable[[], bool]] = None,
                 hud_connected: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.time,
                 layer: Optional[bool] = None, admin_levels: Optional[Path] = None) -> None:
        self.root = Path(repo_root).resolve()
        self.data = Path(data_dir)
        self.daemon_dir = Path(daemon_dir or Path(__file__).resolve().parent)
        self.dir = orion_layer.selfprog_dir(self.data)
        (self.dir / "changes").mkdir(parents=True, exist_ok=True)
        self.layer = orion_layer.layer_mode(self.root, self.daemon_dir) if layer is None else bool(layer)
        self.evolver = evolver
        self.board = board
        self.approvals = approvals
        self.publish = publish
        self.restart = restart or (lambda: None)
        self.runner = runner
        self.screenshot = screenshot
        self.health = health
        self.hud_connected = hud_connected
        self.clock = clock
        self.admin_levels = admin_levels
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.lock = threading.RLock()
        self.previews: dict[str, PreviewServer] = {}
        self._watch: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._fails = 0

    # ------------------------------------------------------------------ wiring
    @classmethod
    def for_daemon(cls, daemon: Any) -> "SelfProgram":
        from config import settings

        def restart() -> None:
            loop = getattr(daemon, "loop", None)
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(daemon.request_restart(0.5), loop)
            # if the daemon is hung, the normal restart never happens: leave with the restart code ourselves
            t = threading.Timer(20.0, lambda: os._exit(orion_layer_restart_code()))
            t.daemon = True
            t.start()

        def hud() -> bool:
            try:
                return any(c.get("kind") == "desktop" for c in daemon.bus.clients_state())
            except Exception:
                return False

        sp = cls(repo_root=settings.repo_root, data_dir=settings.data_dir, evolver=getattr(daemon, "evolver", None),
                 board=getattr(daemon, "board", None), approvals=getattr(daemon, "approvals", None),
                 publish=daemon.bus.publish, restart=restart, health=lambda: self_health(settings.port),
                 hud_connected=hud)
        if sp.layer and sp.evolver is not None:
            sp.evolver.root = sp.view_root()
        sp.install_board()
        return sp

    def install_board(self) -> None:
        board = self.board
        if board is None:
            return
        try:
            import boardroom
            boardroom.TASK_TYPES.setdefault(SELF_PROTECTED_TASK, boardroom.TaskType(
                SELF_PROTECTED_TASK, "Change Orion's protected core", "code", reversible=True, money=False,
                sensitive=True))
            board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (SELF_PROTECTED_TASK,))
        except Exception:
            log.exception("couldn't register the protected-change decision type")
        board.executors[SELF_PROTECTED_TASK] = self._board_execute
        if self._board_decided not in board.on_decided:
            board.on_decided.append(self._board_decided)

    def start(self) -> None:
        """Called once the daemon is up: keeps watch over a swap that is on probation."""
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if orion_layer.probation(self.data) and (self._watch is None or not self._watch.is_alive()):
            self._stop.clear()
            self._watch = threading.Thread(target=self._watch_loop, daemon=True, name="selfprog-probation")
            self._watch.start()

    def stop(self) -> None:
        self._stop.set()
        for pid in list(self.previews):
            self.previews.pop(pid).stop()

    def _emit(self, event: dict[str, Any]) -> None:
        if self.publish is None:
            return
        try:
            res = self.publish(event)
            if not asyncio.iscoroutine(res):
                return
            try:
                asyncio.get_running_loop().create_task(res)
            except RuntimeError:
                if self.loop is not None and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(res, self.loop)
                else:
                    asyncio.run(res)         # plain code with no event loop (tools, tests)
        except Exception:
            log.debug("publish failed", exc_info=True)

    # ------------------------------------------------------------------ storage
    def _cdir(self, cid: str) -> Path:
        if not isinstance(cid, str) or not CHANGE_ID.match(cid):
            raise SelfProgramError("That isn't a change Orion knows.")
        return self.dir / "changes" / cid

    def _save(self, c: dict[str, Any]) -> None:
        d = self._cdir(c["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "change.json.tmp"
        tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "change.json")

    def get(self, cid: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads((self._cdir(cid) / "change.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def owns(self, cid: str) -> bool:
        try:
            return self.get(cid) is not None
        except SelfProgramError:
            return False

    def _must(self, cid: str) -> dict[str, Any]:
        c = self.get(cid)
        if c is None:
            raise SelfProgramError("That isn't a change Orion knows.")
        return c

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted((self.dir / "changes").glob("patch-*/change.json"), key=lambda p: p.stat().st_mtime,
                        reverse=True)[:20]:
            try:
                out.append(self.public(json.loads(p.read_text(encoding="utf-8")), diff=False))
            except (OSError, ValueError):
                continue
        return out

    @staticmethod
    def public(c: dict[str, Any], diff: bool = True) -> dict[str, Any]:
        pub = {k: v for k, v in c.items() if k not in ("contents", "diff")}
        pub["files"] = [{k: v for k, v in f.items() if k != "new_content"} for f in c.get("files", [])]
        pub["screens"] = {k: bool(v) for k, v in (c.get("screens") or {}).items()}
        if diff:
            pub["diff"] = c.get("diff", "")
        return pub

    # ------------------------------------------------------------------ where the running code is
    def view_root(self) -> Path:
        """A folder laid out like the repo holding what runs now."""
        if not self.layer:
            return self.root
        n = orion_layer.current_gen(self.data)
        if n:
            g = orion_layer.gen_dir(self.data, n)
            if (g / "apps" / "daemon").is_dir():
                return g
        base = orion_layer.layer_dir(self.data) / "base"
        src = Path(os.environ.get("ORION_LAYER_BASE") or self.daemon_dir)
        stamp = _read_bytes(src / "main.py") or b""
        mark = base / "image.sha256"
        if not (base / "apps" / "daemon").is_dir() or (_read_bytes(mark) or b"").decode() != _sha(stamp):
            if base.exists():
                shutil.rmtree(base)
            orion_layer._copytree(src, base / "apps" / "daemon")
            mark.write_text(_sha(stamp), encoding="utf-8")
        return base

    def classify(self, files: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        hits = protected_core.check(files)
        if hits:
            return "protected_core", hits
        if any(not _ui_only(f["path"]) for f in files):
            return "daemon_code", []
        return "ui_code", []

    def _with_before(self, files: list[dict[str, Any]], root: Optional[Path] = None) -> list[dict[str, Any]]:
        root = root or self.view_root()
        out = []
        for f in files:
            raw = _read_bytes(root / f["path"])
            out.append({"path": f["path"], "action": f.get("action", "modify"),
                        "before": raw.decode("utf-8", "replace") if raw is not None else "",
                        "after": f.get("new_content", "")})
        return out

    # ------------------------------------------------------------------ 1-3: stage = sandbox + build + test
    async def stage(self, proposal: dict[str, Any]) -> dict[str, Any]:
        self.loop = self.loop or asyncio.get_running_loop()
        full = proposal
        if self.evolver is not None and "new_content" not in (proposal.get("files") or [{}])[0]:
            full = self.evolver._load(proposal["id"])
        c = self.new_change(full)
        self._emit({"event": "SELFPROG_CHANGE", "change": self.public(c, diff=False)})
        c = await asyncio.to_thread(self.build_and_test, c["id"])
        self._emit({"event": "SELFPROG_CHANGE", "change": self.public(c, diff=False)})
        if c["status"] == "ready":
            await self._after_ready(c)
        return self.public(self._must(c["id"]))

    def new_change(self, proposal: dict[str, Any]) -> dict[str, Any]:
        if (frozen := __import__("freeze").blocked("change its own code")):
            raise SelfProgramError(frozen)
        files = [{"path": _clean_rel(f["path"]), "action": f.get("action", "modify"), "additions": f.get("additions", 0),
                  "deletions": f.get("deletions", 0), "original_sha": f.get("original_sha"),
                  "new_content": f["new_content"]} for f in proposal["files"]]
        kind, hits = self.classify(self._with_before(files))
        c = {"id": proposal["id"], "request": proposal.get("request", ""),
             "summary": proposal.get("summary", ""),
             "reason": proposal.get("reason") or proposal.get("approach") or proposal.get("request", ""),
             "diff": proposal.get("diff", ""), "diff_sha256": _sha(proposal.get("diff", "").encode("utf-8")),
             "files": files, "kind": kind, "protected": hits, "protected_words": protected_core.describe(hits),
             "mode": "layer" if self.layer else "checkout",
             "touches_daemon": any(not f["path"].startswith(("apps/desktop/", "packages/")) for f in files),
             "touches_ui": any(f["path"].startswith(("apps/desktop/", "packages/")) for f in files),
             "status": "building", "checks": [], "screens": {}, "created": self.clock(), "error": None}
        self._save(c)
        return c

    def build_and_test(self, cid: str) -> dict[str, Any]:
        c = self._must(cid)
        checks: list[dict[str, Any]] = []
        try:
            wt = self._sandbox(c)
            c["sandbox"] = str(wt)
            self._save(c)
            ui_ready = c["touches_ui"] and self._hud_tools(wt)
            if ui_ready and find_renderer() is not None:      # screenshot BEFORE the change, from the same sandbox
                self._build_hud(wt, self._cdir(cid) / "before")
            for f in c["files"]:
                p = wt / f["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("w", encoding="utf-8", newline="") as fh:
                    fh.write(f["new_content"])
            c["commit"] = self._commit(wt, f"Orion: {c['summary'][:70] or 'self-made change'}",
                                       [f["path"] for f in c["files"]])
            (self._cdir(cid) / "upstream.patch").write_text(
                self._git(wt, "format-patch", "-1", "--stdout", c["commit"]), encoding="utf-8")
            checks += self._checks(c, wt, ui_ready)
        except Exception as exc:                              # noqa: BLE001 - every failure becomes a sentence
            log.warning("self-change %s couldn't be built: %s", cid, exc)
            checks.append({"name": "sandbox", "label": "Copy of Orion for the change", "ok": False,
                           "skipped": False, "detail": str(exc)[:600]})
        c["checks"] = checks
        failed = [k for k in checks if not k["ok"] and not k["skipped"]]
        c["status"] = "failed" if failed else "ready"
        c["error"] = f"{failed[0]['label']}: {failed[0]['detail'][-400:]}" if failed else None
        c["checked_at"] = self.clock()
        self._save(c)
        return c

    # -- sandbox
    def _git(self, cwd: Path, *args: str, check: bool = True) -> str:
        res = subprocess.run(["git", "-c", "user.name=Orion", "-c", "user.email=orion@localhost",
                              "-c", "commit.gpgsign=false", *args], cwd=str(cwd), capture_output=True, text=True,
                             timeout=300)
        if check and res.returncode != 0:
            raise SelfProgramError(f"git {args[0]} failed: {(res.stderr or res.stdout).strip()[-400:]}")
        return res.stdout

    def _commit(self, wt: Path, message: str, paths: Optional[list[str]] = None) -> str:
        self._git(wt, "add", "-A", "--", *(paths or ["."]))
        if self._git(wt, "status", "--porcelain").strip():
            self._git(wt, "commit", "-q", "-m", message)
        return self._git(wt, "rev-parse", "HEAD").strip()

    def _sandbox(self, c: dict[str, Any]) -> Path:
        wt = self.dir / "worktrees" / c["id"]
        if wt.exists():
            self._drop_sandbox(c, wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        if not self.layer:
            branch = f"orion-self/{c['id']}"
            self._git(self.root, "worktree", "prune", check=False)       # forget copies whose folder is gone
            if self._git(self.root, "branch", "--list", branch).strip():
                self._git(self.root, "branch", "-D", branch)
            self._git(self.root, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
            c["branch"] = branch
            self._copy_dirty(wt)
            c["base_commit"] = self._commit(wt, "Running copy of Orion (base for the change)")
            for rel in ("node_modules", "apps/desktop/node_modules"):
                src, dst = self.root / rel, wt / rel
                if src.is_dir() and not dst.exists():
                    try:
                        dst.symlink_to(src, target_is_directory=True)
                    except OSError:
                        pass                       # Windows without symlink rights: the HUD check says it's skipped
        else:
            orion_layer._copytree(self.view_root() / "apps" / "daemon", wt / "apps" / "daemon")
            self._git(wt, "init", "-q")
            c["base_commit"] = self._commit(wt, "Orion layer (base for the change)")
        return wt

    def _copy_dirty(self, wt: Path) -> None:
        out = self._git(self.root, "status", "--porcelain", "-z", "--untracked-files=all")
        entries = [e for e in out.split("\0") if e]
        n = 0
        skip_next = False
        for e in entries:
            if skip_next:
                skip_next = False
                continue
            code, rel = e[:2], e[3:]
            if code[0] in "RC":
                skip_next = True                  # "-z" gives rename sources as a separate entry
            parts = set(Path(rel).parts)
            if parts & SKIP_NAMES or Path(rel).name.startswith(".env") or ".orion-signing" in parts:
                continue
            src, dst = self.root / rel, wt / rel
            if "D" in code:
                try:
                    dst.unlink()
                except OSError:
                    pass
                continue
            if src.is_file() and src.stat().st_size < 2_000_000:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                n += 1
                if n >= MAX_COPY_DIRTY:
                    break

    def _drop_sandbox(self, c: dict[str, Any], wt: Optional[Path] = None) -> None:
        wt = wt or Path(c.get("sandbox") or self.dir / "worktrees" / c["id"])
        if not wt.exists():
            return
        if not self.layer:
            self._git(self.root, "worktree", "remove", "--force", str(wt), check=False)
        if wt.exists() and self.dir in wt.parents:
            shutil.rmtree(wt, ignore_errors=True)
        if not self.layer:
            self._git(self.root, "worktree", "prune", check=False)

    # -- checks
    def _hud_tools(self, wt: Path) -> bool:
        mods = (wt / "apps" / "desktop" / "node_modules", wt / "node_modules")     # npm workspaces hoist to the root
        return any(m.exists() for m in mods) and shutil.which("npx") is not None

    def _build_hud(self, wt: Path, out: Path) -> tuple[int, str]:
        return self.runner([shutil.which("npx") or "npx", "vite", "build", "--base", "./", "--outDir", str(out), "--emptyOutDir",
                            "--logLevel", "warn"], wt / "apps" / "desktop", CHECK_TIMEOUT_S)

    def pick_tests(self, wt: Path, files: list[dict[str, Any]]) -> list[str]:
        tdir = wt / "apps" / "daemon" / "tests"
        if os.environ.get("SELFPROG_FULL_TESTS") == "1":
            return ["tests"]
        mods = {Path(f["path"]).stem for f in files
                if f["path"].startswith("apps/daemon/") and f["path"].endswith(".py")
                and not f["path"].startswith("apps/daemon/tests/")}
        picked = {f"tests/{Path(f['path']).name}" for f in files
                  if f["path"].startswith("apps/daemon/tests/test_") and f["path"].endswith(".py")}
        if tdir.is_dir() and mods:
            pat = re.compile(r"^\s*(?:import\s+(?:[\w, ]+,\s*)?(%s)\b|from\s+(%s)\s+import)" %
                             ("|".join(map(re.escape, mods)), "|".join(map(re.escape, mods))), re.M)
            for t in sorted(tdir.glob("test_*.py")):
                try:
                    body = t.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if any(m in t.stem for m in mods) or pat.search(body):
                    picked.add(f"tests/{t.name}")
        for s in SMOKE_TESTS:
            if (wt / "apps" / "daemon" / s).exists():
                picked.add(s)
        return sorted(picked)

    def _checks(self, c: dict[str, Any], wt: Path, ui_ready: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        def add(name: str, label: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
            out.append({"name": name, "label": label, "ok": ok, "skipped": skipped, "detail": detail[-1500:]})

        py = [f for f in c["files"] if f["path"].endswith(".py")]
        errs = []
        for f in py:
            try:
                compile(f["new_content"], f["path"], "exec")
            except SyntaxError as exc:
                errs.append(f"{f['path']} line {exc.lineno}: {exc.msg}")
        add("compile", "Python code is valid", not errs, "\n".join(errs) or f"{len(py)} file(s)",
            skipped=not py)
        if errs:
            return out
        daemon = wt / "apps" / "daemon"
        if c["touches_daemon"] and daemon.is_dir():
            scratch = self._cdir(c["id"]) / "scratch"
            scratch.mkdir(parents=True, exist_ok=True)
            env = {"ORION_IMPORT_CHECK": "1", "ORION_DATA_DIR": str(scratch),
                   "ORION_TOKEN_FILE": str(scratch / "daemon.token"), "PYTHONDONTWRITEBYTECODE": "1"}
            code, text = self.runner([sys.executable, "-c", "import main"], daemon, CHECK_TIMEOUT_S, env)
            add("import", "The changed daemon starts (import check)", code == 0, text.strip() or "imports cleanly")
            if code == 0:
                tests = self.pick_tests(wt, c["files"])
                if tests:
                    code, text = self.runner([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
                                              *tests], daemon, TEST_TIMEOUT_S, {"PYTHONDONTWRITEBYTECODE": "1"})
                    add("tests", f"Orion's tests ({len(tests)} file{'s' if len(tests) != 1 else ''})", code == 0,
                        text.strip().splitlines()[-1] if code == 0 and text.strip() else text)
                else:
                    add("tests", "Orion's tests", True, "no tests cover these files", skipped=True)
        if c["touches_ui"]:
            if not ui_ready:
                why = "the screen's build tools (Node and its packages) aren't installed here"
                add("hud_types", "Screen code type check", True, why, skipped=True)
                add("visual", "Visual check (before and after)", True, why, skipped=True)
                return out
            code, text = self.runner([shutil.which("npx") or "npx", "tsc", "--noEmit", "-p", "."],
                                     wt / "apps" / "desktop", CHECK_TIMEOUT_S)
            add("hud_types", "Screen code type check", code == 0, text.strip() or "no errors")
            if code != 0:
                return out
            preview = self._cdir(c["id"]) / "preview"
            code, text = self._build_hud(wt, preview)
            add("hud_build", "Screen builds", code == 0, text.strip() or "built")
            if code == 0:
                c["preview_dir"] = str(preview)
                out.append(self._visual(c, wt, preview))
        return out

    def _visual(self, c: dict[str, Any], wt: Path, after_dir: Path) -> dict[str, Any]:
        label = "Visual check (before and after)"
        if find_renderer() is None and self.screenshot is default_screenshot:
            return {"name": "visual", "label": label, "ok": True, "skipped": True,
                    "detail": "no headless browser here to take screenshots"}
        screens = self._cdir(c["id"]) / "screens"
        screens.mkdir(parents=True, exist_ok=True)
        shots: dict[str, str] = {}
        for which, d in (("before", self._cdir(c["id"]) / "before"), ("after", after_dir)):
            if not (d / "index.html").exists():
                continue
            srv = PreviewServer(d)
            try:
                if self.screenshot(srv.url, screens / f"{which}.png"):
                    shots[which] = str(screens / f"{which}.png")
            finally:
                srv.stop()
        c["screens"] = shots
        if "after" not in shots:
            return {"name": "visual", "label": label, "ok": True, "skipped": True,
                    "detail": "couldn't take a screenshot of the new screen"}
        share = pixel_change(Path(shots["before"]), Path(shots["after"])) if "before" in shots else None
        detail = f"{share}% of the screen changed" if share is not None else "screenshot taken"
        blank = False
        try:
            from PIL import Image
            blank = Image.open(shots["after"]).convert("L").getextrema()[0] == \
                Image.open(shots["after"]).convert("L").getextrema()[1]
        except Exception:
            pass
        if blank:
            return {"name": "visual", "label": label, "ok": False, "skipped": False,
                    "detail": "the new screen came out blank"}
        return {"name": "visual", "label": label, "ok": True, "skipped": False, "detail": detail}

    # ------------------------------------------------------------------ after the checks
    async def _after_ready(self, c: dict[str, Any]) -> None:
        if c["kind"] == "protected_core" or needs_fingerprint(c["kind"]):
            if self.board is not None:
                try:
                    await self.ask_signature(c["id"])
                except Exception as exc:
                    log.warning("couldn't ask for a signature: %s", exc)
            return
        if gate_level(c["kind"], self.data, self.admin_levels) >= 2:
            try:                                        # "tell me after" (only screen changes, set by the admin)
                self.approve(c["id"], by="Orion (your level: tell me after)")
            except SelfProgramError as exc:
                log.warning("automatic swap refused: %s", exc)

    async def ask_signature(self, cid: str) -> dict[str, Any]:
        c = self._must(cid)
        if self.board is None:
            raise SelfProgramError("The boardroom isn't running, so there's nothing to sign with yet.")
        if c["status"] != "ready":
            raise SelfProgramError("Only a change that passed its checks can be signed.")
        if c.get("decision"):
            d = self.board.get(c["decision"])
            if d and d["status"] == "pending":
                return d
        d = await self.board.propose(
            SELF_PROTECTED_TASK,
            (f"Orion wants to change its protected core: {c['summary'][:140]}" if c["kind"] == "protected_core"
             else f"Orion wants to change its own code: {c['summary'][:140]}"),
            {"change_id": c["id"], "diff_sha256": c["diff_sha256"], "files": [f["path"] for f in c["files"]],
             "protected": c["protected_words"], "reason": c["reason"][:400],
             "checks": [f"{k['label']}: {'skipped' if k['skipped'] else 'passed' if k['ok'] else 'failed'}"
                        for k in c["checks"]]},
            proposer="Orion (self-change)", risk="high", simulated=False)
        c["decision"] = d["id"]
        self._save(c)
        return d

    def _board_execute(self, d: dict[str, Any]) -> dict[str, Any]:
        det = d["packet"]["details"]
        c = self.get(str(det.get("change_id", "")))
        if c is None or c["status"] != "ready":
            raise ValueError("That change isn't waiting any more.")
        if det.get("diff_sha256") != c["diff_sha256"]:
            raise ValueError("The change is different from the one you signed.")
        return {"change": c["id"], "files": [f["path"] for f in c["files"]]}

    def _board_decided(self, d: dict[str, Any]) -> None:
        if d.get("task_type") != SELF_PROTECTED_TASK:
            return
        cid = str(d["packet"]["details"].get("change_id", ""))
        if d.get("status") in ("approved", "edited"):
            try:
                self.approve(cid, signature=d["id"], by=d.get("signer") or "signed")
            except SelfProgramError as exc:
                c = self.get(cid)
                if c:
                    c["error"] = str(exc)
                    self._save(c)
        elif d.get("status") in ("rejected", "cancelled"):
            try:
                self.reject(cid, by="your phone")
            except SelfProgramError:
                pass

    def signature_ok(self, c: dict[str, Any], signature: Optional[str]) -> bool:
        """A signed boardroom decision for exactly this change (phone fingerprint key or boardroom key)."""
        if not signature or self.board is None:
            return False
        d = self.board.get(str(signature))
        if not d or d.get("task_type") != SELF_PROTECTED_TASK:
            return False
        if d.get("status") not in ("approved", "edited") or not str(d.get("signer") or "").strip():
            return False
        det = (d.get("packet") or {}).get("details") or {}
        return det.get("change_id") == c["id"] and det.get("diff_sha256") == c["diff_sha256"] == \
            _sha(c.get("diff", "").encode("utf-8"))

    # ------------------------------------------------------------------ 4: approve -> hot swap
    def approve(self, cid: str, *, signature: Optional[str] = None, by: str = "you") -> dict[str, Any]:
        with self.lock:
            if (frozen := __import__("freeze").blocked("change its own code")):
                raise SelfProgramError(frozen)
            c = self._must(cid)
            if c["status"] != "ready":
                raise SelfProgramError(f"This change is {c['status']}; only a change that passed its checks can go "
                                       "in.")
            # Look again at what will really be written, against what runs now (never trust the stored label).
            kind, hits = self.classify(self._with_before(c["files"]))
            if kind == "protected_core" or c["kind"] == "protected_core":
                if not self.signature_ok(c, signature):
                    raise SelfProgramError(protected_core.describe(hits or c["protected"]) +
                                           " Approve it on your phone.")
            elif needs_fingerprint(kind, c["kind"]) and not self.signature_ok(c, signature):
                # self_levels: "Orion changing its own brain — needs your fingerprint" (the decision already made)
                raise SelfProgramError("Changes to Orion's brain need your fingerprint. Tap 'Ask my phone' and "
                                       "approve it there.")
            self._swap(c, by)
            return self.public(self._must(cid))

    def _swap(self, c: dict[str, Any], by: str) -> None:
        now = self.clock()
        swap: dict[str, Any] = {"id": f"swap-{c['id'][6:]}-{int(now)}", "change_id": c["id"], "at": now,
                                "summary": c["summary"], "kind": c["kind"], "by": str(by)[:120],
                                "touches_daemon": c["touches_daemon"], "touches_ui": c["touches_ui"],
                                "mode": "layer" if self.layer else "checkout"}
        files = []
        if self.layer:
            if c["touches_ui"]:
                raise SelfProgramError("On the OrionOS image only Orion's brain can change itself; screen changes "
                                       "need the source checkout.")
            base = self.view_root()
            for f in c["files"]:
                if f["action"] == "modify":
                    cur = _read_bytes(base / f["path"])
                    if cur is None or _sha(cur) != f["original_sha"]:
                        raise SelfProgramError(f"{f['path']} changed since the change was made; ask again.")
            prev = orion_layer.current_gen(self.data)
            n = orion_layer.new_gen(self.data, base, {f["path"]: f["new_content"].encode("utf-8")
                                                      for f in c["files"]})
            orion_layer.set_gen(self.data, n)
            orion_layer.prune_gens(self.data)
            swap.update({"prev_gen": prev, "gen": n, "root": str(orion_layer.gen_dir(self.data, n))})
            files = [{"path": f["path"], "action": f["action"]} for f in c["files"]]
            if self.evolver is not None:
                self.evolver.root = orion_layer.gen_dir(self.data, n)
        else:
            for f in c["files"]:
                p = self.root / f["path"]
                if f["action"] == "modify":
                    cur = _read_bytes(p)
                    if cur is None or _sha(cur) != f["original_sha"]:
                        raise SelfProgramError(f"{f['path']} changed since the change was made; ask again.")
                elif p.exists():
                    raise SelfProgramError(f"{f['path']} exists now; ask again.")
            backup = self._cdir(c["id"]) / "backup"
            for f in c["files"]:
                p = self.root / f["path"]
                if f["action"] == "modify":
                    dst = backup / f["path"]
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
            for f in c["files"]:
                p = self.root / f["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                body = f["new_content"].encode("utf-8")
                tmp = p.with_name(p.name + ".orion-new")
                tmp.write_bytes(body)
                os.replace(tmp, p)
                files.append({"path": f["path"], "action": f["action"], "new_sha": _sha(body)})
            swap.update({"root": str(self.root), "backup": str(backup)})
        swap["files"] = files
        needs_ui = bool(c["touches_ui"] and self.hud_connected())
        swap["status"] = "probation" if (c["touches_daemon"] or needs_ui) else "kept"
        hist = orion_layer.history(self.data)
        hist.append(swap)
        orion_layer.save_history(self.data, hist)
        if swap["status"] == "probation":
            orion_layer.start_probation(self.data, swap, needs_daemon=c["touches_daemon"], needs_ui=needs_ui,
                                        now=now)
        c.update({"status": "applied", "applied_at": now, "swap": swap["id"], "applied_by": swap["by"]})
        self._save(c)
        self._mark_evolver(c["id"], "applied")
        self._close(c)
        self._emit({"event": "SELFPROG_SWAPPED", "swap": swap, "reload_ui": c["touches_ui"],
                    "restart_daemon": c["touches_daemon"]})
        self._note(f"Orion changed itself: {c['summary'][:120]}", c["reason"], swap["by"])
        if c["touches_daemon"]:
            self.restart()
        elif needs_ui:
            self.start()

    def _close(self, c: dict[str, Any]) -> None:
        srv = self.previews.pop(c["id"], None)
        if srv:
            srv.stop()
        try:
            self._drop_sandbox(c)
        except Exception:
            log.debug("couldn't remove the sandbox", exc_info=True)

    def _mark_evolver(self, cid: str, status: str) -> None:
        if self.evolver is None:
            return
        try:
            p = self.evolver._load(cid)
            p["status"] = status
            p[f"{status}_at"] = self.clock()
            self.evolver._persist(p)
        except Exception:
            pass

    def reject(self, cid: str, by: str = "you") -> dict[str, Any]:
        with self.lock:
            c = self._must(cid)
            if c["status"] in ("applied", "rolled_back"):
                raise SelfProgramError(f"This change is {c['status']}.")
            c.update({"status": "rejected", "rejected_by": by, "rejected_at": self.clock()})
            self._save(c)
            self._mark_evolver(cid, "rejected")
            self._close(c)
            return self.public(c)

    # ------------------------------------------------------------------ 5: undo + automatic rollback
    def undo_last(self, *, by: str = "you", force: bool = False, reason: str = "") -> dict[str, Any]:
        with self.lock:
            live = [s for s in orion_layer.history(self.data) if s.get("status") in ("probation", "kept")]
            if not live:
                raise SelfProgramError("There's no change of Orion's own to undo.")
            swap = live[-1]
            if not force and swap.get("mode") == "checkout":
                changed = [f["path"] for f in swap.get("files", [])
                           if f.get("new_sha") and _sha(_read_bytes(Path(swap["root"]) / f["path"]) or b"")
                           != f["new_sha"]]
                if changed:
                    raise SelfProgramError("These files were edited after the change, so undoing it would erase "
                                           "that work: " + ", ".join(changed))
            return self._rollback(swap, reason or f"Undone by {by}")

    def _rollback(self, swap: dict[str, Any], reason: str) -> dict[str, Any]:
        done = orion_layer.rollback(self.data, swap["id"], reason, now=self.clock())
        c = self.get(swap.get("change_id", ""))
        if c is not None:
            c.update({"status": "rolled_back", "rolled_back_at": self.clock(), "rollback_reason": reason})
            self._save(c)
            self._mark_evolver(c["id"], "reverted")
        if self.layer and self.evolver is not None:
            self.evolver.root = self.view_root()
        self._emit({"event": "SELFPROG_ROLLED_BACK", "swap": done, "reason": reason,
                    "reload_ui": bool(swap.get("touches_ui"))})
        self._note(f"Orion put back its change: {swap.get('summary', '')[:120]}", reason, "Orion")
        if swap.get("touches_daemon"):
            self.restart()
        return done

    def ui_ok(self) -> dict[str, Any]:
        p = orion_layer.probation(self.data)
        if p and p.get("needs_ui"):
            p["ui_ok"] = True
            orion_layer.set_probation(self.data, p)
        return {"ok": True}

    def watch_once(self) -> Optional[str]:
        """One look at a swap on probation. Returns "kept", "rolled_back", "waiting" or None (nothing to watch)."""
        p = orion_layer.probation(self.data)
        if not p:
            return None
        now = self.clock()
        swap = next((s for s in orion_layer.history(self.data) if s.get("id") == p["swap_id"]), None)
        if swap is None or swap.get("status") != "probation":
            orion_layer.set_probation(self.data, None)
            return None
        if p.get("needs_daemon") and self.health is not None and now <= p["deadline"]:
            ok = False
            try:
                ok = bool(self.health())
            except Exception:
                ok = False
            if ok:
                self._fails = 0
                if not p.get("healthy_seen"):
                    p["healthy_seen"] = True
                    orion_layer.set_probation(self.data, p)
            elif p.get("healthy_seen"):
                self._fails += 1
                if self._fails >= HEALTH_FAILS_TO_ROLLBACK:
                    self._rollback(swap, "Orion's health check failed after the change, so it was put back "
                                         "automatically.")
                    return "rolled_back"
        if now < p["deadline"]:
            return "waiting"
        if p.get("needs_daemon") and not p.get("healthy_seen"):
            self._rollback(swap, "Orion didn't answer its health check within 2 minutes of the change, so it was "
                                 "put back automatically.")
            return "rolled_back"
        if p.get("needs_ui") and not p.get("ui_ok"):
            self._rollback(swap, "The Orion screen didn't come back within 2 minutes of the change, so it was "
                                 "put back automatically.")
            return "rolled_back"
        orion_layer.update_swap(self.data, swap["id"], status="kept", kept_at=now)
        orion_layer.set_probation(self.data, None)
        return "kept"

    def _watch_loop(self) -> None:
        self._fails = 0
        while not self._stop.is_set():
            try:
                state = self.watch_once()
            except Exception:
                log.exception("probation watch failed")
                state = "waiting"
            if state != "waiting":
                return
            self._stop.wait(HEALTH_EVERY_S)

    # ------------------------------------------------------------------ 7: upstream (never pushed)
    def offer_upstream(self, cid: str) -> dict[str, Any]:
        c = self._must(cid)
        if c["status"] not in ("ready", "applied"):
            raise SelfProgramError("Only a change that passed its checks can be offered.")
        patch = self._cdir(cid) / "upstream.patch"
        body = (f"{c['summary']}\n\nWhy: {c['reason']}\n\nChecks:\n" +
                "\n".join(f"- {k['label']}: {'skipped (' + k['detail'] + ')' if k['skipped'] else 'passed' if k['ok'] else 'failed'}"
                          for k in c["checks"]) +
                ("\n\nThis touches the protected core and was signed on the owner's phone or the boardroom."
                 if c["kind"] == "protected_core" else "") + "\n\nMade by Orion's self-change flow.\n")
        (self._cdir(cid) / "PULL_REQUEST.md").write_text(f"# {c['summary'][:72]}\n\n{body}", encoding="utf-8")
        if self.layer or not c.get("commit"):
            return {"ok": True, "patch": str(patch), "pull_request": str(self._cdir(cid) / "PULL_REQUEST.md"),
                    "message": "Saved the change as a patch file with a pull request text. Nothing was sent."}
        branch = f"orion-self/{cid}-pr"
        tmp = self.dir / "worktrees" / f"{cid}-pr"
        if tmp.exists():
            self._git(self.root, "worktree", "remove", "--force", str(tmp), check=False)
        if self._git(self.root, "branch", "--list", branch).strip():
            self._git(self.root, "branch", "-D", branch)
        self._git(self.root, "worktree", "add", "-q", "-b", branch, str(tmp), "HEAD")
        try:
            res = subprocess.run(["git", "-c", "user.name=Orion", "-c", "user.email=orion@localhost",
                                  "-c", "commit.gpgsign=false", "cherry-pick", c["commit"]], cwd=str(tmp),
                                 capture_output=True, text=True, timeout=120)
            if res.returncode != 0:
                subprocess.run(["git", "cherry-pick", "--abort"], cwd=str(tmp), capture_output=True, timeout=60)
                self._git(self.root, "worktree", "remove", "--force", str(tmp), check=False)
                self._git(self.root, "branch", "-D", branch, check=False)
                return {"ok": False, "patch": str(patch),
                        "message": "The change builds on edits that aren't committed yet, so it can't be offered "
                                   "on its own. Commit those first, or use the saved patch file."}
        finally:
            if tmp.exists():
                self._git(self.root, "worktree", "remove", "--force", str(tmp), check=False)
        c["upstream_branch"] = branch
        self._save(c)
        return {"ok": True, "branch": branch, "patch": str(patch),
                "pull_request": str(self._cdir(cid) / "PULL_REQUEST.md"),
                "message": f"Branch {branch} is ready for a pull request. Nothing was pushed: Save Orion work to "
                           "GitHub, or git push, sends it when you choose."}

    # ------------------------------------------------------------------ preview
    def preview_url(self, cid: str) -> Optional[str]:
        c = self._must(cid)
        d = Path(c.get("preview_dir") or "")
        if not c.get("preview_dir") or not (d / "index.html").exists():
            return None
        srv = self.previews.get(cid)
        if srv is None:
            srv = self.previews[cid] = PreviewServer(d)
        return srv.url

    def screen_path(self, cid: str, which: str) -> Optional[Path]:
        if which not in ("before", "after"):
            return None
        p = self._cdir(cid) / "screens" / f"{which}.png"
        return p if p.exists() else None

    # ------------------------------------------------------------------ queue card (main.decide_patch)
    async def decide_card(self, daemon: Any, cid: str, action: str) -> None:
        status, error = None, None
        try:
            if action == "apply":
                c = self._must(cid)
                if c["status"] == "building":
                    raise SelfProgramError("Orion is still building and testing this change.")
                if c["kind"] == "protected_core" and self.board is not None:
                    await self.ask_signature(cid)
                    raise SelfProgramError(c["protected_words"] + " Asked your phone.")
                self.approve(cid, by="you (queue)")
                status = "applied"
            elif action == "revert":
                live = [s for s in orion_layer.history(self.data) if s.get("status") in ("probation", "kept")]
                if not live or live[-1].get("change_id") != cid:
                    raise SelfProgramError("Only the most recent change can be undone.")
                self.undo_last(by="you (queue)")
                status = "reverted"
            else:
                self.reject(cid)
                status = "rejected"
        except SelfProgramError as exc:
            error = str(exc)
        q = getattr(daemon, "approvals", None)
        item = None
        if q is not None and q.get(cid) is not None:
            item = q.update(cid, error=error) if status is None else \
                q.update(cid, status=status, error=None, patch=self.public(self._must(cid), diff=False))
        if item:
            await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": item})
        if error:
            await daemon.bus.log(f"Change {cid}: {error}", "warn")

    def _note(self, what: str, why: str, by: str) -> None:
        try:
            import changes
            changes.record_change(what, why=why[:300], kind="self_change", source="Living Orion", approved_by=by)
        except Exception:
            pass

    def state(self) -> dict[str, Any]:
        return {"mode": "layer" if self.layer else "checkout", "changes": self.list(),
                "probation": orion_layer.probation(self.data), "swaps": orion_layer.history(self.data)[::-1],
                "layer_gen": orion_layer.current_gen(self.data) if self.layer else None,
                "levels": {k: gate_level(k, self.data, self.admin_levels) for k in GATE_DEFAULTS}}


def orion_layer_restart_code() -> int:
    from self_evolve import RESTART_EXIT_CODE
    return RESTART_EXIT_CODE


def self_health(port: int, timeout: float = 4.0) -> bool:
    """The daemon's own authenticated /health (the token proves it's this run answering, not just the port)."""
    import urllib.request
    import api_token
    req = urllib.request.Request(f"http://127.0.0.1:{port}/health",
                                 headers={"Authorization": f"Bearer {api_token.get()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed local address
            data = json.loads(r.read().decode("utf-8"))
        return r.status == 200 and data.get("status") == "ok"
    except Exception:
        return False


# =====================================================================================  HTTP (local, token)
def make_router(get: Callable[[], Optional[SelfProgram]]):
    from fastapi import APIRouter
    from fastapi.responses import FileResponse, JSONResponse

    router = APIRouter(prefix="/self", tags=["self-change"])

    def sp() -> SelfProgram:
        s = get()
        if s is None:
            raise SelfProgramError("Orion is still starting.")
        return s

    def bad(exc: Exception, code: int = 409) -> JSONResponse:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=code)

    @router.get("/changes")
    async def changes() -> Any:
        try:
            return sp().state()
        except SelfProgramError as exc:
            return bad(exc, 503)

    @router.get("/changes/{cid}")
    async def change(cid: str) -> Any:
        try:
            c = sp().get(cid)
        except SelfProgramError as exc:
            return bad(exc, 404)
        return {"change": sp().public(c)} if c else bad(SelfProgramError("unknown change"), 404)

    @router.post("/changes/{cid}/approve")
    async def approve(cid: str) -> Any:
        try:
            return {"ok": True, "change": await asyncio.to_thread(sp().approve, cid, by="you (screen)")}
        except SelfProgramError as exc:
            return bad(exc, 403 if "protected core" in str(exc) else 409)

    @router.post("/changes/{cid}/reject")
    async def reject(cid: str) -> Any:
        try:
            return {"ok": True, "change": sp().reject(cid)}
        except SelfProgramError as exc:
            return bad(exc)

    @router.post("/changes/{cid}/ask-signature")
    async def ask(cid: str) -> Any:
        try:
            d = await sp().ask_signature(cid)
            return {"ok": True, "decision": d["id"], "message": "Asked your phone."}
        except SelfProgramError as exc:
            return bad(exc)

    @router.post("/changes/{cid}/upstream")
    async def upstream(cid: str) -> Any:
        try:
            return await asyncio.to_thread(sp().offer_upstream, cid)
        except SelfProgramError as exc:
            return bad(exc)

    @router.get("/changes/{cid}/preview")
    async def preview(cid: str) -> Any:
        try:
            return {"url": sp().preview_url(cid)}
        except SelfProgramError as exc:
            return bad(exc, 404)

    @router.get("/changes/{cid}/screens/{which}")
    async def screen(cid: str, which: str) -> Any:
        try:
            p = sp().screen_path(cid, which)
        except SelfProgramError as exc:
            return bad(exc, 404)
        return FileResponse(str(p), media_type="image/png") if p else bad(SelfProgramError("no screenshot"), 404)

    @router.post("/undo")
    async def undo(request: Request) -> Any:
        try:
            b = await request.json()
        except ValueError:
            b = {}
        try:
            swap = await asyncio.to_thread(sp().undo_last, by="you", force=bool((b or {}).get("force")))
            return {"ok": True, "swap": swap, "message": "Put back."}
        except SelfProgramError as exc:
            return bad(exc)

    @router.post("/probation/ui-ok")
    async def ui_ok() -> Any:
        try:
            return sp().ui_ok()
        except SelfProgramError as exc:
            return bad(exc, 503)

    return router
