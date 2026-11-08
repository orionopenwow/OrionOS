"""Fixes from the independent review (items 1, 5, 6, 7, 8, 9; items 2-4 are in test_phone_missions.py)."""
import asyncio
import base64
import http.client
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

import boardroom as br
import pc_identity
import phone_pairing_ui as ppu
from tests.test_phone_pairing_ui import FakePhone, make


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------ 1: no ungated apply while self_program is on
def test_patch_not_owned_by_self_program_is_never_applied_the_old_way():
    import main

    logs, applied = [], []
    fake = SimpleNamespace(
        selfprog=SimpleNamespace(owns=lambda pid: False),
        evolver=SimpleNamespace(apply=lambda pid: applied.append(pid) or {}),
        approvals=SimpleNamespace(get=lambda pid: {"status": "pending"}),
        bus=SimpleNamespace(log=lambda text, level="info": _alog(logs, text)))
    run(main.OrionDaemon.decide_patch(fake, "p-1", "apply"))
    assert applied == []
    assert any("wasn't tested in a separate copy" in t for t in logs)
    # self_program off: still refused (the existing rule)
    fake.selfprog = None
    logs.clear()
    run(main.OrionDaemon.decide_patch(fake, "p-1", "apply"))
    assert applied == [] and logs


async def _alog(logs, text):
    logs.append(text)


# ------------------------------------------------------------------ 5: slow work off the event loop
def test_pairing_routes_run_commands_off_the_event_loop(tmp_path):
    on_loop = []

    def runner(args, **kw):
        try:
            asyncio.get_running_loop()
            on_loop.append(args[0])      # a command run on the event loop thread blocks every other request
        except RuntimeError:
            pass

        class R:
            stdout, returncode = "", 1
        return R()

    daemon, ui, remote, hud, _ = make(tmp_path, platform="linux", runner=runner)
    ui.route_ip = lambda: None           # forces the tailscale lookup inside address()
    ui.data_dir = tmp_path
    assert hud.post("/phones/pair/open").status_code == 200
    assert hud.get("/phones/pair/status").status_code == 200
    assert hud.get("/phones").status_code == 200
    assert hud.get("/phones/lan").status_code == 200
    assert on_loop == []


def test_slow_executor_runs_in_a_thread_and_only_once(tmp_path):
    board = br.Boardroom(tmp_path)
    key = "test.slow_merge"
    br.TASK_TYPES.setdefault(key, br.TaskType(key, "Slow", "code", reversible=True, money=False, sensitive=True))
    seen = []

    def slow(d):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        time.sleep(0.2)
        return {"ok": True}

    board.executors[key] = slow
    board.slow_executors.add(key)

    async def go():
        d = await board.propose(key, "slow one", {"x": 1}, proposer="test", simulated=False)
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(10):
                await asyncio.sleep(0.01)
                ticks += 1
        t = asyncio.create_task(ticker())
        first = asyncio.create_task(board.verdict(d["id"], "approve", d["packet_hash"]))
        await asyncio.sleep(0.05)
        with pytest.raises(ValueError):          # a second verdict while the first is still running
            await board.verdict(d["id"], "approve", d["packet_hash"])
        out = await first
        await t
        return out, ticks

    try:
        out, ticks = run(go())
    finally:
        br.TASK_TYPES.pop(key, None)
    assert out["status"] == "approved" and seen == ["thread"]
    assert ticks == 10                          # the loop kept running during the merge


def test_mission_merge_is_registered_as_slow():
    src = (Path(__file__).resolve().parents[1] / "missions_routes.py").read_text(encoding="utf-8")
    assert "board.slow_executors.add(missions.TASK_TYPE)" in src


def test_reject_waits_while_a_merge_runs(tmp_path):
    import missions as M

    mc = M.MissionControl(tmp_path / "d")
    m = {"id": "m1", "state": "review", "merge": "merging", "title": "t", "agent_label": "a", "created": 1}
    mc.missions["m1"] = m
    r = mc._op_reject(m)
    assert r["ok"] is False and m["state"] == "review"


# ------------------------------------------------------------------ 6: the Tauri shell and package.json need the fingerprint
def test_tauri_shell_and_package_json_are_daemon_code():
    import protected_core as pc
    import self_program as sp

    cls = sp.SelfProgram.classify
    fake = SimpleNamespace()
    f = lambda p: [{"path": p, "action": "modify", "before": "", "after": "x"}]  # noqa: E731
    assert cls(fake, f("apps/desktop/src/App.tsx"))[0] == "ui_code"
    assert cls(fake, f("apps/desktop/src-tauri/src/main.rs"))[0] == "daemon_code"
    assert cls(fake, f("apps/desktop/package.json"))[0] == "daemon_code"
    assert cls(fake, f("packages/ui/package.json"))[0] == "daemon_code"
    for p in ("apps/desktop/src-tauri/tauri.conf.json", "apps/desktop/src-tauri/capabilities/default.json"):
        assert pc.is_protected_path(p), p
        assert cls(fake, f(p))[0] == "protected_core"
    data = {r["path"] for r in json.loads(pc.DATA_FILE.read_text(encoding="utf-8"))["rules"]}
    code = {r[0] for r in pc.RULES}
    assert {"apps/desktop/src-tauri/tauri.conf.json", "apps/desktop/src-tauri/capabilities/*"} <= data & code


