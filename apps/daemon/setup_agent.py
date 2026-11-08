"""Setup agent: gets a fresh OrionOS lab ready, one signed approval at a time.

1. "Set up your Orion workspace": installs developer tools, Firefox, VS Code and Discord through the root recipe
   runner (os/lab/recipes). Proposed as a boardroom decision; nothing is installed until it is signed on the
   boardroom or a paired phone.
2. "Get the OpenWoW code": once the workspace is ready and you've told Orion the repo address
   (orion-ctl set-repo <url>), clones it into ~/projects/openwow and installs its npm packages.

The agent only ever asks systemd to start `orion-recipe@<name>.service` for names in WORKSPACE; the runner
refuses anything that isn't a root-owned script shipped in /opt/orion/recipes. Signing in to GitHub (for a
private repo) is always done by you: Orion tells you the one command to run.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.setup")

WORKSPACE: list[tuple[str, str]] = [
    ("dev-tools", "Developer tools: git, GitHub CLI, Node.js + npm, build tools"),
    ("firefox", "Firefox (Mozilla's official package)"),
    ("vscode", "VS Code (Microsoft's official package)"),
    ("discord", "Discord (discord.com's official package)"),
]
VOICE = ("voice", "Voice for Orion: phone voice chat, the hot mic and Orion's own voice (about 1.5 GB)")
RECIPE_STATUS_DIR = Path("/var/lib/orion/recipes")
REPO_RE = re.compile(r"^(https://[\w.-]+/[\w.-]+/[\w.-]+?(\.git)?/?|git@[\w.-]+:[\w.-]+/[\w.-]+?(\.git)?)$")

Publish = Callable[[dict[str, Any]], Awaitable[None]]


def _notify(title: str, message: str) -> None:
    try:
        import alerts
        alerts.emit("system", title, message)   # your phone (alerts.py decides how loudly)
    except Exception:
        pass
    try:
        import linux_desktop
        linux_desktop.notify(title, message)
    except Exception:
        pass


class SetupAgent:
    def __init__(self, board: Any, data_dir: Path, publish: Optional[Publish] = None,
                 projects_dir: Optional[Path] = None, enabled: Optional[bool] = None) -> None:
        self.board = board
        self.path = Path(data_dir) / "setup.json"
        self.publish = publish
        self.projects = projects_dir or (Path.home() / "projects")
        self.enabled = (os.environ.get("ORION_SETUP_AGENT") == "1" and sys.platform.startswith("linux")) \
            if enabled is None else enabled
        self._task: Optional[asyncio.Task] = None
        self._jobs: set[asyncio.Task] = set()
        self.notify = _notify
        board.executors["setup.workspace"] = self._exec_workspace
        board.executors["setup.clone"] = self._exec_clone
        board.executors["setup.voice"] = self._exec_voice

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"workspace": "new", "results": {}, "repos": {}, "hinted_repo": False}

    def _save(self, st: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2))
        tmp.replace(self.path)

    def set_repo(self, url: str, name: str = "openwow") -> dict[str, Any]:
        url = url.strip()
        if not REPO_RE.match(url):
            raise ValueError("That doesn't look like a git repo address (e.g. https://github.com/you/openwow)")
        if not re.fullmatch(r"[a-z0-9-]{1,40}", name):
            raise ValueError("bad project name")
        st = self.state()
        st.setdefault("repos", {})[name] = {"url": url, "state": "new"}
        self._save(st)
        return st["repos"][name]

    # ------------------------------------------------------------------ lifecycle
    async def start(self, delay_s: float = 25.0) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._later(delay_s))

    async def stop(self) -> None:
        for t in [self._task, *self._jobs]:
            if t:
                t.cancel()

    async def _later(self, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        try:
            await self.check()
        except Exception:
            log.exception("setup check failed")

    def _pending(self, task_type: str, key: Optional[str] = None) -> bool:
        for d in self.board.pending():
            if d["task_type"] == task_type and (key is None or d["packet"]["details"].get("project") == key):
                return True
        return False

    async def check(self) -> list[str]:
        """Propose whatever the lab still needs. Returns what was proposed (for tests and orion-ctl)."""
        st = self.state()
        proposed: list[str] = []
        if st.get("workspace") in ("new", "failed") and not self._pending("setup.workspace"):
            items = [label for _, label in WORKSPACE]
            await self.board.propose(
                "setup.workspace", "Set up your Orion workspace: dev tools, Firefox, VS Code, Discord",
                {"installs": items, "recipes": [n for n, _ in WORKSPACE],
                 "sources": "Ubuntu, Mozilla, Microsoft and Discord official packages",
                 "runs_as": "root, via OrionOS's fixed install recipes"},
                proposer="Setup agent", risk="medium", simulated=False)
            proposed.append("workspace")
            self.notify("Orion needs your OK", "Set up your workspace (Discord, VS Code, Firefox, dev tools). "
                                               "Approve it on the boardroom or your phone.")
        if st.get("workspace") == "done":
            repos = st.get("repos", {})
            for name, r in repos.items():
                if r.get("state") in ("new", "failed") and not self._pending("setup.clone", name):
                    await self.board.propose(
                        "setup.clone", f"Get the {name} code into ~/projects/{name} and install its packages",
                        {"project": name, "repo": r["url"], "destination": f"~/projects/{name}",
                         "then": "npm install (if it's a Node project)"},
                        proposer="Setup agent", risk="low", simulated=False)
                    proposed.append(f"clone:{name}")
            if st.get("voice", "new") in ("new", "failed") and not self._pending("setup.voice"):
                await self.board.propose(
                    "setup.voice", "Install voice for Orion (hot mic, phone voice chat, Orion's voice)",
                    {"installs": [VOICE[1]], "recipes": [VOICE[0]],
                     "sources": "Ubuntu packages, PyTorch (CPU), faster-whisper, SpeechBrain, Kokoro",
                     "runs_as": "root, via OrionOS's fixed install recipes"},
                    proposer="Setup agent", risk="low", simulated=False)
                st = self.state()
                st["voice"] = "proposed"
                self._save(st)
                proposed.append("voice")
            if not repos and not st.get("hinted_repo"):
                self.notify("Where's OpenWoW?", "Tell Orion your OpenWoW repo: Ctrl+Alt+S → Set OpenWoW repo.")
                st["hinted_repo"] = True
                self._save(st)
        return proposed

    # ------------------------------------------------------------------ executors (run after a valid signature)
    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def _exec_workspace(self, d: dict[str, Any]) -> dict[str, Any]:
        st = self.state()
        st["workspace"] = "running"
        self._save(st)
        self._spawn(self._run_workspace())
        return {"started": True, "note": "Installing now. You'll get a notification as each app finishes."}

    def _exec_voice(self, d: dict[str, Any]) -> dict[str, Any]:
        st = self.state()
        st["voice"] = "running"
        self._save(st)

        async def job() -> None:
            self.notify("Orion is installing", VOICE[1])
            result = await self.run_recipe(VOICE[0])
            st = self.state()
            st["voice"] = "done" if result == "ok" else "failed"
            self._save(st)
            if result == "ok":
                self.notify("Voice is ready", "Restarting Orion to load it. Then: orion-ctl voice enroll "
                                              "(your voice profile) and orion-ctl mic on.")
                await self._sh("systemctl", "--user", "restart", "orion-daemon.service")
            else:
                self.notify("Voice install failed", "Log: /var/log/orion-recipes/voice.log. Run orion-ctl setup to retry.")
        self._spawn(job())
        return {"started": True, "note": "Installing voice now (a few minutes)."}

    def ask_voice_again(self) -> None:
        st = self.state()
        if st.get("voice") in ("proposed", "failed", None):
            st["voice"] = "new"
            self._save(st)

    def _exec_clone(self, d: dict[str, Any]) -> dict[str, Any]:
        name = d["packet"]["details"]["project"]
        st = self.state()
        repo = st.get("repos", {}).get(name)
        if not repo or repo.get("url") != d["packet"]["details"]["repo"]:
            raise ValueError("the repo address changed since this was proposed; it'll be proposed again")
        repo["state"] = "running"
        self._save(st)
        self._spawn(self._run_clone(name, repo["url"]))
        return {"started": True, "note": f"Getting {name} now."}

    # ------------------------------------------------------------------ workers
    async def run_recipe(self, name: str) -> str:
        if name not in {n for n, _ in WORKSPACE} | {VOICE[0]}:
            return "failed"
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "start", f"orion-recipe@{name}.service",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        status_file = RECIPE_STATUS_DIR / f"{name}.status"
        try:
            return status_file.read_text().strip()
        except OSError:
            log.warning("recipe %s: %s", name, out.decode(errors="replace")[-400:])
            return "ok" if proc.returncode == 0 else "failed"

    async def _run_workspace(self) -> None:
        results: dict[str, str] = {}
        for name, label in WORKSPACE:
            self.notify("Orion is installing", label)
            await self._emit({"event": "SETUP_PROGRESS", "step": name, "state": "running"})
            results[name] = await self.run_recipe(name)
            await self._emit({"event": "SETUP_PROGRESS", "step": name, "state": results[name]})
        st = self.state()
        st["results"] = results
        failed = [n for n, r in results.items() if r != "ok"]
        st["workspace"] = "failed" if failed else "done"
        self._save(st)
        if failed:
            self.notify("Some installs failed", f"{', '.join(failed)}. Logs: /var/log/orion-recipes/. "
                                                 "Orion will ask again.")
        else:
            self.notify("Workspace ready", "Discord, VS Code, Firefox and your dev tools are installed. "
                                           "Find them with Ctrl+Alt+Space.")
        await self.check()

    async def _sh(self, *args: str, cwd: Optional[Path] = None, timeout: float = 1800) -> tuple[int, str]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang waiting for a password
        proc = await asyncio.create_subprocess_exec(*args, cwd=str(cwd) if cwd else None, env=env,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "timed out"
        return proc.returncode or 0, out.decode(errors="replace")

    async def _run_clone(self, name: str, url: str) -> None:
        dest = self.projects / name
        self.projects.mkdir(parents=True, exist_ok=True)
        ok, msg = True, ""
        if (dest / ".git").exists():
            rc, out = await self._sh("git", "-C", str(dest), "pull", "--ff-only")
            ok, msg = rc == 0, out
        else:
            gh_ok = shutil.which("gh") and (await self._sh("gh", "auth", "status"))[0] == 0
            if gh_ok and "github.com" in url:
                rc, out = await self._sh("gh", "repo", "clone", url, str(dest))
            else:
                rc, out = await self._sh("git", "clone", url, str(dest))
            ok, msg = rc == 0, out
        if not ok:
            st = self.state()
            st["repos"][name]["state"] = "failed"
            self._save(st)
            needs_login = any(w in msg.lower() for w in ("authentication", "could not read username", "not found",
                                                         "permission denied", "terminal prompts disabled"))
            if needs_login:
                self.notify(f"{name} needs GitHub sign-in",
                            "Press Ctrl+Alt+T and run:  gh auth login   then:  orion-ctl setup")
            else:
                self.notify(f"Couldn't get {name}", msg.strip().splitlines()[-1][:200] if msg.strip() else "git failed")
            await self._emit({"event": "SETUP_PROGRESS", "step": f"clone:{name}", "state": "failed"})
            return
        if (dest / "package.json").exists() and shutil.which("npm"):
            self.notify("Orion is installing packages", f"npm install in ~/projects/{name}")
            cmd = ["npm", "ci"] if (dest / "package-lock.json").exists() else ["npm", "install"]
            rc, out = await self._sh(*cmd, cwd=dest)
            if rc != 0:
                self.notify(f"{name}: npm install had problems", out.strip().splitlines()[-1][:200] if out.strip() else "")
        st = self.state()
        st["repos"][name]["state"] = "done"
        self._save(st)
        self.notify(f"{name} is ready", f"Code in ~/projects/{name}. Open it from Orion Files (Ctrl+Alt+E).")
        await self._emit({"event": "SETUP_PROGRESS", "step": f"clone:{name}", "state": "ok"})

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.publish:
            try:
                await self.publish(event)
            except Exception:
                pass
