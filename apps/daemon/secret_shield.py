"""Secret Shield (toolkit tool 18): no keys in the public repo.

What it does
  * Every commit is scanned before it is made: `.githooks/pre-commit` (installed by
    `node scripts/secret-shield/install-hooks.mjs`, which `npm install` and "Publish to GitHub.cmd" run) uses
    gitleaks with the repo's `.gitleaks.toml`, or the built-in scanner when gitleaks isn't installed.
  * The whole history can be scanned once: `node scripts/secret-shield/scan-history.mjs`.
  * CI runs gitleaks on every push and pull request (.github/workflows/ci.yml).
  * This module answers "Orion, is anything secret in my repos?": it scans the current files and the recent
    history (or the full history) of the repos Orion may use, and reports in plain words with every secret
    replaced by [redacted]. The secret value itself is never returned, logged or stored; findings carry a short
    one-way fingerprint so the same leak isn't reported twice.
  * On findings it posts a phone alert and a Queue card with the rotation steps.

Patterns live in secret_shield_rules.json, shared with the git hook's fallback scanner
(scripts/secret-shield/scanner.mjs) so both catch the same things. Files that hold keys by design
(.env, .orion-signing/) are never opened: if git tracks one, that alone is reported.

Routes (local only): POST /secret-shield/scan, GET /secret-shield/last, GET /secret-shield/status,
GET /secret-shield/guide/{kind}. MCP tool: secret_shield_scan (read-only).
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger("orion.secret_shield")

RULES_PATH = Path(__file__).resolve().with_name("secret_shield_rules.json")
REDACTED = "[redacted]"
QUICK_COMMITS = 300
MAX_FILE_BYTES = 1_000_000
MAX_FINDINGS = 200
HISTORY_MODES = ("none", "quick", "full")

# Files that hold keys by design. They are never opened; tracking one in git is itself a finding.
KEY_FILE_RE = re.compile(
    r"(^|/)(\.env(\.(?!example$|sample$|template$)[\w.-]+)?|\.orion-signing/.*|id_(rsa|dsa|ecdsa|ed25519)|"
    r"[^/]+\.(key|p12|pfx|keystore|jks))$", re.I)


# =====================================================================================  rules
@dataclass
class Rule:
    id: str
    kind: str
    label: str
    regex: re.Pattern
    group: int = 0
    entropy: float = 0.0
    needs_digit_and_letter: bool = False


@dataclass
class Rules:
    rules: list[Rule]
    allow: list[tuple[Optional[re.Pattern], Optional[re.Pattern]]]
    skip_paths: list[re.Pattern]
    placeholders: set[str]
    marker: str
    guides: dict[str, list[str]]


def _flags(spec: str) -> int:
    return re.I if "i" in (spec or "") else 0


_RULES_CACHE: dict[str, Any] = {}


def load_rules(path: Optional[Path] = None) -> Rules:
    path = Path(path or RULES_PATH)
    mtime = path.stat().st_mtime
    hit = _RULES_CACHE.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = [Rule(r["id"], r["kind"], r["label"], re.compile(r["regex"], _flags(r.get("flags", ""))),
                  int(r.get("group", 0)), float(r.get("entropy", 0)), bool(r.get("needsDigitAndLetter")))
             for r in data["rules"]]
    allow = [(re.compile(a["path"]) if a.get("path") else None,
              re.compile(a["secret"], _flags(a.get("flags", ""))) if a.get("secret") else None)
             for a in data.get("allow", [])]
    out = Rules(rules, allow, [re.compile(p) for p in data.get("skipPaths", [])],
                {p.lower() for p in data.get("placeholders", [])}, data.get("inlineAllowMarker", "gitleaks:allow"),
                {k: list(v) for k, v in data.get("guides", {}).items()})
    _RULES_CACHE[str(path)] = (mtime, out)
    return out


def shannon(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def fingerprint(rule_id: str, secret: str) -> str:
    """A short one-way tag so the same leak isn't reported twice. It can't be turned back into the secret."""
    return hashlib.sha256(f"orion-secret-shield:{rule_id}:{secret}".encode()).hexdigest()[:12]


def _placeholder(secret: str, rules: Rules) -> bool:
    s = secret.strip().strip("\"'")
    low = s.lower()
    if low in rules.placeholders or re.fullmatch(r"(x+|\*+|\.+|_+|-+|0+)", low):
        return True
    if re.fullmatch(r"\{[^{}]*\}|%s|%\([^)]*\)s", s):   # template fields: {pw}, %s, %(pw)s
        return True
    return any(t in s for t in ("${", "{{", "%(", "<", ">", "$(")) or low.startswith(("process.env", "os.environ"))


def skip_path(path: str, rules: Rules) -> bool:
    p = path.replace("\\", "/")
    return any(r.search(p) for r in rules.skip_paths)


def is_key_file(path: str) -> bool:
    return bool(KEY_FILE_RE.search(path.replace("\\", "/")))


def redact_spans(line: str, spans: list[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        line = line[:a] + REDACTED + line[b:]
    return line


def preview(line: str, spans: list[tuple[int, int]]) -> str:
    """The line with the secrets replaced, plus a second pass of Orion's general redaction for anything else."""
    text = redact_spans(line, spans).strip()
    try:
        from mcp_tools import redact
        text = redact(text)
    except Exception:
        pass
    return text if len(text) <= 160 else text[:157] + "..."


