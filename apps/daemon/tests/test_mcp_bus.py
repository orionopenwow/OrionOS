"""MCP bus v1: protocol over stdio, path and SQL guards, approvals, redaction, and the daemon routes."""
import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import boardroom as br
import mcp_routes
from mcp_tools import (Backend, DaemonUnavailable, McpConfig, OrionTools, ToolError, mysql_read,
                       project_command_allowed, redact, resolve_inside, resolve_project, sql_read_only)
from runner import ProcessRunner

DAEMON_DIR = Path(__file__).resolve().parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=env)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "first"], check=True, env=env)
    return path


def cfg_for(*roots: Path, **extra) -> McpConfig:
    return McpConfig(roots=list(roots), **extra)


class FakeBackend(Backend):
    """Records what would go to the daemon. `daemon_up=False` behaves as if Orion isn't running."""

    def __init__(self, daemon_up=True):
        self.base_url = "http://test"
        self.timeout = 1
        self._local_runner = None
        self._preload = None
        self.daemon_up = daemon_up
        self.calls = []
        self.entries = []

    async def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if not self.daemon_up:
            raise DaemonUnavailable("down")
        if path == "/mcp/propose":
            return 200, {"decision": {"id": "dec-test", "summary": "the thing", "status": "pending"}}
        raise DaemonUnavailable("not faked")

    def grimoire_entries(self):
        return self.entries


# =====================================================================================  stdio protocol
class StdioClient:
    def __init__(self, cwd: Path, env: dict):
        self.proc = subprocess.Popen([sys.executable, str(DAEMON_DIR / "mcp_server.py")], cwd=str(cwd),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env={**os.environ, **env})

    def send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def recv(self):
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read().decode()[-2000:]
        return json.loads(line)

    def request(self, msg):
        self.send(msg)
        return self.recv()

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=20)


@pytest.fixture
def stdio(tmp_path):
    repo = make_repo(tmp_path / "proj")
    conf = tmp_path / "mcp.json"
    conf.write_text(json.dumps({"roots": [str(tmp_path)]}))
    client = StdioClient(repo, {"ORION_MCP_CONFIG": str(conf), "ORION_DAEMON_URL": "http://127.0.0.1:9",
                                "PYTHONPATH": str(DAEMON_DIR)})
    yield client, repo
    client.close()


def test_stdio_handshake_list_and_call(stdio):
    c, repo = stdio
    init = c.request({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "pytest", "version": "1"}}})
    assert init["id"] == 1
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert init["result"]["serverInfo"]["name"] == "orion"
    c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})   # no reply expected

    listed = c.request({"jsonrpc": "2.0", "id": "two", "method": "tools/list"})
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    assert {"grimoire_list", "grimoire_run", "runner_start", "runner_status", "runner_logs", "git_status",
            "git_diff", "git_log", "git_commit", "cargo_check", "cargo_test", "mysql_query",
            "blender_run_script", "approval_status"} <= set(tools)
    for t in tools.values():   # every tool declares its risk
        assert t["inputSchema"]["type"] == "object"
        assert t["_meta"]["orion/risk"] in ("read", "run", "approval")
        assert "Risk:" in t["description"]
    assert tools["git_status"]["annotations"]["readOnlyHint"] is True
    assert tools["git_commit"]["_meta"]["orion/risk"] == "approval"

    (repo / "new.txt").write_text("x")
    res = c.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "git_status", "arguments": {}}})
    assert res["result"]["isError"] is False
    assert "new.txt" in res["result"]["content"][0]["text"]

    bad = c.request({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "git_status", "arguments": {"project": "/etc"}}})
    assert bad["result"]["isError"] is True

    assert c.request({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "rm_rf", "arguments": {}}})["error"]["code"] == -32602
    assert c.request({"jsonrpc": "2.0", "id": 6, "method": "nope"})["error"]["code"] == -32601
    assert c.request({"jsonrpc": "2.0", "id": 7, "method": "ping"})["result"] == {}

    c.proc.stdin.write(b"{not json\n")
    c.proc.stdin.flush()
    assert c.recv()["error"]["code"] == -32700


