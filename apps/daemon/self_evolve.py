"""Voice-to-Patch Self-Coding ("Talk-to-Develop", V2).

In Deep mode, a spoken feature request ("Orion, add a mute button to the comms tab")
invokes the `self_evolve` tool. Orion:

  1. plans which of its own source files are relevant (frontier model, file index only),
  2. reads them and asks for surgical search/replace edits (+ optional new files),
  3. applies the edits in memory, validates them (each `find` must match exactly once,
     Python must compile, JSON must parse) with one automatic repair round,
  4. pushes a PATCH_PROPOSED card with a unified-diff preview.

Nothing touches disk until the admin approves. Approval backs up the originals to
~/.orion/patches/<id>/backup, writes the files and hot-reloads: Vite HMR picks up UI
changes instantly; daemon changes trigger a clean restart (exit code 75, relaunched by
scripts/run-daemon.mjs). Every applied patch can be reverted from the same card.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.self_evolve")

RESTART_EXIT_CODE = 75
MAX_FILES_TO_READ = 6
MAX_FILE_BYTES = 120_000
MAX_NEW_FILE_BYTES = 60_000

INDEX_GLOBS = (
    "apps/daemon/*.py",
    "apps/desktop/src/**/*.ts",
    "apps/desktop/src/**/*.tsx",
    "apps/desktop/src/**/*.css",
    "apps/desktop/tailwind.config.ts",
    "packages/shared-types/src/*.ts",
    "apps/extension/*.js",
)
WRITABLE_SUFFIXES = {".py", ".ts", ".tsx", ".css", ".js", ".json", ".md"}
FORBIDDEN_PARTS = {".git", "node_modules", ".venv", "__pycache__", "dist", "target"}
FORBIDDEN_NAMES = {".env", "package-lock.json", "package.json", "manifest.json", "vite.config.js",
                   "vite.config.mjs", "vite.config.ts", "tailwind.config.js", "postcss.config.js",
                   "tsconfig.json", "requirements.txt", "requirements-optional.txt"}
# New files may only be created inside the source trees Orion indexes.
CREATE_DIRS = ("apps/daemon/", "apps/desktop/src/", "packages/shared-types/src/", "apps/extension/")
PATCH_ID = re.compile(r"^patch-[0-9a-f]{8}$")

EVOLVE_TRIGGER = re.compile(
    r"^\s*(?:hey\s+)?(?:orion[,:]?\s*)?(?:please\s+)?(?:can you\s+|could you\s+|i want you to\s+|let'?s\s+)?"
    r"(?:add|build|create|implement|make|give (?:me|yourself)|wire up|upgrade|change|update|evolve)\b"
    r".*\b(?:feature|button|tab|panel|widget|command|tool|setting|option|mode|shortcut|hotkey|card|"
    r"view|page|screen|ui|hud|endpoint|integration|yourself|your(?:self)? code)\b",
    re.I,
)
# The request must clearly point at Orion's OWN software. Addressing Orion by name ("Orion, create a
# calendar event...") is not enough — that is an ordinary task for the orchestrator.
SELF_REFERENCE = re.compile(
    r"\b(?:yourself|your\s+(?:own\s+)?(?:code|source|ui|hud|interface|features?|daemon|app|settings|"
    r"tabs?|panels?|widgets?|voice|core|orb|dial)|orion'?s\s+(?:own\s+)?(?:code|ui|hud|interface|app|daemon|"
    r"tabs?|panels?|core)|to\s+(?:you|orion)\b|(?:the|my)\s+(?:orion\s+)?(?:hud|context window|planetary "
    r"(?:context )?window|comms tab|queue tab|chat tab|projects tab|tools tab|cosmic core|earth button|"
    r"approval queue|daemon|orion (?:ui|app|hud)))",
    re.I,
)
SLASH_TRIGGER = re.compile(r"^\s*/(?:evolve|self[-_ ]?evolve)\s+(.+)$", re.I | re.S)

PLAN_SYSTEM = """You are Orion's self-modification planner. Orion is a desktop agent: a Python FastAPI daemon
(apps/daemon), a React + Tailwind + framer-motion Tauri UI (apps/desktop/src), a shared TypeScript IPC
protocol (packages/shared-types/src/index.ts, mirrors apps/daemon/schemas.py) and a Chrome MV3 extension
(apps/extension). Given a feature request and the file index, choose the smallest set of files that must
be READ to implement it (max 6). Respond with JSON: {"files_to_read": [path, ...], "approach": str}"""

EDIT_SYSTEM = """You are Orion modifying its own source code. Implement the requested feature with the
smallest correct change that fits the existing code style.

