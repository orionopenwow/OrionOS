"""Perf Sentinel (Orion Toolkit tool 13): notice when things get slower.

Each project lists its benchmarks in .orion/perf.json:

    {"benches": [
        {"name": "daemon start", "command": "python -c 'import main'", "runs": 5, "warmup": 1},
        {"name": "HUD build size", "size": "apps/desktop/dist"},
        {"name": "world tick", "command": "npm run bench:tick", "runs": 10},
        {"name": "criterion", "cargo_bench": true, "cwd": "server"}],
     "when": ["nightly"],            # any of "commit", "proof", "nightly" (default: nightly only)
     "threshold_pct": 10}

  * command benches run with hyperfine (--export-json) when it's installed; without it Orion times the command
    itself and says so;
  * size benches add up the bytes under a path (smaller is better, like times);
  * cargo_bench runs `cargo bench --message-format json` and reads Criterion's results (the cargo-criterion
    "benchmark-complete" lines, or target/criterion/*/new/estimates.json).

Results go in data_dir/perf_sentinel.db with the commit they ran on, so trends survive restarts. A result more
than threshold_pct (default 10%) worse than the usual value (the median of the previous five) becomes a card that
names the commit range: "Daemon start got 18% slower (1.20 s -> 1.42 s) somewhere in abc1234..def5678 (3 commits)".

"Orion, is the server faster than last week?" compares the newest value with the values from about a week ago.
Benchmarks are run only for projects you set up (a .orion/perf.json), never on their own in someone else's repo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from collision_guard import git, toplevel

log = logging.getLogger("orion.perf")

DEFAULT_THRESHOLD = 10.0
BASELINE_RUNS = 5
BENCH_TIMEOUT_S = 900
COMMIT_DEBOUNCE_S = 600
NIGHTLY_HOUR = 3
WEEK_S = 7 * 86400
UNIT_SCALE = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0}

# (returncode, output) for a command run in a folder. Tests swap this out.
Exec = Callable[[list[str], Path, float], Awaitable[tuple[int, str]]]


async def _exec(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")


def load_config(repo: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads((Path(repo) / ".orion" / "perf.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("benches"), list):
        return None
    when = data.get("when") or ["nightly"]
    data["when"] = [w for w in (when if isinstance(when, list) else [when]) if w in ("commit", "proof", "nightly")]
    data["threshold_pct"] = float(data.get("threshold_pct") or DEFAULT_THRESHOLD)
    data["benches"] = [b for b in data["benches"] if isinstance(b, dict) and b.get("name")]
    return data


# =====================================================================================  result parsers
def parse_hyperfine(raw: str) -> Optional[dict[str, float]]:
    """hyperfine --export-json: {"results": [{"mean": s, "stddev": s, "median": s, ...}]}"""
    try:
        data = json.loads(raw)
        r = data["results"][0]
        return {"value": float(r["mean"]), "stddev": float(r.get("stddev") or 0.0), "unit": "s"}
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def parse_criterion_lines(out: str) -> dict[str, dict[str, float]]:
    """cargo-criterion style JSON lines: {"reason": "benchmark-complete", "id": ..., "typical": {...}}"""
    found: dict[str, dict[str, float]] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("reason") != "benchmark-complete":
            continue
        est = row.get("typical") or row.get("mean") or {}
        try:
            scale = UNIT_SCALE.get(str(est.get("unit") or "ns"), 1e-9)
            found[str(row.get("id"))] = {"value": float(est["estimate"]) * scale, "stddev": 0.0, "unit": "s"}
        except (KeyError, TypeError, ValueError):
            continue
    return found


def read_criterion_dir(target: Path, since: float) -> dict[str, dict[str, float]]:
    """target/criterion/<group>/<bench>/new/estimates.json written after `since` (mean in ns)."""
    found: dict[str, dict[str, float]] = {}
    root = target / "criterion"
    if not root.is_dir():
        return found
    for est in root.rglob("new/estimates.json"):
        try:
            if est.stat().st_mtime < since - 1:
                continue
            data = json.loads(est.read_text(encoding="utf-8"))
            mean = data["mean"]["point_estimate"]
            sd = (data.get("std_dev") or {}).get("point_estimate") or 0.0
        except (OSError, ValueError, KeyError, TypeError):
            continue
        name = est.parent.parent.relative_to(root).as_posix()
        found[name] = {"value": float(mean) * 1e-9, "stddev": float(sd) * 1e-9, "unit": "s"}
    return found


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def show(value: float, unit: str) -> str:
    if unit == "bytes":
        for name, size in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
            if value >= size:
                return f"{value / size:.2f} {name}"
        return f"{int(value)} bytes"
    if value >= 1:
        return f"{value:.2f} s"
    if value >= 1e-3:
        return f"{value * 1e3:.1f} ms"
    if value >= 1e-6:
        return f"{value * 1e6:.1f} µs"
    return f"{value * 1e9:.0f} ns"


# =====================================================================================  the sentinel
class PerfSentinel:
    def __init__(self, data_dir: Path, exec_fn: Exec = _exec, card: Optional[Callable[[dict[str, Any]], Any]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.exec = exec_fn
        self.card = card              # called with a regression dict (alert + approval-queue card in the daemon)
        self.clock = clock
        self.lock = threading.RLock()
        self._busy: set[str] = set()
        self._last_commit_run: dict[str, float] = {}
        self._last_nightly: dict[str, str] = {}
        self.db = sqlite3.connect(str(self.dir / "perf_sentinel.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS results(id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT, project TEXT,
                bench TEXT, value REAL, stddev REAL, unit TEXT, sha TEXT, ts REAL, trigger TEXT, note TEXT);
            CREATE INDEX IF NOT EXISTS results_bench ON results(repo, bench, ts);
            CREATE TABLE IF NOT EXISTS regressions(id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT, bench TEXT,
                ts REAL, before REAL, after REAL, pct REAL, good_sha TEXT, bad_sha TEXT, text TEXT);
        """)
        self.db.commit()

    # ------------------------------------------------------------ storage
    def add_result(self, repo: str, project: str, bench: str, value: float, unit: str, sha: str,
                   trigger: str = "manual", stddev: float = 0.0, note: str = "", ts: Optional[float] = None) -> None:
        with self.lock:
            self.db.execute("INSERT INTO results(repo, project, bench, value, stddev, unit, sha, ts, trigger, note) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (repo, project, bench, float(value), float(stddev), unit, sha, ts or self.clock(),
                             trigger, note))
            self.db.commit()

    def history(self, repo: str, bench: str, limit: int = 60) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM results WHERE repo=? AND bench=? ORDER BY ts DESC LIMIT ?",
                                   (repo, bench, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def benches(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT repo, project, bench, unit, COUNT(*) n, MAX(ts) last FROM results "
                                   "GROUP BY repo, bench ORDER BY last DESC").fetchall()
        return [dict(r) for r in rows]

    def regressions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM regressions ORDER BY ts DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------ running
    async def _one(self, top: Path, b: dict[str, Any]) -> list[tuple[str, dict[str, float], str]]:
        """[(bench name, {value, stddev, unit}, note)] for one entry of perf.json."""
        cwd = (top / str(b.get("cwd") or ".")).resolve()
        if top not in cwd.parents and cwd != top:
            return []
        name = str(b["name"])[:80]
        if b.get("size"):
            p = (top / str(b["size"])).resolve()
            if (top not in p.parents and p != top) or not p.exists():
                return []
            return [(name, {"value": float(dir_size(p)), "stddev": 0.0, "unit": "bytes"}, "")]
        if b.get("cargo_bench"):
            started = time.time()
            code, out = await self.exec(["cargo", "bench", "--message-format", "json"], cwd, BENCH_TIMEOUT_S)
            found = parse_criterion_lines(out)
            if not found:
                target = Path(os.environ.get("CARGO_TARGET_DIR") or cwd / "target")
                found = read_criterion_dir(target, started)
            return [(f"{name}: {k}", v, "") for k, v in sorted(found.items())]
        command = str(b.get("command") or "").strip()
        if not command:
            return []
        runs = max(1, min(int(b.get("runs") or 5), 50))
        warmup = max(0, min(int(b.get("warmup") or 1), 10))
        if shutil.which("hyperfine"):
            with tempfile.TemporaryDirectory(prefix="orion-perf-") as tmp:
                out_json = Path(tmp) / "result.json"
                code, out = await self.exec(["hyperfine", "--style", "none", "--runs", str(runs), "--warmup",
                                             str(warmup), "--export-json", str(out_json), command], cwd,
                                            BENCH_TIMEOUT_S)
                parsed = parse_hyperfine(out_json.read_text(encoding="utf-8")) if out_json.is_file() else None
            if code != 0 or parsed is None:
                return []
            return [(name, parsed, "")]
        times = []
        for i in range(warmup + runs):
            t0 = time.perf_counter()
            code, _out = await self.exec(["sh", "-c", command], cwd, BENCH_TIMEOUT_S)
            if code != 0:
                return []
            if i >= warmup:
                times.append(time.perf_counter() - t0)
        return [(name, {"value": statistics.mean(times), "stddev": statistics.pstdev(times), "unit": "s"},
                 "timed by Orion (hyperfine isn't installed)")]

    async def run(self, repo: Any, trigger: str = "manual") -> dict[str, Any]:
        top = toplevel(repo)
        if top is None:
            raise ValueError(f"{repo or 'That folder'} isn't inside a git repository.")
        cfg = load_config(top)
        if cfg is None:
            raise ValueError(f"{top.name} has no .orion/perf.json, so there's nothing to measure yet.")
        if str(top) in self._busy:
            return {"repo": str(top), "skipped": True, "message": "Benchmarks are already running there."}
        self._busy.add(str(top))
        try:
            sha = git(top, "rev-parse", "HEAD")[1].strip()[:40]
            results, slower = [], []
            for b in cfg["benches"]:
                try:
                    measured = await self._one(top, b)
                except Exception as exc:
                    log.info("perf bench %s failed: %s", b.get("name"), exc)
                    measured = []
                if not measured:
                    results.append({"bench": b["name"], "ok": False})
                    continue
                for name, r, note in measured:
                    self.add_result(str(top), top.name, name, r["value"], r["unit"], sha, trigger, r["stddev"], note)
                    reg = self.check(str(top), name, cfg["threshold_pct"])
                    results.append({"bench": name, "ok": True, "value": r["value"], "unit": r["unit"],
                                    "shown": show(r["value"], r["unit"]), "note": note})
                    if reg:
                        slower.append(reg)
            return {"repo": str(top), "sha": sha[:12], "trigger": trigger, "results": results, "regressions": slower}
        finally:
            self._busy.discard(str(top))

    # ------------------------------------------------------------ regressions
    def check(self, repo: str, bench: str, threshold_pct: float = DEFAULT_THRESHOLD) -> Optional[dict[str, Any]]:
        hist = self.history(repo, bench, BASELINE_RUNS + 1)
        if len(hist) < 2:
            return None
        new, before = hist[-1], hist[:-1]
        base = statistics.median(r["value"] for r in before)
        if base <= 0:
            return None
        pct = (new["value"] - base) / base * 100.0
        if pct <= threshold_pct:
            return None
        good_sha = before[-1]["sha"] or ""
        bad_sha = new["sha"] or ""
        top = Path(repo)
        commits: list[str] = []
        if good_sha and bad_sha and good_sha != bad_sha:
            code, out = git(top, "log", "--format=%h %s", f"{good_sha}..{bad_sha}", "-n", "20")
            commits = [ln.strip() for ln in out.splitlines() if ln.strip()] if code == 0 else []
        span = (f"between {good_sha[:7]} and {bad_sha[:7]}" if good_sha and bad_sha and good_sha != bad_sha
                else f"at {bad_sha[:7]}" if bad_sha else "")
        count = f" ({len(commits)} commit{'s' if len(commits) != 1 else ''})" if commits else ""
        word = "bigger" if new["unit"] == "bytes" else "slower"
        text = (f"{bench} in {top.name} got {pct:.0f}% {word} ({show(base, new['unit'])} -> "
                f"{show(new['value'], new['unit'])}) {span}{count}.").replace("  ", " ")
        reg = {"repo": repo, "project": top.name, "bench": bench, "before": base, "after": new["value"],
               "pct": round(pct, 1), "good_sha": good_sha[:12], "bad_sha": bad_sha[:12], "commits": commits[:10],
               "text": text, "unit": new["unit"]}
        with self.lock:
            self.db.execute("INSERT INTO regressions(repo, bench, ts, before, after, pct, good_sha, bad_sha, text) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (repo, bench, self.clock(), base, new["value"], pct, good_sha, bad_sha, text))
            self.db.commit()
        if self.card is not None:
            try:
                res = self.card(reg)
                if asyncio.iscoroutine(res):
                    asyncio.get_running_loop().create_task(res)
            except Exception:
                log.exception("perf card failed")
        return reg

    # ------------------------------------------------------------ triggers
    def wants(self, repo: Any, trigger: str) -> bool:
        top = toplevel(repo)
        cfg = load_config(top) if top else None
        return bool(cfg) and trigger in cfg["when"]

    async def on_commit(self, repo: Any) -> Optional[dict[str, Any]]:
        top = toplevel(repo)
        if top is None or not self.wants(top, "commit"):
            return None
        now = self.clock()
        if now - self._last_commit_run.get(str(top), 0.0) < COMMIT_DEBOUNCE_S:
            return None
        self._last_commit_run[str(top)] = now
        return await self.run(top, "commit")

    async def on_proof(self, result: dict[str, Any]) -> Optional[dict[str, Any]]:
        repo = result.get("repo_path") or ""
        if result.get("verdict") != "green" or not repo or not self.wants(repo, "proof"):
            return None
        return await self.run(repo, "proof")

    async def nightly(self, repos: list[Any], hour: int = NIGHTLY_HOUR) -> list[dict[str, Any]]:
        lt = time.localtime(self.clock())
        if lt.tm_hour != hour:
            return []
        day = time.strftime("%Y-%m-%d", lt)
        done = []
        for repo in repos:
            top = toplevel(repo)
            if top is None or self._last_nightly.get(str(top)) == day or not self.wants(top, "nightly"):
                continue
            self._last_nightly[str(top)] = day
            try:
                done.append(await self.run(top, "nightly"))
            except ValueError:
                continue
        return done

    # ------------------------------------------------------------ "is the server faster than last week?"
    def compare(self, words_said: str, days: float = 7) -> str:
        said = set(re.findall(r"[a-z0-9]+", words_said.lower())) - {"the", "is", "a", "than", "faster", "slower"}
        benches = self.benches()
        if not benches:
            return "Perf Sentinel has no measurements yet. Add a .orion/perf.json to a project to start."
        picked = [b for b in benches if said & set(re.findall(r"[a-z0-9]+", f"{b['bench']} {b['project']}".lower()))]
        if not picked:
            alias = {"server": ("tick", "world", "wow", "openwow"), "orion": ("daemon",), "hud": ("desktop",)}
            for word, extra in alias.items():
                if word in said:
                    picked = [b for b in benches if any(e in f"{b['bench']} {b['project']}".lower() for e in extra)]
                    if picked:
                        break
        if not picked:
            names = ", ".join(sorted({b["bench"] for b in benches})[:6])
            return f"I don't have a benchmark that matches that. I measure: {names}."
        now = self.clock()
        lines = []
        for b in picked[:4]:
            hist = self.history(b["repo"], b["bench"], 400)
            latest = hist[-1]
            then = [r["value"] for r in hist if now - days * 86400 * 1.5 <= r["ts"] <= now - days * 86400 * 0.5]
            if not then:
                older = [r for r in hist if r["ts"] <= now - 86400]
                if not older:
                    lines.append(f"{b['bench']}: {show(latest['value'], latest['unit'])} now; no older numbers yet.")
                    continue
                then = [older[0]["value"]]
            base = statistics.median(then)
            if base <= 0:
                continue
            pct = (latest["value"] - base) / base * 100
            unit = latest["unit"]
            if abs(pct) < 3:
                verdict = "about the same as"
            elif pct < 0:
                verdict = f"{abs(pct):.0f}% {'smaller' if unit == 'bytes' else 'faster'} than"
            else:
                verdict = f"{pct:.0f}% {'bigger' if unit == 'bytes' else 'slower'} than"
            lines.append(f"{b['bench']} ({b['project']}) is {verdict} {'last week' if days == 7 else f'{days:g} days ago'}: "
                         f"{show(latest['value'], unit)} now, {show(base, unit)} then.")
        if not lines:
            return "Not enough measurements to compare yet."
        first = lines[0]
        lead = "Yes. " if " faster than" in first or " smaller than" in first else (
            "No. " if " slower than" in first or " bigger than" in first else "")
        return lead + " ".join(lines)


HOOK_MARK = "# orion-perf-sentinel"


def hook_script(port: int) -> str:
    # The daemon needs its per-start key (api_token.py); the hook reads it from the user's private token file and
    # hands it to curl on stdin, so it never appears in the process list.
    return ("#!/bin/sh\n" + HOOK_MARK + "\n# Tells Orion a commit happened, so Perf Sentinel can run this repo's "
            ".orion/perf.json benchmarks.\n"
            "T=\"\"\n"
            "for f in \"${ORION_TOKEN_FILE:-}\" \"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/orion/daemon.token\" "
            "\"${ORION_DATA_DIR:-$HOME/.orion}/daemon.token\"; do\n"
            "  if [ -n \"$f\" ] && [ -r \"$f\" ]; then T=\"$(cat \"$f\")\"; break; fi\n"
            "done\n"
            "printf 'Authorization: Bearer %s\\n' \"$T\" | "
            f"curl -s -m 2 -X POST -H @- -H 'Content-Type: application/json' -d \"{{\\\"repo\\\": \\\"$(pwd)\\\", "
            f"\\\"trigger\\\": \\\"commit\\\"}}\" http://127.0.0.1:{port}/perf/run >/dev/null 2>&1 &\nexit 0\n")


def install_hook(repo: Any, port: int) -> str:
    """Add the optional post-commit hook. Never replaces a hook that isn't Orion's."""
    top = toplevel(repo)
    if top is None:
        raise ValueError("That folder isn't inside a git repository.")
    import subprocess   # plain git here: Orion's git() helper turns hooks off, which hides the hooks folder
    try:
        out = subprocess.run(["git", "-C", str(top), "rev-parse", "--git-path", "hooks"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    hook_dir = (top / out) if out else top / ".git" / "hooks"
    hook = hook_dir / "post-commit"
    if hook.exists() and HOOK_MARK not in hook.read_text(encoding="utf-8", errors="replace"):
        raise ValueError(f"{top.name} already has its own post-commit hook; Orion won't replace it. "
                         "Perf Sentinel still runs on Proof Gate checks or nightly.")
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(hook_script(port), encoding="utf-8")
    hook.chmod(0o755)
    return str(hook)
