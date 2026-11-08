"""Start a project with Claude (the HUD's "Projects" window).

Three steps in the HUD: name it, pick a starter, say what you want. Then Orion:
  1. makes the folder ~/Orion Projects/<name> (same place on Windows and Linux),
  2. copies the starter from project_templates/ (web game, website, Python tool or empty),
  3. writes a CLAUDE.md (the rules Claude follows in this project) and a README,
  4. runs git init and makes the first commit,
  5. starts a Mission (missions.create_mission) so Claude Code builds the first version.

"Ask for a change" starts a follow-up mission on the same folder. "Play it" serves the game folder on
127.0.0.1 only (a tiny static server, never on the network) and opens it in the browser. "Open folder" opens the
folder in the file manager. Nothing is pushed anywhere.

Routes (make_router, registered in main.py):
    GET  /projects                 {root, starters, projects}
    POST /projects                 {name, starter, idea, agent?, budget_usd?}
    POST /projects/{name}/change   {text, agent?, budget_usd?}
    POST /projects/{name}/open     open the folder
    POST /projects/{name}/play     {url} (also opens it in the browser)
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

log = logging.getLogger("orion.projects")

TEMPLATES_DIR = Path(__file__).resolve().parent / "project_templates"
META_FILE = "projects.json"

STARTERS: list[dict[str, str]] = [
    {"id": "web-game", "label": "Web game",
     "description": "A small game that runs in the browser: open index.html and play. No installs.",
     "run": "Open index.html in a browser (or press Play it in Orion). Tests: node --test"},
    {"id": "website", "label": "Website",
     "description": "A simple website: a page, a stylesheet and a little script.",
     "run": "Open index.html in a browser."},
    {"id": "python-tool", "label": "Python tool",
     "description": "A small command-line tool in Python, with a test.",
     "run": "python tool.py --help. Tests: python -m unittest"},
    {"id": "empty", "label": "Empty",
     "description": "Just the folder, the rules and git. Claude starts from nothing.",
     "run": "Whatever Claude builds; the README will say."},
]
STARTER_IDS = {s["id"] for s in STARTERS}

FIRST_BUILD: dict[str, str] = {
    "web-game": ("Turn the starter game (index.html and game.js) into the game described below. Keep it one folder "
                 "that runs by opening index.html: no build step, no downloads, no libraries. Keep a game loop, "
                 "keyboard and touch input, at least one sprite and a score. Keep the rules in plain functions and "
                 "update game.test.js (node --test). Add a short 'How to play' to the README."),
    "website": ("Turn the starter website into the site described below. Plain HTML, CSS and a little JavaScript, "
                "no build step. Make it work on phones. Say in the README how to view it."),
    "python-tool": ("Turn the starter Python tool (tool.py) into the tool described below. Standard library only "
                    "unless a package is really needed (say why). Keep the logic in functions and add tests to "
                    "test_tool.py (python -m unittest). Update the README with how to run it."),
    "empty": ("Build the first small version of what is described below. Pick the simplest way that works, add "
              "tests, and write in the README how to run it."),
}

WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_IDEA = 4000


class ProjectError(Exception):
    """A plain-words reason a request was refused."""


def projects_root() -> Path:
    """~/Orion Projects (ORION_PROJECTS_DIR overrides it, for tests and odd setups)."""
    env = os.environ.get("ORION_PROJECTS_DIR", "").strip()
    return Path(env).expanduser() if env else Path.home() / "Orion Projects"


def clean_name(name: str) -> str:
    """A folder name that is safe on Windows and Linux, or a ProjectError saying what's wrong."""
    n = " ".join(str(name or "").split())
    n = re.sub(r"[^A-Za-z0-9 _\-.]", "", n).strip(" .")
    if not n:
        raise ProjectError("Give the project a name (letters, numbers, spaces, - or _).")
    if len(n) > 60:
        n = n[:60].rstrip(" .")
    if n.split(".")[0].lower() in WINDOWS_RESERVED:
        raise ProjectError(f"{n} is a name Windows keeps for itself. Pick another one.")
    return n