Return JSON only:
{
  "summary": "one or two sentences describing the change",
  "edits": [{"path": "repo/relative/path", "find": "exact existing text", "replace": "new text"}],
  "new_files": [{"path": "repo/relative/path", "content": "full file content"}]
}

Rules:
- `find` must be copied EXACTLY from the provided file contents (whitespace included) and must occur
  exactly once in that file. Include enough surrounding lines to make it unique. Keep finds short.
- Never edit files you were not shown, except by creating new files.
- Keep the daemon <-> UI protocol in sync (schemas.py / main.py dispatch and shared-types index.ts).
- No placeholder code, no TODOs, no explanations outside the JSON."""


class PatchError(RuntimeError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_raw(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_raw(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def detect_evolve_request(text: str) -> Optional[str]:
    """Return the feature request if `text` asks Orion to change itself."""
    m = SLASH_TRIGGER.match(text)
    if m:
        return m.group(1).strip()
    if EVOLVE_TRIGGER.search(text) and SELF_REFERENCE.search(text):
        return text.strip()
    return None


GENERIC_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".rs", ".go", ".lua",
                ".cs", ".java", ".kt", ".vue", ".svelte", ".html", ".md", ".glsl", ".wgsl"}
MAX_GENERIC_FILES = 700

CROSS_PLAN_SYSTEM = """You plan a cross-project code transplant. Files prefixed "source:<project>/" belong to the
SOURCE project (read-only reference). Unprefixed paths belong to the TARGET project, which will be edited.
Choose the smallest set of files to READ (max 6): the source code being transplanted plus the target files it
must integrate with. Respond with JSON: {"files_to_read": [path, ...], "approach": str}"""

CROSS_EDIT_SYSTEM = """You transplant code from one of the developer's projects into another and adapt it to the
target's conventions (framework, language, naming, imports). Files prefixed "source:" are READ-ONLY reference.

