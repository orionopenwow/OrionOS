"""Project Runbook (Toolkit tool 10, docs/ORION_TOOLKIT_PLAN.md): one command per project.

Orion learns how each project starts, tests, builds and deploys, and writes that into a `justfile`, with tool
versions pinned in `.mise.toml`. "Orion, run OpenWoW" and "Orion, test Benilla" then become `just run` and
`just test` in Orion's runner, the same on every machine.

Where the commands come from, best first (the first source that knows a step wins):
  1. the project's own justfile (never overwritten; Orion only *suggests* missing recipes, as a diff)
  2. Makefile targets            3. package.json scripts     4. Cargo.toml     5. pyproject.toml / tests
  6. go.mod                      7. Grimoire's harvest of the commands you actually type in that folder
Tool versions come from .nvmrc / .node-version, package.json (engines, packageManager), rust-toolchain(.toml),
Cargo.toml rust-version, .python-version, pyproject/uv.lock requires-python, go.mod and .tool-versions.

Writing files always starts as a proposal with a diff that you approve (boardroom decision `runbook.write`, on
your phone). After the first approval for a project, Orion may update the files *it* wrote by itself, but only
while nobody has edited them since; a file you wrote or changed always asks again. Deploying, and any recipe that
looks destructive, always asks (`runbook.run`, sensitive).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

from runner import is_destructive

log = logging.getLogger("orion.runbook")

VERBS = ("run", "test", "build", "deploy", "lint")
VERB_WORDS = {   # names a project may already use for each step
    "run": ("run", "start", "dev", "serve"),
    "test": ("test", "tests"),
    "build": ("build",),
    "deploy": ("deploy", "publish"),
    "lint": ("lint", "check"),
}
MARKER = "# orion-runbook: managed"
JUST_HEADER = (f"{MARKER}\n"
               "# Written by Orion's Project Runbook: one command per project (`just run`, `just test`, ...).\n"
               "# Edit it freely. Once you change it, Orion asks before touching it again.\n")
MISE_HEADER = (f"{MARKER}\n"
               "# Tool versions for this project, pinned by Orion's Project Runbook (https://mise.jdx.dev).\n")
TASK_WRITE = "runbook.write"
TASK_RUN = "runbook.run"
NPM_NO_TEST = "no test specified"
IGNORED_JUST_WORDS = {"set", "alias", "export", "import", "mod", "default"}


# =====================================================================================  reading a project
def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _toml(p: Path) -> dict[str, Any]:
    if tomllib is None or not p.is_file():
        return {}
    try:
        return tomllib.loads(_read(p))
    except (ValueError, TypeError):
        return {}


def _json(p: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read(p) or "null")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def justfile_path(project: Path) -> Optional[Path]:
    for name in ("justfile", "Justfile", ".justfile"):
        if (project / name).is_file():
            return project / name
    return None


def justfile_recipes(text: str) -> dict[str, str]:
    """Recipe name -> its body (first lines), from a justfile."""
    out: dict[str, str] = {}
    cur: Optional[str] = None
    for line in text.splitlines():
        m = re.match(r"^@?([A-Za-z_][\w-]*)(?:\s+[^:=]*)?:(?!=)(.*)$", line)
        if m and not line.startswith((" ", "\t")) and m.group(1) not in IGNORED_JUST_WORDS:
            cur = m.group(1)
            out[cur] = ""
            continue
        if cur and line.startswith((" ", "\t")) and line.strip():
            out[cur] = (out[cur] + "\n" + line.strip()).strip()
        elif line.strip() and not line.startswith((" ", "\t")):
            cur = None
    return out


def _make_targets(project: Path) -> set[str]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        text = _read(project / name)
        if text:
            return set(re.findall(r"^([A-Za-z0-9_.\-]+)\s*:(?!=)", text, re.M))
    return set()


def _package_runner(project: Path, pkg: dict[str, Any]) -> str:
    pm = str(pkg.get("packageManager") or "")
    for tool, lock in (("pnpm", "pnpm-lock.yaml"), ("yarn", "yarn.lock"), ("bun", "bun.lockb")):
        if pm.startswith(tool + "@") or (project / lock).exists():
            return tool
    return "npm"


def _script_cmd(runner: str, script: str) -> str:
    if runner == "npm":
        return "npm test" if script == "test" else f"npm run {script}"
    if runner == "bun":
        return f"bun run {script}"
    return f"{runner} {script}"


def _py_prefix(project: Path) -> str:
    if (project / "uv.lock").exists():
        return "uv run "
    if (project / "poetry.lock").exists():
        return "poetry run "
    return ""


def detect_commands(project: Path, grimoire: Optional[list[dict[str, Any]]] = None,
                    use_justfile: bool = True) -> dict[str, dict[str, str]]:
    """verb -> {"command", "source"} for everything Orion can work out about this project."""
    found: dict[str, dict[str, str]] = {}

    def offer(verb: str, command: str, source: str) -> None:
        if verb not in found and command:
            found[verb] = {"command": command, "source": source}

    jf = justfile_path(project) if use_justfile else None
    if jf:
        recipes = justfile_recipes(_read(jf))
        for verb, words in VERB_WORDS.items():
            name = next((w for w in words if w in recipes), None)
            if name:
                offer(verb, f"just {name}", "your justfile")
    targets = _make_targets(project)
    for verb, words in VERB_WORDS.items():
        name = next((w for w in words if w in targets), None)
        if name:
            offer(verb, f"make {name}", "Makefile")
    pkg = _json(project / "package.json")
    if pkg:
        scripts = {k: str(v) for k, v in (pkg.get("scripts") or {}).items()} if isinstance(pkg.get("scripts"), dict) else {}
        runner = _package_runner(project, pkg)
        for verb, words in VERB_WORDS.items():
            name = next((w for w in words if w in scripts and not (w == "test" and NPM_NO_TEST in scripts[w])), None)
            if name:
                offer(verb, _script_cmd(runner, name), "package.json")
    cargo = _toml(project / "Cargo.toml")
    if cargo:
        has_bin = (project / "src/main.rs").exists() or bool(cargo.get("bin")) or bool((cargo.get("package") or {}).get("default-run"))
        if has_bin:
            offer("run", "cargo run", "Cargo.toml")
        offer("test", "cargo test", "Cargo.toml")
        offer("build", "cargo build --release", "Cargo.toml")
        offer("lint", "cargo clippy", "Cargo.toml")
    py = _toml(project / "pyproject.toml")
    if py or (project / "setup.py").exists() or (project / "requirements.txt").exists():
        pre = _py_prefix(project)
        text = _read(project / "pyproject.toml")
        if "pytest" in text or (project / "tests").is_dir() or (project / "pytest.ini").exists():
            offer("test", f"{pre}pytest", "pyproject.toml" if py else "Python tests")
        scripts = ((py.get("project") or {}).get("scripts") or {}) if py else {}
        if scripts:
            offer("run", f"{pre}{next(iter(scripts))}", "pyproject.toml")
        elif (project / "main.py").exists():
            offer("run", f"{pre}python main.py", "main.py")
        if py.get("build-system"):
            offer("build", "uv build" if pre == "uv run " else f"{pre}python -m build", "pyproject.toml")
    if (project / "go.mod").exists():
        offer("run", "go run .", "go.mod")
        offer("test", "go test ./...", "go.mod")
        offer("build", "go build ./...", "go.mod")
    for verb, cmd in _grimoire_commands(project, grimoire or []).items():
        offer(verb, cmd, "your most-used commands (Grimoire)")
    return found


def _grimoire_commands(project: Path, entries: list[dict[str, Any]]) -> dict[str, str]:
    """Commands you run in this folder, from Grimoire's harvest, sorted by how often you use them."""
    out: dict[str, str] = {}
    try:
        root = project.resolve()
    except OSError:
        return out
    ranked = sorted(entries, key=lambda e: -int(e.get("uses") or e.get("count") or 0))
    for e in ranked:
        cmd = str(e.get("command") or "").strip()
        cwd = e.get("cwd")
        if not cmd or not cwd or e.get("kind") not in (None, "shell", "bash"):
            continue
        try:
            c = Path(os.path.expanduser(str(cwd))).resolve()
        except OSError:
            continue
        if not (c == root or c.is_relative_to(root)):
            continue
        hay = " ".join([str(e.get("id", "")), str(e.get("description", "")), cmd]).lower()
        for verb, words in VERB_WORDS.items():
            if verb not in out and any(re.search(rf"\b{w}\b", hay) for w in words) and not is_destructive(cmd):
                out[verb] = cmd
    return out