def _run_git(cwd: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, **kw)
    except FileNotFoundError:
        return 127, "git isn't installed"
    except subprocess.TimeoutExpired:
        return 124, "git took too long"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def claude_md(name: str, starter: dict[str, str], idea: str) -> str:
    return f"""# {name}

What this project is, in the owner's words:

> {idea.strip() or "(not described yet)"}

Starter: {starter['label']}. How to run it: {starter['run']}

## Rules for Claude in this project

- Small steps: make one small change at a time and commit it with a clear message.
- Tests: add or update a test for each change you can test, and run the tests before saying it's done.
- Plain words: explain what you did in short, plain sentences. No jargon.
- No secrets: never put passwords, API keys or tokens in any file, and never read files that hold them.
- Never push to GitHub or any other remote, and never change git remotes, without asking first.
- Keep it simple: no new dependencies unless they are really needed; say why when you add one.
- Only work inside this folder.
"""


def readme(name: str, starter: dict[str, str], idea: str) -> str:
    return f"""# {name}

{idea.strip() or "A new project."}

Made with Orion and Claude, from the {starter['label'].lower()} starter.

## How to run it

{starter['run']}
"""


def first_build_text(name: str, starter_id: str, idea: str) -> str:
    return (f"Project: {name}\nRead CLAUDE.md first and follow its rules.\n\n{FIRST_BUILD[starter_id]}\n\n"
            f"What the owner wants, in their words:\n{idea.strip() or '(nothing more said; make a fun, simple first version)'}")


class _QuietHandler(SimpleHTTPRequestHandler):
    """Static files only, no hidden files (.git, .env), no logging to stderr."""

    def send_head(self):  # type: ignore[override]
        from urllib.parse import unquote
        parts = [p for p in unquote(self.path.split("?")[0]).replace("\\", "/").split("/") if p]
        if any(p.startswith(".") for p in parts):
            self.send_error(404)
            return None
        import local_static   # [review-fix 9] only 127.0.0.1/localhost:<port>, and no symlinks out of the folder
        if local_static.refuse(self):
            return None
        return super().send_head()

    def log_message(self, *args: Any) -> None:  # noqa: D401
        pass


