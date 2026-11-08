"""Boardroom: pairing, signatures, paper ledger and the autonomy learner (offline)."""
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import boardroom as br

ROOT = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def board(tmp_path):
    return br.Boardroom(tmp_path)


def pub_of(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def proof(code, pub):
    return hmac.new(br.Boardroom._norm_code(code).encode(), pub, hashlib.sha256).hexdigest()


def pair(board, key):
    code = board.new_pairing_code()
    pub = pub_of(key)
    fp = board.pair(base64.b64encode(pub).decode(), proof(code, pub))
    return board.confirm_pending(fp)


_TS = [int(time.time()) - 60]


def signed(key, method, path, body=b""):
    _TS[0] += 1
    ts = str(_TS[0])
    sig = base64.b64encode(key.sign(br.signing_message(method, path, ts, body))).decode()
    return {"X-Orion-Ts": ts, "X-Orion-Sig": sig, "Content-Type": "application/json"}


# ------------------------------------------------------------------ pairing
def test_pairing_needs_the_code(board):
    key = Ed25519PrivateKey.generate()
    pub = pub_of(key)
    board.new_pairing_code()
    with pytest.raises(PermissionError):
        board.pair(base64.b64encode(pub).decode(), proof("AAAA-BBBB", pub))
    assert board.pubkey is None


def test_pairing_is_one_shot(board):
    key = Ed25519PrivateKey.generate()
    fp = pair(board, key)
    assert fp == br.fingerprint(pub_of(key))
    with pytest.raises(ValueError):
        board.new_pairing_code()  # already paired: a second boardroom can't sneak in


# ------------------------------------------------------------------ signatures over HTTP
def test_remote_endpoint_rejects_unsigned_forged_and_replayed(board):
    key = Ed25519PrivateKey.generate()
    pair(board, key)
    client = TestClient(br.build_remote_app(board))
    assert client.get("/boardroom/pending").status_code == 401
    other = Ed25519PrivateKey.generate()
    assert client.get("/boardroom/pending", headers=signed(other, "GET", "/boardroom/pending")).status_code == 401
    h = signed(key, "GET", "/boardroom/pending")
    assert client.get("/boardroom/pending", headers=h).status_code == 200
    assert client.get("/boardroom/pending", headers=h).status_code == 401  # replay


def test_hud_style_unsigned_verdict_cannot_move_money(board):
    run(board.simulate("donation"))
    d = run(board.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 500, "memo": "x"}, "CFO"))
    client = TestClient(br.build_remote_app(board))
    r = client.post("/boardroom/verdict", json={"id": d["id"], "verdict": "approve", "packet_hash": d["packet_hash"]})
    assert r.status_code == 401
    assert board.get(d["id"])["status"] == "pending"


# ------------------------------------------------------------------ verdicts and the paper ledger
def test_signed_payout_debits_paper_ledger(board):
    board.ledger.receive(100_00, "test")
    assert board.ledger.balances()["hosting"] == 60_00 and board.ledger.balances()["api_fuel"] == 40_00
    board.ledger.receive(50_00, "test")
    assert board.ledger.balances()["bounties"] == 50_00
    d = run(board.propose("payout.bounty", "pay 20", {"bucket": "bounties", "amount_cents": 20_00, "memo": "m"}, "CFO"))
    out = run(board.verdict(d["id"], "approve", d["packet_hash"]))
    assert out["status"] == "approved" and out["executed"]["simulated"] is True
    assert board.ledger.balances()["bounties"] == 30_00
    assert board.ledger.verify_chain()


def test_tampered_packet_is_refused(board):
    board.ledger.receive(300_00, "t")
    d = run(board.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 10_00, "memo": "m"}, "CFO"))
    with pytest.raises(PermissionError):
        run(board.verdict(d["id"], "approve", "0" * 64))


def test_edit_can_lower_but_not_raise_a_payout(board):
    board.ledger.receive(300_00, "t")
    d = run(board.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 40_00, "memo": "m"}, "CFO"))
    up = run(board.verdict(d["id"], "edit", d["packet_hash"], edits={"amount_cents": 90_00}))
    assert up["status"] == "failed"
    d2 = run(board.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 40_00, "memo": "m"}, "CFO"))
    down = run(board.verdict(d2["id"], "edit", d2["packet_hash"], edits={"amount_cents": 15_00}))
    assert down["status"] == "edited" and board.ledger.balances()["bounties"] == 135_00


