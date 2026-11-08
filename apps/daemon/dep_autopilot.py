"""Dependency Autopilot (docs/ORION_TOOLKIT_PLAN.md, tool 17): updates that test themselves.

Once a week, for each project (the git repos in the folders Orion may use, or the list in Settings), Orion:
  1. finds updates: `cargo outdated` (if installed) or `cargo update --dry-run`, `npm outdated --json`,
     `pip list --outdated` in the project's .venv, or `uv lock --upgrade --dry-run`; Renovate's lookup is added
     when the Renovate CLI is installed (never required);
  2. tries them in a throwaway copy of the repo (a git worktree, run in a Clean Box when the project pins its
     tools with mise and Podman is installed; otherwise in the copy with a clean environment that holds none of
     your keys): all the compatible updates together first, then one by one only if that fails, and each major
     update on its own;
  3. runs the project's tests (the Runbook's `just test`, or the test command Orion detects);
  4. brings ONE card: "9 updates ready, all tests pass, 1 needs a code change".
Approving it (on your phone or in the HUD; boardroom decision `deps.apply`) makes a branch orion/deps-<date> in
your repo with one commit holding exactly the tested manifest and lockfile changes. Never on main, never pushed,
and your working folder isn't touched.

Security fixes come immediately: when the Supply-Chain Scan finds a hole with a fix, the project's run starts at
once and the fixed packages are marked as security updates.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from sec_common import Shell, loads, plural, read_json, write_json

TASK_APPLY = "deps.apply"
MANIFESTS = ("Cargo.toml", "Cargo.lock", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
             "pyproject.toml", "uv.lock", "poetry.lock", "requirements*.txt")
APPLY_FAILED = "@@ORION-APPLY-FAILED"
APPLIED = "@@ORION-APPLIED"
UPDATE_RE = re.compile(r"Updat\w*\s+(\S+)\s+v?([\w.+\-]+)\s+->\s+v?([\w.+\-]+)")


def _major(a: str, b: str) -> bool:
    """Whether going from a to b crosses a semver-incompatible line (0.x counts the minor)."""
    pa = [int(x) for x in re.findall(r"\d+", a)[:3]] + [0, 0, 0]
    pb = [int(x) for x in re.findall(r"\d+", b)[:3]] + [0, 0, 0]
    if pa[0] == 0 and pb[0] == 0:
        return pa[1] != pb[1]
    return pa[0] != pb[0]


def update(eco: str, name: str, current: str, target: str, source: str, **extra: Any) -> dict[str, Any]:
    return {"ecosystem": eco, "name": name, "current": current, "target": target,
            "major": _major(current, target) if current else False, "security": False, "source": source, **extra}


# =====================================================================================  finding updates
def parse_cargo_dry_run(text: str) -> list[dict[str, Any]]:
    out = []
    for m in UPDATE_RE.finditer(text or ""):
        out.append(update("cargo", m.group(1), m.group(2), m.group(3), "cargo update"))
    return out


def parse_cargo_outdated(text: str) -> list[dict[str, Any]]:
    """`cargo outdated --format json --root-deps-only` (one JSON object per crate in the workspace)."""
    out: dict[str, dict[str, Any]] = {}
    for line in (text or "").splitlines():
        data = loads(line)
        if not isinstance(data, dict):
            continue
        for d in data.get("dependencies") or []:
            name, cur = d.get("name"), str(d.get("project", ""))
            compat, latest = str(d.get("compat", "---")), str(d.get("latest", "---"))
            if not name or cur in ("---", "Removed"):
                continue
            if compat not in ("---", "Removed", cur):
                out[f"{name}/c"] = update("cargo", name, cur, compat, "cargo outdated")
            if latest not in ("---", "Removed", cur, compat):
                out[f"{name}/m"] = update("cargo", name, cur, latest, "cargo outdated", major=True)
    return list(out.values())


def parse_npm_outdated(data: Any, lock: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    out = []
    pkgs = (lock or {}).get("packages") or {}
    for name, v in (data or {}).items():
        if not isinstance(v, dict):
            continue
        cur = str(v.get("current") or (pkgs.get(f"node_modules/{name}") or {}).get("version") or "")
        wanted, latest = str(v.get("wanted") or ""), str(v.get("latest") or "")
        if wanted and wanted != cur:
            out.append(update("npm", name, cur, wanted, "npm outdated", major=False))
        if latest and latest not in (cur, wanted) and not latest.startswith(("git", "file:", "link:")):
            out.append(update("npm", name, cur, latest, "npm outdated", major=True))
    return out


def direct_python_deps(project: Path) -> set[str]:
    names: set[str] = set()
    for f in list(project.glob("requirements*.txt")) + [project / "pyproject.toml"]:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"^\s*[\"']?([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?\s*(?:[=<>~!]=|>|<|[\"',;]|$)",
                             text, re.M):
            names.add(m.group(1).lower().replace("_", "-"))
    return names


def parse_pip_outdated(data: Any, direct: set[str]) -> list[dict[str, Any]]:
    out = []
    for d in data or []:
        name = str(d.get("name", ""))
        if direct and name.lower().replace("_", "-") not in direct:
            continue
        out.append(update("pypi", name, str(d.get("version", "")), str(d.get("latest_version", "")), "pip"))
    return out


def parse_uv_dry_run(text: str) -> list[dict[str, Any]]:
    return [update("pypi", m.group(1), m.group(2), m.group(3), "uv", lock_only=True)
            for m in UPDATE_RE.finditer(text or "")]


def parse_renovate(text: str) -> list[dict[str, Any]]:
    """Renovate's JSON log (LOG_FORMAT=json, --dry-run=lookup): the 'packageFiles with updates' line."""
    managers = {"cargo": "cargo", "npm": "npm", "pip_requirements": "pypi", "pep621": "pypi", "poetry": "pypi",
                "pip-compile": "pypi"}
    out = []
    for line in (text or "").splitlines():
        data = loads(line)
        if not isinstance(data, dict) or data.get("msg") != "packageFiles with updates":
            continue
        for manager, files in (data.get("config") or {}).items():
            eco = managers.get(manager)
            if not eco:
                continue
            for pf in files or []:
                for dep in pf.get("deps") or []:
                    cur = str(dep.get("currentVersion") or dep.get("currentValue") or "").lstrip("^~=v ")
                    for u in dep.get("updates") or []:
                        nv = str(u.get("newVersion") or "")
                        if nv and dep.get("depName"):
                            out.append(update(eco, dep["depName"], cur, nv, "Renovate",
                                              major=u.get("updateType") == "major"))
    return out


