"""Dependency Autopilot (toolkit tool 17): find updates, try them in a throwaway copy, one card, a tested branch."""
import asyncio
import json
import re
import subprocess
from pathlib import Path

import dep_autopilot as D
from sec_fakes import FakeShell, fixture


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True, check=True).stdout


def make_repo(tmp_path) -> Path:
    repo = tmp_path / "hud"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "package.json").write_text(json.dumps({"name": "hud", "scripts": {"test": "node --test"},
                                                   "dependencies": {"vite": "5.4.2", "three": "0.169.0",
                                                                    "typescript": "5.6.0"}}, indent=1) + "\n")
    (repo / "README.md").write_text("hi\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


class Sandbox:
    """Pretends to be npm + the tests: applies `npm install a@1 b@2` to package.json in the copy; vite 6 breaks
    the tests."""

    def __init__(self, base_fails=False):
        self.scripts = []
        self.base_fails = base_fails

    def __call__(self, script, copy, timeout):
        self.scripts.append(script)
        pkg = json.loads((copy / "package.json").read_text())
        for line in script.splitlines():
            if line.startswith("npm install --no-audit"):
                for name, ver in re.findall(r"'?([\w@/.-]+?)@(\d[\w.]*)'?", line.split("||")[0]):
                    pkg["dependencies"][name] = ver
        (copy / "package.json").write_text(json.dumps(pkg, indent=1) + "\n")
        out = D.APPLIED + "\n"
        if pkg["dependencies"]["vite"].startswith("6") or self.base_fails:
            return 1, out + "not ok 1 - renders the HUD\n# pass 4\n# fail 1\n", "a fake box"
        return 0, out + "# pass 5\n# fail 0\n", "a fake box"


def shell():
    return FakeShell({"npm outdated --json": (1, fixture("npm_outdated.json"))}, tools=["npm"])


def test_finding_updates(tmp_path):
    c = D.parse_cargo_dry_run(fixture("cargo_update_dry_run.txt"))
    assert [(u["name"], u["current"], u["target"], u["major"]) for u in c] == [
        ("serde", "1.0.190", "1.0.210", False), ("tokio", "1.32.0", "1.40.0", False), ("time", "0.3.20", "0.3.36", False)]
    co = D.parse_cargo_outdated(fixture("cargo_outdated.json"))
    assert [(u["name"], u["target"], u["major"]) for u in co] == [
        ("serde", "1.0.210", False), ("rand", "0.8.5", True), ("mysql", "24.0.1", False), ("mysql", "25.0.1", True)]
    n = D.parse_npm_outdated(json.loads(fixture("npm_outdated.json")))
    assert [(u["name"], u["target"], u["major"]) for u in n] == [
        ("vite", "5.4.10", False), ("vite", "6.0.1", True), ("three", "0.170.0", True), ("typescript", "5.6.3", False)]
    r = D.parse_renovate(fixture("renovate.log"))
    assert [(u["ecosystem"], u["name"], u["target"], u["major"]) for u in r] == [
        ("cargo", "serde", "1.0.210", False), ("npm", "left-pad", "1.3.0", False), ("npm", "left-pad", "2.0.0", True)]
    (tmp_path / "requirements.txt").write_text("requests==2.25.0\nFlask>=3\n")
    pip = D.parse_pip_outdated([{"name": "requests", "version": "2.25.0", "latest_version": "2.32.3"},
                                {"name": "urllib3", "version": "1.26.0", "latest_version": "2.2.0"}],
                               D.direct_python_deps(tmp_path))
    assert [u["name"] for u in pip] == ["requests"]                 # only what the project asks for directly
    assert D.parse_uv_dry_run("Updated anyio v4.3.0 -> v4.4.0\n")[0]["lock_only"]


def test_security_marking_and_commands():
    ups = [D.update("npm", "vite", "5.4.2", "5.4.10", "npm"), D.update("cargo", "time", "0.3.20", "0.3.36", "cargo"),
           D.update("pypi", "requests", "2.25.0", "2.31.0", "pip")]
    D.mark_security(ups, [{"ecosystem": "npm", "package": "vite", "fixed": "5.4.6", "id": "GHSA-x"},
                          {"ecosystem": "PyPI", "package": "requests", "fixed": "2.32.0", "id": "PYSEC-1"}])
    assert [u["security"] for u in ups] == [True, False, False]
    cmds, skipped = D.apply_commands(ups, all_compat_cargo=False, sh=FakeShell(), has_cargo_edit=False)
    assert cmds[0] == "cargo update -p time@0.3.20 --precise 0.3.36"
    assert cmds[1] == "npm install --no-audit --no-fund vite@5.4.10" and "requests" in cmds[2]
    major = D.update("cargo", "rand", "0.7.3", "0.8.5", "x", major=True)
    assert D.apply_commands([major], False, FakeShell(), False)[1] == [major]
    assert D.apply_commands([major], False, FakeShell(), True)[0] == ["cargo upgrade --incompatible -p rand@0.8.5"]


def test_requirements_pin_rewrite(tmp_path):
    (tmp_path / "requirements.txt").write_text("Requests[socks]==2.25.0 ; python_version > '3'\nflask==3.0.0\n")
    cmd = D._pin_command(D.update("pypi", "requests", "2.25.0", "2.31.0", "pip"))
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, check=True)
    assert (tmp_path / "requirements.txt").read_text().splitlines() == [
        "Requests[socks]==2.31.0 ; python_version > '3'", "flask==3.0.0"]


