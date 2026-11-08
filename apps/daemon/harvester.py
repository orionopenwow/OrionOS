"""Preload Harvester (Feature Bible, Part II.3) — pre-fills Orion's brain from your machine in ~30 s.

  * Repo & package scanner: every folder with .git / package.json / Cargo.toml / pyproject.toml under
    your dev roots becomes a Projects-tab workspace (with its npm scripts and a sensible start command).
  * Shell-history miner: the 50 commands you run most (PowerShell ConsoleHost_history.txt, bash/zsh
    history) are grouped into named Grimoire macros by the local Ollama model (heuristics offline).
    Lines that look like they contain secrets are never read into Orion.
  * Markdown & docs ingester: README / plans / architecture docs in each project are chunked and
    embedded into memory so Orion can answer questions about any project on day one.

Nothing is executed. Harvested macros are added with requires_approval=true.
Run from the HUD (Projects tab → Harvest) or:  python harvester.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.harvester")

PROJECT_MARKERS = (".git", "package.json", "Cargo.toml", "pyproject.toml", "setup.py", "go.mod", "project.godot")
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env", "__pycache__", "dist", "build", "target", ".next",
    ".cache", "AppData", "Library", ".npm", ".cargo", ".rustup", "site-packages", ".idea", ".vscode",
    "Program Files", "Program Files (x86)", "Windows", "$Recycle.Bin", ".Trash",
}
DOC_SKIP_DIRS = SKIP_DIRS | {"vendor", "third_party", "coverage", "out"}
SECRET_RE = re.compile(
    r"(pass(word)?|passwd|secret|token|api[_-]?key|apikey|bearer|authorization|private[_-]?key|credential|"
    r"sk-[\w-]{12,}|sk_(live|test)_\w{8,}|gh[pousr]_\w{8,}|github_pat_|glpat-|xox[abprs]-|AKIA[0-9A-Z]{12,}|"
    r"AIza[\w-]{20,}|hf_\w{20,}|npm_\w{20,}|--key\b|(?<![\w-])-p(?!\s*\d+(?::\d+)*(?:/\w+)?(?:\s|$))\s*\S{6,}|-u\s+\S+:(?!\d+(?:\s|$))\S+|"
    r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@|"          # credentials embedded in any URL
    r"connectionstring|mongodb(\+srv)?://)",
    re.I,
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_\-]{32,}")


def _high_entropy(text: str) -> bool:
    """Long random-looking tokens (keys we don't have a pattern for)."""
    import math

    for tok in _TOKEN_RE.findall(text):
        if tok.startswith(("http", "/")) or tok.isalpha() or tok.isdigit():
            continue
        counts = {c: tok.count(c) for c in set(tok)}
        entropy = -sum(n / len(tok) * math.log2(n / len(tok)) for n in counts.values())
        if entropy > 4.0:
            return True
    return False


def looks_secret(text: str) -> bool:
    return bool(SECRET_RE.search(text)) or _high_entropy(text)


TRIVIAL_CMD = re.compile(r"^(cd|ls|ll|dir|cls|clear|exit|pwd|code \.|git status|git diff|history|whoami|echo)\b", re.I)
MAX_DOCS_PER_PROJECT = 40
MAX_DOC_BYTES = 200_000
CHUNK_CHARS = 1500

Progress = Callable[[str, float], Awaitable[None]]


def default_roots(repo_root: Path) -> list[Path]:
    env = os.getenv("HARVEST_ROOTS", "").strip()
    if env:
        return [Path(os.path.expanduser(p.strip())) for p in env.split(",") if p.strip()]
    home = Path.home()
    candidates = [repo_root.parent, home / "Documents", home / "dev", home / "projects", home / "code",
                  home / "source" / "repos", home / "src", home / "Desktop"]
    seen, out = set(), []
    for c in candidates:
        try:
            key = str(c.resolve()).lower()
        except OSError:
            continue
        if c.is_dir() and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "project"


def describe_project(path: Path) -> Optional[dict[str, Any]]:
    markers = [m for m in PROJECT_MARKERS if (path / m).exists()]
    if not markers:
        return None
    name = path.name
    scripts: list[str] = []
    start_cmd = ""
    tags: set[str] = set()
    pkg = path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            name = data.get("name") or name
            scripts = list((data.get("scripts") or {}).keys())[:30]
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            for dep, tag in (("react", "react"), ("three", "threejs"), ("vite", "vite"), ("@tauri-apps/api", "tauri"),
                             ("next", "nextjs"), ("electron", "electron"), ("discord.js", "discord"), ("typescript", "typescript")):
                if dep in deps:
                    tags.add(tag)
            tags.add("node")
            for s in ("dev", "start", "serve"):
                if s in scripts:
                    start_cmd = f"npm run {s}" if s != "start" else "npm start"
                    break
        except (OSError, ValueError):
            pass
    if (path / "Cargo.toml").exists():
        tags.add("rust")
        start_cmd = start_cmd or "cargo run"
    if (path / "pyproject.toml").exists() or (path / "setup.py").exists() or (path / "requirements.txt").exists():
        tags.add("python")
        if not start_cmd and (path / "main.py").exists():
            start_cmd = "python main.py"
    if (path / "go.mod").exists():
        tags.add("go")
        start_cmd = start_cmd or "go run ."
    if (path / "project.godot").exists():
        tags.add("godot")
    return {
        "id": _slug(path.name),
        "name": str(name),
        "path": str(path),
        "start_cmd": start_cmd,
        "ports": [],
        "tags": sorted(tags),
        "scripts": scripts,
        "git": (path / ".git").exists(),
    }


def scan_workspaces(roots: list[Path], max_depth: int = 3, limit: int = 200) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(directory: Path, depth: int) -> None:
        if len(found) >= limit or depth > max_depth:
            return
        try:
            key = str(directory.resolve()).lower()
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        project = describe_project(directory) if depth > 0 or directory.name else None
        if project:
            found.append(project)
            return  # a project's subfolders are part of it (monorepo packages stay together)
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            walk(child, depth + 1)

    for root in roots:
        if root.is_dir():
            # A root that is itself a project (e.g. you pointed at one repo) counts too.
            project = describe_project(root)
            if project:
                if str(root.resolve()).lower() not in seen:
                    seen.add(str(root.resolve()).lower())
                    found.append(project)
                continue
            walk(root, 0)
    return found


# ------------------------------------------------------------------ shell history
def history_files() -> list[Path]:
    files = []
    appdata = os.getenv("APPDATA")
    if appdata:
        files.append(Path(appdata) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt")
    home = Path.home()
    files += [home / ".bash_history", home / ".zsh_history"]
    return [f for f in files if f.exists()]


def top_commands(limit: int = 50) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for f in history_files():
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]
        except OSError:
            continue
        for line in lines:
            line = re.sub(r"^: \d+:\d+;", "", line).strip()  # zsh extended history prefix
            if len(line) < 8 or len(line) > 300 or TRIVIAL_CMD.match(line) or looks_secret(line):
                continue
            counter[line] += 1
    return [(cmd, n) for cmd, n in counter.most_common(limit * 2) if n >= 2][:limit]


MACRO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "macros": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "voice_triggers": {"type": "array", "items": {"type": "string"}},
                    "command": {"type": "string"},
                },
                "required": ["id", "description", "voice_triggers", "command"],
            },
        }
    },
    "required": ["macros"],
}

