"""Review items B1 (API token, Host allowlist, extension pairing), B2 (Telegram pairing) and runner hardening."""
import asyncio
import json
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api_token
import main
import security
from conftest import LOCAL_BASE, auth_headers, daemon_client, ws_url


@pytest.fixture(scope="module")
def client():
    with daemon_client(main.app) as c:
        yield c


def bare():
    """A local client that does NOT have the token."""
    return TestClient(main.app, base_url=LOCAL_BASE)


# =====================================================================================  token on HTTP
def test_routes_need_the_token():
    c = bare()
    for method, path in (("GET", "/approvals"), ("GET", "/memory/recent"), ("POST", "/shutdown"),
                         ("POST", "/automation/execute"), ("POST", "/key-proxy/tokens"), ("POST", "/approvals/x")):
        r = c.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.headers.get("www-authenticate") == "Bearer"
    assert c.get("/approvals", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/approvals", headers={"Authorization": api_token.get()}).status_code == 401  # needs "Bearer"
    assert c.get("/approvals", headers=auth_headers()).status_code == 200


def test_health_says_only_ok_and_version_without_the_token():
    # The version stays public so the HUD can tell its own daemon is already running (Windows log, 2026-10-18).
    c = bare()
    r = c.get("/health")
    assert r.status_code == 200 and set(r.json()) == {"ok", "version", "build"} and r.json()["ok"] is True
    full = c.get("/health", headers=auth_headers()).json()
    assert full["ok"] is True and full["status"] == "ok" and "version" in full and "build" in full


def test_tripwire_alarm_stays_public():
    c = TestClient(main.app, base_url="http://intruder.example:9120")
    r = c.get("/tripwire/abc123/creds")
    assert r.status_code != 401 and "host not allowed" not in r.text   # the bait's own answer, not ours


def test_cors_preflight_needs_no_token_and_allows_the_header():
    r = bare().options("/assets/upload", headers={"Origin": "http://tauri.localhost",
                                                  "Access-Control-Request-Method": "POST"})
    assert r.status_code == 204
    assert "Authorization" in r.headers["access-control-allow-headers"]
    r = bare().get("/approvals", headers={"Origin": "http://tauri.localhost"})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == "http://tauri.localhost"


# =====================================================================================  Host allowlist
@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:9120", True), ("localhost:9120", True), ("LOCALHOST", True), ("[::1]:9120", True),
    ("localhost.:9120", True), ("evil.example:9120", False), ("127.0.0.1.nip.io:9120", False), ("", False),
    ("192.168.1.5:9120", False),
])
def test_host_allowlist(host, ok):
    assert security.host_allowed(host) is ok
    r = TestClient(main.app).get("/approvals", headers=auth_headers({"host": host}))
    assert (r.status_code == 200) is ok, (host, r.status_code)
    if not ok:
        assert r.status_code == 403


def test_extra_hosts_can_be_allowed(monkeypatch):
    monkeypatch.setenv("ORION_ALLOWED_HOSTS", "orion.lan")
    assert security.host_allowed("orion.lan:9120")


# =====================================================================================  WebSocket
def test_ws_needs_the_token(client):
    with pytest.raises(WebSocketDisconnect):
        with bare().websocket_connect("ws://127.0.0.1:9120/") as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with bare().websocket_connect("ws://127.0.0.1:9120/?token=nope") as ws:
            ws.receive_json()
    with client.websocket_connect(ws_url("/")) as ws:
        assert ws.receive_json()["event"] == "HELLO"
    with client.websocket_connect("ws://127.0.0.1:9120/ws", headers=auth_headers()) as ws:   # header works too
        assert ws.receive_json()["event"] == "HELLO"


def test_ws_refuses_foreign_host_even_with_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"ws://rebound.example:9120/?token={api_token.get()}") as ws:
            ws.receive_json()


