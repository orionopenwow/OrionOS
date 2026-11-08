"""Secret Shield (toolkit tool 18): patterns, repo scans, plain-words reports, alerts, routes, MCP tool, git hook.

Every fake secret here is assembled at test time from pieces, so this file never contains anything a secret
scanner would flag (and none of these values is a real key).
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import secret_shield as ss
from mcp_tools import Backend, DaemonUnavailable, McpConfig, OrionTools

DAEMON_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DAEMON_DIR.parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}
NODE = shutil.which("node")

# Random-looking bodies (fixed so tests are repeatable). Joined with their prefixes only at runtime.
B36 = "Q7f3Kp9Zr2Lm8Xv4Bn6Wt1Yc5Hd0JsGa3Uea"
B40 = B36 + "R8iO"


def fake(kind: str) -> str:
    parts = {
        "openai": ["sk-", "proj-", B36],
        "anthropic": ["sk-", "ant-", "api03-", B40],
        "github": ["gh", "p_", B36],
        "aws": ["AK", "IA", "Q7F3KP9ZR2LM8XV4"],
        "telegram": ["7123456789", ":AA", B36[:33]],
        "discord": ["MTA", "xMjM0NTY3ODkwMTIzNDU2", ".", "Gk9xYz", ".", B36[:30]],
        "slack": ["xo", "xb-", "1234567890-", B36[:20]],
        "stripe": ["sk", "_live_", B36[:24]],
        "google": ["AI", "za", B36[:35]],
        "pem": ["-----BEGIN ", "OPENSSH ", "PRIVATE KEY-----"],
    }[kind]
    return "".join(parts)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("hello\n")
    (path / ".gitignore").write_text("ignored.txt\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "first")
    return path


# =====================================================================================  patterns
@pytest.mark.parametrize("kind,expect", [
    ("openai", "openai"), ("anthropic", "anthropic"), ("github", "github"), ("aws", "aws"),
    ("telegram", "telegram"), ("discord", "discord"), ("slack", "slack"), ("stripe", "stripe"),
    ("google", "google"), ("pem", "private_key"),
])
def test_each_secret_kind_is_caught_and_never_echoed(kind, expect):
    secret = fake(kind)
    line = f'CONFIG_VALUE = "{secret}"'
    found = ss.scan_line(line, "src/app.py", 7, ss.load_rules())
    assert found, kind
    assert found[0]["kind"] == expect
    assert found[0]["line"] == 7 and found[0]["file"] == "src/app.py"
    blob = json.dumps(found)
    assert secret not in blob
    if kind != "pem":
        assert secret[8:24] not in blob      # not even a slice of it


def test_password_in_url_and_generic_assignment():
    rules = ss.load_rules()
    pw = "Tr0ub4dor" + "Horse99"
    f = ss.scan_line(f"DB_URL=mysql://game:{pw}@db.staging.lan/game", "deploy.env.txt", 1, rules)
    assert [x["kind"] for x in f] == ["url_password"]
    assert pw not in f[0]["preview"] and "[redacted]" in f[0]["preview"]
    g = ss.scan_line(f'MYSQL_PASSWORD = "{"K9x" + "Lq2Vm7Rt4Zp8Wy"}"', "settings.py", 3, rules)
    assert [x["kind"] for x in g] == ["generic"]


@pytest.mark.parametrize("line", [
    "password = os.environ['MYSQL_PASSWORD']",
    'token: "${{ secrets.GITHUB_TOKEN }}"',
    "api_key = settings.openai_api_key",
    "password: changeme",
    "postgres://user:${DB_PASSWORD}@localhost/db",
    "git push origin main",
    "const TOKEN_RE = /[A-Za-z0-9]{32,}/;",
    "secret_key = <your-secret-key>",
    "def check_token(token: str) -> bool:",
    "https://github.com/gitleaks/gitleaks/releases/download/v8.28.0/gitleaks_8.28.0_linux_x64.tar.gz",
])
def test_ordinary_lines_are_not_flagged(line):
    assert ss.scan_line(line, "src/x.py", 1, ss.load_rules()) == []


def test_inline_allow_marker_and_test_allowlist():
    rules = ss.load_rules()
    secret = fake("openai")
    assert ss.scan_line(f'KEY = "{secret}"  # gitleaks:allow', "src/a.py", 1, rules) == []
    alpha = "sk-" + "abcdefgh" + "ijklmnop1234"      # the deliberate fakes used by the redaction tests
    assert ss.scan_line(f"x = '{alpha}'", "apps/daemon/tests/test_x.py", 1, rules) == []
    assert ss.scan_line(f"x = '{alpha}'", "apps/daemon/main.py", 1, rules), "allowlist is limited to tests"


def test_fingerprint_is_stable_and_one_way():
    a = ss.fingerprint("openai-api-key", fake("openai"))
    assert a == ss.fingerprint("openai-api-key", fake("openai")) and len(a) == 12
    assert a not in fake("openai")


def test_key_files_are_recognised_without_opening():
    for p in (".env", "apps/daemon/.env", ".env.local", ".orion-signing/orion.key", "id_ed25519", "x/server.key"):
        assert ss.is_key_file(p), p
    for p in (".env.example", "apps/daemon/.env.example", "environment.py", "keys.md", "docs/key.png"):
        assert not ss.is_key_file(p), p


# =====================================================================================  repo scans
def test_working_tree_and_history(tmp_path):
    repo = make_repo(tmp_path / "proj")
    secret = fake("github")
    (repo / "deploy.sh").write_text(f"#!/bin/sh\nexport GH={secret}\n")
    git(repo, "add", "deploy.sh")
    git(repo, "commit", "-q", "-m", "deploy")
    (repo / "deploy.sh").write_text("#!/bin/sh\nexport GH=$GH_TOKEN\n")    # removed from the files...
    git(repo, "commit", "-q", "-am", "fix")
    (repo / "notes.txt").write_text("call " + fake("telegram") + " later\n")   # untracked but not ignored
    (repo / "ignored.txt").write_text(fake("openai") + "\n")                  # ignored: left alone

    tree = ss.scan_working_tree(repo)
    assert [(f["file"], f["kind"]) for f in tree] == [("notes.txt", "telegram")]
    hist, commits = ss.scan_history(repo, max_commits=None)
    assert commits == 3
    assert [(f["file"], f["line"], f["kind"]) for f in hist] == [("deploy.sh", 2, "github")]   # ...but not history
    assert hist[0]["commit"]

    report = ss.scan_repos([repo], history="full", engine="builtin")
    assert not report["ok"] and len(report["findings"]) == 2
    blob = json.dumps(report)
    assert secret not in blob and fake("telegram") not in blob and fake("openai") not in blob
    s = report["summary"]
    assert "Found 2 possible secrets in proj" in s
    assert "deploy.sh line 2 (in past commit" in s and "notes.txt line 1" in s
    assert "BotFather" in s and "github.com/settings/tokens" in s      # rotation steps for what was found
    quick = ss.scan_repos([repo], history="none", engine="builtin")
    assert len(quick["findings"]) == 1


def test_tracked_env_file_is_reported_but_never_read(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    (repo / ".env").write_text("OPENAI_API_KEY=" + fake("openai") + "\n")
    git(repo, "add", "-f", ".env")
    git(repo, "commit", "-q", "-m", "oops")
    opened = []
    real = Path.read_bytes

    def spy(self):
        opened.append(self.name)
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", spy)
    tree = ss.scan_working_tree(repo)
    assert ".env" not in opened
    assert [(f["file"], f["kind"], f["preview"]) for f in tree] == [(".env", "key_file", "(file not opened)")]
    hist, _ = ss.scan_history(repo, max_commits=None)
    assert [f["kind"] for f in hist] == ["key_file"]
    assert fake("openai") not in json.dumps(hist)


def test_clean_repo_reads_plainly(tmp_path):
    repo = make_repo(tmp_path / "clean")
    r = ss.scan_repos([repo], history="quick", engine="builtin")
    assert r["ok"] and r["summary"] == "Checked clean (the current files and the last 300 commits). Nothing secret found."


def test_default_repos_finds_repos_inside_a_root(tmp_path):
    make_repo(tmp_path / "code" / "a")
    make_repo(tmp_path / "code" / "b")
    (tmp_path / "code" / "notes").mkdir()
    solo = make_repo(tmp_path / "solo")
    assert [p.name for p in ss.default_repos([tmp_path / "code", solo])] == ["a", "b", "solo"]


def test_this_repo_is_clean():
    """The real repo (with the allowlist for the tests' deliberate fakes) has no secrets in its files."""
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    found = ss.scan_working_tree(REPO_ROOT)
    assert found == [], ss.plain_report([{"name": "orion"}], found, "none")


@pytest.mark.skipif(not (os.getenv("ORION_GITLEAKS") or shutil.which("gitleaks")), reason="gitleaks not installed")
def test_gitleaks_history_engine(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "a.py").write_text(f'GITHUB = "{fake("github")}"\n')
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "a")
    hist, ran = ss.gitleaks_history(repo, None, ss.gitleaks_path())
    assert ran and hist and hist[0]["file"] == "a.py" and hist[0]["kind"] == "github"
    assert fake("github") not in json.dumps(hist)