def test_run_brings_one_card_and_approve_makes_a_branch(tmp_path):
    repo = make_repo(tmp_path)
    box = Sandbox()
    ap = D.Autopilot(tmp_path / "data", sh=shell(), sandbox=box)
    rec = ap.run(repo)
    assert rec["state"] == "ready"
    assert [D._line(u) for u in rec["ready"]] == ["typescript ? -> 5.6.3", "vite 5.4.2 -> 5.4.10",
                                                  "three 0.169.0 -> 0.170.0 (major)"]
    assert [u["name"] for u in rec["needs_change"]] == ["vite"] and "renders the HUD" in rec["needs_change"][0]["why"]
    assert rec["text"].startswith("3 updates ready, all tests pass, 1 needs a code change.")
    assert rec["trials"] == 4                    # all compatible together, vite 6, three, then the final set
    assert "Tested with `npm test` in a fake box." in rec["text"]
    patch = (tmp_path / "data" / "deps" / f"{rec['id']}.patch").read_text()
    assert '"three": "0.170.0"' in patch and "README" not in patch
    assert not list(Path(tmp_path).glob("orion-deps-*")) and "orion-deps" not in git(repo, "worktree", "list")
    assert git(repo, "status", "--porcelain") == ""          # your working folder is untouched

    res = ap.execute_apply({"packet": {"details": {"run": rec["id"], "branch": "orion/deps-2026-10-16"}}})
    assert res["state"] == "done" and res["branch"] == "orion/deps-2026-10-16", res
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert json.loads(git(repo, "show", "orion/deps-2026-10-16:package.json"))["dependencies"] == {
        "vite": "5.4.10", "three": "0.170.0", "typescript": "5.6.3"}
    assert "Dependency Autopilot" in git(repo, "log", "-1", "--format=%B", "orion/deps-2026-10-16")
    assert json.loads((repo / "package.json").read_text())["dependencies"]["vite"] == "5.4.2"
    again = ap.execute_apply({"packet": {"details": {"run": rec["id"], "branch": "orion/deps-2026-10-16"}}})
    assert again["branch"] == "orion/deps-2026-10-16-2"
    assert D.make_branch(repo, Path("x"), rec, "main", FakeShell())["state"] == "failed"


def test_tests_already_failing_and_no_tests(tmp_path):
    repo = make_repo(tmp_path)
    rec = D.Autopilot(tmp_path / "data", sh=shell(), sandbox=Sandbox(base_fails=True)).run(repo)
    assert rec["state"] == "tests_fail" and "already fail" in rec["text"]
    pkg = json.loads((repo / "package.json").read_text())
    pkg["scripts"] = {}
    (repo / "package.json").write_text(json.dumps(pkg))
    git(repo, "commit", "-qam", "no tests")
    rec = D.Autopilot(tmp_path / "data", sh=shell(), sandbox=Sandbox()).run(repo)
    assert rec["state"] == "no_tests"
    up = D.Autopilot(tmp_path / "data", sh=FakeShell({"npm outdated --json": (0, "{}")}, tools=["npm"]),
                     sandbox=Sandbox()).run(repo)
    assert up["state"] == "up_to_date"


def test_propose_uses_the_boardroom(tmp_path):
    repo = make_repo(tmp_path)

    class Board:
        def __init__(self):
            self.proposed = []

        def pending(self):
            return []

        async def propose(self, task_type, summary, details, proposer, risk="low", simulated=True):
            self.proposed.append((task_type, summary, details, risk, simulated))
            return {"id": "dec-1"}

    board = Board()
    ap = D.Autopilot(tmp_path / "data", sh=shell(), sandbox=Sandbox(), board=board)
    rec = ap.run(repo)
    dec = asyncio.run(ap.propose(rec))
    assert dec["id"] == "dec-1"
    tt, summary, det, risk, simulated = board.proposed[0]
    assert tt == "deps.apply" and summary.startswith("hud: 3 updates ready") and simulated is False
    assert det["branch"].startswith("orion/deps-") and len(det["updates"]) == 3
    import boardroom
    assert boardroom.TASK_TYPES["deps.apply"].max_level == 3       # a local branch: reversible, never pushed