def scan_line(line: str, path: str, line_no: int, rules: Rules) -> list[dict[str, Any]]:
    """Findings on one line. Never includes the secret: only where it is, what kind, and a redacted preview."""
    if rules.marker and rules.marker in line:
        return []
    if len(line) > 4000:          # minified bundles: check only the start
        line = line[:4000]
    norm = path.replace("\\", "/")
    found: list[tuple[Rule, str, tuple[int, int]]] = []
    taken: list[tuple[int, int]] = []
    for rule in rules.rules:
        for m in rule.regex.finditer(line):
            g = rule.group if rule.group <= (m.re.groups or 0) else 0
            secret = m.group(g)
            if not secret:
                continue
            span = m.span(g)
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue            # an earlier, more specific rule already covers it
            if _placeholder(secret, rules):
                continue
            if rule.entropy and shannon(secret) < rule.entropy:
                continue
            if rule.needs_digit_and_letter and not (re.search(r"\d", secret) and re.search(r"[A-Za-z]", secret)):
                continue
            if any((pr is None or pr.search(norm)) and (sr is None or sr.search(secret)) for pr, sr in rules.allow):
                continue
            taken.append(span)
            found.append((rule, secret, span))
    if not found:
        return []
    prev = preview(line, [s for _, _, s in found])
    return [{"rule": r.id, "kind": r.kind, "what": r.label, "file": norm, "line": line_no, "preview": prev,
             "fingerprint": fingerprint(r.id, secret)} for r, secret, _ in found]


def scan_text(text: str, path: str, rules: Optional[Rules] = None) -> list[dict[str, Any]]:
    rules = rules or load_rules()
    if skip_path(path, rules):
        return []
    out: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        out += scan_line(line, path, i, rules)
    return out


def key_file_finding(path: str, where: str, commit: Optional[str] = None) -> dict[str, Any]:
    return {"rule": "key-file-tracked", "kind": "key_file", "what": "a file that holds keys (it should never be in git)",
            "file": path.replace("\\", "/"), "line": 0, "preview": "(file not opened)", "where": where,
            "commit": commit, "fingerprint": fingerprint("key-file", path)}


# =====================================================================================  git
def _git(repo: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    try:
        res = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], capture_output=True,
                             timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return res.returncode, res.stdout.decode("utf-8", "replace")


def is_git_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()


def scan_working_tree(repo: Path, rules: Optional[Rules] = None) -> list[dict[str, Any]]:
    """Files git tracks or would add (ignored files such as .env are left alone)."""
    rules = rules or load_rules()
    repo = Path(repo)
    code, out = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if code != 0:
        raise RuntimeError(f"{repo} isn't a git repo (or git failed).")
    findings: list[dict[str, Any]] = []
    tracked = set(_git(repo, "ls-files", "-z")[1].split("\0"))
    for rel in sorted({p for p in out.split("\0") if p}):
        if is_key_file(rel):
            findings.append(key_file_finding(rel, "current files" if rel in tracked else "not ignored"))
            continue
        if skip_path(rel, rules):
            continue
        f = repo / rel
        try:
            if not f.is_file() or f.is_symlink() or f.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = f.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue                # binary
        for item in scan_text(raw.decode("utf-8", "replace"), rel, rules):
            item["where"] = "current files"
            item["commit"] = None
            findings.append(item)
        if len(findings) >= MAX_FINDINGS:
            break
    return findings