MACRO_PROMPT = """These are the shell commands this developer runs most often (count, command).
Turn the useful ones into named macros for a voice assistant. Keep each command EXACTLY as written
(you may join closely related consecutive commands with &&). Give 2-3 short natural voice triggers each.
Skip anything trivial. Respond with JSON only.

{commands}"""


def heuristic_macros(cmds: list[tuple[str, int]]) -> list[dict[str, Any]]:
    out = []
    for cmd, n in cmds[:25]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]+", cmd)[:4]
        label = " ".join(words) or cmd[:30]
        out.append({
            "id": _slug(label).replace("-", "_"),
            "description": f"Frequently used command ({n}×): {cmd[:80]}",
            "voice_triggers": [f"run {label.lower()}"],
            "command": cmd,
        })
    return out


async def group_macros(cmds: list[tuple[str, int]], orchestrator: Any = None) -> list[dict[str, Any]]:
    if not cmds:
        return []
    macros: list[dict[str, Any]] = []
    if orchestrator is not None:
        try:
            raw, _ = await orchestrator.ollama_json(
                MACRO_PROMPT.format(commands="\n".join(f"{n}\t{c}" for c, n in cmds)), MACRO_SCHEMA, timeout=90.0)
            known = {c for c, _ in cmds}
            for m in raw.get("macros", []):
                command = str(m.get("command", "")).strip()
                parts = [p.strip() for p in command.split("&&")]
                if not command or not all(p in known for p in parts) or looks_secret(command):
                    continue  # the model may only reuse commands it was shown
                macros.append({
                    "id": _slug(str(m.get("id") or m.get("description") or command)).replace("-", "_"),
                    "description": str(m.get("description", ""))[:200],
                    "voice_triggers": [str(t)[:60] for t in m.get("voice_triggers", [])][:4],
                    "command": command,
                })
        except Exception as exc:
            log.info("Ollama macro grouping unavailable (%s); using heuristics", exc)
    if not macros:
        macros = heuristic_macros(cmds)
    for m in macros:
        m.update({"requires_approval": True, "harvested": True, "kind": "shell"})
    return macros


