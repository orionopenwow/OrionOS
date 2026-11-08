"""Orion's tools for coding agents (MCP bus v1): the safety rules and the tool implementations.

Transport-free on purpose: mcp_server.py speaks the Model Context Protocol over stdio and calls in here;
mcp_routes.py (inside the daemon) reuses the same checks before it runs or proposes anything.

Safety rules, in one place:
  * Every tool declares a risk: "read" (looks only), "run" (runs a command inside an allowed project folder)
    or "approval" (commits, database writes, unknown commands — these are only *proposed*; nothing happens
    until you sign the decision on the boardroom or your phone).
  * Projects must live under an allowed root: settings.repo_root plus the "roots" list in
    ~/.config/orion/mcp.json. Paths are resolved (symlinks, "..") before the check.
  * Output is capped and passed through redact() so tokens, passwords and keys never reach the agent.
  * MySQL is read-only unless you approve a write, and only connections to hosts you listed as staging work.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from harvester import SECRET_RE, _TOKEN_RE, _high_entropy, looks_secret
from runner import is_destructive

log = logging.getLogger("orion.mcp")

DEFAULT_MAX_OUTPUT = 20_000
DEFAULT_CONFIG_PATH = Path(os.path.expanduser("~/.config/orion/mcp.json"))


class ToolError(Exception):
    """A problem the agent should read (bad path, refused SQL...). Becomes an isError tool result."""


# =====================================================================================  config
@dataclass
class McpConfig:
    roots: list[Path] = field(default_factory=list)
    commands: dict[str, list[str]] = field(default_factory=dict)   # project root -> extra allowed commands
    mysql: dict[str, Any] = field(default_factory=dict)
    blender: str = ""
    max_output: int = DEFAULT_MAX_OUTPUT

    @classmethod
    def load(cls, path: Optional[Path] = None, repo_root: Optional[Path] = None) -> "McpConfig":
        if path is None:
            env = os.getenv("ORION_MCP_CONFIG", "").strip()
            path = Path(os.path.expanduser(env)) if env else DEFAULT_CONFIG_PATH
        data: dict[str, Any] = {}
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s (%s); using defaults", path, exc)
        if repo_root is None:
            from config import settings
            repo_root = settings.repo_root
        roots = [Path(repo_root)]
        for r in data.get("roots", []) or []:
            if isinstance(r, str) and r.strip():
                roots.append(Path(os.path.expanduser(r.strip())))
        commands = {}
        for root, cmds in (data.get("commands") or {}).items():
            if isinstance(cmds, list):
                commands[str(_safe_resolve(Path(os.path.expanduser(root))))] = [str(c) for c in cmds]
        try:
            max_output = int(data.get("max_output_chars", DEFAULT_MAX_OUTPUT))
        except (TypeError, ValueError):
            max_output = DEFAULT_MAX_OUTPUT
        return cls(roots=roots, commands=commands, mysql=dict(data.get("mysql") or {}),
                   blender=str(data.get("blender") or ""), max_output=max(1000, min(max_output, 500_000)))

    def resolved_roots(self) -> list[Path]:
        out = []
        for r in self.roots:
            rr = _safe_resolve(r)
            if rr is not None and rr.is_dir():
                out.append(rr)
        return out


def _safe_resolve(p: Path) -> Optional[Path]:
    try:
        return p.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


# =====================================================================================  paths
def resolve_project(path: Optional[str], cfg: McpConfig, default: Optional[str] = None) -> Path:
    """An existing folder under one of the allowed roots, with symlinks and '..' resolved first."""
    raw = (path or default or "").strip()
    if not raw:
        raise ToolError("Say which project folder to use (the 'project' argument).")
    if "\x00" in raw:
        raise ToolError("That path isn't valid.")
    p = Path(os.path.expanduser(raw))
    roots = cfg.resolved_roots()
    if not p.is_absolute():
        if not roots:
            raise ToolError("No project folders are allowed yet. Add them to ~/.config/orion/mcp.json.")
        p = roots[0] / p
    real = _safe_resolve(p)
    if real is None or not real.is_dir():
        raise ToolError(f"Folder not found: {raw}")
    for root in roots:
        if real == root or real.is_relative_to(root):
            return real
    raise ToolError(f"{raw} isn't inside a folder Orion lets agents use. "
                    "Add it to \"roots\" in ~/.config/orion/mcp.json if it should be.")


def resolve_inside(project: Path, rel: str, must_exist: bool = True) -> Path:
    """A file path inside `project` (no escaping through '..' or symlinks)."""
    if not rel or "\x00" in rel:
        raise ToolError("That path isn't valid.")
    p = Path(os.path.expanduser(rel))
    if not p.is_absolute():
        p = project / p
    try:
        real = p.resolve(strict=must_exist)
    except (OSError, RuntimeError):
        raise ToolError(f"File not found: {rel}")
    if not (real == project or real.is_relative_to(project)):
        raise ToolError(f"{rel} is outside the project folder.")
    return real


# =====================================================================================  output hygiene
_TOKEN_PATTERNS = re.compile(
    r"(sk-[\w-]{12,}|sk_(?:live|test)_\w{8,}|gh[pousr]_\w{8,}|github_pat_\w+|glpat-[\w-]+|xox[abprs]-[\w-]+|"
    r"AKIA[0-9A-Z]{12,}|AIza[\w-]{20,}|hf_\w{20,}|npm_\w{20,})")
_URL_CREDS = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@", re.I)
_ASSIGN = re.compile(
    r"(?i)([\w.-]*(?:pass(?:word)?|passwd|pwd|secret|token|api[_-]?key|apikey|private[_-]?key|credential|"
    r"auth)[\w.-]*[\"']?\s*(?:[:=]|=>)\s*)([\"']?)([^\s\"',;]+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_DASH_P = re.compile(r"(?<![\w-])-p(?!\s*\d+(?::\d+)*(?:/\w+)?(?:\s|$))(\s*)\S{6,}")
REDACTED = "[redacted]"


def _wordy(tok: str) -> bool:
    """Names like worktree-agent-af560b61 or build_release_x86_64 look random but are made of words."""
    words = [w for w in re.split(r"[-_./]+", tok) if len(w) >= 3 and w.isalpha()]
    return len(words) >= 2


def _random_token(tok: str) -> bool:
    return _high_entropy(tok) and not _wordy(tok)


def redact(text: str) -> str:
    """Remove anything that looks like a secret. Uses harvester.looks_secret as the final check."""
    if not text:
        return text
    out = []
    for line in text.splitlines(keepends=True):
        if not looks_secret(line):
            out.append(line)
            continue
        line = _TOKEN_PATTERNS.sub(REDACTED, line)
        line = _URL_CREDS.sub(lambda m: m.group(1) + REDACTED + "@", line)
        line = _BEARER.sub(lambda m: m.group(1) + " " + REDACTED, line)
        line = _ASSIGN.sub(lambda m: m.group(0) if m.group(3).lower() in ("bearer", "basic") or REDACTED in m.group(3)
                           else m.group(1) + m.group(2) + REDACTED, line)
        line = _DASH_P.sub(lambda m: "-p" + m.group(1) + REDACTED, line)
        line = _TOKEN_RE.sub(lambda m: REDACTED if _random_token(m.group(0)) else m.group(0), line)
        # Safety net: any remaining token that still looks like a secret on its own (not just a keyword).
        parts = re.split(r"(\s+)", line)
        for i, tok in enumerate(parts):
            if not tok or tok.isspace() or REDACTED in tok:
                continue
            if re.fullmatch(r"[\"'(]?[A-Za-z_.\-]+[:=]?[\"')]?|[\W_]+", tok):
                continue   # a bare word such as "password:" is not itself a secret
            if SECRET_RE.search(tok) or _random_token(tok):
                parts[i] = REDACTED
        out.append("".join(parts))
    return "".join(out)


def cap(text: str, limit: int) -> str:
    """Keep the start and (mostly) the end: errors are usually at the bottom."""
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n... [{cut} characters cut] ...\n{text[-tail:]}"


def clean(text: str, limit: int) -> str:
    return cap(redact(text), limit)


# =====================================================================================  SQL guard
READ_START = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}
WRITE_WORDS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT", "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "LOAD", "CALL", "DO", "HANDLER", "LOCK", "UNLOCK", "SET", "IMPORT", "INSTALL",
    "UNINSTALL", "OPTIMIZE", "REPAIR", "FLUSH", "RESET", "KILL", "SHUTDOWN", "PREPARE", "EXECUTE",
    "DEALLOCATE", "BEGIN", "COMMIT", "ROLLBACK", "START", "SAVEPOINT", "RELEASE", "XA", "INTO", "OUTFILE",
    "DUMPFILE", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "SLEEP", "BENCHMARK", "CLONE",
    "RESTART", "CACHE", "PURGE", "CHANGE", "STOP", "BINLOG",
}


def _strip_sql(sql: str) -> str:
    """Blank out string literals, quoted identifiers and comments; raise on anything ambiguous."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise ToolError("The query has an unfinished string.")
            out.append(" '' ")
            i = j + 1
        elif c == "`":
            j = sql.find("`", i + 1)
            while j != -1 and j + 1 < n and sql[j + 1] == "`":
                j = sql.find("`", j + 2)
            if j == -1:
                raise ToolError("The query has an unfinished `quoted` name.")
            out.append(" ident ")
            i = j + 1
        elif c == "/" and nxt == "*":
            if sql.startswith("/*!", i) or sql.startswith("/*M!", i):
                raise ToolError("MySQL executable comments (/*! ... */) aren't allowed.")
            j = sql.find("*/", i + 2)
            if j == -1:
                raise ToolError("The query has an unfinished comment.")
            out.append(" ")
            i = j + 2
        elif c == "#" or (c == "-" and nxt == "-" and (i + 2 >= n or sql[i + 2] in " \t\r\n")):
            j = sql.find("\n", i)
            out.append(" ")
            i = n if j == -1 else j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def sql_read_only(sql: str) -> tuple[bool, str]:
    """(True, '') for one SELECT/SHOW/DESCRIBE/EXPLAIN statement that can't change anything; else (False, why).

    Raises ToolError for queries that can't be parsed safely at all (they are refused, never proposed).
    """
    if not sql or not sql.strip():
        raise ToolError("The query is empty.")
    stripped = _strip_sql(sql)
    statements = [s for s in stripped.split(";") if s.strip()]
    if len(statements) != 1:
        raise ToolError("Send one statement at a time.")
    words = [w.upper() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", statements[0])]
    if not words:
        raise ToolError("That isn't a SQL statement.")
    if words[0] not in READ_START:
        return False, f"{words[0]} statements change data"
    bad = sorted({w for w in words if w in WRITE_WORDS})
    if bad:
        return False, "it uses " + ", ".join(bad)
    if "@" in statements[0] and re.search(r"@\w+\s*:=", statements[0]):
        return False, "it assigns variables"
    return True, ""


# =====================================================================================  project commands
SHELL_OPERATORS = re.compile(r"[;&|`$<>(){}\\\n\r]")
CARGO_SUBCOMMANDS = {"build", "check", "test", "run", "clippy", "fmt", "doc", "bench", "tree", "metadata"}
CARGO_BAD_FLAGS = ("--target-dir", "--manifest-path", "--config", "-Z", "--out-dir", "--artifact-dir", "--root")


def _package_scripts(project: Path) -> set[str]:
    try:
        data = json.loads((project / "package.json").read_text(encoding="utf-8"))
        return {str(k) for k in (data.get("scripts") or {})}
    except (OSError, ValueError, AttributeError):
        return set()


def _make_targets(project: Path) -> set[str]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        try:
            text = (project / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        return set(re.findall(r"^([A-Za-z0-9_.\-]+)\s*:(?!=)", text, re.M))
    return set()


def project_command_allowed(project: Path, command: str, cfg: McpConfig) -> tuple[bool, str]:
    """Is `command` one of this project's own commands (npm scripts, cargo, make targets, tests...)?

    Those run straight away. Anything else is sent to you for approval instead.
    """
    command = (command or "").strip()
    if not command:
        return False, "no command given"
    if is_destructive(command):
        return False, "it can delete or overwrite things"
    for root, extra in cfg.commands.items():
        rp = Path(root)
        if (project == rp or project.is_relative_to(rp)) and command in extra:
            return True, "listed in mcp.json"
    if SHELL_OPERATORS.search(command):
        return False, "it uses shell operators"
    try:
        argv = shlex.split(command)
    except ValueError:
        return False, "it couldn't be parsed"
    if any(".." in a or a.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", a) for a in argv[1:]):
        return False, "it points outside the project"
    tool, rest = argv[0], argv[1:]
    if tool in ("npm", "pnpm", "yarn", "bun"):
        scripts = _package_scripts(project)
        if tool == "npm" and rest[:1] in (["test"], ["start"]) and rest[0] in scripts:
            return True, "npm script"
        if rest[:1] in (["run"], ["run-script"]) and len(rest) > 1 and rest[1] in scripts:
            return True, f"{tool} script"
        if tool in ("pnpm", "yarn") and rest and rest[0] in scripts:
            return True, f"{tool} script"
        return False, "not one of this project's package.json scripts"
    if tool == "cargo":
        if not (project / "Cargo.toml").exists():
            return False, "there's no Cargo.toml here"
        if rest and rest[0] in CARGO_SUBCOMMANDS and not any(a.startswith(CARGO_BAD_FLAGS) for a in rest):
            return True, "cargo"
        return False, "that cargo command isn't on the safe list"
    if tool == "make":
        targets = _make_targets(project)
        if targets and all(t in targets for t in rest if not t.startswith("-")) and \
                not any(a.startswith(("-C", "--directory", "-f", "--file")) for a in rest):
            return True, "make target"
        return False, "not one of this project's make targets"
    if tool == "pytest" or argv[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"]):
        return True, "tests"
    if tool == "go" and rest[:1] and rest[0] in ("build", "test", "vet", "run"):
        return True, "go"
    if tool == "dotnet" and rest[:1] and rest[0] in ("build", "test", "run"):
        return True, "dotnet"
    return False, "it isn't one of this project's known commands"


# =====================================================================================  grimoire
RUNNABLE_KINDS = {"shell", "bash", "python", "node", "powershell", "batch"}


def find_grimoire_entry(entries: list[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    want = (name or "").strip().lower()
    if not want:
        return None
    for key in ("id", "description"):
        for e in entries:
            if str(e.get(key, "")).lower() == want:
                return e
    for e in entries:
        if want in [t.lower() for t in e.get("voice_triggers", [])]:
            return e
    return None


def grimoire_command(entry: dict[str, Any], args: list[str]) -> str:
    """The shell line for a saved macro plus quoted arguments."""
    if entry.get("kind") not in RUNNABLE_KINDS or not entry.get("command"):
        raise ToolError(f"“{entry.get('description')}” is a {entry.get('kind')} — run it from Orion itself; "
                        "only shell macros and scripts run through here.")
    if sys.platform == "win32":
        quoted = [subprocess.list2cmdline([a]) for a in args]
    else:
        quoted = [shlex.quote(a) for a in args]
    return " ".join([entry["command"], *quoted])


def grimoire_needs_approval(entry: dict[str, Any], command: str) -> bool:
    return bool(entry.get("requires_approval")) or is_destructive(command)


# =====================================================================================  subprocesses
async def run_argv(argv: list[str], cwd: Path, timeout: float, env: Optional[dict[str, str]] = None,
                   limit_bytes: int = 4_000_000) -> tuple[int, str, bool]:
    """(exit code, combined output, timed out). Kills the whole process group on timeout."""
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL, env={**os.environ, **(env or {})}, **kwargs)
    except (OSError, ValueError) as exc:
        return 127, f"Could not start {argv[0]}: {exc}", False
    chunks: list[bytes] = []
    size = 0

    async def pump() -> None:
        nonlocal size
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            size += len(chunk)
            chunks.append(chunk)
            while size > limit_bytes and len(chunks) > 1:   # keep the most recent output
                size -= len(chunks.pop(0))

    timed_out = False
    try:
        await asyncio.wait_for(pump(), timeout)
        code = await proc.wait()
    except asyncio.TimeoutError:
        timed_out = True
        try:
            if sys.platform != "win32":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        code = await proc.wait()
    return code if code is not None else -1, b"".join(chunks).decode("utf-8", errors="replace"), timed_out


async def git(project: Path, *args: str, timeout: float = 30) -> tuple[int, str]:
    code, out, timed_out = await run_argv(
        ["git", "-c", "core.pager=cat", "-c", "color.ui=false", *args], project, timeout,
        env={"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    if timed_out:
        raise ToolError("git took too long and was stopped.")
    return code, out


async def git_staged(project: Path) -> dict[str, Any]:
    """What a commit would contain right now, plus a hash that pins it for the approval."""
    code, root = await git(project, "rev-parse", "--show-toplevel")
    if code != 0:
        raise ToolError("This folder isn't a git repository.")
    _, names = await git(project, "diff", "--cached", "--name-only")
    _, stat = await git(project, "diff", "--cached", "--stat")
    _, patch = await git(project, "diff", "--cached", "--binary")
    _, branch = await git(project, "rev-parse", "--abbrev-ref", "HEAD")
    files = [n for n in names.splitlines() if n.strip()]
    return {"files": files, "stat": stat.strip(), "branch": branch.strip(),
            "staged_hash": hashlib.sha256(patch.encode("utf-8", errors="replace")).hexdigest()}


GIT_REF = re.compile(r"^[A-Za-z0-9_./~^@{}\-]{1,200}$")


def check_ref(ref: str) -> str:
    """A branch, tag, commit or range such as main..HEAD — never something git could read as an option."""
    if not GIT_REF.match(ref) or ref.startswith("-"):
        raise ToolError(f"{ref!r} isn't a valid git ref.")
    return ref


# =====================================================================================  MySQL
def mysql_connection_settings(cfg: McpConfig, name: Optional[str]) -> dict[str, Any]:
    conns = cfg.mysql.get("connections") or {}
    if not conns:
        raise ToolError("No MySQL connections are set up. Add one under \"mysql\" in ~/.config/orion/mcp.json.")
    if not name:
        name = cfg.mysql.get("default") or (next(iter(conns)) if len(conns) == 1 else "")
    if name not in conns:
        raise ToolError(f"Unknown connection {name!r}. Configured: {', '.join(conns)}.")
    c = dict(conns[name])
    staging = {str(h).strip().lower() for h in cfg.mysql.get("staging_hosts", []) or []}
    host = str(c.get("host") or "").strip().lower()
    if not host or host not in staging:
        raise ToolError(f"Connection {name!r} points at {host or 'no host'}, which isn't in your staging_hosts "
                        "list. Orion only talks to databases you've marked as staging.")
    password = c.get("password") or ""
    if c.get("password_env"):
        password = os.getenv(str(c["password_env"]), "")
    return {"name": name, "host": c["host"], "port": int(c.get("port") or 3306), "user": str(c.get("user") or ""),
            "password": password, "database": c.get("database") or None}


def _connect_mysql(conn: dict[str, Any]):
    try:
        import pymysql  # optional: pip install PyMySQL
    except ImportError:
        raise ToolError("The MySQL tool needs the PyMySQL package (pip install PyMySQL).")
    return pymysql.connect(host=conn["host"], port=conn["port"], user=conn["user"], password=conn["password"],
                           database=conn["database"], connect_timeout=8, read_timeout=60, write_timeout=60,
                           charset="utf8mb4", autocommit=False)


MySQLConnect = Callable[[dict[str, Any]], Any]


def mysql_read(conn: dict[str, Any], sql: str, max_rows: int, timeout_s: int,
               connect: Optional[MySQLConnect] = None) -> dict[str, Any]:
    """Run a checked read-only query inside a READ ONLY transaction (a second line of defence)."""
    ok, why = sql_read_only(sql)
    if not ok:
        raise ToolError(f"Refused: {why}.")
    db = (connect or _connect_mysql)(conn)
    try:
        cur = db.cursor()
        try:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(timeout_s * 1000)}")
        except Exception:
            pass  # MariaDB / old MySQL
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("START TRANSACTION READ ONLY")
        cur.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = [list(r) for r in cur.fetchmany(max_rows + 1)] if cols else []
        db.rollback()
    finally:
        try:
            db.close()
        except Exception:
            pass
    truncated = len(rows) > max_rows
    return {"columns": cols, "rows": rows[:max_rows], "truncated": truncated}


def mysql_write(conn: dict[str, Any], sql: str, connect: Optional[MySQLConnect] = None) -> dict[str, Any]:
    """Run one approved write statement and commit it (called by the daemon after a signed approval)."""
    _strip_sql(sql)   # still refuse executable comments / unfinished strings
    db = (connect or _connect_mysql)(conn)
    try:
        cur = db.cursor()
        affected = cur.execute(sql)
        db.commit()
    except Exception:
        try:
            db.rollback()
        finally:
            raise
    finally:
        try:
            db.close()
        except Exception:
            pass
    return {"rows_affected": affected}


# =====================================================================================  tools
@dataclass
class Tool:
    name: str
    title: str
    description: str
    risk: str                                 # read | run | approval
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]

    def describe(self) -> dict[str, Any]:
        label = {"read": "Read-only.", "run": "Runs a command inside the project folder.",
                 "approval": "Needs your approval: proposes the action and waits for a signed OK."}[self.risk]
        return {
            "name": self.name,
            "title": self.title,
            "description": f"{self.description} Risk: {label}",
            "inputSchema": self.schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.risk == "read",
                "destructiveHint": self.risk == "approval",
                "idempotentHint": self.risk == "read",
                "openWorldHint": False,
            },
            "_meta": {"orion/risk": self.risk},
        }


def _schema(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


PROJECT = {"type": "string", "description": "Project folder (absolute, or relative to Orion's repo). "
                                            "Defaults to the folder the agent started in."}
_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict, "number": (int, float)}


def validate_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("Arguments must be an object.")
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            raise ToolError(f"Missing argument: {key}")
    for key, value in args.items():
        if key not in props:
            raise ToolError(f"Unknown argument: {key}")
        want = props[key].get("type")
        py = _TYPES.get(want)
        if py and (not isinstance(value, py) or (want in ("integer", "number") and isinstance(value, bool))):
            raise ToolError(f"{key} must be a {want}")
        if want == "array" and not all(isinstance(v, str) for v in value):
            raise ToolError(f"{key} must be a list of strings")
    return args


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


class DaemonUnavailable(Exception):
    pass


class Backend:
    """Where runs and approvals go. The default talks to the running daemon (so the HUD and phone see them);
    runs fall back to this process when the daemon is down. Approvals never fall back."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0) -> None:
        from config import settings
        self.base_url = (base_url or os.getenv("ORION_DAEMON_URL") or f"http://127.0.0.1:{settings.port}").rstrip("/")
        self.timeout = timeout
        self._local_runner: Any = None
        self._preload: Any = None

    async def call(self, method: str, path: str, body: Optional[dict[str, Any]] = None,
                   timeout: Optional[float] = None) -> tuple[int, dict]:
        import httpx
        try:
            import api_token   # review B1: the daemon's per-start token, read fresh (it changes on restart)
            tok = api_token.read_local()
            headers = {"Authorization": f"Bearer {tok}"} if tok else {}
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
                res = await client.request(method, self.base_url + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise DaemonUnavailable(str(exc))
        try:
            data = res.json()
        except ValueError:
            data = {"error": res.text[:300]}
        if res.status_code == 404 and path.startswith(("/mcp/", "/toolkit/")) and data.get("detail") == "Not Found":
            raise DaemonUnavailable("this Orion version has no MCP routes")
        return res.status_code, data if isinstance(data, dict) else {"data": data}

    def local_runner(self):
        if self._local_runner is None:
            from runner import ProcessRunner

            async def _quiet(_e: dict[str, Any]) -> None:
                return None
            self._local_runner = ProcessRunner(_quiet)
        return self._local_runner

    def grimoire_entries(self) -> list[dict[str, Any]]:
        from config import settings
        from grimoire import Grimoire
        from preload import Preload
        if self._preload is None:
            self._preload = Preload(settings.data_dir, Path(__file__).resolve().parent, settings.repo_root)
        else:
            try:
                self._preload.reload()
            except Exception:
                pass
        return Grimoire(self._preload, settings.data_dir, embedder=None).entries()


def _toolkit_tools(module: str, tools: "OrionTools") -> list[Tool]:
    """Toolkit modules define their own MCP tools (imported here lazily: they import this module)."""
    import importlib
    return importlib.import_module(module).mcp_tools(tools)


class OrionTools:
    def __init__(self, cfg: Optional[McpConfig] = None, backend: Optional[Backend] = None,
                 default_project: Optional[str] = None, mysql_connect: Optional[MySQLConnect] = None) -> None:
        self.cfg = cfg or McpConfig.load()
        self.backend = backend or Backend()
        self.default_project = default_project if default_project is not None else os.getcwd()
        self.mysql_connect = mysql_connect
        self._runs_local: set[str] = set()
        self.tools: dict[str, Tool] = {t.name: t for t in self._build()}

    # ----------------------------------------------------------------- helpers
    def project(self, args: dict[str, Any]) -> Path:
        explicit = args.get("project")
        default = self.default_project
        if not explicit:
            try:
                return resolve_project(default, self.cfg)
            except ToolError:
                raise ToolError("Say which project folder to use (the 'project' argument); the folder this agent "
                                "started in isn't one Orion lets agents use.")
        return resolve_project(explicit, self.cfg)

    def out(self, text: str) -> str:
        return clean(text, self.cfg.max_output)

    async def propose(self, body: dict[str, Any]) -> str:
        try:
            status, data = await self.backend.call("POST", "/mcp/propose", body)
        except DaemonUnavailable:
            raise ToolError("This needs your approval, but Orion isn't running so it can't ask you. "
                            "Start Orion and try again. Nothing was done.")
        if status >= 400:
            raise ToolError(str(data.get("error") or data.get("detail") or f"Orion refused it ({status})."))
        dec = data.get("decision") or {}
        return (f"Waiting for your approval: {dec.get('summary', 'request sent')}.\n"
                f"Decision {dec.get('id')} — approve it on the boardroom or your phone. Nothing has happened yet. "
                f"Check on it with approval_status(decision_id=\"{dec.get('id')}\").")

    # ----------------------------------------------------------------- runs (daemon first, local fallback)
    async def _start_run(self, body: dict[str, Any], project: Path, command: str, label: str,
                         long_running: bool) -> dict[str, Any]:
        try:
            status, data = await self.backend.call("POST", "/mcp/run", body)
            if status == 403 and data.get("needs_approval"):
                raise ToolError(str(data.get("error")))
            if status >= 400:
                raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
            return {**data["run"], "where": "orion"}
        except DaemonUnavailable:
            run = await self.backend.local_runner().start(
                command, cwd=str(project), label=label, kind="service" if long_running else "task")
            self._runs_local.add(run.id)
            return {**run.info(), "where": "here"}

    async def _run_info(self, run_id: str, lines: int = 60) -> Optional[dict[str, Any]]:
        if run_id in self._runs_local:
            run = self.backend.local_runner().runs.get(run_id)
            if not run:
                return None
            return {**run.info(), "tail": list(run.tail)[-lines:], "where": "here"}
        try:
            status, data = await self.backend.call("GET", f"/mcp/runs/{run_id}?lines={lines}")
        except DaemonUnavailable:
            return None
        return data.get("run") if status == 200 else None

    async def _wait_run(self, run: dict[str, Any], wait_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + wait_s
        info = run
        while info.get("running") and time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            info = await self._run_info(run["id"], 200) or info
        return info

    def _describe_run(self, info: dict[str, Any], with_tail: bool = True) -> str:
        if info.get("running"):
            state = "still running"
        else:
            state = f"finished with exit code {info.get('exit_code')}"
        lines = [f"Run {info['id']} ({info.get('label') or info.get('command')}): {state}.",
                 f"Command: {info.get('command')}", f"Folder: {info.get('cwd')}"]
        diag = info.get("diagnosis") or {}
        if diag.get("diagnosis"):
            lines.append(f"Orion's diagnosis: {diag['diagnosis']}")
            if diag.get("fix_command"):
                lines.append(f"Suggested fix: {diag['fix_command']}")
        if with_tail and info.get("tail"):
            lines += ["", "Output (latest lines):", *info["tail"]]
        return self.out("\n".join(lines))

    # ----------------------------------------------------------------- tool handlers
    async def grimoire_list(self, a: dict[str, Any]) -> str:
        q = (a.get("query") or "").lower().strip()
        rows = []
        for e in self.backend.grimoire_entries():
            hay = " ".join([e["id"], e["description"], *e.get("voice_triggers", [])]).lower()
            if q and not all(w in hay for w in q.split()):
                continue
            rows.append({"name": e["id"], "description": e["description"], "kind": e["kind"],
                         "runs_here": e["kind"] in RUNNABLE_KINDS and bool(e.get("command")),
                         "needs_approval": bool(e.get("requires_approval")),
                         "command": e.get("command") or ""})
        if not rows:
            return "No saved macros match." if q else "The Grimoire is empty."
        return self.out(json.dumps(rows[:200], indent=1, ensure_ascii=False))

    async def grimoire_run(self, a: dict[str, Any]) -> str:
        project = self.project(a)
        entry = find_grimoire_entry(self.backend.grimoire_entries(), a["name"])
        if not entry:
            raise ToolError(f"No saved macro called {a['name']!r}. Use grimoire_list to see them.")
        command = grimoire_command(entry, a.get("args") or [])
        if grimoire_needs_approval(entry, command):
            return await self.propose({"action": "run", "project": str(project), "command": command,
                                       "label": f"Grimoire: {entry['description'][:50]}",
                                       "why": "this macro is marked as needing approval or can delete things"})
        run = await self._start_run({"project": str(project), "grimoire_id": entry["id"],
                                     "args": a.get("args") or []},
                                    project, command, f"Grimoire: {entry['description'][:50]}", False)
        info = await self._wait_run(run, _clamp(a.get("wait_seconds"), 0, 600, 60))
        return self._describe_run(info)

    async def runner_start(self, a: dict[str, Any]) -> str:
        project = self.project(a)
        command = a["command"].strip()
        ok, why = project_command_allowed(project, command, self.cfg)
        label = (a.get("label") or command)[:60]
        if not ok:
            return await self.propose({"action": "run", "project": str(project), "command": command,
                                       "label": label, "long_running": bool(a.get("long_running")), "why": why})
        run = await self._start_run({"project": str(project), "command": command, "label": label,
                                     "long_running": bool(a.get("long_running"))},
                                    project, command, label, bool(a.get("long_running")))
        info = await self._wait_run(run, _clamp(a.get("wait_seconds"), 0, 600, 0 if a.get("long_running") else 20))
        return self._describe_run(info)

    async def runner_status(self, a: dict[str, Any]) -> str:
        if a.get("run_id"):
            info = await self._run_info(a["run_id"], 20)
            if not info:
                raise ToolError(f"No run called {a['run_id']}.")
            return self._describe_run(info, with_tail=False)
        runs: list[dict[str, Any]] = []
        try:
            status, data = await self.backend.call("GET", "/mcp/runs")
            if status == 200:
                runs += data.get("runs", [])
        except DaemonUnavailable:
            pass
        local = self.backend.local_runner().runs if self._runs_local else {}
        runs += [local[r].info() for r in self._runs_local if r in local]
        if not runs:
            return "No runs started through Orion's tools yet."
        return self.out("\n".join(
            f"{r['id']}  {'running' if r.get('running') else 'exit ' + str(r.get('exit_code'))}  "
            f"{r.get('label') or r.get('command')}  ({r.get('cwd')})" for r in runs))

    async def runner_logs(self, a: dict[str, Any]) -> str:
        info = await self._run_info(a["run_id"], _clamp(a.get("lines"), 1, 200, 200))
        if not info:
            raise ToolError(f"No run called {a['run_id']}.")
        return self._describe_run(info)

    async def git_status(self, a: dict[str, Any]) -> str:
        project = self.project(a)
        code, out = await git(project, "status", "--short", "--branch")
        if code != 0:
            raise ToolError(self.out(out.strip() or "git status failed."))
        return self.out(out.strip() or "Nothing changed.")

    async def git_diff(self, a: dict[str, Any]) -> str:
        project = self.project(a)
        args = ["diff", "--no-ext-diff", "--no-color"]
        if a.get("staged"):
            args.append("--cached")
        if a.get("stat_only"):
            args.append("--stat")
        if a.get("ref"):
            args.append(check_ref(a["ref"]))
        args.append("--")
        for p in a.get("paths") or []:
            args.append(str(resolve_inside(project, p, must_exist=False)))
        code, out = await git(project, *args)
        if code != 0:
            raise ToolError(self.out(out.strip() or "git diff failed."))
        return self.out(out or "No differences.")

    async def git_log(self, a: dict[str, Any]) -> str:
        project = self.project(a)
        n = _clamp(a.get("limit"), 1, 200, 20)
        args = ["log", f"-n{n}", "--date=short", "--pretty=format:%h %ad %an  %s"]
        if a.get("ref"):
            args.append(check_ref(a["ref"]))
        args.append("--")
        for p in a.get("paths") or []:
            args.append(str(resolve_inside(project, p, must_exist=False)))
        code, out = await git(project, *args)
        if code != 0:
            raise ToolError(self.out(out.strip() or "git log failed."))
        return self.out(out or "No commits yet.")

    async def git_commit(self, a: dict[str, Any]) -> str:
        project = self.project(a)
        message = a["message"].strip()
        if not message:
            raise ToolError("Give the commit a message.")
        paths = [str(resolve_inside(project, p, must_exist=False)) for p in a.get("paths") or []]
        if paths:   # staging only touches this repo's index, so it's done straight away
            code, out = await git(project, "add", "--", *paths)
            if code != 0:
                raise ToolError(self.out(out.strip() or "git add failed."))
        staged = await git_staged(project)
        if not staged["files"]:
            raise ToolError("Nothing is staged to commit. Pass the files in 'paths' (or git add them first).")
        return await self.propose({"action": "git_commit", "project": str(project), "message": message})

    async def _cargo(self, sub: str, a: dict[str, Any]) -> str:
        project = self.project(a)
        if not (project / "Cargo.toml").exists():
            raise ToolError(f"There's no Cargo.toml in {project}.")
        cargo = shutil.which("cargo")
        if not cargo:
            raise ToolError("cargo isn't installed on this computer.")
        argv = [cargo, sub, "--color", "never"]
        if sub == "check":
            argv += ["--message-format", "short"]
        if a.get("package"):
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", a["package"]):
                raise ToolError("That package name isn't valid.")
            argv += ["-p", a["package"]]
        if a.get("workspace"):
            argv.append("--workspace")
        if a.get("all_targets"):
            argv.append("--all-targets")
        if a.get("release"):
            argv.append("--release")
        feats = a.get("features") or []
        if feats:
            if not all(re.fullmatch(r"[A-Za-z0-9_\-/]+", f) for f in feats):
                raise ToolError("A feature name isn't valid.")
            argv += ["--features", ",".join(feats)]
        if sub == "test" and a.get("filter"):
            if a["filter"].startswith("-"):
                raise ToolError("The test filter can't start with '-'.")
            argv.append(a["filter"])
        timeout = _clamp(a.get("timeout_seconds"), 10, 3600, 600)
        code, out, timed_out = await run_argv(argv, project, timeout, env={"CARGO_TERM_COLOR": "never"})
        head = (f"cargo {sub} stopped after {timeout} s (timeout)." if timed_out else
                f"cargo {sub} {'passed' if code == 0 else 'failed'} (exit code {code}).")
        return self.out(head + "\n\n" + out.strip())

    async def cargo_check(self, a: dict[str, Any]) -> str:
        return await self._cargo("check", a)

    async def cargo_test(self, a: dict[str, Any]) -> str:
        return await self._cargo("test", a)

    async def mysql_query(self, a: dict[str, Any]) -> str:
        conn = mysql_connection_settings(self.cfg, a.get("connection"))
        sql = a["sql"].strip()
        ok, why = sql_read_only(sql)
        if not ok:
            return await self.propose({"action": "mysql_write", "connection": conn["name"], "sql": sql, "why": why})
        max_rows = _clamp(a.get("max_rows"), 1, 1000, 200)
        try:
            result = await asyncio.to_thread(mysql_read, conn, sql, max_rows, 30, self.mysql_connect)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(self.out(f"MySQL error: {exc}"))
        text = json.dumps(result, default=str, ensure_ascii=False)
        return self.out(text)

    async def blender_run_script(self, a: dict[str, Any]) -> str:
        blender = self.cfg.blender or shutil.which("blender") or ""
        if not blender or not Path(blender).exists():
            raise ToolError("Blender isn't installed on this computer, so this tool is unavailable.")
        project = self.project(a)
        script = resolve_inside(project, a["script"])
        if script.suffix.lower() != ".py" or not script.is_file():
            raise ToolError("The script must be a .py file inside the project.")
        argv = [blender, "--background", "--factory-startup", "--python-exit-code", "1"]
        if a.get("blend_file"):
            blend = resolve_inside(project, a["blend_file"])
            if blend.suffix.lower() != ".blend":
                raise ToolError("blend_file must be a .blend file inside the project.")
            argv.append(str(blend))
        argv += ["--python", str(script), "--", *(a.get("args") or [])]
        timeout = _clamp(a.get("timeout_seconds"), 10, 3600, 300)
        code, out, timed_out = await run_argv(argv, project, timeout)
        head = (f"Blender stopped after {timeout} s (timeout)." if timed_out else
                f"Blender finished with exit code {code}.")
        return self.out(head + "\n\n" + out.strip())

    async def approval_status(self, a: dict[str, Any]) -> str:
        try:
            status, data = await self.backend.call("GET", f"/mcp/decision/{a['decision_id']}")
        except DaemonUnavailable:
            raise ToolError("Orion isn't running, so the approval can't be checked.")
        if status != 200:
            raise ToolError(str(data.get("error") or "Unknown decision."))
        words = {"pending": "still waiting for your approval", "approved": "approved", "edited": "approved",
                 "rejected": "declined", "failed": "approved but it failed", "auto_executed": "done automatically"}
        lines = [f"{data.get('summary')}: {words.get(data.get('status'), data.get('status'))}."]
        if data.get("result"):
            lines.append(json.dumps(data["result"], indent=1, default=str))
        return self.out("\n".join(lines))

    # ----------------------------------------------------------------- Toolkit: Collision Guard / Proof Gate / Baton
    # The daemon holds the live map (agents, claims), so these ask it first; without it they still work on git alone.
    def _toolkit_project(self, a: dict[str, Any], required: bool = True) -> str:
        if a.get("project") or required:
            return str(self.project(a))
        try:
            return str(resolve_project(self.default_project, self.cfg))
        except ToolError:
            return ""

    async def _toolkit_call(self, path: str, body: dict[str, Any], timeout: float = 15.0) -> Optional[dict]:
        """The daemon's answer, or None when Orion isn't running."""
        try:
            status, data = await self.backend.call("POST", path, body, timeout=timeout)
        except DaemonUnavailable:
            return None
        if status >= 400:
            raise ToolError(str(data.get("error") or data.get("detail") or f"Orion refused it ({status})."))
        return data

    async def collision_check(self, a: dict[str, Any]) -> str:
        repo = self._toolkit_project(a)
        body = {"repo": repo, "files": a.get("files") or [], "party": a.get("party") or ""}
        data = await self._toolkit_call("/toolkit/collisions/check", body)
        if data is None:
            from collision_guard import CollisionGuard
            from config import settings
            data = await asyncio.to_thread(CollisionGuard(settings.data_dir, extra_repos=()).check, repo,
                                           body["files"] or None, body["party"], [])
            data["answer"] += "\n(Orion isn't running, so only git was checked: other agents are unknown.)"
        return self.out(data.get("answer") or "No answer.")

    async def collision_claim(self, a: dict[str, Any]) -> str:
        repo = self._toolkit_project(a)
        body = {"repo": repo, "party": a["party"], "files": a["files"], "branch": a.get("branch") or "",
                "ttl_minutes": _clamp(a.get("minutes"), 5, 7 * 24 * 60, 240), "note": a.get("note") or ""}
        data = await self._toolkit_call("/toolkit/collisions/claim", body)
        if data is None:
            raise ToolError("Orion isn't running, so the claim can't be shared with the other agents. Nothing was saved.")
        c = data["claim"]
        head = f"Claimed {len(c['patterns'])} path(s) in {Path(repo).name} for {c['party']} (claim {c['id']})."
        return self.out(head + "\n" + data.get("answer", ""))

    async def proof_prove(self, a: dict[str, Any]) -> str:
        target = (a.get("agent_or_branch") or "").strip()
        repo = self._toolkit_project(a, required=bool(a.get("project")))
        body = {"target": target, "repo": repo, "claim": a.get("claim") or "",
                "screenshot_url": a.get("screenshot_url") or ""}
        timeout = _clamp(a.get("timeout_seconds"), 30, 3600, 1800)
        data = await self._toolkit_call("/toolkit/proof", body, timeout=timeout)
        if data is None:
            if not repo:
                raise ToolError("Orion isn't running, so agents can't be looked up. Give the project (and a branch).")
            from config import settings
            from proof_gate import ProofGate
            gate = ProofGate(settings.data_dir, auto=False)
            data = await gate.prove(target=target if target not in ("", ".") else "", repo=repo,
                                    claim=body["claim"], screenshot=body["screenshot_url"] or None)
        return self.out(describe_proof(data))

    async def baton_handoff(self, a: dict[str, Any]) -> str:
        repo = self._toolkit_project(a, required=bool(a.get("project")))
        body = {"from_agent": a["from_agent"], "to_agent": a["to_agent"], "repo": repo, "goal": a.get("goal") or "",
                "notes": a.get("notes") or ""}
        data = await self._toolkit_call("/toolkit/baton", body, timeout=180)
        if data is None:
            if not repo:
                raise ToolError("Orion isn't running, so agents can't be looked up. Give the project folder.")
            from baton import Baton, BatonError
            from config import settings
            try:
                data = await Baton(settings.data_dir).handoff(a["from_agent"], a["to_agent"], repo=repo,
                                                              goal=body["goal"], notes=body["notes"])
            except BatonError as exc:
                raise ToolError(str(exc))
        return self.out(f"Hand-off written: {data['path']}\n"
                        f"Next step: {data['next_step']}\n\n"
                        f"Prompt for {data['to']} (ready to paste):\n{data['prompt']}")

    # ----------------------------------------------------------------- catalogue
    def _build(self) -> list[Tool]:
        S = {"type": "string"}
        B = {"type": "boolean"}
        I = {"type": "integer"}
        L = {"type": "array", "items": {"type": "string"}}
        return [
            Tool("grimoire_list", "List saved macros",
                 "List the shell macros and scripts saved in Orion's Grimoire.", "read",
                 _schema({"query": {**S, "description": "Words to filter by"}}), self.grimoire_list),
            Tool("grimoire_run", "Run a saved macro",
                 "Run a saved Grimoire macro in the project folder and return its output. Macros marked as "
                 "needing approval (or that can delete things) are sent to the user for approval instead.", "run",
                 _schema({"name": {**S, "description": "Macro name from grimoire_list"},
                          "args": {**L, "description": "Extra arguments, passed quoted"},
                          "project": PROJECT,
                          "wait_seconds": {**I, "description": "How long to wait for it to finish (0-600, default 60)"}},
                         ("name",)), self.grimoire_run),
            Tool("runner_start", "Start a project command",
                 "Start one of the project's own commands (npm/pnpm/yarn scripts, cargo, make targets, pytest, "
                 "go, dotnet, or commands listed in mcp.json) through Orion's self-healing runner. Other commands "
                 "are sent to the user for approval.", "run",
                 _schema({"command": S, "project": PROJECT, "label": S,
                          "long_running": {**B, "description": "A server or watcher that keeps running"},
                          "wait_seconds": {**I, "description": "Wait this long for output (default 20)"}},
                         ("command",)), self.runner_start),
            Tool("runner_status", "Run status",
                 "Show runs started through these tools, or one run's status.", "read",
                 _schema({"run_id": S}), self.runner_status),
            Tool("runner_logs", "Run output", "The latest output lines of a run.", "read",
                 _schema({"run_id": S, "lines": {**I, "description": "1-200, default 200"}}, ("run_id",)),
                 self.runner_logs),
            Tool("git_status", "Git status", "Branch and changed files.", "read",
                 _schema({"project": PROJECT}), self.git_status),
            Tool("git_diff", "Git diff", "Show changes (working tree, staged, or against a ref).", "read",
                 _schema({"project": PROJECT, "staged": B, "ref": S, "paths": L, "stat_only": B}), self.git_diff),
            Tool("git_log", "Git log", "Recent commits.", "read",
                 _schema({"project": PROJECT, "limit": I, "ref": S, "paths": L}), self.git_log),
            Tool("git_commit", "Propose a commit",
                 "Stage the given files and ask the user to approve a commit of exactly what is staged. "
                 "The commit is made only after a signed approval.", "approval",
                 _schema({"project": PROJECT, "message": S,
                          "paths": {**L, "description": "Files to stage first (inside the project)"}},
                         ("message",)), self.git_commit),
            Tool("cargo_check", "cargo check", "Run cargo check and return the (shortened) output.", "run",
                 _schema({"project": PROJECT, "package": S, "features": L, "workspace": B, "all_targets": B,
                          "release": B, "timeout_seconds": I}), self.cargo_check),
            Tool("cargo_test", "cargo test", "Run cargo test and return the (shortened) output.", "run",
                 _schema({"project": PROJECT, "package": S, "features": L, "workspace": B, "all_targets": B,
                          "release": B, "filter": S, "timeout_seconds": I}), self.cargo_test),
            Tool("mysql_query", "MySQL query",
                 "Run a read-only query (SELECT, SHOW, DESCRIBE, EXPLAIN) on a staging database from mcp.json. "
                 "Anything that would change data is sent to the user for approval instead.", "read",
                 _schema({"sql": S, "connection": S, "max_rows": I}, ("sql",)), self.mysql_query),
            Tool("blender_run_script", "Run a Blender script",
                 "Run a Python script from the project in Blender without a window (blender --background). "
                 "Unavailable if Blender isn't installed.", "run",
                 _schema({"project": PROJECT, "script": S, "blend_file": S, "args": L, "timeout_seconds": I},
                         ("script",)), self.blender_run_script),
            Tool("approval_status", "Check an approval",
                 "Whether a proposed action was approved, declined, or is still waiting, and its result.", "read",
                 _schema({"decision_id": S}, ("decision_id",)), self.approval_status),
            # Toolkit: Secret Shield
            *_toolkit_tools("secret_shield", self),
            # Toolkit: One Rulebook
            *_toolkit_tools("rulebook", self),
            # Get apps: ask the user to install a Flathub app (an approval card, never a direct install)
            *_toolkit_tools("get_apps", self),
            # Orion Studio: the Craft apps' own MCP tools (photocraft.*, pdfcraft.*) behind Orion's rules
            *_toolkit_tools("craft_bridge", self),
            # Toolkit: Clean Box (throwaway Ubuntu container) and Backup status
            *_toolkit_tools("clean_box", self),
            *_toolkit_tools("backup", self),
            # Toolkit: Package Reality Check / Injection Firewall / Key Proxy
            *_toolkit_tools("agent_safety", self),
            # Toolkit: Project Runbook / Release Butler / DB Time Machine / Raid Night (ops_toolkit.py)
            *_toolkit_tools("ops_toolkit", self),
            *_toolkit_tools("self_running", self),   # Self-running (complete plan step 4): self_status (read)
            # Toolkit: AI Blame / Context Pack / Log Lens (dev_lens.py)
            *_toolkit_tools("dev_lens", self),
            # Toolkit: Dependency Autopilot / Supply-Chain Scan / Exposure Map / Host Health (security_toolkit.py)
            *_toolkit_tools("security_toolkit", self),
            # Safety net, read-only: health_status, snapshots_list (trust_kit.py)
            *_toolkit_tools("trust_kit", self),
            # Toolkit: Collision Guard / Proof Gate / Baton
            Tool("collision_check", "Is it safe to start?",
                 "Collision Guard: before editing, check whether another agent (or the user) is changing the same "
                 "files in this repo. Answers yes, or lists the conflicts with a worktree command and a merge "
                 "order.", "read",
                 _schema({"project": PROJECT, "files": {**L, "description": "Files or globs you plan to change "
                                                                            "(empty: every conflict in the repo)"},
                          "party": {**S, "description": "Who is asking, e.g. Claude"}}), self.collision_check),
            Tool("collision_claim", "Claim files",
                 "Collision Guard: tell Orion which files or globs you are about to change, so other agents are "
                 "warned before they touch them. Replaces your earlier claim in this repo; expires after 'minutes' "
                 "(default 240). Only Orion's own map changes.", "run",
                 _schema({"project": PROJECT, "party": {**S, "description": "Your name, e.g. Codex"},
                          "files": {**L, "description": "Paths or globs, relative to the repo"}, "branch": S,
                          "minutes": I, "note": S}, ("party", "files")), self.collision_claim),
            Tool("proof_prove", "Prove it",
                 "Proof Gate: independently check an agent's 'done' in a temporary copy of its branch: runs the "
                 "build and tests, compares the files it claimed with the real git diff, and optionally takes a "
                 "screenshot of a local page. Answers green or amber with the reason.", "run",
                 _schema({"agent_or_branch": {**S, "description": "An agent (id, name or tool, e.g. Codex) or a "
                                                                  "branch; empty: the project's current checkout"},
                          "project": PROJECT,
                          "claim": {**S, "description": "What was claimed, e.g. 'tests pass, changed app.py'"},
                          "screenshot_url": {**S, "description": "A page on this computer to screenshot"},
                          "timeout_seconds": I}), self.proof_prove),
            Tool("baton_handoff", "Hand work to another agent",
                 "Baton: write a hand-off (goal, decisions, files changed, failing tests, exact next step) to "
                 ".orion/batons/ in the repo and return a prompt ready to paste into the receiving agent. Starts "
                 "nothing by itself.", "run",
                 _schema({"from_agent": {**S, "description": "The agent handing over (id, name or tool)"},
                          "to_agent": {**S, "description": "Who picks it up, e.g. Codex"}, "project": PROJECT,
                          "goal": S, "notes": {**S, "description": "Decisions or context to pass on"}},
                         ("from_agent", "to_agent")), self.baton_handoff),
        ]

    def list_tools(self) -> list[dict[str, Any]]:
        return [t.describe() for t in self.tools.values()]

    async def call(self, name: str, args: Any) -> tuple[str, bool]:
        """(text, is_error). Unknown tools raise KeyError (a protocol error)."""
        tool = self.tools[name]
        try:
            check = getattr(tool, "validate", None)   # proxied tools (craft_bridge) check their own schema
            args = check(args) if check else validate_args(tool.schema, args)
            import agent_safety   # Toolkit: Injection Firewall + Package Reality Check (before), taint labels (after)
            refused = await agent_safety.precheck(self, tool, args)
            if refused:
                return redact(refused), True
            text = await tool.handler(args)
            return agent_safety.postprocess(self, tool, args, text), False
        except ToolError as exc:
            return redact(str(exc)), True
        except Exception as exc:   # never leak a traceback with paths/secrets to the agent
            log.exception("tool %s failed", name)
            return redact(f"{name} failed: {type(exc).__name__}: {exc}")[:2000], True


# =====================================================================================  Toolkit: Proof Gate output
def describe_proof(r: dict[str, Any]) -> str:
    """A Proof Gate result in plain words, for agents."""
    verdict = "GREEN" if r.get("verdict") == "green" else "AMBER"
    who = r.get("label") or r.get("agent") or "the work"
    lines = [f"{verdict}: {who}. {r.get('reason', '')}".strip()]
    if r.get("branch"):
        lines.append(f"Checked {r.get('repo')} at {r.get('branch')} ({r.get('sha')}), "
                     f"{len(r.get('changed_files') or [])} changed file(s), in a temporary copy.")
    for c in r.get("checks") or []:
        state = "passed" if c.get("ok") else ("timed out" if c.get("timed_out") else f"failed (exit {c.get('exit_code')})")
        counts = ""
        if c.get("name") == "test" and (c.get("passed") is not None or c.get("failed") is not None):
            counts = f" [{c.get('passed') or 0} passed, {c.get('failed') or 0} failed]"
        lines.append(f"- {c.get('name')}: `{c.get('command')}` in {c.get('project')}: {state}{counts}")
    if r.get("failing_tests"):
        lines += ["Failing tests:"] + [f"- {t}" for t in r["failing_tests"][:15]]
    shot = r.get("screenshot") or {}
    if shot.get("note"):
        lines.append(shot["note"] + (f" ({shot['path']})" if shot.get("path") else ""))
    for n in r.get("notes") or []:
        lines.append(f"Note: {n}")
    failed_check = next((c for c in r.get("checks") or [] if not c.get("ok")), None)
    if failed_check and failed_check.get("output_tail"):
        lines += ["", f"End of the {failed_check['name']} output:", failed_check["output_tail"][-1500:]]
    return "\n".join(lines)