def scan_history(repo: Path, rules: Optional[Rules] = None, max_commits: Optional[int] = QUICK_COMMITS
                 ) -> tuple[list[dict[str, Any]], int]:
    """Lines added in past commits (all branches). Returns (findings, commits looked at)."""
    rules = rules or load_rules()
    args = ["log", "--all", "-p", "-U0", "--no-color", "--no-ext-diff", "--no-renames", "--format=%x00commit %H"]
    if max_commits:
        args.insert(1, f"-n{int(max_commits)}")
    code, out = _git(Path(repo), *args, timeout=600)
    if code != 0:
        return [], 0
    findings: dict[tuple[str, str], dict[str, Any]] = {}
    commit, path, new_line, commits = "", "", 0, 0
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in out.splitlines():
        if line.startswith("\0commit "):
            commit, path = line[8:].strip(), ""
            commits += 1
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
            if path and is_key_file(path):
                fp = key_file_finding(path, "history", commit[:10])
                findings.setdefault((fp["fingerprint"], path), fp)
                path = ""
            elif path and skip_path(path, rules):
                path = ""
        elif line.startswith("@@"):
            m = hunk.match(line)
            new_line = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            for item in scan_line(line[1:], path, new_line, rules):
                item["where"] = "history"
                item["commit"] = commit[:10]
                findings[(item["fingerprint"], path)] = item   # git log is newest first: keep the oldest
            new_line += 1
        if len(findings) >= MAX_FINDINGS:
            break
    return list(findings.values()), commits


# =====================================================================================  gitleaks (optional)
GITLEAKS_KINDS = (("anthropic", "anthropic"), ("openai", "openai"), ("github", "github"), ("gitlab", "gitlab"),
                  ("discord", "discord"), ("telegram", "telegram"), ("slack", "slack"), ("stripe", "stripe"),
                  ("aws", "aws"), ("gcp", "google"), ("google", "google"), ("huggingface", "huggingface"),
                  ("npm", "npm"), ("private-key", "private_key"))


def gitleaks_path() -> Optional[str]:
    env = os.getenv("ORION_GITLEAKS", "").strip()
    if env:
        return env if Path(env).exists() else None
    return shutil.which("gitleaks")


def _gitleaks_kind(rule_id: str) -> str:
    rid = rule_id.lower()
    return next((k for p, k in GITLEAKS_KINDS if p in rid), "generic")


def gitleaks_history(repo: Path, max_commits: Optional[int], exe: str) -> tuple[list[dict[str, Any]], bool]:
    """(findings, ran). Uses --redact so the secret never reaches us."""
    repo = Path(repo)
    with tempfile.TemporaryDirectory(prefix="orion-shield-") as tmp:
        report = Path(tmp) / "report.json"
        argv = [exe, "git", str(repo), "--no-banner", "--redact", "--exit-code", "0", "--report-format", "json",
                "--report-path", str(report), "--log-level", "error"]
        if (repo / ".gitleaks.toml").exists():
            argv += ["--config", str(repo / ".gitleaks.toml")]
        opts = "--all" + (f" -n {int(max_commits)}" if max_commits else "")
        argv += ["--log-opts", opts]
        try:
            res = subprocess.run(argv, capture_output=True, timeout=900, cwd=str(repo))
        except (OSError, subprocess.TimeoutExpired):
            return [], False
        if res.returncode not in (0, 1) or not report.exists():
            return [], False
        try:
            rows = json.loads(report.read_text(encoding="utf-8") or "[]") or []
        except ValueError:
            return [], False
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        rid = str(r.get("RuleID", "secret"))
        path = str(r.get("File", "")).replace("\\", "/")
        kind = _gitleaks_kind(rid)
        prev = str(r.get("Match", "")).replace("REDACTED", REDACTED)
        try:
            from mcp_tools import redact
            prev = redact(prev)
        except Exception:
            pass
        what = next((x.label for x in load_rules().rules if x.kind == kind), "") if kind != "generic" else ""
        item = {"rule": rid, "kind": kind, "what": what or f"a secret ({rid.replace('-', ' ')})", "file": path,
                "line": int(r.get("StartLine") or 0), "preview": prev[:160], "where": "history",
                "commit": str(r.get("Commit", ""))[:10] or None,
                "fingerprint": hashlib.sha256(f"{rid}:{path}:{r.get('Fingerprint', '')}".encode()).hexdigest()[:12]}
        out.setdefault((rid, path + str(item["line"])), item)
    return list(out.values()), True


