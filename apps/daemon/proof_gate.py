"""Proof Gate (Orion Toolkit tool 3): "done" means proven.

When a coding agent says it has finished (its robot turns "done", or someone asks "Orion, prove it" through
the MCP tool proof_prove / POST /toolkit/proof), Orion checks the work itself before the robot turns green:

  1. a temporary git worktree of the agent's branch (plus its uncommitted changes and new files) is made in
     Orion's own data folder, so the agent's checkout is never touched;
  2. the build and the tests are found and run there (test detection shared with the crash healer; a repo can
     override it with .orion/proof.json), with a clean environment (no API keys), a timeout and an output cap.
     Nothing needs the network: Rust runs with --offline, Node reuses the checkout's node_modules;
  3. the files the agent said it changed are compared with the real git diff;
  4. optionally a screenshot of a local page with headless Chromium (Playwright, if it's installed).

The answer is green, or amber with the reason in plain words ("Said tests pass; 2 fail"). Results go out as
`proof.result` events, decorate the swarm robots and, when an agent's claim was wrong, are reported to One
Rulebook (rulebook.record_violation) if that module is installed.

.orion/proof.json (all optional): {"build": "npm run build", "test": "npm test", "timeout_s": 900,
"screenshot": {"url": "http://127.0.0.1:5173/"} or {"file": "dist/index.html"}}
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
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from collision_guard import default_branch, git, ignored, list_worktrees, toplevel

log = logging.getLogger("orion.proof")

BUILD_TIMEOUT_S = 600
TEST_TIMEOUT_S = 900
OUTPUT_CAP = 200_000
KEEP_TAIL = 3000
MAX_RESULTS = 60
MAX_PROJECTS = 3
UNTRACKED_MAX_BYTES = 5_000_000
UNTRACKED_TOTAL_BYTES = 50_000_000
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
# Extra checks on the agent's changes: async fn(src, base, changed, copy) -> [problems]. Package Reality Check
# (agent_safety.py) adds one for new dependencies.
EXTRA_CHECKS: list[Callable[..., Awaitable[list[str]]]] = []

Publish = Callable[[dict[str, Any]], Awaitable[None]]


# =====================================================================================  claims
_TESTS_PASS = re.compile(r"\b(all\s+)?tests?\b[^.;\n]{0,40}\b(pass(es|ed|ing)?|green|succeed(s|ed)?|ok)\b|"
                         r"\bpassing\s+tests?\b|\ball\s+green\b", re.I)
_BUILD_OK = re.compile(r"\bbuild(s|ing)?\b[^.;\n]{0,30}\b(pass(es)?|ok|works?|succeed(s|ed)?|clean(ly)?|fine|green)\b|"
                       r"\bcompiles?\b", re.I)
_NEGATED = re.compile(r"\b(not|don't|doesn't|didn't|fail(s|ed|ing)?|broken)\b", re.I)
_FILE_TOKEN = re.compile(r"(?<![\w/.-])([\w.-]+(?:/[\w.-]+)*\.[A-Za-z][\w]{0,7})(?![\w/])")
_UI_WORDS = re.compile(r"\b(ui|screen|page|layout|css|style|button|hud|window)\b", re.I)


def parse_claim(claim: Any, fallback_files: Optional[list[str]] = None) -> dict[str, Any]:
    """What the agent said it did: {"tests_pass": bool|None, "build_ok": bool|None, "files": [...], "ui": bool}."""
    out: dict[str, Any] = {"tests_pass": None, "build_ok": None, "files": [], "ui": False, "text": ""}
    if isinstance(claim, dict):
        for k in ("tests_pass", "build_ok", "ui"):
            if claim.get(k) is not None:
                out[k] = bool(claim[k])
        out["files"] = [str(f) for f in claim.get("files") or [] if str(f).strip()][:100]
        claim = str(claim.get("text") or "")
    text = str(claim or "").strip()[:4000]
    out["text"] = text
    if text:
        for sentence in re.split(r"[.;\n]+", text):
            if out["tests_pass"] is None and _TESTS_PASS.search(sentence) and not _NEGATED.search(sentence):
                out["tests_pass"] = True
            if out["build_ok"] is None and _BUILD_OK.search(sentence) and not _NEGATED.search(sentence):
                out["build_ok"] = True
        if not out["files"]:
            out["files"] = sorted({m for m in _FILE_TOKEN.findall(text) if not re.fullmatch(r"\d+\.\d+.*", m)})[:100]
        out["ui"] = out["ui"] or bool(_UI_WORDS.search(text))
    if not out["files"] and fallback_files:
        out["files"] = [f for f in fallback_files if f][:100]
    return out


# =====================================================================================  test output
def parse_tests(out: str) -> dict[str, Any]:
    """Counts and failing test names from pytest, cargo, node --test (TAP), jest and vitest output."""
    passed = failed = None
    failing: list[str] = []
    cargo = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed", out)
    if cargo:
        passed = sum(int(p) for p, _ in cargo)
        failed = sum(int(f) for _, f in cargo)
        failing = re.findall(r"^test (\S+) \.\.\. FAILED", out, re.M)
    elif re.search(r"^# (pass|fail) \d+", out, re.M):
        passed = sum(int(x) for x in re.findall(r"^# pass (\d+)", out, re.M))
        failed = sum(int(x) for x in re.findall(r"^# fail (\d+)", out, re.M))
        failing = [x.strip() for x in re.findall(r"^\s*not ok \d+ - (.+)$", out, re.M)]
    else:
        tail = out[-6000:]
        f = re.findall(r"(\d+) (?:failed|failing)", tail)
        p = re.findall(r"(\d+) (?:passed|passing)", tail)
        e = re.findall(r"(\d+) errors?\b", tail)
        if f or p or e:
            failed = (int(f[-1]) if f else 0) + (int(e[-1]) if e else 0)
            passed = int(p[-1]) if p else 0
        failing = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M) or \
            [x.strip() for x in re.findall(r"^\s*(?:✕|×|FAIL)\s+(.+)$", out, re.M)]
    seen: list[str] = []
    for name in failing:
        if name not in seen:
            seen.append(name)
    return {"passed": passed, "failed": failed, "failing": seen[:30]}


# =====================================================================================  detection
def _python_for(project: Path, source_root: Optional[Path]) -> str:
    for base in [project, source_root]:
        if base is None:
            continue
        for venv in (".venv", "venv"):
            for rel in ("bin/python", "Scripts/python.exe"):
                cand = base / venv / rel
                if cand.exists():
                    return str(cand)
    return sys.executable


def _project_kind(d: Path) -> Optional[str]:
    if (d / "Cargo.toml").is_file():
        return "rust"
    if (d / "package.json").is_file():
        return "node"
    if any((d / m).exists() for m in ("pyproject.toml", "setup.cfg", "pytest.ini", "conftest.py", "setup.py")):
        return "python"
    if (d / "tests").is_dir() and (any((d / "tests").glob("test_*.py")) or any((d / "tests").glob("*_test.py"))):
        return "python"
    return None


def find_projects(root: Path, changed: list[str]) -> list[tuple[str, str]]:
    """[(project dir relative to root, language)] nearest to the changed files (at most MAX_PROJECTS)."""
    found: list[tuple[str, str]] = []
    starts = [Path(c).parent for c in changed if not ignored(c)] or [Path(".")]
    for start in starts:
        d = start
        while True:
            full = root / d
            kind = _project_kind(full) if full.is_dir() else None
            if kind:
                key = (d.as_posix() if str(d) != "." else ".", kind)
                if key not in found:
                    found.append(key)
                break
            if str(d) in (".", ""):
                break
            d = d.parent
        if len(found) >= MAX_PROJECTS:
            break
    return found


def commands_for(wt: Path, rel: str, kind: str, source_root: Optional[Path]) -> dict[str, Optional[str]]:
    """{"build": cmd|None, "test": cmd|None} for one project inside the temporary worktree."""
    project = wt / rel
    if kind == "node":
        try:
            scripts = json.loads((project / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        test = str(scripts.get("test") or "")
        return {"build": "npm run build --silent" if scripts.get("build") else None,
                "test": "npm test --silent" if test and "no test specified" not in test else None}
    if kind == "rust":
        return {"build": None, "test": "cargo test --offline --color never"}
    py = _python_for((source_root / rel) if source_root else project, source_root)
    from crash_healer import _guess_test_command   # same test detection as the crash healer
    test = _guess_test_command("python", project, {"python": py}, False)
    if test is None and (project / "conftest.py").exists():
        test = f"{shlex.quote(py)} -m pytest -q -p no:cacheprovider"
    build = (f"{shlex.quote(py)} -m compileall -q -x "
             f"{shlex.quote(r'(node_modules|[.]venv|venv|target|[.]git|build|dist)')} .")
    return {"build": build, "test": test}


def load_proof_config(wt: Path) -> dict[str, Any]:
    try:
        data = json.loads((wt / ".orion" / "proof.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# =====================================================================================  running
def clean_env(home: str, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    from crash_healer import _clean_env   # no API keys, tokens or daemon settings reach the tests
    env = _clean_env(home)
    env.update({"npm_config_offline": "true", "npm_config_audit": "false", "npm_config_fund": "false",
                "npm_config_update_notifier": "false", "CARGO_NET_OFFLINE": "true"})
    env.update(extra or {})
    return env


async def run_shell(command: str, cwd: Path, timeout: float, env: dict[str, str]) -> tuple[int, str, bool]:
    """(exit code, output (capped), timed out). The whole process group is killed on timeout."""
    kwargs: dict[str, Any] = {"start_new_session": True} if sys.platform != "win32" else {}
    argv = ["/bin/sh", "-c", command] if sys.platform != "win32" else ["cmd.exe", "/d", "/s", "/c", command]
    try:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    **kwargs)
    except OSError as exc:
        return 127, f"Could not start: {exc}", False
    chunks: list[bytes] = []
    size = 0

    async def pump() -> None:
        nonlocal size
        assert proc.stdout is not None
        while True:
            b = await proc.stdout.read(65536)
            if not b:
                break
            chunks.append(b)
            size += len(b)
            while size > OUTPUT_CAP and len(chunks) > 1:   # keep the end: summaries are at the bottom
                size -= len(chunks.pop(0))

    try:
        await asyncio.wait_for(pump(), timeout)
        rc = await asyncio.wait_for(proc.wait(), 15)
        return rc, b"".join(chunks).decode("utf-8", errors="replace"), False
    except asyncio.TimeoutError:
        try:
            if sys.platform != "win32":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        await proc.wait()
        return 124, b"".join(chunks).decode("utf-8", errors="replace"), True


def _redact(text: str) -> str:
    try:
        from mcp_tools import redact
        return redact(text)
    except Exception:
        return text


def _tail(text: str, n: int = KEEP_TAIL) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text or "")
    return _redact(text[-n:])


def report_violation(rule_id: str, agent: str, evidence: str) -> bool:
    """Tell One Rulebook (tk-guard's rulebook.py) an agent's claim was wrong. Silent if it isn't installed."""
    try:
        import rulebook  # type: ignore[import-not-found]
        fn = getattr(rulebook, "record_violation", None)
        if fn is None:
            return False
        fn(rule_id, agent, evidence)
        return True
    except Exception:
        log.debug("rulebook not available", exc_info=True)
        return False


# =====================================================================================  shared with Baton
def match_agent(agents: list[dict[str, Any]], ref: str) -> Optional[dict[str, Any]]:
    """A swarm agent by id, name ("Claude · orion") or tool ("Codex"); only ones with a known folder."""
    low = (ref or "").strip().lower()
    if not low:
        return None
    for test in (lambda a: a["id"].lower() == low, lambda a: str(a.get("name", "")).lower() == low,
                 lambda a: str(a.get("tool", "")).lower() == low):
        hit = next((a for a in agents if test(a) and a.get("cwd")), None)
        if hit:
            return hit
    return None


def base_commit(top: Path, sha: str, branch: str, started: float = 0.0) -> str:
    """Where the agent's work starts: the merge-base with the default branch, or (working on the default
    branch itself) the last commit before the agent started, or HEAD (only uncommitted changes count)."""
    default = default_branch(top)
    if default:
        dsha = git(top, "rev-parse", f"{default}^{{commit}}")[1].strip()
        if dsha != sha and branch not in (default, default.split("/", 1)[-1]):
            code, mb = git(top, "merge-base", default, sha)
            if code == 0 and mb.strip():
                return mb.strip()
    if started:
        code, before = git(top, "rev-list", "-n1", f"--before={int(started)}", sha)
        if code == 0 and before.strip():
            return before.strip()
    return sha


# =====================================================================================  the gate
class ProofError(Exception):
    """A plain-words reason Orion couldn't run the proof at all."""


class ProofGate:
    def __init__(self, data_dir: Path, publish: Optional[Publish] = None,
                 agents_fn: Optional[Callable[[], list[dict[str, Any]]]] = None,
                 auto: Optional[bool] = None, build_timeout: float = BUILD_TIMEOUT_S,
                 test_timeout: float = TEST_TIMEOUT_S) -> None:
        self.dir = Path(data_dir) / "proof"
        self.store = Path(data_dir) / "proof_results.json"
        self.publish = publish
        self.agents_fn = agents_fn
        self.auto = (os.environ.get("ORION_PROOF_AUTO", "true").lower() not in ("0", "false", "no", "off")
                     if auto is None else auto)
        self.build_timeout = build_timeout
        self.test_timeout = test_timeout
        self.results: list[dict[str, Any]] = self._load()
        self.robots: dict[str, dict[str, Any]] = {}     # agent id -> {"phase", "reason", "result_id", "at"}
        self._done_seen: dict[str, float] = {}          # agent id -> last_activity of the "done" we handled
        self._last_hash: dict[str, tuple[str, str]] = {}  # worktree -> (diff hash, result id)
        self._sem = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ storage
    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.results[-MAX_RESULTS:], ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self.store)
        except OSError as exc:
            log.warning("Could not save proof results: %s", exc)

    def get(self, result_id: str) -> Optional[dict[str, Any]]:
        return next((r for r in self.results if r.get("id") == result_id), None)

    def latest_for(self, agent: str = "", worktree: str = "") -> Optional[dict[str, Any]]:
        for r in reversed(self.results):
            if (agent and r.get("agent") == agent) or (worktree and r.get("worktree") == worktree):
                return r
        return None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(reversed(self.results[-limit:]))

    # ------------------------------------------------------------ what to prove
    def find_agent(self, ref: str) -> Optional[dict[str, Any]]:
        if not ref or not self.agents_fn:
            return None
        try:
            return match_agent(self.agents_fn() or [], ref)
        except Exception:
            return None

    def resolve(self, target: str = "", repo: str = "", agent: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Work out the source: {repo_root, source (worktree or None), sha, branch, label, agent, started}."""
        target = (target or "").strip()
        if agent is None and target:
            agent = self.find_agent(target)
        if agent is not None:
            top = toplevel(agent.get("cwd"))
            if not top:
                raise ProofError(f"{agent.get('name') or agent['id']} isn't working in a git repository, "
                                 "so there's nothing Orion can check.")
            code, sha = git(top, "rev-parse", "HEAD")
            if code != 0:
                raise ProofError("That repository has no commits yet, so Orion can't make a copy to test in.")
            branch = git(top, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
            return {"repo_root": top, "source": top, "sha": sha.strip(), "branch": branch,
                    "label": str(agent.get("name") or agent["id"]), "agent": str(agent["id"]),
                    "started": float(agent.get("started") or 0), "agent_files": list(agent.get("files") or [])}
        top = toplevel(repo) if repo else None
        if not top:
            if target and not repo:
                raise ProofError(f"I can't see an agent called {target!r}. Give the repo too to check a branch.")
            raise ProofError(f"{repo or 'That folder'} isn't inside a git repository.")
        if not target or target in ("HEAD", "."):
            branch = git(top, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
            sha = git(top, "rev-parse", "HEAD")[1].strip()
            return {"repo_root": top, "source": top, "sha": sha, "branch": branch, "label": branch or top.name,
                    "agent": "", "started": 0.0, "agent_files": []}
        if target.startswith("-") or not re.fullmatch(r"[\w./@{}~^-]{1,200}", target):
            raise ProofError(f"{target!r} isn't a valid branch name.")
        code, sha = git(top, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
        if code != 0:
            raise ProofError(f"There's no branch or agent called {target!r} in {top.name}.")
        source = next((wt for wt, br in list_worktrees(top) if br == target), None)
        return {"repo_root": top, "source": source, "sha": sha.strip(), "branch": target, "label": target,
                "agent": "", "started": 0.0, "agent_files": []}

    def base_for(self, src: dict[str, Any]) -> str:
        return base_commit(src["repo_root"], src["sha"], src["branch"], src.get("started") or 0.0)

    def changed_files(self, src: dict[str, Any], base: str) -> list[str]:
        source = src["source"]
        if source is not None:
            _, names = git(source, "diff", "--name-only", "--no-renames", base)
            _, new = git(source, "ls-files", "--others", "--exclude-standard")
            files = names.splitlines() + new.splitlines()
        else:
            _, names = git(src["repo_root"], "diff", "--name-only", "--no-renames", base, src["sha"])
            files = names.splitlines()
        out = []
        for f in files:
            f = f.strip()
            if f and f not in out and not ignored(f):
                out.append(f)
        return out

    def diff_hash(self, src: dict[str, Any], base: str) -> str:
        h = hashlib.sha256(f"{src['sha']}|{base}".encode())
        if src["source"] is not None:
            h.update(git(src["source"], "diff", "--binary", "HEAD")[1].encode("utf-8", "replace"))
            h.update(git(src["source"], "ls-files", "--others", "--exclude-standard", "-s")[1].encode())
            for f in git(src["source"], "ls-files", "--others", "--exclude-standard")[1].splitlines()[:500]:
                try:
                    st = (src["source"] / f).stat()
                    h.update(f"{f}:{st.st_size}:{st.st_mtime_ns}".encode())
                except OSError:
                    continue
        return h.hexdigest()

    # ------------------------------------------------------------ the temporary copy
    def _make_copy(self, src: dict[str, Any], run_id: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        wt = self.dir / f"wt-{run_id}"
        code, out = git(src["repo_root"], "worktree", "add", "--detach", str(wt), src["sha"], timeout=180)
        if code != 0:
            raise ProofError(f"Orion couldn't make a separate copy to test in: {out.strip()[-200:]}")
        source: Optional[Path] = src["source"]
        if source is None:
            return wt
        _, patch = git(source, "diff", "--binary", "HEAD", timeout=60)
        if patch.strip():
            pf = self.dir / f"patch-{run_id}.diff"
            pf.write_text(patch, encoding="utf-8")
            try:
                code, out = git(wt, "apply", "--whitespace=nowarn", str(pf), timeout=60)
            finally:
                pf.unlink(missing_ok=True)
            if code != 0:
                raise ProofError(f"Orion couldn't copy the uncommitted changes: {out.strip()[-200:]}")
        total = 0
        for rel in git(source, "ls-files", "--others", "--exclude-standard")[1].splitlines():
            if not rel or ignored(rel):
                continue
            s = source / rel
            try:
                if not s.is_file() or s.is_symlink():
                    continue
                size = s.stat().st_size
                if size > UNTRACKED_MAX_BYTES or total + size > UNTRACKED_TOTAL_BYTES:
                    continue
                total += size
                d = wt / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
            except OSError:
                continue
        return wt

    def _link_deps(self, wt: Path, src: dict[str, Any], projects: list[tuple[str, str]]) -> list[str]:
        """Reuse the checkout's installed packages (read-only use) so nothing has to be downloaded."""
        notes: list[str] = []
        source: Path = src["source"] or src["repo_root"]
        for rel in {".", *(p for p, _ in projects)}:
            s, d = source / rel / "node_modules", wt / rel / "node_modules"
            if s.is_dir() and not d.exists():
                try:
                    d.symlink_to(s, target_is_directory=True)
                except OSError:
                    notes.append(f"couldn't reuse node_modules in {rel}")
        return notes

    def _discard_copy(self, src: dict[str, Any], wt: Path) -> None:
        if self.dir.resolve() not in wt.resolve().parents:   # only ever delete inside our own folder
            return
        for link in wt.rglob("node_modules"):
            if link.is_symlink():
                try:
                    link.unlink()
                except OSError:
                    pass
        git(src["repo_root"], "worktree", "remove", "--force", str(wt), timeout=120)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        git(src["repo_root"], "worktree", "prune", timeout=60)

    # ------------------------------------------------------------ screenshot
    async def _screenshot(self, spec: Any, wt: Path, run_id: str) -> dict[str, Any]:
        if not spec:
            return {"skipped": True, "note": ""}
        url = ""
        if isinstance(spec, dict) and spec.get("file"):
            f = (wt / str(spec["file"])).resolve()
            if wt.resolve() not in f.parents or not f.is_file():
                return {"skipped": True, "note": f"Screenshot skipped: {spec['file']} isn't in the project."}
            url = f.as_uri()
        else:
            url = str(spec.get("url") if isinstance(spec, dict) else spec)
            host = urlparse(url).hostname or ""
            if urlparse(url).scheme not in ("http", "https") or host not in LOCAL_HOSTS:
                return {"skipped": True, "note": "Screenshot skipped: only pages on this computer are opened."}
        try:
            from playwright.async_api import async_playwright  # type: ignore[import-not-found]
        except ImportError:
            return {"skipped": True, "note": "Screenshot skipped: Playwright isn't installed."}
        shots = self.dir / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        out = shots / f"{run_id}.png"
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(viewport={"width": 1280, "height": 800})
                    errors: list[str] = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    resp = await page.goto(url, wait_until="load", timeout=30_000)
                    await page.screenshot(path=str(out))
                finally:
                    await browser.close()
        except Exception as exc:
            return {"skipped": True, "note": f"Screenshot skipped: headless Chromium couldn't open it ({exc})."[:300]}
        bad = resp is not None and resp.status >= 400
        return {"skipped": False, "path": str(out), "ok": not bad and not errors,
                "note": (f"The page answered {resp.status}." if bad else
                         f"The page logged an error: {errors[0][:120]}" if errors else "Screenshot taken.")}

    # ------------------------------------------------------------ prove
    async def prove(self, target: str = "", repo: str = "", claim: Any = None,
                    agent: Optional[dict[str, Any]] = None, screenshot: Any = None,
                    auto: bool = False) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:10]
        started = time.time()
        try:
            src = await asyncio.to_thread(self.resolve, target, repo, agent)
        except ProofError as exc:
            return self._finish({"id": run_id, "verdict": "amber", "reason": str(exc), "checks": [],
                                 "agent": (agent or {}).get("id", ""), "label": target or repo,
                                 "started_at": started, "auto": auto, "error": True})
        if src["agent"]:
            self.robots[src["agent"]] = {"phase": "checking", "reason": "Orion is running the build and tests",
                                         "at": time.time()}
        async with self._sem:
            result = await self._prove(src, claim, screenshot, run_id, started, auto)
        return self._finish(result)

    async def _prove(self, src: dict[str, Any], claim: Any, screenshot: Any, run_id: str, started: float,
                     auto: bool) -> dict[str, Any]:
        base = await asyncio.to_thread(self.base_for, src)
        changed = await asyncio.to_thread(self.changed_files, src, base)
        claimed = parse_claim(claim, src.get("agent_files"))
        result: dict[str, Any] = {
            "id": run_id, "agent": src["agent"], "label": src["label"], "repo": src["repo_root"].name,
            "repo_path": str(src["repo_root"]), "worktree": str(src["source"] or ""), "branch": src["branch"],
            "sha": src["sha"][:12], "base": base[:12], "changed_files": changed[:200], "claimed": claimed,
            "checks": [], "failing_tests": [], "started_at": started, "auto": auto}
        dh = await asyncio.to_thread(self.diff_hash, src, base)
        result["diff_hash"] = dh
        prev = self._last_hash.get(str(src["source"] or src["sha"]))
        if auto and prev and prev[0] == dh and self.get(prev[1]):
            old = self.get(prev[1]) or {}
            result.update({k: old.get(k) for k in ("checks", "failing_tests", "screenshot")})
            result["reused"] = prev[1]
            return self._judge(result, claimed, changed)
        wt: Optional[Path] = None
        home = tempfile.mkdtemp(prefix="orion-proof-home-")
        try:
            wt = await asyncio.to_thread(self._make_copy, src, run_id)
            cfg = load_proof_config(wt)
            projects = find_projects(wt, changed)
            notes = await asyncio.to_thread(self._link_deps, wt, src, projects)
            env = clean_env(home, {"CARGO_TARGET_DIR": str(self.dir / "cargo-target")})
            timeout_cfg = cfg.get("timeout_s")
            plan: list[tuple[str, str, str]] = []    # (kind, project dir, command)
            if cfg.get("build") or cfg.get("test"):
                if cfg.get("build"):
                    plan.append(("build", ".", str(cfg["build"])))
                if cfg.get("test"):
                    plan.append(("test", ".", str(cfg["test"])))
            else:
                for rel, kind in projects:
                    cmds = commands_for(wt, rel, kind, src["source"] or src["repo_root"])
                    if kind == "node" and not (wt / rel / "node_modules").exists() and \
                            not (wt / "node_modules").exists():
                        notes.append(f"{rel}: packages aren't installed, so its build and tests were skipped")
                        continue
                    for k in ("build", "test"):
                        if cmds.get(k):
                            plan.append((k, rel, str(cmds[k])))
            for kind, rel, cmd in plan:
                limit = float(timeout_cfg or (self.build_timeout if kind == "build" else self.test_timeout))
                t0 = time.time()
                rc, out, timed_out = await run_shell(cmd, wt / rel, limit, env)
                check: dict[str, Any] = {"name": kind, "project": rel, "command": cmd, "ok": rc == 0 and not timed_out,
                                         "exit_code": rc, "timed_out": timed_out, "seconds": round(time.time() - t0, 1),
                                         "output_tail": _tail(out)}
                if kind == "test":
                    check.update(parse_tests(out))
                    result["failing_tests"] += [f"{rel}: {n}" if rel != "." else n for n in check["failing"]]
                result["checks"].append(check)
            want_shot = screenshot or cfg.get("screenshot") if (screenshot or claimed.get("ui") or
                                                                cfg.get("screenshot")) else None
            result["screenshot"] = await self._screenshot(want_shot, wt, run_id)
            result["notes"] = notes
            for extra in EXTRA_CHECKS:   # e.g. Package Reality Check (agent_safety.py)
                result.setdefault("safety", []).extend(await extra(src, base, changed, wt))
        except ProofError as exc:
            result.update({"verdict": "amber", "reason": str(exc), "error": True})
            return result
        except Exception as exc:
            log.exception("proof %s failed", run_id)
            result.update({"verdict": "amber", "reason": f"Orion couldn't finish the check: {exc}"[:300],
                           "error": True})
            return result
        finally:
            if wt is not None:
                await asyncio.to_thread(self._discard_copy, src, wt)
            shutil.rmtree(home, ignore_errors=True)
        self._last_hash[str(src["source"] or src["sha"])] = (dh, run_id)
        return self._judge(result, claimed, changed)

    # ------------------------------------------------------------ verdict
    def _judge(self, result: dict[str, Any], claimed: dict[str, Any], changed: list[str]) -> dict[str, Any]:
        problems: list[str] = []
        violations: list[tuple[str, str]] = []
        good: list[str] = []
        checks = result.get("checks") or []
        builds = [c for c in checks if c["name"] == "build"]
        tests = [c for c in checks if c["name"] == "test"]
        for b in builds:
            where = "" if b["project"] == "." else f" in {b['project']}"
            if b["timed_out"]:
                problems.append(f"The build{where} took longer than {int(b['seconds'] // 60) or 1} min and was stopped")
            elif not b["ok"]:
                if claimed.get("build_ok"):
                    problems.append(f"Said it builds; the build{where} fails")
                    violations.append(("proof.build-claim", f"Claimed the build works; it fails{where}."))
                else:
                    problems.append(f"The build{where} fails")
        if builds and all(b["ok"] for b in builds):
            good.append("the build passes")
        failed_total = passed_total = 0
        for t in tests:
            where = "" if t["project"] == "." else f" in {t['project']}"
            failed = t.get("failed")
            passed_total += t.get("passed") or 0
            if t["timed_out"]:
                problems.append(f"The tests{where} took too long and were stopped")
            elif not t["ok"]:
                n = failed if failed else None
                failed_total += n or 0
                what = f"{n} fail" if n else "they fail"
                if claimed.get("tests_pass"):
                    problems.append(f"Said tests pass; {what}{where}")
                    violations.append(("proof.tests-pass-claim",
                                       f"Claimed tests pass; {what}{where}. "
                                       + ", ".join(result.get("failing_tests", [])[:5])))
                else:
                    problems.append(f"Tests fail{where}" + (f" ({n})" if n else ""))
        if tests and all(t["ok"] for t in tests):
            good.append(f"{passed_total} tests pass" if passed_total else "the tests pass")
        if not tests and claimed.get("tests_pass") and not result.get("error"):
            problems.append("Said tests pass, but Orion found no tests to run")
        changed_names = {Path(c).name for c in changed} | set(changed)
        missing = [f for f in claimed.get("files") or [] if f not in changed_names and Path(f).name not in changed_names]
        if missing:
            shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
            problems.append(f"Said it changed {shown}, but {'it has' if len(missing) == 1 else 'they have'} no changes")
            violations.append(("proof.files-changed-claim", f"Claimed changes to {shown}; git shows none."))
        elif claimed.get("files"):
            good.append(f"the {len(claimed['files'])} file{'s' if len(claimed['files']) != 1 else ''} it named really changed")
        problems += list(result.get("safety") or [])   # Package Reality Check and other extra checks
        shot = result.get("screenshot") or {}
        if shot and not shot.get("skipped") and not shot.get("ok"):
            problems.append(f"The screenshot check failed: {shot.get('note')}")
        if result.get("error"):
            return result
        if problems:
            result["verdict"] = "amber"
            result["reason"] = "; ".join(problems[:3]) + "."
        else:
            result["verdict"] = "green"
            if not good:
                good.append("no build or tests were found, so only the changed files were checked")
            result["reason"] = "Proven: " + ", ".join(good) + "."
        result["violations"] = [{"rule_id": r, "evidence": e} for r, e in violations]
        result["failed_total"] = failed_total
        return result

    def _finish(self, result: dict[str, Any]) -> dict[str, Any]:
        result["finished_at"] = time.time()
        result["duration_s"] = round(result["finished_at"] - result.get("started_at", result["finished_at"]), 1)
        who = result.get("agent") or result.get("label") or "unknown agent"
        reported = []
        for v in result.get("violations") or []:
            if report_violation(v["rule_id"], who, v["evidence"]):
                reported.append(v["rule_id"])
        result["reported_to_rulebook"] = reported
        self.results.append(result)
        del self.results[:-MAX_RESULTS]
        self._save()
        if result.get("agent"):
            self.robots[result["agent"]] = {"phase": result["verdict"], "reason": result["reason"],
                                            "result_id": result["id"], "at": time.time()}
        return result

    async def announce(self, result: dict[str, Any]) -> None:
        if self.publish:
            try:
                await self.publish({"event": "proof.result", "id": result["id"], "agent": result.get("agent"),
                                    "label": result.get("label"), "repo": result.get("repo"),
                                    "branch": result.get("branch"), "verdict": result["verdict"],
                                    "reason": result["reason"], "failing_tests": result.get("failing_tests", [])[:10]})
            except Exception:
                log.debug("proof event not delivered", exc_info=True)

    async def prove_and_announce(self, **kw: Any) -> dict[str, Any]:
        result = await self.prove(**kw)
        await self.announce(result)
        return result

    # ------------------------------------------------------------ automatic: robots that say "done"
    def observe(self, agents: list[dict[str, Any]]) -> list[str]:
        """Called with each swarm snapshot. Starts a proof for agents that just finished with changed files."""
        started: list[str] = []
        live = {a["id"] for a in agents}
        for aid in list(self.robots):
            a = next((x for x in agents if x["id"] == aid), None)
            if a is None or (a["state"] != "done" and self.robots[aid]["phase"] != "checking"):
                self.robots.pop(aid, None)   # gone, or working again: the old verdict no longer applies
        for aid in list(self._done_seen):
            if aid not in live:
                self._done_seen.pop(aid, None)
        if not self.auto:
            return started
        for a in agents:
            if a.get("state") != "done" or not a.get("cwd") or not a.get("files"):
                continue
            marker = float(a.get("last_activity") or 0)
            if self._done_seen.get(a["id"]) == marker:
                continue
            self._done_seen[a["id"]] = marker
            self.robots[a["id"]] = {"phase": "checking", "reason": "Orion is running the build and tests",
                                    "at": time.time()}
            task = asyncio.get_running_loop().create_task(self.prove_and_announce(agent=dict(a), auto=True))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            started.append(a["id"])
        return started

    def decorate(self, snapshot: dict[str, Any]) -> None:
        """agent_swarm decorator: a finished robot only turns green once its work is proven."""
        for a in snapshot.get("agents", []):
            r = self.robots.get(a["id"])
            if not r:
                continue
            a["proof"] = {"verdict": r["phase"], "reason": r["reason"], "id": r.get("result_id")}
            if a["state"] not in ("done", "waiting") and r["phase"] != "checking":
                continue
            if r["phase"] == "checking":
                a.update(state="waiting", status="Checking its work", reason=r["reason"])
            elif r["phase"] == "amber":
                a.update(state="failed", status="Not proven", reason=r["reason"])
            else:
                a.update(state="done", status="Proven", reason=r["reason"])

    async def settle(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