# ------------------------------------------------------------------ markdown docs
def find_docs(project: Path) -> list[Path]:
    docs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [d for d in dirnames if d not in DOC_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith((".md", ".mdx")):
                p = Path(dirpath) / fn
                try:
                    if p.stat().st_size <= MAX_DOC_BYTES:
                        docs.append(p)
                except OSError:
                    continue
            if len(docs) >= MAX_DOCS_PER_PROJECT:
                return docs
    return docs


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text)
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current.strip())
            current = ""
        while len(para) > size:
            chunks.append(para[:size])
            para = para[size:]
        current += para + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


async def ingest_docs(workspaces: list[dict[str, Any]], memory: Any, state: dict[str, str],
                      progress: Optional[Progress] = None) -> tuple[int, int]:
    """Returns (files ingested, chunks). `state` maps file path -> content hash (updated in place)."""
    files = chunks_total = 0
    for i, ws in enumerate(workspaces):
        root = Path(ws["path"])
        if not root.is_dir():
            continue
        for doc in await asyncio.to_thread(find_docs, root):
            try:
                text = await asyncio.to_thread(doc.read_text, encoding="utf-8", errors="replace")
            except OSError:
                continue
            digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
            key = str(doc)
            if state.get(key) == digest or not text.strip():
                continue
            rel = doc.relative_to(root).as_posix()
            items = [("docs", f"[{ws['name']} · {rel}]\n{c}") for c in chunk_text(text)[:30]]
            chunks_total += await memory.ingest_batch(items)
            state[key] = digest
            files += 1
        if progress:
            await progress(f"Reading docs in {ws['name']}", 0.6 + 0.35 * (i + 1) / max(1, len(workspaces)))
    return files, chunks_total


# ------------------------------------------------------------------ orchestration
async def harvest(preload: Any, memory: Any, orchestrator: Any = None, roots: Optional[list[Path]] = None,
                  progress: Optional[Progress] = None) -> dict[str, Any]:
    async def report(msg: str, frac: float) -> None:
        if progress:
            await progress(msg, frac)

    roots = roots or default_roots(preload.repo_root)
    await report(f"Scanning {len(roots)} folders for projects…", 0.05)
    workspaces = await asyncio.to_thread(scan_workspaces, roots)
    added_ws = preload.merge_workspaces(workspaces)

    await report("Mining your most-used shell commands…", 0.35)
    cmds = await asyncio.to_thread(top_commands)
    macros = await group_macros(cmds, orchestrator)
    added_macros = preload.merge_grimoire(macros)

    await report("Reading project docs…", 0.6)
    state_key = "docs"
    state = preload.ingest_state().get(state_key)
    doc_state = json.loads(state) if isinstance(state, str) and state.startswith("{") else {}
    files, chunks = await ingest_docs(preload.workspaces, memory, doc_state, progress)
    preload.mark_ingested(state_key, json.dumps(doc_state))

    result = {
        "roots": [str(r) for r in roots],
        "projects_found": len(workspaces),
        "projects_added": added_ws,
        "commands_seen": len(cmds),
        "macros_added": added_macros,
        "docs_ingested": files,
        "doc_chunks": chunks,
    }
    await report("Harvest complete", 1.0)
    log.info("Harvest: %s", result)
    return result


if __name__ == "__main__":  # python harvester.py  — standalone run (no Ollama grouping)
    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, str(Path(__file__).parent))
    from config import settings
    from memory_core import MemoryCore
    from preload import Preload

    async def _main() -> None:
        pre = Preload(settings.data_dir, Path(__file__).parent, settings.repo_root)

        async def show(msg: str, frac: float) -> None:
            print(f"[{int(frac * 100):3d}%] {msg}")
        print(json.dumps(await harvest(pre, MemoryCore(), None, progress=show), indent=2))

    asyncio.run(_main())