def test_stdio_unknown_version_and_batch(stdio):
    c, _ = stdio
    init = c.request({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "1999-01-01", "capabilities": {},
                                 "clientInfo": {"name": "old", "version": "0"}}})
    assert init["result"]["protocolVersion"] == "2025-11-25"   # our newest; the client decides if it can cope
    batch = c.request([{"jsonrpc": "2.0", "id": 10, "method": "ping"},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 11, "method": "tools/list"}])
    assert sorted(r["id"] for r in batch) == [10, 11]


def test_official_sdk_client_can_talk_to_it(tmp_path):
    mcp = pytest.importorskip("mcp")
    import anyio
    repo = make_repo(tmp_path / "proj")
    conf = tmp_path / "mcp.json"
    conf.write_text(json.dumps({"roots": [str(tmp_path)]}))

    async def go():
        params = mcp.StdioServerParameters(
            command=sys.executable, args=[str(DAEMON_DIR / "mcp_server.py")], cwd=str(repo),
            env={**os.environ, "ORION_MCP_CONFIG": str(conf), "ORION_DAEMON_URL": "http://127.0.0.1:9"})
        async with mcp.stdio_client(params) as (r, w):
            async with mcp.ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool("git_log", {"limit": 1})
                return names, res
    names, res = anyio.run(go)
    assert "git_commit" in names
    assert "first" in res.content[0].text


# =====================================================================================  paths
def test_project_paths_stay_inside_allowed_roots(tmp_path):
    root = tmp_path / "root"
    (root / "proj" / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    cfg = cfg_for(root)
    assert resolve_project(str(root / "proj"), cfg) == (root / "proj").resolve()
    assert resolve_project("proj", cfg) == (root / "proj").resolve()        # relative to the first root
    for bad in (str(outside), str(root / "proj" / ".." / ".." / "outside"), str(root / "escape"),
                "/etc", "proj\x00", "", str(root / "missing")):
        with pytest.raises(ToolError):
            resolve_project(bad, cfg)
    proj = (root / "proj").resolve()
    (proj / "a.py").write_text("")
    assert resolve_inside(proj, "a.py") == proj / "a.py"
    for bad in ("../../outside", "/etc/passwd", "src/../../x"):
        with pytest.raises(ToolError):
            resolve_inside(proj, bad, must_exist=False)


def test_config_file_adds_roots(tmp_path):
    extra = tmp_path / "games"
    extra.mkdir()
    conf = tmp_path / "mcp.json"
    conf.write_text(json.dumps({"roots": [str(extra)], "max_output_chars": 5000,
                                "commands": {str(extra): ["./serve.sh"]}}))
    cfg = McpConfig.load(conf, repo_root=tmp_path / "repo")
    assert extra in cfg.roots and cfg.max_output == 5000
    assert project_command_allowed(extra.resolve(), "./serve.sh", cfg)[0]


# =====================================================================================  SQL guard
@pytest.mark.parametrize("sql", [
    "SELECT * FROM users WHERE id = 1",
    "select name, `update` from t;",
    "SHOW TABLES",
    "DESCRIBE accounts",
    "EXPLAIN SELECT * FROM t",
    "SELECT 'DROP TABLE x; DELETE' AS s",
    "SELECT * FROM t -- delete everything\n",
    "SELECT updated_at FROM t /* insert */",
])
def test_sql_read_only_allows(sql):
    assert sql_read_only(sql) == (True, "")


@pytest.mark.parametrize("sql", [
    "UPDATE users SET admin = 1",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "DROP TABLE t",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT * FROM t FOR UPDATE",
    "EXPLAIN DELETE FROM t",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT SLEEP(100)",
    "SET GLOBAL read_only = 0",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "CALL do_things()",
])
def test_sql_read_only_refuses_writes(sql):
    assert sql_read_only(sql)[0] is False


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE t",
    "SELECT 1 /*! ; DROP TABLE t */",
    "SELECT 'unterminated",
    "",
])
def test_sql_guard_refuses_tricks_outright(sql):
    with pytest.raises(ToolError):
        sql_read_only(sql)


class FakeCursor:
    def __init__(self, log):
        self.log = log
        self.description = None

    def execute(self, sql):
        self.log.append(sql)
        if sql.startswith("SELECT"):
            self.description = [("id",), ("token",)]
        return 0

    def fetchmany(self, n):
        return [(i, "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789") for i in range(min(n, 3))]