# =====================================================================================  extension pairing
def test_extension_pairs_with_a_code_and_stays_limited(client):
    ext = {"origin": "chrome-extension://orion-bridge"}
    main.ext_pairing.forget_all()
    # the extension can't start pairing itself, and a guessed code is refused
    assert bare().post("/extension/pair/start", headers=ext).status_code == 403
    assert bare().post("/extension/pair", json={"code": "AAAA-AAAA"}, headers=ext).status_code == 403
    code = client.post("/extension/pair/start").json()["code"]
    assert len(code) == 9 and code[4] == "-"
    r = bare().post("/extension/pair", json={"code": code.lower()}, headers=ext)
    assert r.status_code == 200
    key = r.json()["key"]
    assert bare().post("/extension/pair", json={"code": code}, headers=ext).status_code == 403   # one use only
    assert client.get("/extension/paired").json()["paired"] == 1
    # the key opens the WebSocket from an extension origin, as a limited extension client ...
    with bare().websocket_connect(f"ws://127.0.0.1:9120/?token={key}", headers=ext) as ws:
        assert ws.receive_json()["event"] == "HELLO"
        ws.send_json({"event": "APPROVAL_DECISION", "id": "x", "decision": "approve"})
        for _ in range(40):
            m = ws.receive_json()
            if m.get("event") == "LOG":
                assert "not allowed" in m["message"]
                break
        else:
            raise AssertionError("privileged event was not refused")
    # ... but not from anywhere else, and never over HTTP
    with pytest.raises(WebSocketDisconnect):
        with bare().websocket_connect(f"ws://127.0.0.1:9120/?token={key}") as ws:
            ws.receive_json()
    assert bare().get("/approvals", headers={"Authorization": f"Bearer {key}"}).status_code == 401
    client.post("/extension/unpair")
    with pytest.raises(WebSocketDisconnect):
        with bare().websocket_connect(f"ws://127.0.0.1:9120/?token={key}", headers=ext) as ws:
            ws.receive_json()
    stored = (main.ext_pairing.path).read_text()
    assert key not in stored


def test_pairing_code_dies_after_wrong_tries(tmp_path):
    import extension_pairing as ep
    now = [1000.0]
    p = ep.ExtensionPairing(tmp_path, clock=lambda: now[0])
    code = p.start()["code"]
    for _ in range(ep.MAX_TRIES):
        assert p.pair("ZZZZ-ZZZZ") is None
    assert p.pair(code) is None                       # dead after too many wrong tries
    code = p.start()["code"]
    now[0] += ep.CODE_TTL_S + 1
    assert p.pair(code) is None                       # expired
    code = p.start()["code"]
    key = p.pair(code)
    assert key and p.check(key) and not p.check(key + "x")


# =====================================================================================  token file
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_token_file_is_private(tmp_path, monkeypatch):
    rt = tmp_path / "run"
    rt.mkdir()
    monkeypatch.delenv("ORION_TOKEN_FILE", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(rt))
    stale = tmp_path / "data-token"
    path = api_token.write()
    try:
        assert path == rt / "orion" / "daemon.token"
        assert path.read_text().strip() == api_token.get()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert api_token.read_local() == api_token.get()
    finally:
        api_token.remove()
    assert not path.exists()
    assert not stale.exists()


# =====================================================================================  Telegram pairing (B2)
class FakeTelegram:
    def __init__(self, tmp_path, clock=None):
        import telegram_bridge as tb
        self.user, self.foreign, self.sent = [], [], []

        async def on_user(text, chat):
            self.user.append((text, chat))

        async def on_foreign(item):
            self.foreign.append(item)
        kw = {"clock": clock} if clock else {}
        self.bridge = tb.TelegramBridge("123:abc", "", on_user_message=on_user, on_foreign_message=on_foreign,
                                        owner_file=tmp_path / "telegram_owner.json", **kw)

        async def send(text, chat_id=None, parse_mode="Markdown"):
            self.sent.append((chat_id, text))
            return True
        self.bridge.send_message = send

    def msg(self, chat, text, n=[0]):
        n[0] += 1
        return asyncio.run(self.bridge.handle_update(
            {"update_id": n[0], "message": {"message_id": n[0], "text": text, "date": 1, "chat": {"id": chat},
                                            "from": {"username": f"u{chat}"}}}))


def test_telegram_never_adopts_the_first_sender(tmp_path):
    t = FakeTelegram(tmp_path)
    t.msg(555, "/screen")
    t.msg(555, "hello")
    assert t.user == [] and t.bridge.chat_id == ""
    assert [f["text"] for f in t.foreign] == ["/screen", "hello"]      # only ever inbox text