# ------------------------------------------------------------------ 7: the phone can check it's talking to its own PC
def _verify(der_b64, msg, sig_b64):
    key = load_der_public_key(base64.b64decode(der_b64))
    key.verify(base64.b64decode(sig_b64), msg, ec.ECDSA(hashes.SHA256()))


def test_pairing_answer_carries_the_pc_key_and_hello_signs_the_nonce(tmp_path):
    daemon, ui, remote, hud, _ = make(tmp_path)
    ui.data_dir = tmp_path
    code = hud.post("/phones/pair/open").json()["code"]
    phone = FakePhone()
    r = remote.post("/phone/pair", json=phone.pair_body(code)).json()
    der = base64.b64decode(r["pc_key"])
    assert r["pc_key_proof"] == pc_identity.pair_proof(code, der)
    assert r["pc_key_proof"] != pc_identity.pair_proof("WRONG-CODE", der)
    hud.post(f"/phones/{phone.device_id}/confirm", json={"fingerprint": r["fingerprint"]})

    nonce = base64.urlsafe_b64encode(os.urandom(24)).decode()
    body = json.dumps({"nonce": nonce}).encode()
    h = remote.post("/phone/hello", content=body, headers=phone.headers("POST", "/phone/hello", body)).json()
    assert h["pc_key"] == r["pc_key"]
    _verify(r["pc_key"], pc_identity.hello_message(h["pc"]["id"], nonce), h["pc_sig"])
    with pytest.raises(Exception):   # bound to the nonce: another nonce doesn't verify
        _verify(r["pc_key"], pc_identity.hello_message(h["pc"]["id"], nonce + "x"), h["pc_sig"])
    # no nonce: no signature (older phones keep working)
    body = json.dumps({"battery": 50}).encode()
    assert "pc_sig" not in remote.post("/phone/hello", content=body,
                                       headers=phone.headers("POST", "/phone/hello", body)).json()


def test_pc_key_is_made_once_and_kept_owner_only(tmp_path):
    a = pc_identity.PcKey(tmp_path)
    b64 = a.public_b64()
    assert pc_identity.PcKey(tmp_path).public_b64() == b64
    if sys.platform != "win32":
        assert (tmp_path / pc_identity.KEY_FILE).stat().st_mode & 0o077 == 0


# ------------------------------------------------------------------ 8: no hostname for phones or the Wi-Fi
def test_pc_name_is_neutral_unless_you_name_it(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "owners-laptop-x7")
    monkeypatch.delenv("ORION_PC_NAME", raising=False)
    zc = __import__("tests.test_phone_pairing_ui", fromlist=["FakeZeroconfModule"]).FakeZeroconfModule()
    daemon, ui, remote, hud, _ = make(tmp_path, zc=zc)
    ui.data_dir = tmp_path
    assert ui.pc_name() == "Orion PC"
    j = hud.post("/phones/pair/open").json()
    assert j["pc"]["name"] == "Orion PC"
    assert zc.registered and all("owners-laptop" not in i.name and "owners-laptop" not in str(i.properties) for i in zc.registered)
    assert hud.post("/phones/pc-name", json={"name": "Studio <box>!"}).json()["name"] == "Studio box"
    assert hud.get("/phones").json()["pc"]["name"] == "Studio box"
    assert hud.post("/phones/pc-name", json={"name": ""}).json()["name"] == "Orion PC"
    import phone_pair
    pp = phone_pair.PhonePair(SimpleNamespace(), data_dir=tmp_path)
    assert "owners-laptop" not in pp._pc({})["name"]


# ------------------------------------------------------------------ 9: the little file servers
def _get(port, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("GET", path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_play_server_refuses_symlinks_out_and_foreign_hosts(tmp_path):
    import projects

    game = tmp_path / "game"
    game.mkdir()
    (game / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    secret = tmp_path / "outside.txt"
    secret.write_text("private", encoding="utf-8")
    (game / "leak.txt").symlink_to(secret)
    p = projects.Projects(tmp_path / "data", root=tmp_path, opener=lambda *_: None, browser=lambda *_: None)
    try:
        url = p.serve(game)
        port = int(url.split(":")[2].split("/")[0])
        assert _get(port, "/index.html")[0] == 200
        assert _get(port, "/index.html", host=f"localhost:{port}")[0] == 200
        assert _get(port, "/leak.txt")[0] == 404
        assert _get(port, "/index.html", host="evil.example:80")[0] == 403
    finally:
        p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_preview_server_refuses_symlinks_out_and_foreign_hosts(tmp_path):
    import self_program as sp

    d = tmp_path / "preview"
    d.mkdir()
    (d / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("private", encoding="utf-8")
    (d / "leak.txt").symlink_to(tmp_path / "outside.txt")
    srv = sp.PreviewServer(d)
    try:
        port = srv.httpd.server_address[1]
        prefix = srv.url.split(str(port), 1)[1]
        assert _get(port, prefix + "index.html")[0] == 200
        assert _get(port, prefix + "leak.txt")[0] == 404
        assert _get(port, prefix + "index.html", host="rebind.example")[0] == 403
    finally:
        srv.stop()