# =====================================================================================  reports
def _where(f: dict[str, Any]) -> str:
    loc = f"{f['file']} line {f['line']}" if f.get("line") else f["file"]
    if f.get("where") == "history":
        return f"{loc} (in past commit {f.get('commit')})"
    if f.get("where") == "staged":
        return f"{loc} (about to be committed)"
    if f.get("where") == "not ignored":
        return f"{loc} (not in .gitignore, so 'git add -A' would commit it)"
    return f"{loc} (in the current files)"


def guides_for(kinds: Iterable[str], rules: Optional[Rules] = None) -> dict[str, list[str]]:
    rules = rules or load_rules()
    out: dict[str, list[str]] = {}
    for k in kinds:
        if k == "key_file":
            out[k] = ["Stop tracking the file: git rm --cached <file> (this keeps your copy) and make sure .gitignore "
                      "lists it.", "If the repo was ever pushed, treat every key in that file as leaked and rotate "
                      "each one using the steps for its service."]
        else:
            out[k] = rules.guides.get(k) or rules.guides["generic"]
    return out


def plain_report(repos: list[dict[str, Any]], findings: list[dict[str, Any]], history: str,
                 rules: Optional[Rules] = None) -> str:
    rules = rules or load_rules()
    looked = {"none": "the current files", "quick": f"the current files and the last {QUICK_COMMITS} commits",
              "full": "the current files and the whole history"}[history]
    names = ", ".join(r["name"] for r in repos) or "no repos"
    if not findings:
        return f"Checked {names} ({looked}). Nothing secret found."
    lines = [f"Found {len(findings)} possible secret{'s' if len(findings) != 1 else ''} in {names} ({looked}). "
             "The values are hidden here; only where they are is shown.", ""]
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        by_repo.setdefault(f.get("repo", ""), []).append(f)
    for repo, items in by_repo.items():
        if repo:
            lines.append(f"{repo}:")
        for f in items[:60]:
            lines.append(f"  - {f['what']} at {_where(f)}: {f['preview']}")
        if len(items) > 60:
            lines.append(f"  ... and {len(items) - 60} more")
    lines += ["", "What to do: anything that was ever pushed to GitHub counts as leaked, so rotate it "
                  "(make a new one and kill the old one). Steps:"]
    for kind, steps in guides_for(sorted({f["kind"] for f in findings}), rules).items():
        label = next((f["what"] for f in findings if f["kind"] == kind), kind)
        lines.append(f"\n{label[0].upper() + label[1:]}:")
        lines += [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
    lines.append("\nThen:")
    lines += [f"  - {s}" for s in rules.guides.get("_after", [])]
    lines.append("If a finding is a deliberate fake (for example in a test), add the comment gitleaks:allow at the end "
                 "of that line.")
    return "\n".join(lines)


def scan_repos(repos: list[Path], history: str = "quick", engine: str = "auto",
               rules: Optional[Rules] = None) -> dict[str, Any]:
    """Blocking scan of several repos. engine: auto (gitleaks for history when installed) | builtin."""
    if history not in HISTORY_MODES:
        raise ValueError("history must be none, quick or full")
    rules = rules or load_rules()
    exe = gitleaks_path() if engine == "auto" else None
    started = time.time()
    findings: list[dict[str, Any]] = []
    scanned: list[dict[str, Any]] = []
    for repo in repos:
        repo = Path(repo)
        info: dict[str, Any] = {"name": repo.name, "path": str(repo), "history_engine": None, "commits": 0}
        items = scan_working_tree(repo, rules)
        if history != "none":
            limit = QUICK_COMMITS if history == "quick" else None
            ran = False
            if exe:
                hist, ran = gitleaks_history(repo, limit, exe)
                info["history_engine"] = "gitleaks" if ran else None
            if not ran:
                hist, info["commits"] = scan_history(repo, rules, limit)
                info["history_engine"] = "built-in"
            seen = {(f["file"], f["fingerprint"]) for f in items}
            items += [h for h in hist if (h["file"], h["fingerprint"]) not in seen]
        for f in items:
            f["repo"] = repo.name
        info["findings"] = len(items)
        scanned.append(info)
        findings += items
    findings = findings[:MAX_FINDINGS]
    return {"ok": not findings, "at": round(started, 3), "seconds": round(time.time() - started, 2),
            "history": history, "repos": scanned, "findings": findings,
            "summary": plain_report(scanned, findings, history, rules),
            "guides": guides_for(sorted({f["kind"] for f in findings}), rules),
            "gitleaks": bool(exe)}


def default_repos(roots: Iterable[Path]) -> list[Path]:
    """Allowed roots that are git repos, plus git repos one level inside a root folder (e.g. ~/code/*)."""
    out: list[Path] = []
    for root in roots:
        root = Path(root)
        if is_git_repo(root):
            out.append(root)
            continue
        try:
            out += sorted(p for p in root.iterdir() if p.is_dir() and is_git_repo(p))
        except OSError:
            pass
    seen: set[str] = set()
    return [p for p in out if not (str(p) in seen or seen.add(str(p)))]


def hooks_installed(repo: Path) -> bool:
    code, out = _git(Path(repo), "config", "--get", "core.hooksPath", timeout=10)
    return code == 0 and out.strip().rstrip("/") in (".githooks", str(Path(repo) / ".githooks"))


# =====================================================================================  service (daemon)
class SecretShield:
    """Holds the last report and raises one alert + Queue card per new set of findings."""

    CARD_KIND = "secret_shield"

    def __init__(self, data_dir: Path, daemon: Any = None) -> None:
        self.path = Path(data_dir) / "secret_shield.json"
        self.daemon = daemon
        self.state: dict[str, Any] = {"last": None, "notified": []}
        try:
            if self.path.exists():
                self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self._lock = asyncio.Lock()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Could not save Secret Shield state: %s", exc)

    async def scan(self, repos: list[Path], history: str = "quick", engine: str = "auto") -> dict[str, Any]:
        async with self._lock:
            report = await asyncio.to_thread(scan_repos, repos, history, engine)
            self.state["last"] = report
            new = sorted({f["fingerprint"] for f in report["findings"]} - set(self.state.get("notified", [])))
            if new:
                self.state["notified"] = (list(self.state.get("notified", [])) + new)[-2000:]
                await self._notify(report, len(new))
            self._save()
            return report

    def last(self) -> Optional[dict[str, Any]]:
        return self.state.get("last")

    async def _notify(self, report: dict[str, Any], new: int) -> None:
        n = len(report["findings"])
        title = f"Secret Shield found {n} possible secret{'s' if n != 1 else ''}"
        first = report["findings"][0]
        body = f"{first['what']} in {first.get('repo', '')}: {_where(first)}. Open Orion for the rotation steps."
        try:
            import alerts
            alerts.emit("system", title, body, ref="secret-shield")
        except Exception:
            pass
        d = self.daemon
        if d is None or not hasattr(d, "approvals"):
            return
        try:
            item = d.approvals.add(self.CARD_KIND, platform="Secret Shield", channel="Secret Shield",
                                   author="Secret Shield", category="urgent", title=title,
                                   original=report["summary"][:4000],
                                   draft="Approve = I've rotated these. Reject = dismiss (for example a deliberate "
                                         "fake). Either way the same findings won't raise a new card.",
                                   findings=[{k: f.get(k) for k in ("repo", "what", "file", "line", "where", "commit")}
                                             for f in report["findings"][:50]])
            await d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
            await d.bus.log(f"{title} ({new} new). See the Queue.", "warn")
        except Exception:
            log.exception("Could not post the Secret Shield card")


async def decide_card(daemon: Any, item: dict[str, Any], decision: str) -> None:
    """Queue card decision: approve = handled (rotated), reject = dismissed. Nothing else happens."""
    status = "rejected" if decision == "reject" else "handled"
    updated = daemon.approvals.update(item["id"], status=status)
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})