Return JSON only:
{
  "summary": "one or two sentences describing the change",
  "edits": [{"path": "target/relative/path", "find": "exact existing text", "replace": "new text"}],
  "new_files": [{"path": "target/relative/path", "content": "full file content"}]
}
Rules: `find` must be copied exactly from a TARGET file you were shown and match exactly once. Never edit
"source:" files. No placeholders or TODOs."""


def _walk_files(root: Path, limit: int = MAX_GENERIC_FILES) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in FORBIDDEN_PARTS and not d.startswith("."))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.suffix.lower() in GENERIC_EXTS:
                out.append(p)
                if len(out) >= limit:
                    return out
    return out


class SelfEvolveEngine:
    def __init__(self, orchestrator: Any, repo_root: Path, data_dir: Path, *,
                 source_roots: Optional[dict[str, Path]] = None, generic: bool = False,
                 label: str = "Orion") -> None:
        self.orchestrator = orchestrator
        self.root = repo_root.resolve()
        self.dir = data_dir / "patches"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.proposals: dict[str, dict[str, Any]] = {}
        self._busy = asyncio.Lock()
        # Cross-repo mode: other projects readable as "source:<name>/<path>", the target is generic.
        self.source_roots = {k: v.resolve() for k, v in (source_roots or {}).items()}
        self.generic = generic or bool(self.source_roots)
        self.label = label

    # ------------------------------------------------------------ files
    def _safe_path(self, rel: str, root: Optional[Path] = None) -> Path:
        root = (root or self.root).resolve()
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel or ".." in Path(rel).parts:
            raise PatchError(f"illegal path {rel!r}")
        path = (root / rel).resolve()
        if root not in path.parents:
            raise PatchError(f"path escapes repo: {rel}")
        parts = set(path.relative_to(root).parts)
        if parts & FORBIDDEN_PARTS or path.name in FORBIDDEN_NAMES or path.name.startswith(".env"):
            raise PatchError(f"protected path: {rel}")
        if path.suffix not in (GENERIC_EXTS if self.generic else WRITABLE_SUFFIXES):
            raise PatchError(f"unsupported file type: {rel}")
        return path

    def _source_path(self, rel: str) -> Path:
        """'source:<project>/<path>' -> absolute path inside that (read-only) project."""
        rest = rel[len("source:"):]
        name, _, sub = rest.partition("/")
        root = self.source_roots.get(name)
        if root is None:
            raise PatchError(f"unknown source project in {rel!r}")
        return self._safe_path(sub, root)

    def _safe_new_path(self, rel: str) -> Path:
        path = self._safe_path(rel)
        if self.generic:
            return path
        rel_posix = path.relative_to(self.root).as_posix()
        if not rel_posix.startswith(CREATE_DIRS):
            raise PatchError(f"new files may only be created under {', '.join(CREATE_DIRS)}: {rel}")
        return path

    @staticmethod
    def _check_id(patch_id: str) -> str:
        if not isinstance(patch_id, str) or not PATCH_ID.match(patch_id):
            raise PatchError(f"invalid patch id {patch_id!r}")
        return patch_id

    def file_index(self) -> list[dict[str, Any]]:
        seen: dict[str, int] = {}
        if self.generic:
            for p in _walk_files(self.root):
                seen[p.relative_to(self.root).as_posix()] = p.stat().st_size
            for name, root in self.source_roots.items():
                for p in _walk_files(root):
                    seen[f"source:{name}/{p.relative_to(root).as_posix()}"] = p.stat().st_size
            return [{"path": k, "bytes": v} for k, v in sorted(seen.items())]
        for pattern in INDEX_GLOBS:
            for p in self.root.glob(pattern):
                if p.is_file() and not (set(p.relative_to(self.root).parts) & FORBIDDEN_PARTS):
                    seen[p.relative_to(self.root).as_posix()] = p.stat().st_size
        return [{"path": k, "bytes": v} for k, v in sorted(seen.items())]

    def _read(self, rel: str) -> str:
        path = self._source_path(rel) if rel.startswith("source:") else self._safe_path(rel)
        if not path.exists():
            raise PatchError(f"file not found: {rel}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PatchError(f"file too large to edit safely: {rel}")
        return _read_raw(path)

    # ------------------------------------------------------------ propose
    async def propose(self, request: str, *, trigger: str = "voice") -> dict[str, Any]:
        if (frozen := __import__("freeze").blocked("change its own code")):  # kill switch (freeze.py)
            raise PatchError(frozen)
        if self._busy.locked():
            raise PatchError("Another self-evolve patch is already being generated.")
        async with self._busy:
            started = time.perf_counter()
            index = await asyncio.to_thread(self.file_index)
            plan_raw, plan_model = await self.orchestrator.frontier_text(
                CROSS_PLAN_SYSTEM if self.source_roots else PLAN_SYSTEM,
                f"Feature request: {request}\n\nFile index (path, bytes):\n"
                + "\n".join(f"{f['path']} ({f['bytes']})" for f in index),
                json_mode=True, max_tokens=600, temperature=0.1, timeout=120.0,
            )
            plan = _loads(plan_raw)
            wanted = [p for p in plan.get("files_to_read", []) if isinstance(p, str)][:MAX_FILES_TO_READ]
            known = {f["path"] for f in index}
            wanted = [p for p in wanted if p in known] or [p for p in ("apps/daemon/main.py",) if p in known]
            if not wanted:
                raise PatchError("The planner didn't pick any files it could read in this project.")

            originals: dict[str, str] = {}
            crlf: set[str] = set()
            for rel in wanted:
                try:
                    raw_text = self._read(rel)
                except PatchError as exc:
                    log.info("Skipping %s: %s", rel, exc)
                    continue
                if "\r\n" in raw_text:
                    crlf.add(rel)
                originals[rel] = raw_text.replace("\r\n", "\n")

            context = "\n\n".join(f"===== FILE: {rel} =====\n{body}" for rel, body in originals.items())
            user = (f"Feature request (spoken by the user): {request}\n\nPlanner approach: "
                    f"{plan.get('approach', '')}\n\n{context}")

            last_errors: list[str] = []
            model = plan_model
            for attempt in range(2):
                prompt = user if attempt == 0 else (
                    user + "\n\nYour previous edits failed validation:\n- " + "\n- ".join(last_errors)
                    + "\nReturn the full corrected JSON.")
                raw, model = await self.orchestrator.frontier_text(CROSS_EDIT_SYSTEM if self.source_roots else EDIT_SYSTEM,
                                                                   prompt, json_mode=True,
                                                                   max_tokens=8000, temperature=0.1, timeout=420.0)
                try:
                    spec = _loads(raw)
                    proposal = self._build(spec, originals, request, crlf)
                    break
                except PatchError as exc:
                    last_errors = str(exc).split("\n")
                    log.warning("Self-evolve validation failed (attempt %d): %s", attempt + 1, exc)
            else:
                raise PatchError("Could not produce a valid patch: " + "; ".join(last_errors[:5]))

            proposal.update({
                "trigger": trigger,
                "model": model,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            })
            self.proposals[proposal["id"]] = proposal
            self._persist(proposal)
            return self.public(proposal)

    def _build(self, spec: dict[str, Any], originals: dict[str, str], request: str,
               crlf: Optional[set[str]] = None) -> dict[str, Any]:
        crlf = crlf or set()
        errors: list[str] = []
        updated = dict(originals)
        touched: set[str] = set()
        edits = spec.get("edits") or []
        new_specs = spec.get("new_files") or []
        if not isinstance(edits, list) or not all(isinstance(e, dict) for e in edits):
            raise PatchError("`edits` must be a list of {path, find, replace} objects")
        if not isinstance(new_specs, list) or not all(isinstance(n, dict) for n in new_specs):
            raise PatchError("`new_files` must be a list of {path, content} objects")
        for e in edits:
            rel = str(e.get("path", "")).replace("\\", "/")
            find, repl = e.get("find"), e.get("replace")
            if isinstance(find, str):
                find = find.replace("\r\n", "\n")
            if isinstance(repl, str):
                repl = repl.replace("\r\n", "\n")
            if rel.startswith("source:"):
                errors.append(f"{rel}: source files are read-only — edit the target project instead")
                continue
            if rel not in updated:
                errors.append(f"{rel}: not one of the files you were shown")
                continue
            if not isinstance(find, str) or not isinstance(repl, str) or not find:
                errors.append(f"{rel}: edit needs non-empty string find/replace")
                continue
            count = updated[rel].count(find)
            if count != 1:
                errors.append(f"{rel}: find text matched {count} times (must be exactly 1): {find[:80]!r}")
                continue
            updated[rel] = updated[rel].replace(find, repl, 1)
            touched.add(rel)
        new_files: dict[str, str] = {}
        for nf in new_specs:
            rel = str(nf.get("path", "")).replace("\\", "/")
            content = nf.get("content")
            try:
                path = self._safe_new_path(rel)
            except PatchError as exc:
                errors.append(str(exc))
                continue
            if path.exists():
                errors.append(f"{rel}: already exists; use edits instead")
            elif not isinstance(content, str) or not content.strip() or len(content) > MAX_NEW_FILE_BYTES:
                errors.append(f"{rel}: new file content missing or too large")
            else:
                new_files[rel] = content
        if not touched and not new_files:
            errors.append("patch contains no changes")

        for rel in list(touched) + list(new_files):
            body = updated.get(rel, new_files.get(rel, ""))
            if rel.endswith(".py"):
                try:
                    compile(body, rel, "exec")
                except SyntaxError as exc:
                    errors.append(f"{rel}: Python syntax error line {exc.lineno}: {exc.msg}")
            elif rel.endswith(".json"):
                try:
                    json.loads(body)
                except ValueError as exc:
                    errors.append(f"{rel}: invalid JSON: {exc}")
        if errors:
            raise PatchError("\n".join(errors))

        files = []
        diff_parts = []
        for rel in sorted(touched | set(new_files)):
            before = originals.get(rel, "")
            after = updated.get(rel, new_files.get(rel, ""))
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{rel}" if rel in originals else "/dev/null", tofile=f"b/{rel}", n=3,
            ))
            add = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
            rem = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
            eol = "\r\n" if rel in crlf else "\n"
            files.append({
                "path": rel,
                "action": "create" if rel in new_files else "modify",
                "additions": add,
                "deletions": rem,
                "original_sha": _sha(before.replace("\n", eol)) if rel in originals else None,
                "new_content": after.replace("\n", eol),
            })
            diff_parts.append(diff)
        return {
            "id": f"patch-{uuid.uuid4().hex[:8]}",
            "root": str(self.root),
            "target_name": self.label,
            "kind": "cross_repo" if self.source_roots else "self",
            "request": request,
            "summary": str(spec.get("summary") or "Self-evolve patch")[:600],
            "files": files,
            "diff": "".join(diff_parts),
            "created_at": time.time(),
            "status": "proposed",
        }

    @staticmethod
    def public(proposal: dict[str, Any]) -> dict[str, Any]:
        """Proposal without full file bodies (what the UI receives)."""
        own = proposal.get("kind", "self") == "self"
        return {
            **{k: v for k, v in proposal.items() if k != "files"},
            "files": [{k: v for k, v in f.items() if k != "new_content"}
                      for f in proposal["files"]],
            "touches_daemon": own and any(f["path"].startswith("apps/daemon/") for f in proposal["files"]),
            "touches_ui": own and any(f["path"].startswith(("apps/desktop/", "packages/")) for f in proposal["files"]),
            "touches_extension": own and any(f["path"].startswith("apps/extension/") for f in proposal["files"]),
        }

    def _persist(self, proposal: dict[str, Any]) -> None:
        d = self.dir / proposal["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "proposal.json").write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        (d / "patch.diff").write_text(proposal["diff"], encoding="utf-8")

    def _load(self, patch_id: str) -> dict[str, Any]:
        self._check_id(patch_id)
        if patch_id in self.proposals:
            return self.proposals[patch_id]
        f = self.dir / patch_id / "proposal.json"
        if not f.exists():
            raise PatchError(f"unknown patch {patch_id}")
        proposal = json.loads(f.read_text(encoding="utf-8"))
        self.proposals[patch_id] = proposal
        return proposal

    # ------------------------------------------------------------ apply / revert
    def _delegate(self, proposal: dict[str, Any]) -> Optional["SelfEvolveEngine"]:
        """Cross-repo patches are applied inside their own target project."""
        root = Path(proposal.get("root") or self.root).resolve()
        if proposal.get("kind") != "cross_repo" and root == self.root:
            return None
        eng = SelfEvolveEngine(self.orchestrator, root, self.dir.parent, generic=True,
                               label=proposal.get("target_name", root.name))
        eng.proposals = self.proposals
        return eng

    def apply(self, patch_id: str) -> dict[str, Any]:
        if (frozen := __import__("freeze").blocked("change its own code")):  # kill switch (freeze.py)
            raise PatchError(frozen)
        proposal = self._load(patch_id)
        other = self._delegate(proposal)
        if other is not None:
            return other.apply(patch_id)
        if proposal["status"] != "proposed":
            raise PatchError(f"patch is {proposal['status']}; only proposed patches can be applied")
        # Phase 8 protected core (protected_core.py): this direct path never writes those files. They go through
        # self_program.py, which needs a phone-fingerprint or boardroom signature.
        if proposal.get("kind", "self") == "self":
            import protected_core
            hits = protected_core.check([
                {"path": f["path"], "action": f["action"], "after": f["new_content"],
                 "before": _read_raw(self.root / f["path"]) if (self.root / f["path"]).exists() else ""}
                for f in proposal["files"]])
            if hits:
                raise PatchError(protected_core.describe(hits))
        # Refuse if any target changed on disk since the patch was generated.
        for f in proposal["files"]:
            path = self._safe_path(f["path"])
            if f["action"] == "modify":
                current = _read_raw(path) if path.exists() else ""
                if _sha(current) != f["original_sha"]:
                    raise PatchError(f"{f['path']} changed since the patch was generated; regenerate it")
            elif path.exists():
                raise PatchError(f"{f['path']} now exists; regenerate the patch")
        backup = self.dir / patch_id / "backup"
        backup.mkdir(parents=True, exist_ok=True)
        written = []
        for f in proposal["files"]:
            path = self._safe_path(f["path"])
            if f["action"] == "modify":
                dest = backup / f["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_raw(path, f["new_content"])
            f["new_sha"] = _sha(f["new_content"])
            written.append(f["path"])
        proposal["status"] = "applied"
        proposal["applied_at"] = time.time()
        self._persist(proposal)
        if proposal.get("kind", "self") == "self" and any(f["path"].startswith("apps/daemon/") for f in proposal["files"]):
            # Lets scripts/run-daemon.mjs roll back automatically if the restarted daemon crashes.
            (self.dir / "last_applied.json").write_text(
                json.dumps({"id": patch_id, "applied_at": proposal["applied_at"]}), encoding="utf-8")
        log.info("Self-evolve patch %s applied: %s", patch_id, ", ".join(written))
        return {**self.public(proposal), "written": written}

    def revert(self, patch_id: str, force: bool = False) -> dict[str, Any]:
        proposal = self._load(patch_id)
        other = self._delegate(proposal)
        if other is not None:
            return other.revert(patch_id, force)
        if proposal["status"] != "applied":
            raise PatchError("only applied patches can be reverted")
        backup = self.dir / patch_id / "backup"
        if not force:
            # Never silently erase later patches or hand edits made on top of this one.
            changed = []
            for f in proposal["files"]:
                path = self._safe_path(f["path"])
                current = _read_raw(path) if path.exists() else None
                expected = f.get("new_sha") or _sha(f["new_content"])
                if current is None or _sha(current) != expected:
                    changed.append(f["path"])
            if changed:
                raise PatchError("these files changed after the patch was applied (later patch or manual edit), "
                                 "so reverting would erase that work: " + ", ".join(changed))
        for f in proposal["files"]:
            path = self._safe_path(f["path"])
            if f["action"] == "modify":
                shutil.copy2(backup / f["path"], path)
            elif path.exists():
                path.unlink()
        proposal["status"] = "reverted"
        proposal["reverted_at"] = time.time()
        self._persist(proposal)
        last = self.dir / "last_applied.json"
        try:
            if last.exists() and json.loads(last.read_text(encoding="utf-8")).get("id") == patch_id:
                last.unlink()
        except Exception:
            pass
        return self.public(proposal)

    def import_check(self, timeout: float = 120.0) -> tuple[bool, str]:
        """Import the daemon in a fresh interpreter, so an approved patch that compiles but crashes at import
        (missing name, bad import) is caught before the hot-reload restart."""
        daemon_dir = self.root / "apps" / "daemon"
        try:
            res = subprocess.run(
                [sys.executable, "-c", "import main"], cwd=str(daemon_dir), capture_output=True, text=True,
                timeout=timeout, env={**__import__("os").environ, "ORION_IMPORT_CHECK": "1"},
            )
        except subprocess.TimeoutExpired:
            return False, f"import check timed out after {int(timeout)}s"
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-6:]
            return False, "\n".join(tail)
        return True, ""

    def reject(self, patch_id: str) -> dict[str, Any]:
        proposal = self._load(patch_id)
        if proposal["status"] != "proposed":
            raise PatchError(f"patch is {proposal['status']}; only proposed patches can be rejected")
        proposal["status"] = "rejected"
        self._persist(proposal)
        return self.public(proposal)


def _loads(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise PatchError("model returned no JSON object")
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError as exc:
        raise PatchError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PatchError("model JSON must be an object")
    return data


def _revert_last_cli() -> int:
    """Used by scripts/run-daemon.mjs when the daemon crashes right after a self-evolve hot reload."""
    from config import settings

    import orion_layer  # Phase 8: a swap on probation (self_program.py) is put back first
    p = orion_layer.probation(settings.data_dir)
    if p:
        try:
            orion_layer.rollback(settings.data_dir, str(p["swap_id"]), "Orion crashed right after the change, so "
                                 "it was put back automatically.")
            print(f"Put back self-made change {p['swap_id']} after a failed restart.")
            return 0
        except KeyError:
            orion_layer.set_probation(settings.data_dir, None)
    engine = SelfEvolveEngine(None, settings.repo_root, settings.data_dir)
    last = engine.dir / "last_applied.json"
    if not last.exists():
        print("No recently applied daemon patch to revert.")
        return 1
    patch_id = json.loads(last.read_text(encoding="utf-8")).get("id", "")
    try:
        engine.revert(patch_id, force=True)
    except PatchError as exc:
        print(f"Could not revert {patch_id}: {exc}")
        return 1
    print(f"Reverted self-evolve patch {patch_id} after a failed restart.")
    return 0


if __name__ == "__main__" and "--revert-last" in sys.argv:
    logging.basicConfig(level=logging.INFO)
    sys.exit(_revert_last_cli())