class Projects:
    def __init__(self, data_dir: Path | str, root: Optional[Path] = None, missions_mod: Any = None,
                 opener: Optional[Callable[[str], Any]] = None, browser: Optional[Callable[[str], Any]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.data_dir = Path(data_dir)
        self._root = root
        self._missions = missions_mod
        self.opener = opener or _open_path
        self.browser = browser or _open_url
        self.clock = clock
        self._lock = threading.Lock()
        self._servers: dict[str, tuple[ThreadingHTTPServer, str]] = {}

    # ---------- storage ----------
    @property
    def root(self) -> Path:
        return self._root or projects_root()

    def _meta_path(self) -> Path:
        return self.data_dir / META_FILE

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._meta_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._meta_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._meta_path())

    def missions(self) -> Any:
        if self._missions is None:
            import missions
            self._missions = missions
        return self._missions

    # ---------- reading ----------
    def _mission_view(self, mid: str) -> Optional[dict[str, Any]]:
        try:
            mc = self.missions().current()
            m = mc.get(mid)
            return mc.public(m) if m else None
        except Exception:
            return None

    def public(self, p: dict[str, Any]) -> dict[str, Any]:
        mids = list(p.get("missions") or [])
        latest = self._mission_view(mids[-1]) if mids else None
        path = Path(p["path"])
        return {**p, "exists": path.is_dir(), "playable": self._play_dir(p) is not None, "latest": latest}

    def list(self) -> list[dict[str, Any]]:
        data = self._load()
        return [self.public(p) for p in sorted(data.values(), key=lambda p: p.get("created", 0), reverse=True)]

    def get(self, name: str) -> dict[str, Any]:
        p = self._load().get(name)
        if not p:
            raise ProjectError(f"Orion doesn't know a project called {name!r}.")
        return p

    def snapshot(self) -> dict[str, Any]:
        return {"root": str(self.root), "starters": STARTERS, "projects": self.list()}

    # ---------- making ----------
    def create(self, name: str, starter: str = "web-game", idea: str = "", agent: Optional[str] = None,
               budget_usd: Optional[float] = None, start_mission: bool = True) -> dict[str, Any]:
        name = clean_name(name)
        if starter not in STARTER_IDS:
            raise ProjectError("Pick a starter: web game, website, Python tool or empty.")
        idea = str(idea or "").strip()[:MAX_IDEA]
        st = next(s for s in STARTERS if s["id"] == starter)
        with self._lock:
            data = self._load()
            path = self.root / name
            if name in data or path.exists():
                raise ProjectError(f"There's already a project called {name}. Pick another name.")
            self.root.mkdir(parents=True, exist_ok=True)
            path.mkdir()
            try:
                self._fill(path, name, starter, st, idea)
                self._git_first_commit(path, st)
            except Exception:
                shutil.rmtree(path, ignore_errors=True)      # our own fresh folder only
                raise
            rec = {"name": name, "path": str(path), "starter": starter, "idea": idea, "created": self.clock(),
                   "missions": []}
            data[name] = rec
            self._save(data)
        out: dict[str, Any] = {"project": rec, "mission": None, "message": f"Made {name} in {path}."}
        if start_mission:
            res = self._start_mission(name, f"Build the first version of {name}",
                                      first_build_text(name, starter, idea), agent, budget_usd)
            out.update(res)
            out["message"] = f"Made {name}. " + res["message"]
        out["project"] = self.public(self.get(name))
        return out

    def _fill(self, path: Path, name: str, starter: str, st: dict[str, str], idea: str) -> None:
        src = TEMPLATES_DIR / starter
        for f in sorted(src.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(src)
            target = path / (".gitignore" if rel.as_posix() == "gitignore" else rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text(encoding="utf-8")
            target.write_text(text.replace("{{name}}", name), encoding="utf-8", newline="\n")
        (path / "CLAUDE.md").write_text(claude_md(name, st, idea), encoding="utf-8", newline="\n")
        (path / "README.md").write_text(readme(name, st, idea), encoding="utf-8", newline="\n")

    def _git_first_commit(self, path: Path, st: dict[str, str]) -> None:
        code, out = _run_git(path, "init")
        if code != 0:
            raise ProjectError("Orion couldn't start git in the folder"
                               + (" (git isn't installed)." if code == 127 else f": {out.strip()[-160:]}"))
        _run_git(path, "symbolic-ref", "HEAD", "refs/heads/main")
        ident: list[str] = []
        if _run_git(path, "config", "user.email")[0] != 0:
            ident += ["-c", "user.email=orion@localhost"]
        if _run_git(path, "config", "user.name")[0] != 0:
            ident += ["-c", "user.name=Orion"]
        _run_git(path, "add", "-A")
        code, out = _run_git(path, *ident, "commit", "-q", "-m", f"First version: the {st['label'].lower()} starter")
        if code != 0:
            raise ProjectError(f"Orion couldn't make the first commit: {out.strip()[-160:]}")

    # ---------- missions ----------
    def _start_mission(self, name: str, title: str, description: str, agent: Optional[str],
                       budget_usd: Optional[float]) -> dict[str, Any]:
        p = self.get(name)
        mm = self.missions()
        try:
            m = mm.create_mission(title, repo=p["path"], agent=agent or None, budget_usd=budget_usd, source="hud",
                                  description=description)
        except Exception as exc:     # MissionError carries plain words; anything else is reported briefly
            msg = str(exc) or exc.__class__.__name__
            return {"mission": None, "message": f"Claude couldn't start yet: {msg}"}
        with self._lock:
            data = self._load()
            if name in data:
                data[name].setdefault("missions", []).append(m["id"])
                self._save(data)
        return {"mission": m, "message": f"Claude is on it ({m.get('agent_label') or m.get('agent') or 'an agent'})."}

    def ask_change(self, name: str, text: str, agent: Optional[str] = None,
                   budget_usd: Optional[float] = None) -> dict[str, Any]:
        p = self.get(name)
        text = " ".join(str(text or "").split())[:MAX_IDEA]
        if not text:
            raise ProjectError("Say what you'd like changed.")
        title = text if len(text) <= 80 else text[:77] + "..."
        desc = f"Project: {p['name']}\nRead CLAUDE.md first and follow its rules.\n\nChange asked for:\n{text}"
        res = self._start_mission(name, title, desc, agent, budget_usd)
        return {**res, "project": self.public(self.get(name))}

    # ---------- open and play ----------
    def open_folder(self, name: str) -> dict[str, Any]:
        path = Path(self.get(name)["path"])
        if not path.is_dir():
            raise ProjectError("The folder isn't there any more.")
        self.opener(str(path))
        return {"ok": True, "path": str(path), "message": f"Opened {path}."}

    def _play_dir(self, p: dict[str, Any]) -> Optional[Path]:
        """Where index.html is: the latest mission's work folder while it isn't merged yet, else the project."""
        for mid in reversed(list(p.get("missions") or [])):
            m = self._mission_view(mid)
            wt = (m or {}).get("worktree")
            if wt and (Path(wt) / "index.html").is_file() and not (m or {}).get("merge"):
                return Path(wt)
        path = Path(p["path"])
        return path if (path / "index.html").is_file() else None

    def play(self, name: str, open_browser: bool = True) -> dict[str, Any]:
        p = self.get(name)
        folder = self._play_dir(p)
        if folder is None:
            raise ProjectError("There's no index.html to play yet.")
        url = self.serve(folder)
        if open_browser:
            self.browser(url)
        latest = folder != Path(p["path"])
        return {"ok": True, "url": url, "folder": str(folder),
                "message": "Playing Claude's newest version (not merged yet)." if latest else "Playing it."}

    def serve(self, folder: Path) -> str:
        key = str(folder.resolve())
        with self._lock:
            if key in self._servers:
                return self._servers[key][1]
            handler = functools.partial(_QuietHandler, directory=key)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
            threading.Thread(target=srv.serve_forever, name=f"orion-play-{folder.name}", daemon=True).start()
            self._servers[key] = (srv, url)
            return url

    def stop(self) -> None:
        with self._lock:
            servers, self._servers = list(self._servers.values()), {}
        for srv, _ in servers:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass


def _open_path(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _open_url(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


# ---------- routes ----------
class NewProject(BaseModel):
    name: str
    starter: str = "web-game"
    idea: str = ""
    agent: Optional[str] = None
    budget_usd: Optional[float] = None


class Change(BaseModel):
    text: str
    agent: Optional[str] = None
    budget_usd: Optional[float] = None


def attach(daemon: Any) -> Projects:
    pj = getattr(daemon, "projects", None)
    if isinstance(pj, Projects):
        return pj
    from config import settings
    pj = Projects(settings.data_dir)
    try:
        daemon.projects = pj
    except Exception:
        pass
    return pj


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    r = APIRouter(tags=["projects"])

    def pj() -> Projects:
        return attach(get_daemon())

    def refuse(exc: Exception, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": str(exc), "message": str(exc)}, status_code=code)

    @r.get("/projects")
    async def list_projects() -> Any:
        return pj().snapshot()

    @r.post("/projects")
    async def new_project(body: NewProject) -> Any:
        try:
            return pj().create(body.name, body.starter, body.idea, body.agent, body.budget_usd)
        except ProjectError as exc:
            return refuse(exc)

    @r.post("/projects/{name}/change")
    async def change(name: str, body: Change) -> Any:
        try:
            return pj().ask_change(name, body.text, body.agent, body.budget_usd)
        except ProjectError as exc:
            return refuse(exc)

    @r.post("/projects/{name}/open")
    async def open_folder(name: str) -> Any:
        try:
            return pj().open_folder(name)
        except ProjectError as exc:
            return refuse(exc)
        except OSError as exc:
            return refuse(ProjectError(f"Couldn't open the folder: {exc}"), 500)

    @r.post("/projects/{name}/play")
    async def play(name: str) -> Any:
        try:
            return pj().play(name)
        except ProjectError as exc:
            return refuse(exc)

    return r