# =====================================================================================  routes
def _allowed_repo_paths(raw: Optional[list[Any]]) -> list[Path]:
    from mcp_tools import McpConfig, ToolError, resolve_project
    cfg = McpConfig.load()
    if not raw:
        return default_repos(cfg.resolved_roots())
    out = []
    for r in raw:
        p = resolve_project(str(r), cfg)            # same folder rules as every MCP tool
        if not is_git_repo(p):
            raise ToolError(f"{r} isn't a git repo.")
        out.append(p)
    return out


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
    from mcp_tools import ToolError

    router = APIRouter(prefix="/secret-shield", tags=["toolkit"])

    def shield() -> SecretShield:
        d = get_daemon()
        s = getattr(d, "secret_shield", None)
        if s is None:
            from config import settings
            s = SecretShield(settings.data_dir, d)
            try:
                d.secret_shield = s
            except Exception:
                pass
        return s

    @router.post("/scan")
    async def scan(request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        history = str(body.get("history") or "quick")
        if history not in HISTORY_MODES:
            return JSONResponse({"error": "history must be none, quick or full"}, status_code=400)
        repos = body.get("repos")
        if repos is not None and not (isinstance(repos, list) and all(isinstance(r, str) for r in repos)):
            return JSONResponse({"error": "repos must be a list of folder paths"}, status_code=400)
        try:
            paths = _allowed_repo_paths(repos)
        except ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not paths:
            return JSONResponse({"error": "No git repos found in the folders Orion may use."}, status_code=404)
        engine = "builtin" if body.get("engine") == "builtin" else "auto"
        return await shield().scan(paths, history, engine)

    @router.get("/last")
    async def last() -> Any:
        return {"report": shield().last()}

    @router.get("/status")
    async def status() -> Any:
        from config import settings
        return {"gitleaks": gitleaks_path(), "trufflehog": shutil.which("trufflehog"),
                "hooks_installed": hooks_installed(settings.repo_root),
                "push_protection": "Turn on in GitHub: repo Settings > Code security > Push protection."}

    @router.get("/guide/{kind}")
    async def guide(kind: str) -> Any:
        rules = load_rules()
        if kind != "key_file" and kind not in rules.guides:
            return JSONResponse({"error": "Unknown secret type.", "types": sorted(k for k in rules.guides
                                                                                 if not k.startswith("_"))},
                                status_code=404)
        return {"kind": kind, "steps": guides_for([kind], rules)[kind], "after": rules.guides.get("_after", [])}

    return router


# =====================================================================================  MCP tool
def mcp_tools(tools: Any) -> list[Any]:
    """Tool definitions for mcp_tools.OrionTools (imported lazily there)."""
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema, resolve_project

    async def secret_shield_scan(a: dict[str, Any]) -> str:
        history = a.get("history") or "quick"
        if history not in HISTORY_MODES:
            raise ToolError("history must be none, quick or full.")
        raw = a.get("repos") or []
        paths = [resolve_project(r, tools.cfg) for r in raw] if raw else default_repos(tools.cfg.resolved_roots())
        if not paths:
            raise ToolError("No git repos found in the folders Orion lets agents use.")
        for p in paths:
            if not is_git_repo(p):
                raise ToolError(f"{p} isn't a git repo.")
        note = ""
        try:
            status, data = await tools.backend.call("POST", "/secret-shield/scan",
                                                    {"repos": [str(p) for p in paths], "history": history})
            if status >= 400:
                raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
            report = data
        except DaemonUnavailable:
            report = await asyncio.to_thread(scan_repos, paths, history)
            note = "\n\n(Orion isn't running, so this was checked here and no alert was raised.)"
        return tools.out(report["summary"] + note)

    return [Tool("secret_shield_scan", "Is anything secret in my repos?",
                 "Scan repos for passwords, tokens and keys: the current files plus recent history ('quick', the "
                 "default), the whole history ('full') or the current files only ('none'). Answers in plain words "
                 "with every secret hidden, and the steps to rotate anything found. Findings also reach the user "
                 "as an alert.", "read",
                 _schema({"repos": {"type": "array", "items": {"type": "string"},
                                    "description": "Repo folders (default: every repo Orion lets agents use)"},
                          "history": {"type": "string", "enum": list(HISTORY_MODES),
                                      "description": "none | quick (last 300 commits) | full"}}),
                 secret_shield_scan)]