class FakeDB:
    def __init__(self):
        self.log = []
        self.rolled_back = self.closed = False

    def cursor(self):
        return FakeCursor(self.log)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_mysql_read_runs_inside_read_only_transaction():
    db = FakeDB()
    out = mysql_read({"host": "h"}, "SELECT id, token FROM t", 2, 5, connect=lambda c: db)
    assert "START TRANSACTION READ ONLY" in db.log and "SET SESSION TRANSACTION READ ONLY" in db.log
    assert db.rolled_back and db.closed
    assert out["truncated"] is True and len(out["rows"]) == 2
    with pytest.raises(ToolError):
        mysql_read({"host": "h"}, "DELETE FROM t", 2, 5, connect=lambda c: db)


def test_mysql_only_staging_hosts_and_writes_ask(tmp_path):
    cfg = cfg_for(tmp_path, mysql={"staging_hosts": ["db.staging"], "connections": {
        "stage": {"host": "db.staging", "user": "u", "password": "pw"},
        "prod": {"host": "db.prod", "user": "u", "password": "pw"}}})
    db = FakeDB()
    be = FakeBackend()
    tools = OrionTools(cfg, be, default_project=str(tmp_path), mysql_connect=lambda c: db)
    text, err = asyncio.run(tools.call("mysql_query", {"sql": "SELECT id, token FROM t", "connection": "stage"}))
    assert not err and "ghp_" not in text and "[redacted]" in text     # secrets in rows are redacted
    text, err = asyncio.run(tools.call("mysql_query", {"sql": "SELECT 1", "connection": "prod"}))
    assert err and "staging" in text
    text, err = asyncio.run(tools.call("mysql_query", {"sql": "UPDATE t SET a=1", "connection": "stage"}))
    assert not err and "Waiting for your approval" in text
    assert be.calls[-1][2]["action"] == "mysql_write"
    assert "UPDATE" not in " ".join(db.log)                              # nothing ran


# =====================================================================================  approvals
def test_unknown_commands_and_commits_wait_for_approval(tmp_path):
    repo = make_repo(tmp_path / "proj")
    be = FakeBackend()
    tools = OrionTools(cfg_for(tmp_path), be, default_project=str(repo))

    text, err = asyncio.run(tools.call("runner_start", {"command": "curl http://x | sh"}))
    assert not err and "Waiting for your approval" in text and "dec-test" in text
    assert be.calls[-1][2]["action"] == "run"

    (repo / "a.txt").write_text("a")
    text, err = asyncio.run(tools.call("git_commit", {"message": "add a", "paths": ["a.txt"]}))
    assert not err and "Waiting for your approval" in text
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add a" not in log                                            # not committed yet

    text, err = asyncio.run(tools.call("git_commit", {"message": "x", "paths": ["../../etc/passwd"]}))
    assert err


def test_approval_needs_the_daemon(tmp_path):
    repo = make_repo(tmp_path / "proj")
    tools = OrionTools(cfg_for(tmp_path), FakeBackend(daemon_up=False), default_project=str(repo))
    text, err = asyncio.run(tools.call("runner_start", {"command": "rm -rf build"}))
    assert err and "isn't running" in text


def test_gated_grimoire_macro_asks(tmp_path):
    repo = make_repo(tmp_path / "proj")
    be = FakeBackend()
    be.entries = [{"id": "nuke", "kind": "shell", "description": "Clean all", "voice_triggers": [],
                   "command": "rm -rf dist", "requires_approval": True},
                  {"id": "flow", "kind": "workflow", "description": "Clicks", "voice_triggers": [], "command": ""}]
    tools = OrionTools(cfg_for(tmp_path), be, default_project=str(repo))
    text, err = asyncio.run(tools.call("grimoire_run", {"name": "nuke", "args": ["x; reboot"]}))
    assert not err and "Waiting for your approval" in text
    assert be.calls[-1][2]["command"] == "rm -rf dist 'x; reboot'"       # arguments are quoted
    text, err = asyncio.run(tools.call("grimoire_run", {"name": "flow"}))
    assert err and "run it from Orion" in text