def _plain_version(spec: str) -> str:
    """'>=3.11' -> '3.11', 'v20.11.1' -> '20.11.1', '^18' -> '18'."""
    m = re.search(r"(\d+(?:\.\d+){0,2})", spec or "")
    return m.group(1) if m else ""


def detect_tools(project: Path) -> dict[str, dict[str, str]]:
    """tool -> {"version", "source"}: the versions this project expects."""
    tools: dict[str, dict[str, str]] = {}

    def pin(tool: str, version: str, source: str) -> None:
        if tool not in tools and version:
            tools[tool] = {"version": version, "source": source}

    for line in _read(project / ".tool-versions").splitlines():
        parts = line.split("#")[0].split()
        if len(parts) >= 2:
            pin({"nodejs": "node"}.get(parts[0], parts[0]), parts[1], ".tool-versions")
    for name in (".nvmrc", ".node-version"):
        v = _read(project / name).strip()
        if v and re.match(r"^v?\d", v):
            pin("node", v.lstrip("v"), name)
    pkg = _json(project / "package.json")
    if pkg:
        eng = (pkg.get("engines") or {}) if isinstance(pkg.get("engines"), dict) else {}
        pin("node", _plain_version(str(eng.get("node") or "")), "package.json engines")
        pm = str(pkg.get("packageManager") or "")
        m = re.match(r"^(pnpm|yarn|bun)@(\d[\w.\-]*)", pm)
        if m:
            pin(m.group(1), m.group(2).split("+")[0], "package.json packageManager")
    rt = _toml(project / "rust-toolchain.toml")
    if rt:
        pin("rust", str((rt.get("toolchain") or {}).get("channel") or ""), "rust-toolchain.toml")
    plain = _read(project / "rust-toolchain").strip()
    if plain and "\n" not in plain and "[" not in plain:
        pin("rust", plain, "rust-toolchain")
    cargo = _toml(project / "Cargo.toml")
    rv = (cargo.get("package") or {}).get("rust-version") or ((cargo.get("workspace") or {}).get("package") or {}).get("rust-version")
    if rv:
        pin("rust", str(rv), "Cargo.toml rust-version")
    v = _read(project / ".python-version").strip().splitlines()
    if v and re.match(r"^\d", v[0]):
        pin("python", v[0].strip(), ".python-version")
    for name in ("uv.lock",):
        m = re.search(r'^requires-python\s*=\s*"([^"]+)"', _read(project / name), re.M)
        if m:
            pin("python", _plain_version(m.group(1)), name)
    py = _toml(project / "pyproject.toml")
    if py:
        pin("python", _plain_version(str((py.get("project") or {}).get("requires-python") or "")), "pyproject.toml")
    m = re.search(r"^go\s+(\d+\.\d+(?:\.\d+)?)", _read(project / "go.mod"), re.M)
    if m:
        pin("go", m.group(1), "go.mod")
    return tools