def find_updates(project: Path, sh: Shell, use_renovate: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
    """(updates, notes)."""
    project = Path(project)
    found: list[dict[str, Any]] = []
    notes: list[str] = []
    if (project / "Cargo.toml").exists() and sh.which("cargo"):
        if sh.which("cargo-outdated"):
            _, out = sh.run(["cargo", "outdated", "--format", "json", "--root-deps-only"], cwd=str(project),
                            timeout=600, stderr=False)
            found += parse_cargo_outdated(out)
        else:
            _, out = sh.run(["cargo", "update", "--dry-run"], cwd=str(project), timeout=600)
            found += parse_cargo_dry_run(out)
            notes.append("Major Rust updates aren't looked up (install cargo-outdated and cargo-edit for those).")
    if (project / "package.json").exists() and sh.which("npm"):
        _, out = sh.run(["npm", "outdated", "--json"], cwd=str(project), timeout=300, stderr=False)
        found += parse_npm_outdated(loads(out, {}), read_json(project / "package-lock.json", {}))
    if (project / "uv.lock").exists() and sh.which("uv"):
        _, out = sh.run(["uv", "lock", "--upgrade", "--dry-run"], cwd=str(project), timeout=300)
        found += parse_uv_dry_run(out)
    else:
        py = next((project / v / "bin" / "python" for v in (".venv", "venv")
                   if (project / v / "bin" / "python").exists()), None)
        if py is not None:
            _, out = sh.run([str(py), "-m", "pip", "list", "--outdated", "--format=json"], cwd=str(project),
                            timeout=300, stderr=False)
            found += parse_pip_outdated(loads(out, []), direct_python_deps(project))
    if use_renovate and sh.which("renovate"):
        rc, out = sh.run(["renovate", "--platform=local", "--dry-run=lookup"], cwd=str(project), timeout=900,
                         env={**os.environ, "LOG_FORMAT": "json", "LOG_LEVEL": "debug", "RENOVATE_ONBOARDING": "false"},
                         stderr=False)
        have = {(u["ecosystem"], u["name"].lower(), u["target"]) for u in found}
        for u in parse_renovate(out):
            if (u["ecosystem"], u["name"].lower(), u["target"]) not in have:
                found.append(u)
    seen, uniq = set(), []
    for u in found:
        k = (u["ecosystem"], u["name"].lower(), u["target"])
        if k not in seen and u["target"] and u["target"] != u["current"]:
            seen.add(k)
            uniq.append(u)
    return uniq, notes


def mark_security(updates: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
    """Flag updates that reach a version fixing a known hole (from the Supply-Chain Scan)."""
    eco_of = {"crates.io": "cargo", "npm": "npm", "PyPI": "pypi"}
    for u in updates:
        for f in findings:
            if eco_of.get(f.get("ecosystem", ""), "") != u["ecosystem"] or f["package"].lower() != u["name"].lower():
                continue
            if not f.get("fixed") or _version_key(u["target"]) >= _version_key(f["fixed"]):
                u["security"] = True
                u["advisory"] = f["id"]


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


# =====================================================================================  applying (in the copy)
def apply_commands(updates: list[dict[str, Any]], all_compat_cargo: bool, sh: Shell, has_cargo_edit: bool
                   ) -> tuple[list[str], list[dict[str, Any]]]:
    """Shell commands that apply these updates, and the updates that can't be applied automatically."""
    cmds: list[str] = []
    skipped: list[dict[str, Any]] = []
    cargo = [u for u in updates if u["ecosystem"] == "cargo"]
    if cargo and all_compat_cargo and not any(u["major"] for u in cargo):
        cmds.append("cargo update")
    else:
        for u in cargo:
            if not u["major"]:
                cmds.append(f"cargo update -p {shlex.quote(u['name'] + '@' + u['current'])} "
                            f"--precise {shlex.quote(u['target'])}")
            elif has_cargo_edit:
                cmds.append(f"cargo upgrade --incompatible -p {shlex.quote(u['name'] + '@' + u['target'])}")
            else:
                skipped.append(u)
    npm = [f"{u['name']}@{u['target']}" for u in updates if u["ecosystem"] == "npm"]
    if npm:
        cmds.append("npm install --no-audit --no-fund " + " ".join(shlex.quote(x) for x in npm))
    for u in updates:
        if u["ecosystem"] != "pypi":
            continue
        if u.get("lock_only"):
            cmds.append(f"uv lock --upgrade-package {shlex.quote(u['name'] + '==' + u['target'])}")
        else:
            cmds.append(_pin_command(u))
    return cmds, skipped


def _pin_command(u: dict[str, Any]) -> str:
    """Rewrite `name==old` to `name==new` in requirements*.txt (exact pins only)."""
    pat = re.escape(u["name"]).replace("\\-", "[-_.]").replace("_", "[-_.]")
    sed = f"s/^({pat})(\\[[^]]*\\])?==[^ ;#]*/\\1\\2=={u['target']}/I"
    return f"sed -E -i {shlex.quote(sed)} requirements*.txt 2>/dev/null; true"


def prepare_command(copy: Path) -> str:
    steps = []
    if (copy / "package.json").exists():
        steps.append("[ -d node_modules ] || npm ci --no-audit --no-fund || npm install --no-audit --no-fund")
    if (copy / "uv.lock").exists():
        steps.append("uv sync -q")
    elif list(copy.glob("requirements*.txt")):
        reqs = " ".join(f"-r {shlex.quote(p.name)}" for p in sorted(copy.glob("requirements*.txt")))
        steps.append(f"python3 -m venv .orion-venv && .orion-venv/bin/pip install -q {reqs} pytest "
                     "&& export PATH=\"$PWD/.orion-venv/bin:$PATH\"")
    return " && ".join(steps)


def test_command(copy: Path) -> Optional[str]:
    import runbook
    cmds = runbook.detect_commands(copy)
    cmd = (cmds.get("test") or {}).get("command")
    if cmd and cmd.startswith("just ") and not shutil.which("just"):
        cmd = (runbook.detect_commands_without_justfile(copy).get("test") or {}).get("command")
    return cmd


def trial_script(apply: list[str], prepare: str, test: str) -> str:
    lines = ["set -o pipefail"]
    for c in apply:
        lines.append(f"{c} || {{ echo {APPLY_FAILED}; exit 90; }}")
    lines.append(f"echo {APPLIED}")
    if prepare:
        lines.append(f"{prepare} || {{ echo '@@ORION-PREPARE-FAILED'; exit 91; }}")
    lines.append(test)
    return "\n".join(lines)


# =====================================================================================  the autopilot
class Autopilot:
    def __init__(self, data_dir: Path, sh: Optional[Shell] = None, board: Any = None,
                 notify: Optional[Callable[[str, str], None]] = None, sandbox: Optional[Callable[..., Any]] = None,
                 max_trials: int = 8, trial_timeout: float = 1800) -> None:
        self.path = Path(data_dir) / "dep_autopilot.json"
        self.patch_dir = Path(data_dir) / "deps"
        self.sh = sh or Shell()
        self.board = board
        self.notify = notify or (lambda t, b: None)
        self.sandbox = sandbox or self._run_in_sandbox
        self.max_trials = max_trials
        self.trial_timeout = trial_timeout

    # ------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        st = read_json(self.path, {}) or {}
        st.setdefault("runs", {})
        st.setdefault("last", {})
        return st

    def _save(self, st: dict[str, Any]) -> None:
        runs = st["runs"]
        if len(runs) > 60:
            for rid in sorted(runs, key=lambda r: runs[r].get("at", 0))[:-60]:
                runs.pop(rid, None)
                try:
                    (self.patch_dir / f"{rid}.patch").unlink()
                except OSError:
                    pass
        write_json(self.path, st)

    def last_runs(self) -> list[dict[str, Any]]:
        st = self.state()
        return [st["runs"][r] for r in st["last"].values() if r in st["runs"]]

    def due(self, project: Path, now: float, every_days: float = 7) -> bool:
        st = self.state()
        rid = st["last"].get(str(project))
        at = st["runs"].get(rid, {}).get("at", 0) if rid else 0
        return now - at >= every_days * 86400

    # ------------------------------------------------------------ the copy
    def _git(self, repo: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
        return self.sh.run(["git", "-C", str(repo), *args], timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})

    def _make_copy(self, project: Path) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="orion-deps-"))
        copy = tmp / project.name
        rc, out = self._git(project, "worktree", "add", "--detach", str(copy), "HEAD")
        if rc != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"couldn't make a copy of {project.name}: {out.strip()[-200:]}")
        return copy

    def _drop_copy(self, project: Path, copy: Path) -> None:
        self._git(project, "worktree", "remove", "--force", str(copy))
        shutil.rmtree(copy.parent, ignore_errors=True)
        self._git(project, "worktree", "prune")

    def _run_in_sandbox(self, script: str, copy: Path, timeout: float) -> tuple[int, str, str]:
        """(exit code, output, where). A Clean Box when the project pins its tools with mise and Podman is here."""
        from clean_box import TOOL_FILES, CleanBox, Mount
        box = CleanBox()
        mise = self.sh.which("mise")
        if box.available() and mise and any((copy / f).exists() for f in TOOL_FILES):
            inner = "cd /work && mise trust -a >/dev/null 2>&1; mise install >/dev/null && mise exec -- bash -c " + \
                shlex.quote(script)
            try:
                r = box.run(inner, [Mount(str(copy), "/work", read_only=False),
                                    Mount(str(Path(mise).resolve()), "/usr/local/bin/mise")],
                            timeout=timeout, network=True, workdir="/work")
                return r.rc, r.output, "a Clean Box"
            except Exception as exc:   # fall back to the plain copy
                note = f" (Clean Box failed: {exc})"
        else:
            note = ""
        from crash_healer import _clean_env
        home = tempfile.mkdtemp(prefix="orion-deps-home-")
        real = os.path.expanduser("~")
        env = _clean_env(home)
        env.update({"npm_config_cache": os.path.join(real, ".npm"), "UV_CACHE_DIR": os.path.join(real, ".cache", "uv"),
                    "PIP_CACHE_DIR": os.path.join(real, ".cache", "pip"), "npm_config_fund": "false",
                    "npm_config_update_notifier": "false"})
        try:
            rc, out = self.sh.run(["bash", "-c", script], cwd=str(copy), timeout=timeout, env=env)
        finally:
            shutil.rmtree(home, ignore_errors=True)
        return rc, out, "a throwaway copy with none of your keys" + note

    def trial(self, project: Path, updates: list[dict[str, Any]], all_compat_cargo: bool = False
              ) -> dict[str, Any]:
        """Apply `updates` in a fresh copy, run the tests; returns {ok, applied, tests, patch, where, ...}."""
        from proof_gate import parse_tests
        copy = self._make_copy(project)
        try:
            cmds, skipped = apply_commands(updates, all_compat_cargo, self.sh, bool(self.sh.which("cargo-upgrade")))
            test = test_command(copy)
            if not test:
                return {"ok": False, "applied": False, "no_tests": True, "skipped": skipped}
            rc, out, where = self.sandbox(trial_script(cmds, prepare_command(copy), test), copy, self.trial_timeout)
            applied = APPLIED in out and APPLY_FAILED not in out
            counts = parse_tests(out) if applied else {"passed": None, "failed": None, "failing": []}
            patch = ""
            if rc == 0 and applied:
                _, patch = self._git(copy, "diff", "--binary", "HEAD", "--",
                                     *[f":(glob)**/{m}" for m in MANIFESTS])
            return {"ok": rc == 0 and applied, "applied": applied, "rc": rc, "tests": counts, "test_command": test,
                    "where": where, "patch": patch, "skipped": skipped, "tail": _tail(out)}
        finally:
            self._drop_copy(project, copy)

    # ------------------------------------------------------------ one project
    def run(self, project: Path, findings: Optional[list[dict[str, Any]]] = None, reason: str = "weekly"
            ) -> dict[str, Any]:
        project = Path(project)
        rid = f"deps-{uuid.uuid4().hex[:10]}"
        rec: dict[str, Any] = {"id": rid, "project": str(project), "name": project.name, "at": round(time.time(), 3),
                               "reason": reason, "ready": [], "needs_change": [], "not_tried": [], "notes": [],
                               "trials": 0, "state": "running"}
        if not (project / ".git").exists():
            return self._finish(rec, "error", "Dependency Autopilot only works on git repos.")
        updates, notes = find_updates(project, self.sh)
        rec["notes"] += notes
        mark_security(updates, findings or [])
        rec["found"] = len(updates)
        if not updates:
            return self._finish(rec, "up_to_date", f"{project.name}: everything is up to date.")
        updates.sort(key=lambda u: (not u["security"], u["major"], u["ecosystem"], u["name"]))
        compat = [u for u in updates if not u["major"]]
        majors = [u for u in updates if u["major"]]
        ready: list[dict[str, Any]] = []
        broken: list[dict[str, Any]] = []
        last_ok: Optional[dict[str, Any]] = None
        together: set[tuple[str, str, str]] = set()      # the set of updates last_ok tested together
        try:
            if compat:
                t = self._try(rec, project, compat, all_compat_cargo=True)
                if t.get("no_tests"):
                    rec["not_tried"] = updates
                    return self._finish(rec, "no_tests", f"{project.name} has {plural(len(updates), 'update')} but "
                                        "no tests Orion can run, so none could be proven. Add a test step (just test).")
                rec["not_tried"] += t["skipped"]
                if t["ok"]:
                    ready, last_ok = [u for u in compat if u not in t["skipped"]], t
                    together = {_k(u) for u in ready}
                else:
                    base = self._try(rec, project, [])
                    if not base["ok"]:
                        rec["tail"] = base.get("tail", "")
                        rec["not_tried"] = updates
                        return self._finish(rec, "tests_fail", f"{project.name}'s tests already fail without any "
                                            "updates, so Orion can't judge them. Fix the tests first.")
                    for u in compat:
                        if rec["trials"] >= self.max_trials:
                            rec["not_tried"].append(u)
                            continue
                        one = self._try(rec, project, [u])
                        (ready if one["ok"] else broken).append(u if one["ok"] else {**u, "why": _why(one)})
            for u in majors:
                if rec["trials"] >= self.max_trials:
                    rec["not_tried"].append(u)
                    continue
                one = self._try(rec, project, [u])
                if one["skipped"]:
                    rec["not_tried"] += one["skipped"]
                elif one["ok"]:
                    ready.append(u)
                else:
                    broken.append({**u, "why": _why(one)})
            if ready and {_k(u) for u in ready} != together:
                final = self._try(rec, project, ready)
                if not final["ok"]:
                    alone = [u for u in ready if u["major"]]
                    ready = [u for u in ready if not u["major"]]
                    broken += [{**u, "why": "works alone, but not together with the other updates"} for u in alone]
                    final = self._try(rec, project, ready) if ready else {"ok": False}
                    if not final["ok"]:
                        broken += [{**u, "why": "works alone, but not together with the other updates"}
                                   for u in ready]
                        ready = []
                last_ok = final if ready else None
        except RuntimeError as exc:
            return self._finish(rec, "error", str(exc))
        rec["ready"], rec["needs_change"] = ready, broken
        if last_ok is not None:
            rec["where"] = last_ok.get("where")
            rec["tests"] = last_ok.get("tests")
            rec["test_command"] = last_ok.get("test_command")
            if last_ok.get("patch"):
                self.patch_dir.mkdir(parents=True, exist_ok=True)
                (self.patch_dir / f"{rid}.patch").write_text(last_ok["patch"], encoding="utf-8")
                rec["patch"] = True
        return self._finish(rec, "ready" if ready else "nothing_ready", card_text(rec))

    def _try(self, rec: dict[str, Any], project: Path, ups: list[dict[str, Any]], all_compat_cargo: bool = False
             ) -> dict[str, Any]:
        rec["trials"] += 1
        return self.trial(project, ups, all_compat_cargo)

    def _finish(self, rec: dict[str, Any], state: str, text: str) -> dict[str, Any]:
        rec["state"], rec["text"] = state, text
        st = self.state()
        st["runs"][rec["id"]] = rec
        st["last"][rec["project"]] = rec["id"]
        self._save(st)
        return rec

    # ------------------------------------------------------------ the card / approval
    async def propose(self, rec: dict[str, Any]) -> Optional[dict[str, Any]]:
        if self.board is None or rec.get("state") != "ready" or not rec.get("patch"):
            return None
        for d in self.board.pending():
            if d["task_type"] == TASK_APPLY and d["packet"]["details"].get("project") == rec["project"]:
                try:   # an older card for the same project is out of date
                    self.board.store.x("UPDATE decisions SET status='cancelled', decided=?, note=? WHERE id=? AND "
                                       "status='pending'", (time.time(), "replaced by a newer check", d["id"]))
                    await self.board._mirror(self.board.get(d["id"]))
                except Exception:
                    pass
        dec = await self.board.propose(
            TASK_APPLY, f"{rec['name']}: {card_text(rec).splitlines()[0]}",
            {"project": rec["project"], "run": rec["id"], "branch": branch_name(),
             "updates": [_line(u) for u in rec["ready"]][:60],
             "needs_a_code_change": [f"{_line(u)}: {u.get('why', '')}" for u in rec["needs_change"]][:30],
             "tested_with": rec.get("test_command"), "tested_in": rec.get("where"),
             "what_happens": "Approve = Orion makes the branch orion/deps-<date> in your repo with one commit holding "
                             "exactly the tested changes. Nothing on main changes and nothing is pushed."},
            proposer="Dependency Autopilot", risk="high" if any(u["security"] for u in rec["ready"]) else "low",
            simulated=False)
        rec["decision_id"] = dec["id"]
        st = self.state()
        st["runs"][rec["id"]] = rec
        self._save(st)
        return dec

    def execute_apply(self, d: dict[str, Any]) -> dict[str, Any]:
        """Boardroom executor: the tested patch becomes one commit on a new branch (never main, never pushed)."""
        det = d["packet"]["details"]
        rec = self.state()["runs"].get(det.get("run", ""))
        patch = self.patch_dir / f"{det.get('run', '')}.patch"
        if rec is None or not patch.exists():
            return {"state": "failed", "error": "That update run is gone; ask Orion to check the updates again."}
        return make_branch(Path(rec["project"]), patch, rec, det.get("branch") or branch_name(), self.sh)