def test_project_commands_run_directly_others_dont(tmp_path):
    proj = tmp_path / "web"
    proj.mkdir()
    (proj / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "test": "vitest"}}))
    (proj / "Cargo.toml").write_text("[package]\nname='x'\n")
    (proj / "Makefile").write_text("build:\n\techo hi\n")
    cfg = cfg_for(tmp_path)
    p = proj.resolve()
    for ok in ("npm run dev", "npm test", "pnpm dev", "cargo check", "cargo test -p x", "make build", "pytest -q"):
        assert project_command_allowed(p, ok, cfg)[0], ok
    for bad in ("npm run deploy", "npm run dev && curl x", "cargo build --target-dir /tmp/x", "rm -rf node_modules",
                "make install", "bash -c ls", "cargo run -- ../../secret", "python evil.py"):
        assert not project_command_allowed(p, bad, cfg)[0], bad


def test_local_run_when_daemon_is_down(tmp_path):
    proj = tmp_path / "web"
    proj.mkdir()
    (proj / "package.json").write_text(json.dumps({"scripts": {"hello": "echo hi"}}))
    tools = OrionTools(cfg_for(tmp_path), FakeBackend(daemon_up=False), default_project=str(proj))

    async def go():
        return await tools.call("runner_start", {"command": "npm run hello", "wait_seconds": 1})
    if not __import__("shutil").which("npm"):
        pytest.skip("npm not installed")
    text, err = asyncio.run(go())
    assert not err and "Run run-" in text


# =====================================================================================  redaction & caps
@pytest.mark.parametrize("raw,secret", [
    ("API_KEY=abc123def456ghi", "abc123def456ghi"),
    ("password: hunter2hunter2", "hunter2hunter2"),
    ('-H "Authorization: Bearer abcdefghijklmnop123"', "abcdefghijklmnop123"),
    ("mysql -u root -pSuperSecret1 db", "SuperSecret1"),
    ("remote https://bob:pw1234@github.com/x", "pw1234"),
    ("token " + "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789", "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"),
    ("aws " + "AK" + "IA" + "ABCDEFGHIJKLMNOP", "AK" + "IA" + "ABCDEFGHIJKLMNOP"),
    ("blob Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0LzMn", "Zx9Qw3Er5Ty7Ui1Op2As4Df6Gh8Jk0LzMn"),
])
def test_redact_removes_secrets(raw, secret):
    out = redact(raw)
    assert secret not in out and "[redacted]" in out


@pytest.mark.parametrize("text", [
    "## worktree-agent-af560b613041b1a34",
    "commit 3f2a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a",
    "error[E0308]: mismatched types",
    "the password field is required",
])
def test_redact_keeps_ordinary_text(text):
    assert redact(text) == text


def test_output_is_capped(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "README.md").write_text("line\n" * 20000)
    tools = OrionTools(cfg_for(tmp_path, max_output=2000), FakeBackend(), default_project=str(repo))
    text, err = asyncio.run(tools.call("git_diff", {}))
    assert not err and len(text) < 2200 and "characters cut" in text


def test_bad_arguments_are_tool_errors(tmp_path):
    tools = OrionTools(cfg_for(tmp_path), FakeBackend(), default_project=str(tmp_path))
    text, err = asyncio.run(tools.call("git_log", {"limit": "ten"}))
    assert err and "integer" in text
    text, err = asyncio.run(tools.call("git_log", {"evil": 1}))
    assert err
    text, err = asyncio.run(tools.call("git_diff", {"ref": "--output=/tmp/x"}))
    assert err and "valid git ref" in text


def test_blender_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    tools = OrionTools(cfg_for(tmp_path), FakeBackend(), default_project=str(tmp_path))
    text, err = asyncio.run(tools.call("blender_run_script", {"script": "x.py"}))
    assert err and "isn't installed" in text


# =====================================================================================  daemon routes + executors
class RouteHarness:
    def __init__(self, tmp_path, monkeypatch):
        conf = tmp_path / "mcp.json"
        conf.write_text(json.dumps({"roots": [str(tmp_path)]}))
        monkeypatch.setenv("ORION_MCP_CONFIG", str(conf))
        for k, v in GIT_ENV.items():
            monkeypatch.setenv(k, v)

        async def quiet(_e):
            return None
        self.board = br.Boardroom(tmp_path / "data")
        self.runner = ProcessRunner(quiet)
        self.daemon = types.SimpleNamespace(board=self.board, ext=types.SimpleNamespace(
            runner=self.runner, grimoire=types.SimpleNamespace(get=lambda _id: None)))
        mcp_routes.install(self.daemon)
        app = FastAPI()
        app.include_router(mcp_routes.router)
        self.app = app

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")


