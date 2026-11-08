"""Release Butler (Toolkit 11): changelog, bump, tests and signing on a separate release branch; nothing is pushed
or published without a signed decision. Uses a temp git repo, a local bare 'origin', and fake gh / minisign."""
import asyncio
import json
import os
import subprocess

import pytest

import boardroom as br
import release_butler as rel


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, msg, name="file.txt", text=None):
    (repo / name).write_text(text or msg)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "benilla"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "remote", "add", "origin", str(origin))
    (repo / "package.json").write_text(json.dumps({"name": "benilla", "version": "0.1.0"}, indent=2) + "\n")
    (repo / "Cargo.toml").write_text('[package]\nname = "benilla"\nversion = "0.1.0"\n\n[dependencies]\nserde = '
                                     '{ version = "1" }\n')
    commit(repo, "chore: start")
    git(repo, "tag", "v0.1.0")
    commit(repo, "feat: loot tables for raids", "a.txt")
    commit(repo, "fix(server): crash on login", "b.txt")
    commit(repo, "feat!: new save format", "c.txt")
    commit(repo, "chore: bump deps", "d.txt")
    commit(repo, "Tidy the readme", "e.txt")
    # fake gh and minisign on PATH; a fake (not real) key path
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    (bindir / "gh").write_text(f'#!/bin/sh\necho "gh $*" >> {calls}\necho https://github.com/example/benilla/'
                               f'releases/tag/draft\n')
    (bindir / "minisign").write_text(f'#!/bin/sh\necho "minisign $*" >> {calls}\n'
                                     'while [ $# -gt 0 ]; do [ "$1" = -m ] && touch "$2.minisig"; shift; done\n')
    for f in ("gh", "minisign"):
        (bindir / f).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    key = tmp_path / "fake-release-key"
    key.write_text("placeholder for tests, not a key\n")
    monkeypatch.setenv("ORION_RELEASE_KEY", str(key))
    monkeypatch.delenv("ORION_RELEASE_KEY_PASSWORD", raising=False)
    return {"repo": repo, "origin": origin, "calls": calls, "tmp": tmp_path}


class FakeProof:
    def __init__(self, verdict="green"):
        self.verdict, self.calls = verdict, []

    async def prove(self, **kw):
        self.calls.append(kw)
        return {"id": "p1", "verdict": self.verdict,
                "reason": "Proven: 12 tests pass." if self.verdict == "green" else "Said tests pass; 2 fail."}


class H:
    def __init__(self, env, verdict="green"):
        self.loop = asyncio.new_event_loop()
        self.board = br.Boardroom(env["tmp"] / "data")
        self.proof = FakeProof(verdict)
        self.notes = []
        self.butler = rel.ReleaseButler(env["tmp"] / "data", board=self.board, proof=self.proof,
                                        notify=lambda t, b: self.notes.append((t, b)))
        self.jobs = []
        spawn = lambda c: self.jobs.append(self.loop.create_task(c))
        self.board.executors["release.push"] = lambda d: self.butler.execute_push(d, spawn)
        self.board.executors["release.publish"] = lambda d: self.butler.execute_publish(d, spawn)
        self.board.on_decided.append(self.butler.on_decided)

    def run(self, c):
        return self.loop.run_until_complete(c)

    def approve(self, task_type):
        d = next(x for x in self.board.pending() if x["task_type"] == task_type)
        out = self.run(self.board.verdict(d["id"], "approve", br.packet_hash(d["packet"])))
        while self.jobs:
            self.run(self.jobs.pop(0))
        return out


def test_conventional_commits_grouped_in_plain_words():
    commits = [{"sha": "a1", "subject": "feat: loot tables", "body": ""},
               {"sha": "b2", "subject": "fix(server): crash on login", "body": ""},
               {"sha": "c3", "subject": "perf: faster pathing", "body": ""},
               {"sha": "d4", "subject": "refactor: split world.rs", "body": "BREAKING CHANGE: config moved"},
               {"sha": "e5", "subject": "Merge branch 'x'", "body": ""},
               {"sha": "f6", "subject": "Update the readme", "body": ""}]
    text = rel.builtin_changelog("Benilla", "0.2.0", commits, when="2026-10-16")
    assert text.startswith("## Benilla 0.2.0 (2026-10-16)")
    assert "### New\n- Loot tables (a1)" in text and "### Fixes\n- server: Crash on login (b2)" in text
    assert "### Faster" in text and "### Heads up: changes that may need you to act\n- Split world.rs (d4)" in text
    assert "Merge branch" not in text and "### Other changes\n- Update the readme (f6)" in text


def test_versions_normalized():
    assert rel.normalize_version("1.1") == "1.1.0" and rel.normalize_version("v2.0.3-rc.1") == "2.0.3-rc.1"
    with pytest.raises(rel.ReleaseError):
        rel.normalize_version("next")