def test_telegram_pairs_with_the_code_only(tmp_path):
    t = FakeTelegram(tmp_path)
    t.msg(555, "/pair ABC123")                                         # no pairing waiting
    assert t.bridge.chat_id == "" and "No pairing" in t.sent[-1][1]
    code = t.bridge.start_pairing()["code"]
    t.msg(666, "/pair WRONG1")
    assert t.bridge.chat_id == "" and "didn't match" in t.sent[-1][1]
    t.msg(777, f"/pair {code.lower()}")
    assert t.bridge.chat_id == "777"
    assert json.loads((tmp_path / "telegram_owner.json").read_text())["chat_id"] == "777"
    t.msg(777, "/status")
    t.msg(666, "/screen")
    assert t.user == [("/status", "777")]
    assert t.foreign[-1]["thread_id"] == "666"
    t.msg(666, f"/pair {code}")                                        # the code is single-use
    assert t.bridge.chat_id == "777"
    # a restart remembers the pairing (and it wins over TELEGRAM_CHAT_ID)
    import telegram_bridge as tb
    again = tb.TelegramBridge("123:abc", "999", owner_file=tmp_path / "telegram_owner.json")
    assert again.chat_id == "777"
    t.bridge.unpair()
    assert t.bridge.chat_id == "" and tb.load_owner(tmp_path / "telegram_owner.json") == ""


def test_telegram_code_expires_and_dies_after_wrong_tries(tmp_path):
    import telegram_bridge as tb
    now = [100.0]
    t = FakeTelegram(tmp_path, clock=lambda: now[0])
    code = t.bridge.start_pairing()["code"]
    for _ in range(tb.PAIR_MAX_TRIES):
        t.msg(1, "/pair NOPE00")
    t.msg(1, f"/pair {code}")
    assert t.bridge.chat_id == ""
    code = t.bridge.start_pairing()["code"]
    now[0] += tb.PAIR_TTL_S + 1
    t.msg(1, f"/pair {code}")
    assert t.bridge.chat_id == ""


def test_telegram_pair_routes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main.daemon, "telegram", None)
    assert client.post("/telegram/pair").status_code == 409
    assert client.get("/telegram/pair").json() == {"paired": False, "pending": False, "running": False}
    t = FakeTelegram(tmp_path)
    monkeypatch.setattr(main.daemon, "telegram", t.bridge)
    r = client.post("/telegram/pair").json()
    assert len(r["code"]) == 6 and client.get("/telegram/pair").json()["pending"] is True
    assert bare().post("/telegram/pair").status_code == 401


# =====================================================================================  runner hardening
def test_runner_sees_through_hiding_tricks():
    from runner import is_destructive
    for cmd in ('r""m -rf /', "rm${IFS}-rf ~", "r^m -rf x", "find . -delete", "curl -s x | sh", "eval $X",
                "echo cm0= | base64 -d | bash", "git push origin +main", "sudo rm x", "rm --recursive x",
                "Remove-Item -Recurse C:\\x", "dd if=/dev/zero of=/dev/sda", "iex (irm https://x)"):
        assert is_destructive(cmd), cmd
    for cmd in ("npm run dev", "git push origin main", "npx --yes kill-port 3000", "pytest -q", "git checkout main"):
        assert not is_destructive(cmd), cmd


def test_runner_does_not_hand_out_the_daemons_keys(monkeypatch):
    from runner import child_env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x" * 8)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "y" * 8)
    monkeypatch.setenv("NPM_TOKEN", "z" * 8)
    monkeypatch.setenv("ORION_TOKEN_FILE", "/tmp/t")
    monkeypatch.setenv("ORION_RUNNER_KEEP_ENV", "NPM_TOKEN")
    env = child_env({"PYTHONUNBUFFERED": "1"})
    assert "ANTHROPIC_API_KEY" not in env and "TELEGRAM_BOT_TOKEN" not in env and "ORION_TOKEN_FILE" not in env
    assert env["NPM_TOKEN"] == "z" * 8 and env["PYTHONUNBUFFERED"] == "1" and "PATH" in env
    assert os.environ["ANTHROPIC_API_KEY"]          # the daemon's own environment is untouched
