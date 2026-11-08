"""Skill Recorder wired into the real daemon app: voice command, WebSocket protocol, HTTP routes,
Grimoire listing and the HUD approval path (main.decide_approval -> SkillHub.decide)."""
import pytest
from fastapi.testclient import TestClient

import main
from conftest import daemon_client, ws_url

HUD = {"origin": "http://localhost:1420"}
EXT = {"origin": "chrome-extension://orion"}


def recv_until(ws, event, limit=120, where=None):
    for _ in range(limit):
        m = ws.receive_json()
        if m.get("event") == event and (where is None or where(m)):
            return m
    raise AssertionError(f"no {event}")


@pytest.fixture(scope="module")
def client():
    with daemon_client(main.app) as c:
        yield c


def _btn(name, **kw):
    return {"tag": "button", "role": "button", "name": name, "text": name, **kw,
            "selectors": [{"kind": "role", "role": "button", "name": name}]}


def test_record_save_run_approve_via_daemon(client):
    with client.websocket_connect(ws_url("/"), headers=HUD) as hud:
        hud.receive_json()
        hud.send_json({"event": "CLIENT_HELLO", "client": "desktop"})
        with client.websocket_connect(ws_url("/"), headers=EXT) as ext:
            ext.receive_json()
            hud.send_json({"event": "USER_PROMPT", "text": "Orion, watch this"})
            recv_until(ext, "SKILL_RECORD_START")
            ext.send_json({"event": "SKILL_REC_STATE", "state": "started", "rec_id": "r9",
                           "url": "https://mail.example.com/inbox", "title": "Inbox"})
            ext.send_json({"event": "SKILL_REC_EVENT", "rec_id": "r9",
                           "ev": {"t": 1, "action": "click", "target": _btn("Compose")}})
            ext.send_json({"event": "SKILL_REC_EVENT", "rec_id": "r9",
                           "ev": {"t": 2, "action": "fill", "value": "weekly-report.pdf",
                                  "target": {"tag": "input", "name": "Subject", "label": "Subject",
                                             "selectors": [{"kind": "label", "value": "Subject"}]}}})
            ext.send_json({"event": "SKILL_REC_EVENT", "rec_id": "r9",
                           "ev": {"t": 3, "action": "click", "target": _btn("Send")}})
            ext.send_json({"event": "SKILL_REC_STATE", "state": "stopped", "rec_id": "r9"})
            draft = recv_until(hud, "SKILL_DRAFT")["draft"]
            assert draft["card"] == ["1. Go to mail.example.com/inbox", "2. Click the “Compose” button",
                                     "3. Type {{file}} into “Subject”",
                                     "4. Click the “Send” button — needs your OK (looks like it will send)"]

            # extensions can't use the HTTP API at all, the desktop can
            url = f"/browser-skills/drafts/{draft['draft_id']}/save"
            body = {"name": "Send weekly report", "voice_triggers": ["send the weekly report"],
                    "variables": [{"name": "file", "new_name": "report"}]}
            assert client.post(url, json=body, headers=EXT).status_code == 403
            saved = client.post(url, json=body, headers=HUD).json()
            assert saved["id"] == "send_weekly_report" and saved["trusted"]
            listing = client.get("/browser-skills", headers=HUD).json()
            assert any(s["id"] == "send_weekly_report" and s["risky_steps"] == 1 for s in listing["skills"])
            code = client.get("/browser-skills/send_weekly_report/export?lang=js", headers=HUD).text
            assert "await confirm(" in code and "vars.report" in code

            hud.send_json({"event": "GRIMOIRE_LIST"})
            book = recv_until(hud, "GRIMOIRE")["grimoire"]
            assert any(e["id"] == "skill:send_weekly_report" and e["kind"] == "skill" for e in book)

            # run it by voice trigger; the Send step waits for the HUD approval card
            hud.send_json({"event": "USER_PROMPT", "text": "send the weekly report"})
            preview = recv_until(ext, "SKILL_PREVIEW")
            ext.send_json({"event": "SKILL_RUN_CONTROL", "run_id": preview["run_id"], "action": "run_all"})
            for _ in range(3):  # open, click, fill
                step = recv_until(ext, "SKILL_EXEC_STEP")
                assert step["approved"] is False
                ext.send_json({"event": "SKILL_STEP_RESULT", "run_id": step["run_id"], "nonce": step["nonce"], "ok": True})
            item = recv_until(hud, "APPROVAL_REQUIRED", where=lambda m: m["item"]["kind"] == "skill_step")["item"]
            assert "Send" in item["draft"]
            ext.send_json({"event": "APPROVAL_DECISION", "id": item["id"], "decision": "approve"})
            assert "not allowed" in recv_until(ext, "LOG")["message"]
            r = client.post(f"/approvals/{item['id']}", json={"decision": "approve"}, headers=HUD).json()
            assert r["status"] == "approved"
            step = recv_until(ext, "SKILL_EXEC_STEP")
            assert step["approved"] is True and step["step"]["description"] == "Click the “Send” button"
            ext.send_json({"event": "SKILL_STEP_RESULT", "run_id": step["run_id"], "nonce": step["nonce"], "ok": True})
            done = recv_until(hud, "SKILL_RUN_STATE", where=lambda m: m["run"]["status"] == "done")
            assert done["run"]["index"] == 4