def test_orion_bumps_the_same_files_as_release_mjs(tmp_path):
    r = tmp_path / "orion"
    for rel_path in rel.ORION_JSON:
        (r / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (r / rel_path).write_text('{\n  "name": "x",\n  "version": "2.0.0"\n}\n')
    (r / "apps/daemon").mkdir(parents=True, exist_ok=True)
    (r / "apps/daemon/main.py").write_text('V = os.getenv("ORION_APP_VERSION") or "2.0.0"\n')
    (r / "apps/desktop/src-tauri/Cargo.toml").write_text('[package]\nname = "orion"\nversion = "2.0.0"\n')
    (r / "packages/shared-types/src").mkdir(parents=True, exist_ok=True)
    (r / "packages/shared-types/src/index.ts").write_text("export const ORION_VERSION = '2.0.0';\n")
    plan = rel.bump_plan(r, "2.1.0")
    assert sorted(p["path"] for p in plan) == sorted(rel.ORION_JSON + [t[0] for t in rel.ORION_TEXT])
    assert all("2.1.0" in p["after"] and "2.0.0" not in p["after"] for p in plan)
    assert rel.build_hooks(r) == rel.ORION_HOOKS            # checks only; the image is built by CI


def test_dry_run_writes_nothing(env):
    h = H(env)
    before = git(env["repo"], "status", "--porcelain"), git(env["repo"], "branch", "--list")
    rec = h.run(h.butler.start(env["repo"], "0.2", dry_run=True))
    assert rec["state"] == "dry_run" and rec["version"] == "0.2.0" and rec["commits"] == 5
    assert sorted(rec["bumps"]) == ["Cargo.toml", "package.json"]
    assert '-version = "0.1.0"' in rec["bump_diff"] and '+version = "0.2.0"' in rec["bump_diff"]
    assert 'version = "1"' in (env["repo"] / "Cargo.toml").read_text()   # dependency versions untouched
    assert (git(env["repo"], "status", "--porcelain"), git(env["repo"], "branch", "--list")) == before
    assert h.board.pending() == [] and h.proof.calls == [] and not env["calls"].exists()
    assert "Would sign" in rel.ReleaseButler.describe(rec)


def test_release_is_prepared_aside_then_pushed_and_drafted_only_after_approval(env):
    h = H(env)
    rec = h.run(h.butler.start(env["repo"], "0.2.0", name="Benilla"))
    assert rec["state"] == "waiting", rec
    # your own checkout is untouched; the release branch has the bump and the changelog
    assert json.loads((env["repo"] / "package.json").read_text())["version"] == "0.1.0"
    assert git(env["repo"], "rev-parse", "--abbrev-ref", "HEAD") == "main"
    shown = git(env["repo"], "show", "release/v0.2.0:package.json")
    assert '"version": "0.2.0"' in shown
    changelog = git(env["repo"], "show", "release/v0.2.0:CHANGELOG.md")
    assert "## Benilla 0.2.0" in changelog and "Loot tables for raids" in changelog
    assert h.proof.calls[0]["target"] == "release/v0.2.0"        # full tests through Proof Gate
    calls = env["calls"].read_text()
    assert calls.count("minisign -S -s") == 2 and "-W" in calls   # notes + source tarball, existing key flow
    assert len(rec["signatures"]) == 2 and "gh" not in calls.replace("minisign", "")
    assert git(env["origin"], "tag", "--list") == ""             # nothing left the machine
    d = h.board.pending()[0]
    assert d["task_type"] == "release.push" and br.TASK_TYPES["release.push"].max_level == 1
    h.approve("release.push")
    rec = h.butler.get(rec["id"])
    assert rec["state"] == "drafted", rec
    assert git(env["origin"], "tag", "--list") == "v0.2.0"
    assert "release/v0.2.0" in git(env["origin"], "branch", "--list")
    gh = [l for l in env["calls"].read_text().splitlines() if l.startswith("gh ")]
    assert gh[0].startswith("gh release create v0.2.0 --draft --verify-tag --title Benilla 0.2.0")
    assert ".minisig" in gh[0]
    # publishing is its own decision
    with pytest.raises(rel.ReleaseError):
        h.run(h.butler.ask_publish("0000000000"))
    h.run(h.butler.ask_publish(rec["id"]))
    assert h.board.pending()[0]["task_type"] == "release.publish"
    h.approve("release.publish")
    assert h.butler.get(rec["id"])["state"] == "published"
    assert "gh release edit v0.2.0 --draft=false" in env["calls"].read_text()


def test_rejected_push_sends_nothing(env):
    h = H(env)
    rec = h.run(h.butler.start(env["repo"], "0.2.0"))
    d = h.board.pending()[0]
    h.run(h.board.verdict(d["id"], "reject", br.packet_hash(d["packet"])))
    assert git(env["origin"], "tag", "--list") == "" and h.butler.get(rec["id"])["state"] == "declined"
    assert "gh release" not in env["calls"].read_text()


def test_failing_tests_stop_the_release(env):
    h = H(env, verdict="amber")
    rec = h.run(h.butler.start(env["repo"], "0.2.0"))
    assert rec["state"] == "failed" and "2 fail" in rec["message"]
    assert h.board.pending() == [] and h.notes and "stopped" in h.notes[-1][0]


def test_existing_version_and_no_key(env, monkeypatch):
    h = H(env)
    with pytest.raises(rel.ReleaseError):
        h.run(h.butler.start(env["repo"], "0.1.0"))
    monkeypatch.delenv("ORION_RELEASE_KEY")
    rec = h.run(h.butler.start(env["repo"], "0.3.0"))
    assert rec["state"] == "waiting" and rec["signatures"] == []
    assert any(s["name"] == "sign" and "no release key" in s["text"] for s in rec["steps"])
