"""Context Pack (Orion Toolkit tool 5): the smallest brief that does the job.

Given a task ("fix the vault bug") and a repo, Orion builds a markdown brief that fits a token budget:

  1. the project's rules (.orion/rules.md);
  2. the relevant files, from a lightweight repo map: every tracked source file with its top-level symbols
     (functions, classes, types), found with small per-language patterns (no big parser install), ranked by how
     well their path and symbols match the task, plus what Orion has learned about which files agents really
     opened for similar tasks; the best few get short excerpts around the matching lines;
  3. recent errors: crash-healer records and failed runs in that repo;
  4. decisions: matching sections of the plan docs in docs/ and of your knowledge vault notes;
  5. the newest matching Baton hand-off notes (.orion/batons/).

Learning: after a pack is built, Orion watches the agent sessions in that repo (agent_watch's "Reading x" /
"Editing x"). Files they open are remembered against the task's words, so the next pack for a similar task ranks
them higher and needs fewer guesses.

Tokens are estimated as characters / 4. The compute meter is told how many tokens the pack saved compared with
pasting every tracked text file, so the HUD can show the savings.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from collision_guard import git, ignored, repo_key, toplevel

log = logging.getLogger("orion.context_pack")

DEFAULT_BUDGET = 6000
MIN_BUDGET, MAX_BUDGET = 800, 60_000
MAX_FILES = 6000
MAX_FILE_BYTES = 400_000
LEARN_WINDOW_S = 3 * 3600
CHARS_PER_TOKEN = 4

STOP = set("""a an the and or of to in on for with without from by at is are was were be been it its this that these
those i me my we our you your he she they them please can could would should will just about into onto fix bug bugs
issue problem make do does did get got add change update new old some any all more less brief claude codex gemini
cursor agent orion on so then than when why how what which where who whose there here not no yes""".split())

SOURCE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rs", ".go", ".kt", ".kts", ".java", ".c",
              ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".lua", ".sh", ".bash", ".zig", ".sql",
              ".toml", ".json", ".yaml", ".yml", ".md", ".css", ".scss", ".html", ".vue", ".svelte", ".gd"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock", "uv.lock"}

SYMBOL_RX: dict[str, re.Pattern[str]] = {
    "py": re.compile(r"^[ \t]*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M),
    "js": re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+([A-Za-z_$][\w$]*)|class\s+"
                     r"([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function|"
                     r"[A-Za-z_$][\w$]*\s*=>)|(?:interface|type|enum)\s+([A-Za-z_$][\w$]*))", re.M),
    "rs": re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|mod|type|const|static)\s+"
                     r"([A-Za-z_]\w*)|^\s*impl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?([A-Za-z_]\w*)", re.M),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)|^type\s+([A-Za-z_]\w*)", re.M),
    "kt": re.compile(r"^\s*(?:(?:public|private|internal|protected|data|sealed|open|abstract|suspend|inline|"
                     r"override)\s+)*(?:fun|class|object|interface)\s+(?:<[^>]*>\s*)?([A-Za-z_]\w*)", re.M),
    "c": re.compile(r"^(?:[A-Za-z_][\w\s\*]*?\s)?\**([A-Za-z_]\w*)\s*\([^;]*$|^(?:struct|enum|class|typedef)\s+"
                    r"([A-Za-z_]\w*)", re.M),
    "sh": re.compile(r"^(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{", re.M),
    "md": re.compile(r"^#{1,3}\s+(.+)$", re.M),
    "gd": re.compile(r"^(?:func|class_name|class)\s+([A-Za-z_]\w*)", re.M),
}
LANG = {".py": "py", ".js": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".jsx": "js", ".vue": "js",
        ".svelte": "js", ".rs": "rs", ".go": "go", ".kt": "kt", ".kts": "kt", ".java": "kt", ".swift": "kt",
        ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".cs": "c", ".sh": "sh", ".bash": "sh",
        ".md": "md", ".gd": "gd"}


def tokens_of(text: str) -> int:
    return max(0, math.ceil(len(text) / CHARS_PER_TOKEN))


def words(text: str) -> list[str]:
    """Search words: lower case, camelCase and snake_case split, stop words dropped, simple plurals folded."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    out: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", text.lower()):
        if w in STOP or len(w) < 3:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in out:
            out.append(w)
    return out