def branch_name(today: Optional[_dt.date] = None) -> str:
    return f"orion/deps-{(today or _dt.date.today()).isoformat()}"


def make_branch(project: Path, patch: Path, rec: dict[str, Any], branch: str, sh: Shell) -> dict[str, Any]:
    def git(*args: str, cwd: Path = project) -> tuple[int, str]:
        return sh.run(["git", "-C", str(cwd), *args], timeout=120,
                      env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    if branch in ("main", "master") or not branch.startswith("orion/"):
        return {"state": "failed", "error": "Dependency Autopilot only makes orion/ branches."}
    name, n = branch, 2
    while git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}")[0] == 0:
        name, n = f"{branch}-{n}", n + 1
    tmp = Path(tempfile.mkdtemp(prefix="orion-deps-branch-"))
    wt = tmp / project.name
    try:
        rc, out = git("worktree", "add", "-b", name, str(wt), "HEAD")
        if rc != 0:
            return {"state": "failed", "error": f"couldn't make the branch: {out.strip()[-200:]}"}
        rc, out = git("apply", "--index", "--whitespace=nowarn", str(patch), cwd=wt)
        if rc != 0:
            git("worktree", "remove", "--force", str(wt))
            git("branch", "-D", name)
            return {"state": "failed", "error": "The tested changes no longer fit (the repo changed). Ask Orion to "
                                                "check the updates again."}
        msg = "Update dependencies (tested by Orion's Dependency Autopilot)\n\n" + \
            "\n".join(f"- {_line(u)}" for u in rec["ready"]) + \
            (f"\n\nTests: {rec.get('test_command')}" if rec.get("test_command") else "")
        rc, out = git("commit", "-q", "-m", msg, cwd=wt)
        if rc != 0 and ("user.email" in out or "identity" in out.lower()):
            rc, out = git("-c", "user.name=Orion", "-c", "user.email=orion@localhost", "commit", "-q", "-m", msg,
                          cwd=wt)
        if rc != 0:
            git("worktree", "remove", "--force", str(wt))
            git("branch", "-D", name)
            return {"state": "failed", "error": f"couldn't commit: {out.strip()[-200:]}"}
        _, sha = git("rev-parse", "--short", "HEAD", cwd=wt)
        git("worktree", "remove", "--force", str(wt))
        return {"state": "done", "branch": name, "commit": sha.strip(),
                "message": f"Made branch {name} in {project.name} with {plural(len(rec['ready']), 'update')}. "
                           "It isn't merged or pushed; merge it when you're ready."}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        git("worktree", "prune")