# =====================================================================================  service: alert + card
class FakeBus:
    def __init__(self):
        self.events, self.logs = [], []

    async def publish(self, e, **k):
        self.events.append(e)

    async def log(self, m, level="info"):
        self.logs.append(m)


class FakeDaemon:
    def __init__(self, tmp):
        from approval_queue import ApprovalQueue
        self.approvals = ApprovalQueue(Path(tmp) / "approvals.json")
        self.bus = FakeBus()


def test_findings_raise_one_alert_and_card(tmp_path, monkeypatch):
    import alerts
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append(a))
    repo = make_repo(tmp_path / "proj")
    (repo / "bot.py").write_text(f'TOKEN = "{fake("discord")}"\n')
    d = FakeDaemon(tmp_path)
    shield = ss.SecretShield(tmp_path / "data", d)
    r1 = asyncio.run(shield.scan([repo], "none", "builtin"))
    assert len(r1["findings"]) == 1
    assert len(sent) == 1 and "Secret Shield found 1 possible secret" in sent[0][1]
    cards = d.approvals.pending()
    assert len(cards) == 1 and cards[0]["kind"] == "secret_shield"
    assert "Discord" in cards[0]["original"] and fake("discord") not in json.dumps(cards[0])
    assert d.bus.events[0]["event"] == "APPROVAL_REQUIRED"
    # Same findings again: no second alert or card. State survives a restart.
    asyncio.run(ss.SecretShield(tmp_path / "data", d).scan([repo], "none", "builtin"))
    assert len(sent) == 1 and len(d.approvals.pending()) == 1
    assert ss.SecretShield(tmp_path / "data", d).last()["findings"][0]["kind"] == "discord"
    asyncio.run(ss.decide_card(d, cards[0], "approve"))
    assert d.approvals.get(cards[0]["id"])["status"] == "handled"


