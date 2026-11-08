"""AI Blame (Orion Toolkit tool 4): who wrote this line, and why.

A flight recorder for agent work. Every few seconds the toolkit loop hands Orion the agent swarm (agent_watch);
Orion remembers which agent sessions were working in which git repo and when. When a new commit appears in a
watched repo, it is matched to the agent session that was active there at that moment (the files it touched break
ties). For an agent commit Orion keeps:

  * the agent tool and session id;
  * your request: the first thing you typed in that session, cut short and with anything that looks like a key
    taken out (set ORION_BLAME_PROMPTS=0 to keep no request text at all);
  * an estimated cost of the session up to that commit (token counts from the session log, priced with the
    compute meter's price table);
  * the Proof Gate verdict and tests for that commit or session, when there is one (filled in later if the check
    finishes after the commit).

Where it's kept: a git note on the commit under refs/notes/orion-blame (local only: Orion never pushes notes, see
the [no-push] rule; git doesn't push notes unless you ask it to) and a small SQLite index in Orion's data folder.
Commits with no agent active at the time are recorded as "you, by hand".

"Why did this line change?": git blame -> commit -> note -> a plain answer such as
"Codex, Tuesday, in commit 1a2b3c4 ("fix loot crash"). Your request was: "...". The session had cost about $0.40
by then. Tests passed (Proof Gate: 120 tests pass)."
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from collision_guard import git, repo_key, toplevel

log = logging.getLogger("orion.blame")

NOTES_REF = "orion-blame"                 # refs/notes/orion-blame
NOTE_HEADER = "Orion AI Blame"
SESSION_SLACK_BEFORE_S = 120.0            # a commit this long before a session's first log line still counts
SESSION_SLACK_AFTER_S = 600.0             # ... and this long after its last activity (agents commit, then go quiet)
FIRST_LOOK_BACK_S = 30 * 60               # when a repo is first seen, commits this old can still be matched
PROOF_LOOKUP_S = 2 * 86400                # keep trying to attach a Proof Gate result for this long
MAX_SESSION_BYTES = 60_000_000
REQUEST_CHARS = 200
SCAN_MAX_COMMITS = 200
ZERO_SHA = re.compile(r"^0{7,}$")
AGENT_TOOLS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "antigravity": "Antigravity",
               "cursor": "Cursor", "run": "Agent"}
PROVIDERS = {"Claude": "anthropic", "Codex": "openai", "Gemini": "gemini", "Antigravity": "gemini"}
NOTE_IDENTITY = ("-c", "user.name=Orion AI Blame", "-c", "user.email=orion@localhost")


def prompts_enabled() -> bool:
    return os.environ.get("ORION_BLAME_PROMPTS", "1").strip().lower() not in ("0", "false", "no", "off")


# =====================================================================================  redaction
def scrub(text: str, limit: int = REQUEST_CHARS) -> str:
    """One short line with anything that looks like a secret taken out."""
    text = str(text or "")
    try:   # token by token first, so the rest of the line stays readable
        from mcp_tools import redact
        text = redact(text)
    except Exception:
        pass
    try:
        from harvester import looks_secret
        text = "\n".join("[redacted]" if looks_secret(line) else line for line in text.splitlines())
    except Exception:
        pass
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# =====================================================================================  session logs
def _ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _is_wrapper(text: str) -> bool:
    """Tool-generated text in a user turn (command wrappers, environment blocks, instruction files)."""
    t = text.lstrip()
    return (not t or t.startswith("<") or t.startswith("# AGENTS.md") or t.startswith("Caveat:")
            or t.startswith("[Request interrupted"))


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict)
                 and b.get("type") in ("text", "input_text") and not _is_wrapper(str(b.get("text", "")))]
        return " ".join(p for p in parts if p)
    return ""


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        if path.stat().st_size > MAX_SESSION_BYTES:
            return
        with path.open("rb") as fh:
            for raw in fh:
                try:
                    row = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def session_facts(tool: str, path: Optional[Path], until: Optional[float] = None) -> dict[str, Any]:
    """What a session log says about the request and its cost, up to `until` (epoch seconds).

    Only the first real user message is read (for the request); everything else used is token counts."""
    facts: dict[str, Any] = {"request": "", "model": "", "tokens_in": 0, "tokens_out": 0, "cost": None}
    if path is None or not Path(path).is_file():
        return facts
    path = Path(path)
    want_prompt = prompts_enabled()
    if tool == "Codex":
        last_usage: dict[str, Any] = {}
        for row in _rows(path):
            at = _ts(row.get("timestamp"))
            if until is not None and at is not None and at > until:
                break
            item = row.get("payload", row)
            if not isinstance(item, dict):
                continue
            kind = row.get("type")
            if kind == "turn_context" and item.get("model"):
                facts["model"] = str(item["model"])
            elif kind == "session_meta" and item.get("model"):
                facts["model"] = facts["model"] or str(item["model"])
            if item.get("type") == "token_count" and isinstance(item.get("info"), dict):
                usage = item["info"].get("total_token_usage")
                if isinstance(usage, dict):
                    last_usage = usage
            if want_prompt and not facts["request"]:
                if item.get("type") == "message" and item.get("role") == "user":
                    text = _user_text(item.get("content"))
                elif item.get("type") == "user_message":
                    text = str(item.get("message") or "")
                else:
                    text = ""
                if text and not _is_wrapper(text):
                    facts["request"] = scrub(text)
        cached = int(last_usage.get("cached_input_tokens") or 0)
        facts["tokens_in"] = max(0, int(last_usage.get("input_tokens") or 0) - cached) + cached // 10
        facts["tokens_out"] = int(last_usage.get("output_tokens") or 0)
    else:   # Claude Code's jsonl (also the default shape for unknown tools)
        seen: set[str] = set()
        for row in _rows(path):
            at = _ts(row.get("timestamp"))
            if until is not None and at is not None and at > until:
                break
            msg = row.get("message")
            if not isinstance(msg, dict):
                continue
            if row.get("type") == "user" and want_prompt and not facts["request"] and not row.get("isMeta"):
                text = _user_text(msg.get("content"))
                if text and not _is_wrapper(text):
                    facts["request"] = scrub(text)
            elif row.get("type") == "assistant":
                mid = str(msg.get("id") or row.get("uuid") or "")
                if mid in seen:
                    continue
                seen.add(mid)
                if msg.get("model") and not str(msg["model"]).startswith("<"):
                    facts["model"] = str(msg["model"])
                usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                facts["tokens_in"] += (int(usage.get("input_tokens") or 0)
                                       + int(usage.get("cache_creation_input_tokens") or 0)
                                       + int(usage.get("cache_read_input_tokens") or 0) // 10)
                facts["tokens_out"] += int(usage.get("output_tokens") or 0)
    if facts["tokens_in"] or facts["tokens_out"]:
        try:
            from compute_meter import cost_of, load_prices, price_for
            price, _known = price_for(facts["model"] or tool.lower(), PROVIDERS.get(tool, "cloud"), load_prices())
            facts["cost"] = round(cost_of(facts["tokens_in"], facts["tokens_out"], price), 4)
        except Exception:
            facts["cost"] = None
    return facts


def find_session_file(agent_id: str, sources: Optional[list[Any]] = None) -> Optional[Path]:
    """The session log behind a swarm agent id like 'claude:<session>' or 'codex:<rollout-...>'."""
    tool, _, stem = agent_id.partition(":")
    if not stem or tool not in ("claude", "codex"):
        return None
    if sources is None:
        from agent_watch import default_sources
        sources = default_sources()
    want = "Claude" if tool == "claude" else "Codex"
    for src in sources:
        if src.agent != want or not Path(src.root).is_dir():
            continue
        try:
            for hit in Path(src.root).rglob(f"{stem}.jsonl"):
                return hit
        except OSError:
            continue
    return None


# =====================================================================================  the index
class BlameIndex:
    def __init__(self, data_dir: Path, sources: Optional[list[Any]] = None,
                 proofs: Optional[Callable[[], list[dict[str, Any]]]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sources = sources
        self.proofs = proofs
        self.clock = clock
        self.lock = threading.RLock()
        self._no_log: set[str] = set()
        self.db = sqlite3.connect(str(self.dir / "ai_blame.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS repos(key TEXT PRIMARY KEY, path TEXT NOT NULL, watched_since REAL NOT NULL,
                last_scan REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, tool TEXT, repo TEXT, cwd TEXT, started REAL,
                last REAL, path TEXT, files TEXT DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS commits(sha TEXT PRIMARY KEY, repo TEXT, ts REAL, by TEXT, session_id TEXT,
                tool TEXT, request TEXT, cost REAL, tokens INTEGER, model TEXT, proof TEXT, files TEXT, subject TEXT);
            CREATE INDEX IF NOT EXISTS sessions_repo ON sessions(repo, last);
            CREATE INDEX IF NOT EXISTS commits_repo ON commits(repo, ts);
        """)
        self.db.commit()

    # ------------------------------------------------------------ watching
    def watch(self, path: Any, since: Optional[float] = None) -> Optional[str]:
        top = toplevel(path)
        if top is None:
            return None
        key = repo_key(top)
        if not key:
            return None
        with self.lock:
            row = self.db.execute("SELECT key FROM repos WHERE key=?", (key,)).fetchone()
            if row is None:
                start = since if since is not None else self.clock() - FIRST_LOOK_BACK_S
                self.db.execute("INSERT INTO repos(key, path, watched_since) VALUES(?,?,?)", (key, str(top), start))
                self.db.commit()
        return key

    def repos(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM repos").fetchall()]

    def observe(self, agents: list[dict[str, Any]]) -> None:
        """Remember which agent sessions are working in which repo (called from the toolkit loop)."""
        now = self.clock()
        for a in agents or []:
            cwd = str(a.get("cwd") or "")
            aid = str(a.get("id") or "")
            if not cwd or not aid:
                continue
            key = self.watch(cwd, since=min(now - FIRST_LOOK_BACK_S, float(a.get("started") or now)))
            if not key:
                continue
            tool = str(a.get("tool") or AGENT_TOOLS.get(aid.partition(":")[0], "Agent"))
            files = set(a.get("files") or [])
            for ev in a.get("events") or []:   # "Editing app.py", "Reading app.py"
                m = re.match(r"^(?:Editing|Writing|Changing|Reading|Creating)\s+(\S+)$", str(ev.get("text", "")))
                if m:
                    files.add(m.group(1))
            with self.lock:
                old = self.db.execute("SELECT * FROM sessions WHERE id=?", (aid,)).fetchone()
                started = float(a.get("started") or now)
                last = float(a.get("last_activity") or now)
                if old is not None:
                    started = min(started, old["started"] or started)
                    last = max(last, old["last"] or 0)
                    files |= set(json.loads(old["files"] or "[]"))
                path = (old["path"] if old is not None else None) or str(a.get("session_path") or "")
                if not path and aid not in self._no_log:
                    hit = find_session_file(aid, self.sources)
                    path = str(hit) if hit else ""
                    if not hit:
                        self._no_log.add(aid)   # pings, Cursor, Gemini...: no session log to read, don't look again
                self.db.execute(
                    "INSERT OR REPLACE INTO sessions(id, tool, repo, cwd, started, last, path, files) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (aid, tool, key, cwd, started, last, path, json.dumps(sorted(files)[-200:])))
                self.db.commit()

    # ------------------------------------------------------------ commits
    def _new_commits(self, top: Path, since: float) -> list[dict[str, Any]]:
        code, out = git(top, "log", "--branches", "--no-merges", f"--since=@{int(since)}", f"-n{SCAN_MAX_COMMITS}",
                        "--name-only", "--format=%x1e%H%x1f%ct%x1f%an%x1f%s")
        if code != 0:
            return []
        found = []
        for block in out.split("\x1e"):
            head, _, rest = block.partition("\n")
            parts = head.split("\x1f")
            if len(parts) < 4:
                continue
            found.append({"sha": parts[0].strip(), "ts": float(parts[1] or 0), "author": parts[2],
                          "subject": parts[3], "files": [f for f in rest.splitlines() if f.strip()]})
        return found

    def _match(self, key: str, ts: float, files: list[str]) -> Optional[sqlite3.Row]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM sessions WHERE repo=? AND started<=? AND last>=?",
                (key, ts + SESSION_SLACK_BEFORE_S, ts - SESSION_SLACK_AFTER_S)).fetchall()
        if not rows:
            return None
        names = {Path(f).name for f in files} | set(files)

        def score(r: sqlite3.Row) -> tuple[int, float]:
            touched = set(json.loads(r["files"] or "[]"))
            inside = 1 if r["started"] - 1 <= ts <= r["last"] + 1 else 0
            return (len(touched & names) * 10 + inside, -abs((r["last"] or ts) - ts))
        return max(rows, key=score)

    def find_proof(self, sha: str, session_id: str, ts: float) -> Optional[dict[str, Any]]:
        if self.proofs is None:
            return None
        try:
            results = list(self.proofs() or [])
        except Exception:
            return None
        best = None
        for r in results:
            if r.get("sha") and sha.startswith(str(r["sha"])):
                best = r
                break
            done = float(r.get("finished_at") or r.get("started_at") or 0)
            if session_id and r.get("agent") == session_id and ts - 3600 <= done <= ts + 6 * 3600:
                best = best or r
        if best is None:
            return None
        tests = [c for c in best.get("checks") or [] if c.get("name") == "test"]
        return {"id": best.get("id"), "verdict": best.get("verdict"), "reason": scrub(best.get("reason") or "", 300),
                "passed": sum(int(c.get("passed") or 0) for c in tests),
                "failed": sum(int(c.get("failed") or 0) for c in tests),
                "failing_tests": [scrub(t, 120) for t in (best.get("failing_tests") or [])[:5]]}

    def scan(self, path: Any) -> list[dict[str, Any]]:
        """Record every new commit in this repo. Returns the new records."""
        key = self.watch(path)
        if not key:
            return []
        with self.lock:
            repo = self.db.execute("SELECT * FROM repos WHERE key=?", (key,)).fetchone()
        top = Path(repo["path"])
        since = max(repo["watched_since"], (repo["last_scan"] or 0) - 3600)
        now = self.clock()
        added = []
        for c in self._new_commits(top, since):
            with self.lock:
                if self.db.execute("SELECT 1 FROM commits WHERE sha=?", (c["sha"],)).fetchone():
                    continue
            if c["ts"] < repo["watched_since"]:
                continue
            added.append(self.record(top, key, c))
        with self.lock:
            self.db.execute("UPDATE repos SET last_scan=? WHERE key=?", (now, key))
            self.db.commit()
        self.refresh_proofs(top, key)
        return added

    def record(self, top: Path, key: str, c: dict[str, Any]) -> dict[str, Any]:
        s = self._match(key, c["ts"], c["files"])
        rec: dict[str, Any] = {"sha": c["sha"], "repo": key, "ts": c["ts"], "subject": scrub(c["subject"], 160),
                               "files": c["files"][:200], "by": "you", "session_id": "", "tool": "", "request": "",
                               "cost": None, "tokens": 0, "model": "", "proof": None}
        if s is not None:
            facts = session_facts(s["tool"], Path(s["path"]) if s["path"] else None, until=c["ts"] + 5)
            rec.update(by="agent", session_id=s["id"], tool=s["tool"], request=facts["request"], cost=facts["cost"],
                       tokens=facts["tokens_in"] + facts["tokens_out"], model=facts["model"],
                       proof=self.find_proof(c["sha"], s["id"], c["ts"]))
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO commits(sha, repo, ts, by, session_id, tool, request, cost, tokens, model, "
                "proof, files, subject) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec["sha"], key, rec["ts"], rec["by"], rec["session_id"], rec["tool"], rec["request"], rec["cost"],
                 rec["tokens"], rec["model"], json.dumps(rec["proof"]) if rec["proof"] else None,
                 json.dumps(rec["files"]), rec["subject"]))
            self.db.commit()
        if rec["by"] == "agent":
            self.write_note(top, rec)
        return rec

    def refresh_proofs(self, top: Path, key: str) -> int:
        """Attach Proof Gate results that finished after the commit was recorded."""
        n = 0
        with self.lock:
            rows = self.db.execute("SELECT * FROM commits WHERE repo=? AND by='agent' AND proof IS NULL AND ts>=?",
                                   (key, self.clock() - PROOF_LOOKUP_S)).fetchall()
        for r in rows:
            proof = self.find_proof(r["sha"], r["session_id"], r["ts"])
            if not proof:
                continue
            with self.lock:
                self.db.execute("UPDATE commits SET proof=? WHERE sha=?", (json.dumps(proof), r["sha"]))
                self.db.commit()
            rec = self.get(r["sha"])
            if rec:
                self.write_note(top, rec)
            n += 1
        return n

    def get(self, sha: str) -> Optional[dict[str, Any]]:
        with self.lock:
            r = self.db.execute("SELECT * FROM commits WHERE sha=?", (sha,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["files"] = json.loads(d.get("files") or "[]")
        d["proof"] = json.loads(d["proof"]) if d.get("proof") else None
        return d

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.lock:
            shas = [r["sha"] for r in self.db.execute("SELECT sha FROM commits ORDER BY ts DESC LIMIT ?", (limit,))]
        return [x for x in (self.get(s) for s in shas) if x]

    # ------------------------------------------------------------ git notes (local only, never pushed)
    @staticmethod
    def note_text(rec: dict[str, Any]) -> str:
        data = {k: rec.get(k) for k in ("by", "tool", "session_id", "request", "cost", "tokens", "model", "proof",
                                        "ts")}
        return f"{NOTE_HEADER}: {plain_answer(rec, short=True)}\n\n{json.dumps(data, ensure_ascii=False, indent=1)}\n"

    def write_note(self, top: Path, rec: dict[str, Any]) -> bool:
        code, out = git(top, *NOTE_IDENTITY, "notes", f"--ref={NOTES_REF}", "add", "-f", "-m",
                        self.note_text(rec), rec["sha"])
        if code != 0:
            log.info("AI Blame: could not write a note on %s: %s", rec["sha"][:10], out.strip()[:200])
        return code == 0


def read_note(top: Path, sha: str) -> Optional[dict[str, Any]]:
    code, out = git(top, "notes", f"--ref={NOTES_REF}", "show", sha)
    if code != 0 or NOTE_HEADER not in out:
        return None
    start = out.find("{")
    try:
        return json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        return None


# =====================================================================================  answers
def when_text(ts: float, now: Optional[float] = None) -> str:
    now = now or time.time()
    d = datetime.fromtimestamp(ts)
    days = (datetime.fromtimestamp(now).date() - d.date()).days
    if days == 0:
        return "today at " + d.strftime("%H:%M")
    if days == 1:
        return "yesterday at " + d.strftime("%H:%M")
    if 0 < days < 7:
        return d.strftime("%A")
    return d.strftime("%-d %b %Y") if os.name != "nt" else d.strftime("%d %b %Y")


def _money(x: Optional[float]) -> str:
    if x is None:
        return ""
    return f"${x:.2f}" if x >= 0.01 else "less than a cent"


def proof_text(proof: Optional[dict[str, Any]]) -> str:
    if not proof:
        return "Proof Gate didn't check it."
    if proof.get("verdict") == "green":
        n = proof.get("passed") or 0
        return f"Tests passed (Proof Gate{': ' + str(n) + ' tests pass' if n else ''})."
    return f"Proof Gate wasn't happy: {proof.get('reason') or 'see the result'}"


def plain_answer(rec: dict[str, Any], short: bool = False, now: Optional[float] = None) -> str:
    sha = str(rec.get("sha") or "")[:7]
    subject = rec.get("subject") or ""
    where = f"in commit {sha}" + (f" (“{subject}”)" if subject and not short else "") if sha else ""
    when = when_text(float(rec.get("ts") or 0), now) if rec.get("ts") else ""
    if rec.get("by") != "agent":
        return ", ".join(x for x in ("You, by hand", when, where) if x) + ". No agent was working in this repo then."
    parts = [", ".join(x for x in (str(rec.get("tool") or "An agent"), when, where) if x) + "."]
    if rec.get("request"):
        parts.append(f"Your request was: “{rec['request']}”.")
    if rec.get("cost") is not None:
        parts.append(f"The session had cost about {_money(rec['cost'])} by then.")
    parts.append(proof_text(rec.get("proof")))
    return " ".join(parts)


def blame_line(top: Path, rel: str, line: int) -> dict[str, Any]:
    code, out = git(top, "blame", "--porcelain", "-L", f"{line},{line}", "--", rel)
    if code != 0:
        raise ValueError(out.strip().splitlines()[-1] if out.strip() else "git blame failed")
    first = out.splitlines()[0].split()
    info: dict[str, Any] = {"sha": first[0]}
    for ln in out.splitlines()[1:]:
        if ln.startswith("\t"):
            info["text"] = ln[1:]
            break
        k, _, v = ln.partition(" ")
        info[k] = v
    return info


def why(index: BlameIndex, file: str, line: int, repo: str = "", agents: Optional[list[dict[str, Any]]] = None
        ) -> dict[str, Any]:
    """Answer "why did this line change?" for a file (absolute, or relative to `repo`) and a 1-based line."""
    if line < 1:
        raise ValueError("Line numbers start at 1.")
    path = Path(os.path.expanduser(file))
    if not path.is_absolute():
        if not repo:
            raise ValueError("Give the full path of the file, or the repo it's in.")
        path = Path(repo) / path
    top = toplevel(path.parent if path.parent.exists() else repo)
    if top is None:
        raise ValueError(f"{path.name} isn't inside a git repository.")
    try:
        rel = path.resolve().relative_to(top).as_posix()
    except ValueError:
        raise ValueError(f"{path.name} isn't inside {top.name}.")
    if not (top / rel).is_file():
        raise ValueError(f"There's no file {rel} in {top.name}.")
    b = blame_line(top, rel, line)
    sha = b["sha"]
    base = {"file": rel, "line": line, "repo": top.name, "sha": sha, "text": scrub(b.get("text", ""), 200)}
    if ZERO_SHA.match(sha):
        name = Path(rel).name
        busy = [a for a in agents or [] if name in (a.get("files") or []) and toplevel(a.get("cwd")) == top]
        if busy:
            who = ", ".join(sorted({str(a.get("tool") or "An agent") for a in busy}))
            return {**base, "by": "agent", "committed": False,
                    "answer": f"Not committed yet. {who} is changing {name} right now."}
        return {**base, "by": "unknown", "committed": False,
                "answer": "Not committed yet: it was changed in your working copy and hasn't been committed."}
    index.scan(top)   # cheap: picks up commits made since the last loop
    rec = index.get(sha)
    note = read_note(top, sha)
    if note and (rec is None or rec.get("by") != "agent"):
        rec = {**(rec or {}), **note, "sha": sha}
    if rec is None:
        ts = float(b.get("committer-time") or b.get("author-time") or 0)
        watched = next((r for r in index.repos() if Path(r["path"]) == top), None)
        rec = {"sha": sha, "ts": ts, "subject": scrub(b.get("summary", ""), 160)}
        if watched is None or ts < watched["watched_since"]:
            when = when_text(ts) if ts else "a while ago"
            return {**base, "by": "unknown", "committed": True, "ts": ts,
                    "answer": f"Commit {sha[:7]} (“{rec['subject']}”), {when}, by {b.get('author', 'someone')}. "
                              "Orion wasn't watching this repo yet, so it doesn't know if an agent wrote it."}
        rec["by"] = "you"
    rec.setdefault("subject", scrub(b.get("summary", ""), 160))
    return {**base, "committed": True, "by": rec.get("by"), "tool": rec.get("tool", ""),
            "session_id": rec.get("session_id", ""), "request": rec.get("request", ""), "cost": rec.get("cost"),
            "proof": rec.get("proof"), "ts": rec.get("ts"), "answer": plain_answer(rec)}


def find_file(roots: list[Path], spoken: str) -> Optional[tuple[Path, str]]:
    """A tracked file whose name matches what was said ("app py" -> app.py), searched in these repos."""
    want = re.sub(r"[^a-z0-9]+", " ", spoken.lower()).strip()
    if not want:
        return None
    for root in roots:
        code, out = git(root, "ls-files")
        if code != 0:
            continue
        hits = []
        for rel in out.splitlines():
            name = re.sub(r"[^a-z0-9]+", " ", Path(rel).name.lower()).strip()
            full = re.sub(r"[^a-z0-9]+", " ", rel.lower()).strip()
            if want in (name, full) or full.endswith(" " + want):
                hits.append(rel)
        if hits:
            return root, min(hits, key=len)
    return None