def test_ledger_tamper_is_detected(board):
    board.ledger.receive(10_00, "t")
    board.store.x("UPDATE ledger SET amount_cents=999999 WHERE seq=1")
    assert board.ledger.verify_chain() is False


# ------------------------------------------------------------------ autonomy learner
def _approve_n(board, task, n, details):
    for _ in range(n):
        d = run(board.propose(task, "t", details, "agent"))
        if d["status"] == "pending":
            run(board.verdict(d["id"], "approve", d["packet_hash"]))


def test_docs_merges_graduate_only_with_a_signed_promotion(board):
    _approve_n(board, "merge.docs", 10, {"pr": 1})
    promos = [p for p in board.pending() if p["task_type"] == "autonomy.promotion"]
    assert len(promos) == 1 and board.learner.level("merge.docs") == 0
    run(board.verdict(promos[0]["id"], "approve", promos[0]["packet_hash"]))
    assert board.learner.level("merge.docs") == 1
    d = run(board.propose("merge.docs", "t", {"pr": 2}, "agent"))
    assert d["status"] == "pending" and d["packet"]["recommendation"] == "approve"  # level 1 still asks


def test_money_never_passes_level_one(board):
    board.ledger.receive(10_000_00, "t")
    board.learner.set_level("payout.bounty", 3)
    assert board.learner.level("payout.bounty") == 1
    d = run(board.propose("payout.bounty", "t", {"bucket": "bounties", "amount_cents": 1_00, "memo": "m"}, "CFO"))
    assert d["status"] == "pending"


def test_rejection_drops_a_level_and_resets_streak(board):
    board.learner.set_level("merge.docs", 2)
    d = run(board.propose("merge.docs", "t", {"pr": 3}, "agent"))
    assert d["status"] == "auto_executed"  # level 2 acts and notifies
    board.learner.set_level("merge.docs", 1)
    d = run(board.propose("merge.docs", "t", {"pr": 4}, "agent"))
    run(board.verdict(d["id"], "reject", d["packet_hash"]))
    assert board.learner.level("merge.docs") == 0 and board.learner.row("merge.docs")["streak"] == 0


def test_hud_cannot_approve_boardroom_items(tmp_path):
    from approval_queue import ApprovalQueue
    q = ApprovalQueue(tmp_path / "a.json")
    b = br.Boardroom(tmp_path, approvals=q)
    b.ledger.receive(300_00, "t")
    d = run(b.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 10_00, "memo": "m"}, "CFO"))
    assert q.get(d["id"])["kind"] == "boardroom"
    import main
    main.daemon.approvals = q
    main.daemon.board = b
    run(main.daemon.decide_approval(d["id"], "approve"))
    assert b.get(d["id"])["status"] == "pending"


# ------------------------------------------------------------------ the real client, end to end over HTTP
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_boardroom_client_end_to_end(tmp_path, monkeypatch):
    import uvicorn

    board = br.Boardroom(tmp_path / "lab")
    board.ledger.receive(300_00, "t")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(br.build_remote_app(board), host="127.0.0.1", port=port, log_level="error"))
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_until_complete, args=(server.serve(),), daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    monkeypatch.setenv("ORION_BOARDROOM_HOME", str(tmp_path / "vm2"))
    spec = importlib.util.spec_from_file_location("orion_boardroom", ROOT / "boardroom" / "orion_boardroom.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    fp = cli.init_key("correct horse battery")
    lab = cli.Lab(f"http://127.0.0.1:{port}")
    code = board.new_pairing_code()
    pub = cli.public_key()
    res = lab.request("POST", "/boardroom/pair", body={"pubkey": base64.b64encode(pub).decode(),
                                                      "proof": hmac.new(cli.norm_code(code).encode(), pub,
                                                                        hashlib.sha256).hexdigest()})
    assert res["fingerprint"] == fp
    key0 = cli.load_key("correct horse battery")
    with pytest.raises(RuntimeError):  # not confirmed on the lab yet: the key can't read or sign
        lab.request("GET", "/boardroom/pending", key=key0)
    board.confirm_pending(fp)

    fut = asyncio.run_coroutine_threadsafe(
        board.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 25_00, "memo": "m"}, "CFO"), loop)
    fut.result(5)
    key = cli.load_key("correct horse battery")
    with pytest.raises(ValueError):
        cli.load_key("wrong passphrase!!")
    pending = lab.request("GET", "/boardroom/pending", key=key)["pending"]
    d = pending[0]
    assert cli.packet_hash(d["packet"]) == d["packet_hash"]  # client and lab hash identically
    out = lab.request("POST", "/boardroom/verdict", key=key,
                      body={"id": d["id"], "verdict": "approve", "packet_hash": cli.packet_hash(d["packet"])})
    assert out["decision"]["status"] == "approved"
    assert board.ledger.balances()["bounties"] == 125_00
    server.should_exit = True
    t.join(5)


