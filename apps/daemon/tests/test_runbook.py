"""Project Runbook (Toolkit 10): learns a project's steps, writes justfile/.mise.toml only after approval,
never overwrites your own justfile, and runs steps through the runner (deploy asks first)."""
import asyncio
import json
import os
import shutil

import pytest

import boardroom as br
import runbook as rb


class FakeRun:
    def __init__(self, command, cwd, label, kind):
        self.id = f"run{len(command)}"
        self.command, self.cwd, self.label, self.kind = command, cwd, label, kind

    def info(self):
        return {"id": self.id, "command": self.command, "cwd": self.cwd, "running": True}


class FakeRunner:
    def __init__(self):
        self.started = []

    async def start(self, command, *, cwd=None, label="", kind="task", **k):
        self.started.append((command, cwd, kind))
        return FakeRun(command, cwd, label, kind)


def node_project(root, name="openwow"):
    p = root / name
    p.mkdir(parents=True)
    (p / "package.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "packageManager": "pnpm@8.15.4+sha256.abc",
        "engines": {"node": ">=20"},
        "scripts": {"dev": "node server.js", "test": "vitest run", "build": "tsc -p ."}}, indent=2))
    (p / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n")
    (p / ".nvmrc").write_text("v20.11.1\n")
    return p


def make(tmp_path, runner=None, grimoire=None):
    board = br.Boardroom(tmp_path / "data")
    book = rb.Runbook(tmp_path / "data", board=board, runner=runner or FakeRunner(),
                      search_dirs=lambda: [tmp_path / "projects"], grimoire=lambda: grimoire or [])
    board.executors["runbook.write"] = book.execute_write      # what ops_toolkit wires up in the daemon
    return board, book


def approve(board, task_type):
    d = next(x for x in board.pending() if x["task_type"] == task_type)
    return asyncio.run(board.verdict(d["id"], "approve", br.packet_hash(d["packet"])))


def test_detects_node_steps_and_pinned_versions(tmp_path):
    p = node_project(tmp_path / "projects")
    cmds = rb.detect_commands(p)
    assert cmds["run"]["command"] == "pnpm dev" and cmds["test"]["command"] == "pnpm test"
    assert cmds["build"]["command"] == "pnpm build" and cmds["run"]["source"] == "package.json"
    tools = rb.detect_tools(p)
    assert tools["node"]["version"] == "20.11.1" and tools["node"]["source"] == ".nvmrc"
    assert tools["pnpm"]["version"] == "8.15.4"


def test_rust_python_go_and_makefile_priority(tmp_path):
    r = tmp_path / "benilla"
    (r / "src").mkdir(parents=True)
    (r / "Cargo.toml").write_text('[package]\nname = "benilla"\nversion = "0.3.0"\nrust-version = "1.75"\n')
    (r / "src/main.rs").write_text("fn main() {}\n")
    (r / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.79.0"\n')
    cmds = rb.detect_commands(r)
    assert cmds["run"]["command"] == "cargo run" and cmds["test"]["command"] == "cargo test"
    assert rb.detect_tools(r)["rust"] == {"version": "1.79.0", "source": "rust-toolchain.toml"}
    (r / "Makefile").write_text("test:\n\tcargo test --all\nserve:\n\tcargo run --release\n")
    cmds = rb.detect_commands(r)
    assert cmds["test"]["command"] == "make test" and cmds["run"]["command"] == "make serve"   # Makefile wins

    py = tmp_path / "tool"
    (py / "tests").mkdir(parents=True)
    (py / "pyproject.toml").write_text('[project]\nname = "tool"\nrequires-python = ">=3.11"\n'
                                       '[project.scripts]\ntool = "tool:main"\n')
    (py / "uv.lock").write_text('version = 1\nrequires-python = ">=3.12"\n')
    cmds = rb.detect_commands(py)
    assert cmds["test"]["command"] == "uv run pytest" and cmds["run"]["command"] == "uv run tool"
    assert rb.detect_tools(py)["python"]["version"] == "3.12"

    go = tmp_path / "svc"
    go.mkdir()
    (go / "go.mod").write_text("module svc\n\ngo 1.22.1\n")
    assert rb.detect_commands(go)["test"]["command"] == "go test ./..."
    assert rb.detect_tools(go)["go"]["version"] == "1.22.1"


def test_grimoire_harvest_fills_gaps_but_never_destructive(tmp_path):
    p = node_project(tmp_path / "projects")
    entries = [
        {"id": "h1", "kind": "shell", "description": "deploy openwow", "command": "./scripts/deploy.sh staging",
         "cwd": str(p), "uses": 9},
        {"id": "h2", "kind": "shell", "description": "deploy cleanup", "command": "rm -rf /srv/openwow",
         "cwd": str(p), "uses": 50},
        {"id": "h3", "kind": "shell", "description": "deploy elsewhere", "command": "make deploy", "cwd": "/tmp"},
    ]
    cmds = rb.detect_commands(p, entries)
    assert cmds["deploy"] == {"command": "./scripts/deploy.sh staging",
                              "source": "your most-used commands (Scripts)"}
    assert cmds["run"]["source"] == "package.json"      # Grimoire only fills gaps


def test_first_write_waits_for_approval_then_updates_itself(tmp_path):
    p = node_project(tmp_path / "projects")
    board, book = make(tmp_path)
    res = asyncio.run(book.propose(p))
    assert res["state"] == "waiting"
    assert not (p / "justfile").exists() and not (p / ".mise.toml").exists()     # nothing written yet
    d = board.pending()[0]
    assert d["task_type"] == "runbook.write" and "+run:" in d["packet"]["details"]["diff"]
    assert br.TASK_TYPES["runbook.write"].max_level == 3                         # reversible: may learn
    done = approve(board, "runbook.write")
    assert done["status"] == "approved"
    just = (p / "justfile").read_text()
    assert rb.MARKER in just and "run:\n    pnpm dev" in just and "test:\n    pnpm test" in just
    mise = (p / ".mise.toml").read_text()
    assert 'node = "20.11.1"' in mise and 'pnpm = "8.15.4"' in mise
    assert asyncio.run(book.propose(p))["state"] == "up_to_date"
    # the project gains a lint script: Orion updates its own, unchanged file by itself
    pkg = json.loads((p / "package.json").read_text())
    pkg["scripts"]["lint"] = "eslint ."
    (p / "package.json").write_text(json.dumps(pkg))
    res = asyncio.run(book.propose(p))
    assert res["state"] == "applied" and "lint:\n    pnpm lint" in (p / "justfile").read_text()
    assert board.pending() == []
    # once you edit it, it's yours: Orion asks again and only adds
    (p / "justfile").write_text((p / "justfile").read_text() + "\nseed:\n    pnpm seed\n")
    pkg["scripts"]["deploy"] = "node deploy.js"
    (p / "package.json").write_text(json.dumps(pkg))
    res = asyncio.run(book.propose(p))
    assert res["state"] == "waiting" and res["files"][0]["kind"] == "yours"


def test_your_justfile_is_never_replaced(tmp_path):
    p = node_project(tmp_path / "projects")
    mine = "# my own\ntest:\n    pnpm vitest --run\n\nfmt:\n    prettier -w .\n"
    (p / "justfile").write_text(mine)
    board, book = make(tmp_path)
    res = asyncio.run(book.propose(p))
    f = next(x for x in res["files"] if x["path"] == "justfile")
    assert f["kind"] == "yours" and f["content"].startswith(mine)
    assert "\ntest:" not in f["content"][len(mine):]                       # your recipe kept, no duplicate
    approve(board, "runbook.write")
    text = (p / "justfile").read_text()
    assert text.startswith(mine) and "run:\n    pnpm dev" in text and "build:\n    pnpm build" in text


def test_approved_diff_is_not_applied_to_a_file_that_changed_since(tmp_path):
    p = node_project(tmp_path / "projects")
    board, book = make(tmp_path)
    asyncio.run(book.propose(p))
    (p / "justfile").write_text("run:\n    echo mine\n")      # written by you after the diff was shown
    d = approve(board, "runbook.write")
    assert "justfile" in d["executed"]["skipped"]
    assert (p / "justfile").read_text() == "run:\n    echo mine\n"


def test_run_uses_just_when_installed_else_the_raw_command(tmp_path, monkeypatch):
    p = node_project(tmp_path / "projects")
    runner = FakeRunner()
    board, book = make(tmp_path, runner)
    (p / "justfile").write_text("run:\n    pnpm dev\n\ntest:\n    pnpm test\n")
    monkeypatch.setattr(rb.shutil, "which", lambda n: None)
    res = asyncio.run(book.run(p, "run"))
    assert runner.started[-1] == ("pnpm dev", str(p), "service") and "just isn't installed" in res["message"]
    monkeypatch.setattr(rb.shutil, "which", lambda n: "/usr/bin/just" if n == "just" else None)
    asyncio.run(book.run(p, "test"))
    assert runner.started[-1] == ("just test", str(p), "task")


def test_deploy_and_destructive_steps_ask_first(tmp_path, monkeypatch):
    p = node_project(tmp_path / "projects")
    runner = FakeRunner()
    board, book = make(tmp_path, runner)
    monkeypatch.setattr(rb.shutil, "which", lambda n: "/usr/bin/just" if n == "just" else None)
    (p / "justfile").write_text("deploy:\n    scp -r dist server:/srv\n\nbuild:\n    rm -rf dist && pnpm build\n")
    res = asyncio.run(book.run(p, "deploy"))
    assert res["state"] == "waiting" and runner.started == []
    assert br.TASK_TYPES["runbook.run"].max_level == 1                         # sensitive: never automatic
    res = asyncio.run(book.run(p, "build"))
    assert res["state"] == "waiting" and runner.started == []
    with pytest.raises(rb.RunbookError):
        asyncio.run(book.run(p, "lint"))


def test_find_projects_by_spoken_name(tmp_path):
    node_project(tmp_path / "projects", "OpenWoW")
    node_project(tmp_path / "projects", "benilla-client")
    _, book = make(tmp_path)
    assert book.find("openwow").name == "OpenWoW"
    assert book.find("Benilla Client").name == "benilla-client"
    assert book.find("nothing") is None and book.find("/etc") is None
    assert book.match_phrase("test benilla client")[0] == "test"
    assert book.match_phrase("run the openwow project")[1].name == "OpenWoW"
    assert book.match_phrase("run the server") is None