# =====================================================================================  routes
def test_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import alerts
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: None)
    repo = make_repo(tmp_path / "allowed" / "proj")
    outside = make_repo(tmp_path / "outside")
    (repo / "k.txt").write_text(fake("aws") + "\n")
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"roots": [str(tmp_path / "allowed")]}))
    monkeypatch.setenv("ORION_MCP_CONFIG", str(cfg))
    d = FakeDaemon(tmp_path)
    app = FastAPI()
    app.include_router(ss.make_router(lambda: d))
    c = TestClient(app)
    r = c.post("/secret-shield/scan", json={"repos": [str(repo)], "history": "none", "engine": "builtin"})
    assert r.status_code == 200 and r.json()["findings"][0]["kind"] == "aws"
    assert fake("aws") not in r.text
    assert c.get("/secret-shield/last").json()["report"]["findings"][0]["file"] == "k.txt"
    assert c.post("/secret-shield/scan", json={"repos": [str(outside)]}).status_code == 400
    assert c.post("/secret-shield/scan", json={"history": "everything"}).status_code == 400
    g = c.get("/secret-shield/guide/telegram").json()
    assert any("BotFather" in s for s in g["steps"])
    assert c.get("/secret-shield/guide/nope").status_code == 404
    assert "gitleaks" in c.get("/secret-shield/status").json()