# ------------------------------------------------------------------ regressions from the security review
def test_changing_the_stored_packet_after_signing_is_refused(board):
    board.ledger.receive(300_00, "t")
    d = run(board.propose("payout.bounty", "pay", {"bucket": "bounties", "amount_cents": 10_00, "memo": "m"}, "CFO"))
    import json as _j
    p = d["packet"]; p["details"]["amount_cents"] = 40_00
    board.store.x("UPDATE decisions SET packet=? WHERE id=?", (_j.dumps(p), d["id"]))
    with pytest.raises(PermissionError):
        run(board.verdict(d["id"], "approve", d["packet_hash"]))
    assert board.ledger.balances()["bounties"] == 150_00


def test_stale_promotion_cannot_jump_levels(board):
    board.learner.set_level("merge.docs", 1)
    board.store.x("UPDATE autonomy SET streak=29 WHERE task_type='merge.docs'")
    d = run(board.propose("merge.docs", "t", {"pr": 1}, "agent"))
    run(board.verdict(d["id"], "approve", d["packet_hash"]))
    promo = [p for p in board.pending() if p["task_type"] == "autonomy.promotion"][0]
    d2 = run(board.propose("merge.docs", "t", {"pr": 2}, "agent"))
    run(board.verdict(d2["id"], "reject", d2["packet_hash"]))
    assert board.get(promo["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        run(board.verdict(promo["id"], "approve", promo["packet_hash"]))
    assert board.learner.level("merge.docs") == 0


def test_pairing_needs_confirmation_and_limits_guesses(board):
    attacker = Ed25519PrivateKey.generate()
    code = board.new_pairing_code()
    fp = board.pair(base64.b64encode(pub_of(attacker)).decode(), proof(code, pub_of(attacker)))
    assert board.pubkey is None  # waiting, not trusted
    with pytest.raises(PermissionError):
        board.confirm_pending("0000-0000-0000-0000-0000")  # owner reads a different fingerprint on the boardroom
    assert board.pubkey is None and board.pending_fingerprint() == fp
    board.unpair()
    board.new_pairing_code()
    for _ in range(4):
        with pytest.raises(PermissionError):
            board.pair(base64.b64encode(pub_of(attacker)).decode(), proof("WRONG", pub_of(attacker)))
    with pytest.raises(PermissionError, match="too many"):
        board.pair(base64.b64encode(pub_of(attacker)).decode(), proof("WRONG", pub_of(attacker)))
    assert board.store.get("pairing") is None


def test_bad_bodies_get_400_not_500(board):
    key = Ed25519PrivateKey.generate()
    pair(board, key)
    client = TestClient(br.build_remote_app(board))
    body = b"[1]"
    h = signed(key, "POST", "/boardroom/verdict", body)
    assert client.post("/boardroom/verdict", content=body, headers=h).status_code == 400
    assert client.post("/boardroom/pair", content=b"not json").status_code == 400


def test_data_dir_is_private(tmp_path):
    import os, stat
    b = br.Boardroom(tmp_path / "d")
    assert stat.S_IMODE(os.stat(tmp_path / "d").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "d" / "boardroom.db").st_mode) == 0o600