def symbols_in(path: Path, text: Optional[str] = None) -> list[str]:
    rx = SYMBOL_RX.get(LANG.get(path.suffix.lower(), ""))
    if rx is None:
        return []
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    found: list[str] = []
    for m in rx.finditer(text):
        name = next((g for g in m.groups() if g), "").strip()
        if name and name not in found and name not in ("if", "for", "while", "switch", "return"):
            found.append(name[:60])
        if len(found) >= 40:
            break
    return found


def scrub(text: str) -> str:
    try:   # token by token first, then whole lines that still look secret
        from mcp_tools import redact
        text = redact(text)
    except Exception:
        pass
    try:
        from crash_healer import redact as line_redact
        text = line_redact(text)
    except Exception:
        pass
    return text


def _cut(text: str, budget_tokens: int) -> str:
    limit = max(0, budget_tokens) * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 20)].rstrip() + "\n... (cut to fit)\n"


def sections(md: str) -> list[tuple[str, str]]:
    """Markdown split at headings: [(heading, body)]."""
    parts: list[tuple[str, str]] = []
    head, buf = "", []
    for line in md.splitlines():
        if re.match(r"^#{1,4}\s", line):
            if buf or head:
                parts.append((head, "\n".join(buf).strip()))
            head, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    if buf or head:
        parts.append((head, "\n".join(buf).strip()))
    return parts


def _hits(text: str, keys: list[str]) -> int:
    low = text.lower()
    return sum(1 for k in keys if k in low)


# =====================================================================================  repo map
class RepoMap:
    """Tracked files with their symbols, cached by modification time in SQLite."""

    def __init__(self, db: sqlite3.Connection, lock: threading.RLock) -> None:
        self.db, self.lock = db, lock

    def files(self, top: Path) -> list[dict[str, Any]]:
        code, out = git(top, "ls-files", "-z")
        if code != 0:
            return []
        rels = [r for r in out.split("\0") if r][:MAX_FILES]
        key = str(top)
        with self.lock:
            cached = {r["path"]: r for r in self.db.execute(
                "SELECT path, mtime, size, symbols FROM repomap WHERE repo=?", (key,)).fetchall()}
        result, fresh = [], []
        for rel in rels:
            p = top / rel
            if ignored(rel) or p.name in SKIP_NAMES or p.suffix.lower() not in SOURCE_EXT:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            c = cached.get(rel)
            if c is not None and c["mtime"] == st.st_mtime and c["size"] == st.st_size:
                syms = json.loads(c["symbols"] or "[]")
            else:
                syms = symbols_in(p)
                fresh.append((key, rel, st.st_mtime, st.st_size, json.dumps(syms)))
            result.append({"path": rel, "size": st.st_size, "symbols": syms})
        if fresh:
            with self.lock:
                self.db.executemany("INSERT OR REPLACE INTO repomap(repo, path, mtime, size, symbols) VALUES(?,?,?,?,?)",
                                    fresh)
                self.db.commit()
        return result