# =====================================================================================  MCP tool
class DownBackend(Backend):
    def __init__(self):
        self.base_url, self.timeout, self._local_runner, self._preload = "http://test", 1, None, None
        self.calls = []

    async def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        raise DaemonUnavailable("down")


def test_mcp_tool_works_without_the_daemon(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "x.py").write_text(f'KEY = "{fake("anthropic")}"\n')
    be = DownBackend()
    tools = OrionTools(cfg=McpConfig(roots=[tmp_path]), backend=be, default_project=str(repo))
    assert "secret_shield_scan" in tools.tools
    desc = next(t for t in tools.list_tools() if t["name"] == "secret_shield_scan")
    assert desc["annotations"]["readOnlyHint"]
    text, err = asyncio.run(tools.call("secret_shield_scan", {"history": "none"}))
    assert not err, text
    assert "Anthropic" in text and "x.py line 1" in text and "console.anthropic.com" in text
    assert fake("anthropic") not in text and "no alert was raised" in text
    assert be.calls[0][1] == "/secret-shield/scan"
    text, err = asyncio.run(tools.call("secret_shield_scan", {"repos": ["/etc"]}))
    assert err


# =====================================================================================  node fallback + git hook
@pytest.mark.skipif(not NODE, reason="node not installed")
def test_node_scanner_matches_python(tmp_path):
    repo = make_repo(tmp_path / "proj")
    body = "\n".join([f'a = "{fake("openai")}"', "plain line", f"b {fake('slack')}",
                      f"db=postgres://u:{'Zq8' + 'Lm3Vx9'}@host/x", f'password = "{"K9x" + "Lq2Vm7Rt4Zp8Wy"}"',
                      f'ok = "{fake("google")}"  # gitleaks:allow'])
    (repo / "conf.py").write_text(body + "\n")
    out = subprocess.run([NODE, str(REPO_ROOT / "scripts/secret-shield/scanner.mjs"), "--tree", "--json", str(repo)],
                         capture_output=True, text=True)
    assert out.returncode == 1, out.stderr
    js = {(f["file"], f["line"], f["rule"], f["fingerprint"]) for f in json.loads(out.stdout)}
    py = {(f["file"], f["line"], f["rule"], f["fingerprint"]) for f in ss.scan_working_tree(repo)}
    assert js == py and len(py) == 4
    assert fake("openai") not in out.stdout


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_pre_commit_hook_blocks_secrets(tmp_path):
    repo = make_repo(tmp_path / "proj")
    for rel in (".githooks", "scripts/secret-shield"):
        shutil.copytree(REPO_ROOT / rel, repo / rel)
    (repo / "apps/daemon").mkdir(parents=True)
    shutil.copy(DAEMON_DIR / "secret_shield_rules.json", repo / "apps/daemon")
    shutil.copy(REPO_ROOT / ".gitleaks.toml", repo)
    env = {**os.environ, **GIT_ENV, "ORION_GITLEAKS": str(tmp_path / "no-gitleaks")}   # built-in scanner
    r = subprocess.run([NODE, "scripts/secret-shield/install-hooks.mjs"], cwd=repo, capture_output=True, text=True,
                       env=env)
    assert r.returncode == 0, r.stderr
    assert git(repo, "config", "--get", "core.hooksPath").strip() == ".githooks"
    git(repo, "add", "-A")
    subprocess.run(["git", "commit", "-q", "-m", "tools"], cwd=repo, check=True, env=env)

    (repo / "app.py").write_text("import os\n\nKEY = '" + fake("openai") + "'\n")
    git(repo, "add", "app.py")
    r = subprocess.run(["git", "commit", "-m", "leak"], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "Secret Shield stopped this commit" in r.stderr and "app.py:3" in r.stderr and "OpenAI" in r.stderr
    assert "How to fix it" in r.stderr and fake("openai") not in r.stderr + r.stdout
    assert git(repo, "log", "--format=%s", "-n1").strip() == "tools"

    (repo / "app.py").write_text("import os\n\nKEY = os.environ['OPENAI_API_KEY']\n")
    git(repo, "add", "app.py")
    subprocess.run(["git", "commit", "-q", "-m", "clean"], cwd=repo, check=True, env=env)
    assert git(repo, "log", "--format=%s", "-n1").strip() == "clean"