# =====================================================================================  writing files
def render_justfile(commands: dict[str, dict[str, str]], name: str) -> str:
    lines = [JUST_HEADER.rstrip("\n"), f"# Project: {name}", "", "default:", "    @just --list", ""]
    for verb in VERBS:
        c = commands.get(verb)
        if not c or c["command"].startswith("just "):
            continue
        lines += [f"# {verb.capitalize()} (from {c['source']})", f"{verb}:", f"    {c['command']}", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_additions(existing: str, commands: dict[str, dict[str, str]]) -> str:
    """For a justfile you wrote: your file untouched, plus the recipes it doesn't have yet."""
    have = justfile_recipes(existing)
    add = []
    for verb in VERBS:
        c = commands.get(verb)
        if not c or c["command"].startswith("just ") or any(w in have for w in VERB_WORDS[verb]):
            continue
        add += [f"# {verb.capitalize()} (suggested by Orion's Project Runbook, from {c['source']})", f"{verb}:",
                f"    {c['command']}", ""]
    if not add:
        return existing
    return existing.rstrip("\n") + "\n\n" + "\n".join(add).rstrip("\n") + "\n"


def render_mise(tools: dict[str, dict[str, str]]) -> str:
    lines = [MISE_HEADER.rstrip("\n"), "[tools]"]
    for tool in sorted(tools):
        lines.append(f'{tool} = "{tools[tool]["version"]}"  # from {tools[tool]["source"]}')
    return "\n".join(lines) + "\n"


def sha(text: Optional[str]) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest() if text is not None else ""


def unified(path: str, before: Optional[str], after: str) -> str:
    return "".join(difflib.unified_diff((before or "").splitlines(True), after.splitlines(True),
                                        fromfile=f"a/{path}" if before is not None else "/dev/null",
                                        tofile=f"b/{path}"))


# =====================================================================================  the runbook
class RunbookError(Exception):
    pass


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class Runbook:
    def __init__(self, data_dir: Path, board: Any = None, runner: Any = None,
                 search_dirs: Optional[Callable[[], list[Path]]] = None,
                 grimoire: Optional[Callable[[], list[dict[str, Any]]]] = None,
                 notify: Optional[Callable[[str, str], None]] = None) -> None:
        self.dir = Path(data_dir) / "runbook"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.board = board
        self.runner = runner
        self.search_dirs = search_dirs or default_search_dirs
        self.grimoire = grimoire or (lambda: [])
        self.notify = notify or (lambda t, b: None)

    # ------------------------------------------------------------ state
    def _state(self) -> dict[str, Any]:
        data = _json(self.state_path)
        data.setdefault("projects", {})
        data.setdefault("proposals", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------ projects
    def projects(self) -> list[Path]:
        seen: dict[str, Path] = {}
        for p in [Path(x) for x in self._state()["projects"]]:
            if p.is_dir():
                seen[str(p)] = p
        for root in self.search_dirs():
            if not root.is_dir():
                continue
            if _looks_like_project(root):
                seen.setdefault(str(root), root)
            try:
                kids = sorted(root.iterdir())
            except OSError:
                continue
            for c in kids:
                if c.is_dir() and not c.name.startswith(".") and _looks_like_project(c):
                    seen.setdefault(str(c), c)
        return list(seen.values())

    def find(self, name_or_path: str) -> Optional[Path]:
        raw = (name_or_path or "").strip()
        if not raw:
            return None
        projects = self.projects()
        p = Path(os.path.expanduser(raw))
        if p.is_absolute():
            try:
                rp = p.resolve()
            except OSError:
                return None
            return next((x for x in projects if x.resolve() == rp), None)
        want = _key(raw)
        if not want:
            return None
        for x in projects:
            if _key(x.name) == want:
                return x
        return None

    # ------------------------------------------------------------ learning
    def learn(self, project: Path) -> dict[str, Any]:
        """What Orion knows about this project, and the files it would write (nothing is written here)."""
        project = Path(project)
        commands = detect_commands(project, self.grimoire())
        tools = detect_tools(project)
        files = []
        jf = justfile_path(project)
        st = self._state()["projects"].get(str(project), {})
        written = st.get("written", {})
        if jf is None:
            files.append(self._file(project, "justfile", None, render_justfile(commands, project.name), "new"))
        else:
            cur = _read(jf)
            ours = MARKER in cur and written.get(jf.name) == sha(cur)
            if ours:
                # Orion's own file: rebuild it from the project itself, so it follows package.json & co.
                after = render_justfile(detect_commands_without_justfile(project, self.grimoire()), project.name)
                if after != cur:
                    files.append(self._file(project, jf.name, cur, after, "update"))
            else:
                after = render_additions(cur, commands)
                if after != cur:
                    files.append(self._file(project, jf.name, cur, after, "yours"))
        if tools:
            mp = project / ".mise.toml"
            cur = _read(mp) if mp.exists() else None
            if cur is None:
                if not (project / ".tool-versions").exists() and not (project / "mise.toml").exists():
                    files.append(self._file(project, ".mise.toml", None, render_mise(tools), "new"))
            elif MARKER in cur and written.get(".mise.toml") == sha(cur):
                after = render_mise(tools)
                if after != cur:
                    files.append(self._file(project, ".mise.toml", cur, after, "update"))
        return {"project": str(project), "name": project.name, "commands": commands, "tools": tools,
                "files": files, "just_installed": bool(shutil.which("just")),
                "mise_installed": bool(shutil.which("mise"))}

    @staticmethod
    def _file(project: Path, rel: str, before: Optional[str], after: str, kind: str) -> dict[str, Any]:
        return {"path": rel, "kind": kind, "before_sha": sha(before) if before is not None else "",
                "content": after, "diff": unified(rel, before, after)}

    # ------------------------------------------------------------ proposing / applying
    async def propose(self, project: Path, requested_by: str = "Orion") -> dict[str, Any]:
        """Write or update the runbook files: by itself only when that's allowed, otherwise ask first."""
        info = self.learn(project)
        files = info["files"]
        if not files:
            return {"state": "up_to_date", "message": f"{project.name}'s runbook is already up to date.", **info}
        st = self._state()
        pst = st["projects"].get(str(project), {})
        auto = pst.get("approved_once") and all(f["kind"] == "update" for f in files)
        if auto:
            res = apply_files(project, files)
            self._record_written(project, res["written"])
            self.notify(f"Updated {project.name}'s runbook", ", ".join(f["path"] for f in files))
            return {"state": "applied", "message": f"Updated {', '.join(f['path'] for f in files)} in "
                                                   f"{project.name} (only files Orion wrote, unchanged since).", **info}
        for d in (self.board.pending() if self.board is not None else []):
            if d["task_type"] == TASK_WRITE and d["packet"]["details"].get("project") == str(project):
                return {"state": "waiting", "decision_id": d["id"],
                        "message": "A runbook change for this project is already waiting for your OK.", **info}
        if self.board is None:
            return {"state": "proposal", "message": "Here is what Orion would write.", **info}
        pid = uuid.uuid4().hex[:10]
        st["proposals"][pid] = {"project": str(project), "files": files, "created": time.time()}
        self._save(st)
        yours = any(f["kind"] == "yours" for f in files)
        summary = (f"Add missing steps to your justfile in {project.name}" if yours else
                   f"Write {' and '.join(f['path'] for f in files)} for {project.name}")
        dec = await self.board.propose(
            TASK_WRITE, summary,
            {"project": str(project), "proposal": pid, "files": [f["path"] for f in files],
             "diff": "\n".join(f["diff"] for f in files)[:6000],
             "steps": {v: c["command"] for v, c in info["commands"].items()},
             "what_happens": "Approve = Orion writes exactly this diff. Your own files are never replaced, only "
                             "added to.", "requested_by": requested_by},
            proposer="Project Runbook", risk="low", simulated=False)
        return {"state": "waiting", "decision_id": dec["id"],
                "message": f"Asked you to approve the runbook files for {project.name}.", **info}

    def execute_write(self, d: dict[str, Any]) -> dict[str, Any]:
        det = d["packet"]["details"]
        st = self._state()
        prop = st["proposals"].pop(det.get("proposal", ""), None)
        if prop is None:
            return {"state": "failed", "error": "That runbook proposal is gone; ask Orion to learn the project again."}
        project = Path(prop["project"])
        res = apply_files(project, prop["files"])
        self._save(st)
        if res["written"]:
            self._record_written(project, res["written"], approved=True)
        return {"state": "done" if not res["skipped"] else "partial", **res}

    def _record_written(self, project: Path, written: dict[str, str], approved: bool = False) -> None:
        st = self._state()
        p = st["projects"].setdefault(str(project), {})
        p.setdefault("written", {}).update(written)
        if approved:
            p["approved_once"] = True
        self._save(st)

    # ------------------------------------------------------------ running
    def command_for(self, project: Path, verb: str) -> tuple[str, str]:
        """(command, plain-words note)."""
        verb = verb.lower()
        jf = justfile_path(project)
        just = shutil.which("just")
        if jf:
            recipes = justfile_recipes(_read(jf))
            name = next((w for w in VERB_WORDS.get(verb, (verb,)) if w in recipes), verb if verb in recipes else None)
            if name:
                body = recipes.get(name, "")
                if just:
                    return f"just {name}", body
                cmds = detect_commands_without_justfile(project, self.grimoire())
                if verb in cmds:
                    return cmds[verb]["command"], "just isn't installed, so Orion ran the command directly"
                if body and "\n" not in body:
                    return body.lstrip("@-"), "just isn't installed, so Orion ran the recipe's command directly"
        cmds = detect_commands(project, self.grimoire())
        if verb in cmds and not cmds[verb]["command"].startswith("just "):
            return cmds[verb]["command"], f"from {cmds[verb]['source']}"
        raise RunbookError(f"I don't know how to {verb} {project.name} yet. Add a `{verb}` recipe to its justfile, "
                           "or a script to package.json, and I'll pick it up.")

    async def run(self, project: Path, verb: str, requested_by: str = "you") -> dict[str, Any]:
        command, note = self.command_for(project, verb)
        body = note if command.startswith("just ") else command
        needs_ok = verb == "deploy" or is_destructive(command) or is_destructive(body)
        if needs_ok:
            if self.board is None:
                raise RunbookError("That step needs your approval, and Orion can't ask right now.")
            dec = await self.board.propose(
                TASK_RUN, f"{verb.capitalize()} {project.name}: `{command}`",
                {"project": str(project), "command": command, "verb": verb, "recipe": body[:1500],
                 "requested_by": requested_by,
                 "why_it_asks": "deploying changes a server" if verb == "deploy" else "it looks like it deletes or "
                                                                                     "overwrites things"},
                proposer="Project Runbook", risk="medium", simulated=False)
            return {"state": "waiting", "decision_id": dec["id"], "command": command,
                    "message": f"{verb.capitalize()} {project.name} needs your OK first; it's on your phone."}
        if self.runner is None:
            raise RunbookError("Orion's runner isn't available right now.")
        run = await self.runner.start(command, cwd=str(project), label=f"{project.name}: {verb}",
                                      kind="service" if verb == "run" else "task")
        info = run.info() if hasattr(run, "info") else dict(run)
        extra = f" ({note})" if "just isn't" in note else ""
        return {"state": "started", "run": info, "command": command,
                "message": f"Started `{command}` in {project.name}{extra}."}

    def execute_run(self, d: dict[str, Any], spawn: Callable[[Any], Any]) -> dict[str, Any]:
        det = d["packet"]["details"]
        if self.runner is None:
            return {"state": "failed", "error": "Orion's runner isn't available."}

        async def go() -> None:
            await self.runner.start(det["command"], cwd=det["project"], label=f"{Path(det['project']).name}: "
                                    f"{det.get('verb', 'run')}", kind="task")
        spawn(go())
        return {"state": "started"}

    # ------------------------------------------------------------ quick-command plugin
    def match_phrase(self, text: str) -> Optional[tuple[str, Path]]:
        m = re.match(r"^(run|test|build|deploy|lint)( the)? (.+?)( project)?$", text)
        if not m:
            return None
        project = self.find(m.group(3))
        return (m.group(1), project) if project else None


def detect_commands_without_justfile(project: Path, grimoire: Optional[list[dict[str, Any]]] = None) -> dict[str, dict[str, str]]:
    return detect_commands(project, grimoire, use_justfile=False)


def apply_files(project: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    """Write approved files, but only where the file still is exactly what the diff was made from."""
    written: dict[str, str] = {}
    skipped: list[str] = []
    for f in files:
        target = (project / f["path"])
        if target.parent.resolve() != project.resolve():
            skipped.append(f["path"])
            continue
        now = _read(target) if target.exists() else None
        if (sha(now) if now is not None else "") != f["before_sha"]:
            skipped.append(f["path"])
            continue
        tmp = target.with_name(target.name + ".orion-tmp")
        tmp.write_text(f["content"], encoding="utf-8")
        tmp.replace(target)
        written[f["path"]] = sha(f["content"])
    return {"written": written, "skipped": skipped,
            "message": ("Wrote " + ", ".join(written) if written else "Nothing written") +
                       (f". Skipped {', '.join(skipped)}: changed since you saw the diff." if skipped else ".")}


def _looks_like_project(p: Path) -> bool:
    return any((p / n).exists() for n in ("package.json", "Cargo.toml", "pyproject.toml", "Makefile", "justfile",
                                          "go.mod", "setup.py", ".git"))


def default_search_dirs() -> list[Path]:
    dirs: list[Path] = [Path.home() / "projects"]
    try:
        from mcp_tools import McpConfig
        dirs += McpConfig.load().resolved_roots()
    except Exception:
        pass
    return dirs


def _register_task_types() -> None:
    import boardroom
    from boardroom import TaskType
    for t in (TaskType(TASK_WRITE, "Write a project's runbook files", "code", reversible=True, money=False),
              TaskType(TASK_RUN, "Run a risky project step (deploy, deletes)", "code", reversible=False,
                       money=False, sensitive=True)):
        boardroom.TASK_TYPES.setdefault(t.key, t)


_register_task_types()