# =====================================================================================  packs
class ContextPacker:
    def __init__(self, data_dir: Path, healer: Any = None, runner: Any = None, meter: Any = None,
                 vault_dir: Optional[Path] = None, clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.healer, self.runner, self.meter = healer, runner, meter
        self.vault_dir = vault_dir if vault_dir is not None else self.dir / "vault"
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.dir / "context_pack.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS repomap(repo TEXT, path TEXT, mtime REAL, size INTEGER, symbols TEXT,
                PRIMARY KEY(repo, path));
            CREATE TABLE IF NOT EXISTS packs(id TEXT PRIMARY KEY, repo TEXT, ts REAL, task TEXT, words TEXT,
                files TEXT, tokens INTEGER, whole INTEGER);
            CREATE TABLE IF NOT EXISTS opened(repo TEXT, name TEXT, word TEXT, n INTEGER, PRIMARY KEY(repo, name, word));
            CREATE TABLE IF NOT EXISTS learned(session TEXT, name TEXT, PRIMARY KEY(session, name));
        """)
        self.db.commit()
        self.map = RepoMap(self.db, self.lock)

    # ------------------------------------------------------------ learning from agents
    def observe(self, agents: list[dict[str, Any]]) -> int:
        """Files agents open after a pack was built teach Orion what that kind of task needs."""
        now = self.clock()
        learned = 0
        for a in agents or []:
            top = toplevel(a.get("cwd")) if a.get("cwd") else None
            if top is None:
                continue
            rk = repo_key(top) or str(top)
            names = set(a.get("files") or [])
            for ev in a.get("events") or []:
                m = re.match(r"^(?:Reading|Editing|Writing|Changing|Creating)\s+(\S+)$", str(ev.get("text", "")))
                if m:
                    names.add(m.group(1))
            if not names:
                continue
            with self.lock:
                pack = self.db.execute("SELECT * FROM packs WHERE repo=? AND ts>=? ORDER BY ts DESC LIMIT 1",
                                       (rk, now - LEARN_WINDOW_S)).fetchone()
                if pack is None:
                    continue
                for name in names:
                    if self.db.execute("SELECT 1 FROM learned WHERE session=? AND name=?",
                                       (f"{a.get('id')}|{pack['id']}", name)).fetchone():
                        continue
                    self.db.execute("INSERT INTO learned(session, name) VALUES(?,?)", (f"{a.get('id')}|{pack['id']}", name))
                    for w in json.loads(pack["words"] or "[]") + ["*"]:
                        self.db.execute("INSERT INTO opened(repo, name, word, n) VALUES(?,?,?,1) "
                                        "ON CONFLICT(repo, name, word) DO UPDATE SET n=n+1", (rk, name, w))
                    learned += 1
                self.db.commit()
        return learned

    def _learned(self, rk: str, keys: list[str]) -> dict[str, float]:
        with self.lock:
            rows = self.db.execute("SELECT name, word, n FROM opened WHERE repo=?", (rk,)).fetchall()
        boost: dict[str, float] = {}
        for r in rows:
            if r["word"] == "*":
                boost[r["name"]] = boost.get(r["name"], 0.0) + math.log1p(r["n"]) * 0.5
            elif r["word"] in keys:
                boost[r["name"]] = boost.get(r["name"], 0.0) + 3.0 * math.log1p(r["n"])
        return boost

    # ------------------------------------------------------------ ranking
    @staticmethod
    def score_file(f: dict[str, Any], keys: list[str]) -> float:
        path_words = words(f["path"].replace("/", " ").replace(".", " "))
        sym_words = set(words(" ".join(f["symbols"])))
        s = 0.0
        for k in keys:
            if k in path_words:
                s += 4.0
            elif any(k in w for w in path_words):
                s += 2.0
            if k in sym_words:
                s += 2.0
            elif any(k in w for w in sym_words):
                s += 1.0
        if s and "test" in f["path"].lower():
            s *= 0.8
        return s

    def rank(self, top: Path, keys: list[str], files: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
        boost = self._learned(repo_key(top) or str(top), keys)
        scored = []
        for f in files:
            if f["path"].endswith(".md"):   # docs come in through "Decisions from the plans"
                continue
            s = self.score_file(f, keys) + boost.get(Path(f["path"]).name, 0.0)
            if s > 0:
                scored.append((s, f))
        scored.sort(key=lambda t: (-t[0], len(t[1]["path"])))
        return scored

    @staticmethod
    def excerpt(top: Path, rel: str, keys: list[str], max_lines: int = 40) -> str:
        try:
            lines = (top / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        marks = [i for i, ln in enumerate(lines) if any(k in ln.lower() for k in keys)]
        if not marks:
            return "\n".join(lines[:min(15, max_lines)])
        keep: list[int] = []
        for i in marks:
            for j in range(max(0, i - 3), min(len(lines), i + 4)):
                if j not in keep:
                    keep.append(j)
            if len(keep) >= max_lines:
                break
        keep.sort()
        out, prev = [], -2
        for j in keep[:max_lines]:
            if j != prev + 1 and (prev >= 0 or j > 0):
                out.append(f"... (line {j + 1})")
            out.append(lines[j])
            prev = j
        return "\n".join(out)

    # ------------------------------------------------------------ other sources
    def errors(self, top: Path, keys: list[str]) -> list[str]:
        out: list[str] = []
        if self.healer is not None:
            try:
                crashes = self.healer.snapshot(30).get("crashes", [])
            except Exception:
                crashes = []
            for c in crashes:
                proj = str(c.get("project") or "")
                if proj and Path(proj).resolve() != top and top not in Path(proj).resolve().parents:
                    continue
                frame = (c.get("frames") or [{}])[-1] if c.get("frames") else {}
                where = f" at {frame.get('file')}:{frame.get('line')}" if frame.get("file") else ""
                out.append(f"- {c.get('error_type', 'Error')}: {c.get('message', '')[:200]}{where} "
                           f"(seen {c.get('count', 1)}x, {c.get('state', '')})")
        if self.runner is not None:
            try:
                runs = self.runner.list()
            except Exception:
                runs = []
            for r in runs:
                cwd = toplevel(r.get("cwd")) if r.get("cwd") else None
                if cwd != top or r.get("running") or r.get("exit_code") in (0, None):
                    continue
                tail = [ln for ln in (r.get("tail") or []) if ln.strip()][-6:]
                out.append(f"- `{r.get('command', '')[:120]}` failed (exit {r.get('exit_code')})"
                           + ("\n  " + "\n  ".join(t[:200] for t in tail) if tail else ""))
        return out[:8]

    def decisions(self, top: Path, keys: list[str], limit: int = 4) -> list[tuple[str, str, str]]:
        found: list[tuple[int, str, str, str]] = []
        docs = sorted((top / "docs").glob("*.md")) if (top / "docs").is_dir() else []
        vault = sorted(p for p in self.vault_dir.rglob("*") if p.suffix.lower() in (".md", ".txt")
                       ) if self.vault_dir.is_dir() else []
        for p in (docs + vault)[:200]:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:400_000]
            except OSError:
                continue
            for head, body in sections(text):
                n = _hits(head, keys) * 3 + _hits(body, keys)
                if n >= 2:
                    label = p.relative_to(top).as_posix() if top in p.parents else f"vault/{p.name}"
                    found.append((n, label, head, body))
        found.sort(key=lambda t: -t[0])
        return [(label, head, body) for _n, label, head, body in found[:limit]]

    @staticmethod
    def batons(top: Path, keys: list[str], limit: int = 2) -> list[tuple[str, str]]:
        d = top / ".orion" / "batons"
        if not d.is_dir():
            return []
        notes = []
        for p in sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            notes.append((_hits(text, keys), p.name, text))
        notes.sort(key=lambda t: -t[0])
        return [(name, text) for n, name, text in notes[:limit] if n > 0]

    # ------------------------------------------------------------ the pack
    def build(self, task: str, repo: Any, budget: int = DEFAULT_BUDGET, record: bool = True) -> dict[str, Any]:
        task = " ".join(str(task or "").split())[:1000]
        if not task:
            raise ValueError("Say what the task is, for example “fix the vault bug”.")
        top = toplevel(repo)
        if top is None:
            raise ValueError(f"{repo or 'That folder'} isn't inside a git repository.")
        budget = max(MIN_BUDGET, min(MAX_BUDGET, int(budget or DEFAULT_BUDGET)))
        keys = words(task)
        files = self.map.files(top)
        whole = sum(f["size"] for f in files) // CHARS_PER_TOKEN
        ranked = self.rank(top, keys, files)

        parts: list[str] = [f"# Brief: {task}", f"Repo: {top.name}. Built by Orion's Context Pack; it's a starting "
                            "point, open other files when you need them.", ""]
        left = budget - tokens_of("\n".join(parts)) - 60

        def add(block: str, share: float) -> None:
            nonlocal left
            text = _cut(block, int(min(left, budget * share)))
            if text.strip():
                parts.append(text)
                left -= tokens_of(text)

        rules = top / ".orion" / "rules.md"
        if rules.is_file():
            try:
                add("## Project rules\n" + rules.read_text(encoding="utf-8", errors="replace").strip() + "\n", 0.22)
            except OSError:
                pass
        chosen = [f for _s, f in ranked[:12]]
        if chosen:
            lines = ["## Files that look relevant"]
            for f in chosen:
                syms = ", ".join(f["symbols"][:10])
                lines.append(f"- `{f['path']}`" + (f": {syms}" if syms else ""))
            add("\n".join(lines) + "\n", 0.15)
            for f in chosen[:3]:
                if left < 200:
                    break
                ex = self.excerpt(top, f["path"], keys)
                if ex:
                    lang = LANG.get(Path(f["path"]).suffix.lower(), "")
                    add(f"### {f['path']}\n```{lang}\n{scrub(ex)}\n```\n", 0.15)
        else:
            parts.append("## Files\nNo file names or symbols matched the task words; start from the repo layout.\n")
        errs = self.errors(top, keys)
        if errs:
            add("## Recent errors\n" + "\n".join(errs) + "\n", 0.12)
        decs = self.decisions(top, keys)
        if decs:
            block = ["## Decisions from the plans"]
            for label, head, body in decs:
                block.append(f"### {head or label} ({label})\n{body[:1500]}")
            add("\n".join(block) + "\n", 0.18)
        for name, text in self.batons(top, keys):
            add(f"## Hand-off note {name}\n{text[:2500]}\n", 0.1)

        md = scrub("\n".join(parts).rstrip() + "\n")
        used = tokens_of(md)
        saved = max(0, whole - used)
        md += (f"\n_About {used:,} tokens. Pasting every tracked file would be about {whole:,}, so this saves about "
               f"{saved:,}._\n")
        pid = f"pack-{int(self.clock() * 1000):x}"
        if record:
            with self.lock:
                self.db.execute("INSERT OR REPLACE INTO packs(id, repo, ts, task, words, files, tokens, whole) "
                                "VALUES(?,?,?,?,?,?,?,?)",
                                (pid, repo_key(top) or str(top), self.clock(), task[:300], json.dumps(keys[:30]),
                                 json.dumps([f["path"] for f in chosen]), used, whole))
                self.db.commit()
            if self.meter is not None and hasattr(self.meter, "record_saving"):
                try:
                    self.meter.record_saving("context_pack", saved)
                except Exception:
                    pass
        return {"id": pid, "repo": str(top), "task": task, "markdown": md, "tokens": tokens_of(md),
                "whole_repo_tokens": whole, "saved_tokens": saved, "files": [f["path"] for f in chosen],
                "words": keys, "budget": budget}

    def stats(self) -> dict[str, Any]:
        with self.lock:
            r = self.db.execute("SELECT COUNT(*) n, COALESCE(SUM(whole - tokens), 0) saved FROM packs").fetchone()
        return {"packs": r["n"], "saved_tokens": max(0, int(r["saved"] or 0))}


def copy_to_clipboard(text: str) -> bool:
    """wl-copy when it's installed (Sway); xclip as a fallback. False when neither is there."""
    import shutil
    import subprocess
    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(argv[0]):
            try:
                subprocess.run(argv, input=text, text=True, timeout=5, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False