# =====================================================================================  words
def _k(u: dict[str, Any]) -> tuple[str, str, str]:
    return (u["ecosystem"], u["name"], u["target"])


def _line(u: dict[str, Any]) -> str:
    sec = " (security fix)" if u.get("security") else ""
    major = " (major)" if u.get("major") else ""
    return f"{u['name']} {u['current'] or '?'} -> {u['target']}{major}{sec}"


def _why(t: dict[str, Any]) -> str:
    if t.get("skipped"):
        return "can't be applied automatically"
    if not t.get("applied"):
        return "couldn't be installed"
    failing = (t.get("tests") or {}).get("failing") or []
    if failing:
        return "tests fail: " + ", ".join(failing[:3])
    return "tests fail"


def _tail(out: str, n: int = 1500) -> str:
    try:
        from mcp_tools import redact
        out = redact(out)
    except Exception:
        pass
    return out[-n:]


def card_text(rec: dict[str, Any]) -> str:
    ready, broken, skipped = rec.get("ready") or [], rec.get("needs_change") or [], rec.get("not_tried") or []
    if ready:
        head = f"{plural(len(ready), 'update')} ready, all tests pass"
    else:
        head = "No updates passed the tests"
    if broken:
        head += f", {len(broken)} need{'s' if len(broken) == 1 else ''} a code change"
    sec = sum(1 for u in ready if u.get("security"))
    if sec:
        head += f" ({plural(sec, 'security fix', 'security fixes')} included)"
    lines = [head + "."]
    if ready:
        lines += ["Ready:"] + [f"- {_line(u)}" for u in ready]
    if broken:
        lines += ["Needs a code change:"] + [f"- {_line(u)}: {u.get('why', '')}" for u in broken]
    if skipped:
        lines.append(f"Not tried: {', '.join(_line(u) for u in skipped[:10])}")
    if rec.get("where"):
        lines.append(f"Tested with `{rec.get('test_command')}` in {rec['where']}.")
    return "\n".join(lines)


def _register_task_types() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(TASK_APPLY, TaskType(TASK_APPLY, "Make a branch with tested dependency updates",
                                                         "code", reversible=True, money=False))


_register_task_types()
