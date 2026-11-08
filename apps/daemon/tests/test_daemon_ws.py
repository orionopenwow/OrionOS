"""End-to-end WebSocket tests against the real FastAPI app (all bridges offline)."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from conftest import daemon_client, ws_url


def recv_until(ws, event, limit=80):
    for _ in range(limit):
        m = ws.receive_json()
        if m.get("event") == event:
            return m
    raise AssertionError(f"no {event}")


@pytest.fixture(scope="module")
def client():
    with daemon_client(main.app) as c:
        yield c


def test_foreign_origin_is_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(ws_url("/"), headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert client.get("/approvals", headers={"origin": "https://evil.example"}).status_code == 403


def test_comms_roundtrip_via_extension(client):
    with client.websocket_connect(ws_url("/"), headers={"origin": "http://localhost:1420"}) as ws:
        hello = ws.receive_json()
        assert hello["event"] == "HELLO" and "pacing" in hello and "approvals" in hello
        ws.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        with client.websocket_connect(ws_url("/"), headers={"origin": "chrome-extension://orion"}) as ext:
            ext.receive_json()
            ext.send_json({"event": "CLIENT_HELLO", "client": "desktop"})  # can't promote itself
            ext.send_json({"event": "COMMS_INBOUND", "platform": "messenger", "thread_id": "t1",
                           "thread_name": "Sam", "author": "Sam", "text": "dinner?", "external_id": "m1",
                           "thread_url": "https://www.messenger.com/t/t1"})
            assert recv_until(ws, "COMMS_MESSAGE")["message"]["text"] == "dinner?"
            # extensions may not act on the user's behalf
            ext.send_json({"event": "COMMS_REPLY", "thread_key": "messenger:t1", "text": "hijack"})
            assert "not allowed" in recv_until(ext, "LOG")["message"]
            ws.send_json({"event": "COMMS_REPLY", "thread_key": "messenger:t1", "text": "yes!"})
            inj = recv_until(ext, "COMMS_INJECT_REPLY")
            assert inj["text"] == "yes!"
            ext.send_json({"event": "COMMS_INJECT_RESULT", "request_id": inj["request_id"], "ok": True})
            assert recv_until(ws, "COMMS_REPLY_RESULT")["ok"]


def test_double_approve_sends_once(client):
    calls = []

    async def fake_send(item, text):
        calls.append(text)
        return True
    main.daemon.triage.send_reply = fake_send
    item = main.daemon.approvals.add("discord_prompt", platform="discord_server", category="bug", channel="#b",
                                     author="p", original="crash", draft="ty", system="S", user_prompt="U",
                                     channel_id=1, message_id=2)
    with client.websocket_connect(ws_url("/")) as ws:
        ws.receive_json()
        ws.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        for _ in range(3):
            ws.send_json({"event": "APPROVAL_DECISION", "id": item["id"], "decision": "approve"})
        while main.daemon.approvals.get(item["id"])["status"] != "sent":
            ws.receive_json()
    assert calls == ["ty"]


def test_self_evolve_respects_disable_flag(client, monkeypatch):
    monkeypatch.setattr(main.settings.__class__, "self_evolve_enabled", property(lambda s: False))
    started = []
    monkeypatch.setattr(main.daemon.orchestrator, "self_evolve", lambda *a, **k: started.append(a))
    with client.websocket_connect(ws_url("/")) as ws:
        ws.receive_json()
        ws.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        ws.send_json({"event": "SELF_EVOLVE_REQUEST", "request": "add an endpoint"})
        assert "disabled" in recv_until(ws, "LOG")["message"]
    assert not started


def test_consolidate_exports_training(client):
    with client.websocket_connect(ws_url("/")) as ws:
        ws.receive_json()
        ws.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        ws.send_json({"event": "CONSOLIDATE_REQUEST"})
        assert recv_until(ws, "TRAINING_EXPORTED", 200)["result"]["path"].endswith("orion_finetune.jsonl")


def test_feature_bible_commands_over_ws(client):
    with client.websocket_connect(ws_url("/"), headers={"origin": "http://localhost:1420"}) as ws:
        hello = recv_until(ws, "HELLO")
        assert "workspaces" in hello and "grimoire" in hello and "preload" in hello["telemetry"]
        ws.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        ws.send_json({"event": "USER_PROMPT", "text": "I drank a glass of water"})
        assert recv_until(ws, "HYDRATION")["hydration"]["today"] >= 1
        ws.send_json({"event": "RULE_ADD", "text": "Keep Discord announcements casual and short"})
        rules = recv_until(ws, "RULES")["rules"]
        assert any("casual" in r["text"] for r in rules)
        ws.send_json({"event": "GRIMOIRE_LIST"})
        assert any(e["id"] == "kill_stuck_ports" for e in recv_until(ws, "GRIMOIRE")["grimoire"])
        with client.websocket_connect(ws_url("/"), headers={"origin": "chrome-extension://orion"}) as ext:
            ext.receive_json()
            ext.send_json({"event": "HARVEST_REQUEST"})
            assert "Unknown event" in recv_until(ext, "LOG")["message"]


def test_asset_upload(client):
    r = client.post("/assets/upload", content=b"hello world", headers={"x-filename": "notes.txt",
                                                                     "origin": "http://localhost:1420"})
    assert r.status_code == 200 and r.json()["asset"]["name"].endswith(".txt")


def test_extensions_cannot_use_http_actions(client):
    ext = {"origin": "chrome-extension://someone-else"}
    assert client.post("/approvals/x", json={"decision": "approve"}, headers=ext).status_code == 403
    assert client.post("/telegram/send", json={"text": "hi"}, headers=ext).status_code == 403
    assert client.get("/approvals", headers=ext).status_code == 403
    assert client.get("/health", headers=ext).status_code == 200


def test_reply_suggestions_and_health_build(client):
    assert client.get("/health").json()["build"]
    with client.websocket_connect(ws_url("/"), headers={"origin": "http://localhost:1420"}) as ws:
        recv_until(ws, "HELLO")
        ws.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        with client.websocket_connect(ws_url("/"), headers={"origin": "chrome-extension://orion"}) as ext:
            ext.receive_json()
            ext.send_json({"event": "COMMS_INBOUND", "platform": "messenger", "thread_id": "s1", "thread_name": "Ana",
                           "author": "Ana", "text": "are you free tonight?", "external_id": "s1m",
                           "thread_url": "https://www.messenger.com/t/s1"})
            recv_until(ws, "COMMS_MESSAGE")
        ws.send_json({"event": "SUGGEST_REPLIES", "thread_key": "messenger:s1"})
        ev = recv_until(ws, "REPLY_SUGGESTIONS")
        assert ev["thread_key"] == "messenger:s1" and len(ev["replies"]) == 3