def test_commit_is_proposed_then_made_only_after_signed_approval(tmp_path, monkeypatch):
    h = RouteHarness(tmp_path, monkeypatch)
    repo = make_repo(tmp_path / "proj")
    (repo / "b.txt").write_text("b")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)

    async def go():
        async with h.client() as c:
            r = await c.post("/mcp/propose", json={"action": "git_commit", "project": str(repo), "message": "add b"})
            assert r.status_code == 200, r.text
            dec_id = r.json()["decision"]["id"]
            d = h.board.get(dec_id)
            assert d["status"] == "pending" and d["task_type"] == "mcp.git_commit"
            assert d["packet"]["details"]["files"] == ["b.txt"]
            assert br.TASK_TYPES["mcp.git_commit"].max_level == 1           # never runs without asking
            await h.board.verdict(dec_id, "approve", d["packet_hash"], signer="test")
            await asyncio.gather(*list(mcp_routes._jobs))
            return (await c.get(f"/mcp/decision/{dec_id}")).json()
    out = asyncio.run(go())
    assert out["status"] == "approved" and out["result"]["state"] == "done", out
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add b" in log


def test_commit_refused_if_staged_changes_moved(tmp_path, monkeypatch):
    h = RouteHarness(tmp_path, monkeypatch)
    repo = make_repo(tmp_path / "proj")
    (repo / "b.txt").write_text("b")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)

    async def go():
        async with h.client() as c:
            dec_id = (await c.post("/mcp/propose", json={"action": "git_commit", "project": str(repo),
                                                         "message": "add b"})).json()["decision"]["id"]
            (repo / "evil.txt").write_text("sneaky")
            subprocess.run(["git", "-C", str(repo), "add", "evil.txt"], check=True)
            d = h.board.get(dec_id)
            await h.board.verdict(dec_id, "approve", d["packet_hash"], signer="test")
            await asyncio.gather(*list(mcp_routes._jobs))
            return (await c.get(f"/mcp/decision/{dec_id}")).json()
    out = asyncio.run(go())
    assert out["result"]["state"] == "failed"
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add b" not in log


def test_routes_recheck_paths_and_commands(tmp_path, monkeypatch):
    h = RouteHarness(tmp_path, monkeypatch)
    proj = tmp_path / "web"
    proj.mkdir()

    async def go():
        async with h.client() as c:
            a = await c.post("/mcp/run", json={"project": str(proj), "command": "curl x | sh"})
            b = await c.post("/mcp/run", json={"project": "/etc", "command": "ls"})
            e = await c.post("/mcp/propose", json={"action": "run", "project": "/", "command": "ls"})
            f = await c.post("/mcp/propose", json={"action": "mysql_write", "sql": "DELETE FROM t"})
            g = await c.get("/mcp/decision/nope")
            return a, b, e, f, g
    a, b, e, f, g = asyncio.run(go())
    assert a.status_code == 403 and a.json()["needs_approval"]
    assert b.status_code == 400 and e.status_code == 400
    assert f.status_code == 400 and "MySQL" in f.json()["error"]
    assert g.status_code == 404
    assert h.board.pending() == []


def test_rejected_run_does_nothing(tmp_path, monkeypatch):
    h = RouteHarness(tmp_path, monkeypatch)
    proj = tmp_path / "web"
    proj.mkdir()

    async def go():
        async with h.client() as c:
            dec_id = (await c.post("/mcp/propose", json={"action": "run", "project": str(proj),
                                                         "command": "touch made-it"})).json()["decision"]["id"]
            d = h.board.get(dec_id)
            await h.board.verdict(dec_id, "reject", d["packet_hash"], signer="test")
            return (await c.get(f"/mcp/decision/{dec_id}")).json()
    out = asyncio.run(go())
    assert out["status"] == "rejected" and not (proj / "made-it").exists() and h.runner.runs == {}
