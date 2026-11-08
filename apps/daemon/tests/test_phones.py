"""Orion Mobile phone signers: pairing, read vs. sign keys, replay, and approvals over HTTP."""
import asyncio
import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import boardroom as br
import phones as ph


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class FakePhone:
    """Does what the Android app does: two P-256 keys, SPKI DER public keys, SHA256withECDSA signatures."""

    def __init__(self, device_id="pixel-test-0001"):
        self.device_id = device_id
        self.read = ec.generate_private_key(ec.SECP256R1())
        self.sign = ec.generate_private_key(ec.SECP256R1())
        self.ts = int(time.time() * 1000) - 5000

    @staticmethod
    def der(key):
        return key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def pair_body(self, code):
        return {"device_id": self.device_id, "name": "Test Pixel",
                "read_pub": base64.b64encode(self.der(self.read)).decode(),
                "sign_pub": base64.b64encode(self.der(self.sign)).decode(),
                "proof": ph.pairing_proof(code, self.der(self.read), self.der(self.sign))}

    def headers(self, key, method, path, body=b""):
        self.ts += 1
        msg = br.signing_message(method, path, str(self.ts), body)
        sig = base64.b64encode(key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return {"X-Orion-Device": self.device_id, "X-Orion-Ts": str(self.ts), "X-Orion-Sig": sig,
                "Content-Type": "application/json"}


@pytest.fixture()
def lab(tmp_path):
    board = br.Boardroom(tmp_path)
    board.ledger.receive(300_00, "test")
    return board, TestClient(br.build_remote_app(board))


def paired(board, client, phone):
    code = board.phones.new_code()
    r = client.post("/phone/pair", json=phone.pair_body(code))
    assert r.status_code == 200, r.text
    assert board.phones.list() == []  # pending until confirmed on the lab
    board.phones.confirm(r.json()["fingerprint"])
    return r.json()["fingerprint"]


def test_pair_requires_code_and_lab_confirmation(lab):
    board, client = lab
    phone = FakePhone()
    assert client.post("/phone/pair", json=phone.pair_body("NOPE")).status_code == 403  # pairing not open
    board.phones.new_code()
    assert client.post("/phone/pair", json=phone.pair_body("WRONG-CODE")).status_code == 403
    fp = paired(board, client, phone)
    assert fp == ph.fingerprint(phone.der(phone.sign))
    assert [p["fingerprint"] for p in board.phones.list()] == [fp]


def test_read_key_reads_but_cannot_approve(lab):
    board, client = lab
    phone = FakePhone()
    paired(board, client, phone)
    d = run(board.propose("payout.bounty", "pay 10", {"bucket": "bounties", "amount_cents": 10_00, "memo": "m"}, "CFO"))
    r = client.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending"))
    assert r.status_code == 200 and r.json()["pending"][0]["id"] == d["id"]
    item = r.json()["pending"][0]
    assert hashlib.sha256(item["packet_canon"].encode("utf-8")).hexdigest() == item["packet_hash"]
    body = json.dumps({"id": d["id"], "verdict": "approve", "packet_hash": d["packet_hash"]}).encode()
    r = client.post("/phone/verdict", content=body, headers=phone.headers(phone.read, "POST", "/phone/verdict", body))
    assert r.status_code == 401 and "fingerprint" in r.json()["error"]
    assert board.get(d["id"])["status"] == "pending"


def test_sign_key_approves_and_records_the_phone(lab):
    board, client = lab
    phone = FakePhone()
    fp = paired(board, client, phone)
    d = run(board.propose("payout.bounty", "pay 25", {"bucket": "bounties", "amount_cents": 25_00, "memo": "m"}, "CFO"))
    body = json.dumps({"id": d["id"], "verdict": "approve", "packet_hash": br.packet_hash(d["packet"])}).encode()
    h = phone.headers(phone.sign, "POST", "/phone/verdict", body)
    r = client.post("/phone/verdict", content=body, headers=h)
    assert r.status_code == 200 and r.json()["decision"]["status"] == "approved"
    assert board.get(d["id"])["signer"] == f"phone:Test Pixel {fp}"
    assert board.ledger.balances()["bounties"] == 125_00
    assert client.post("/phone/verdict", content=body, headers=h).status_code == 401  # replay


def test_unknown_or_removed_phone_is_refused(lab):
    board, client = lab
    phone = FakePhone()
    paired(board, client, phone)
    board.phones.remove(phone.device_id)
    r = client.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending"))
    assert r.status_code == 401


def test_stranger_key_with_known_device_id_is_refused(lab):
    board, client = lab
    phone = FakePhone()
    paired(board, client, phone)
    stranger = FakePhone(phone.device_id)
    stranger.ts = phone.ts + 100
    r = client.get("/phone/pending", headers=stranger.headers(stranger.read, "GET", "/phone/pending"))
    assert r.status_code == 401


def test_phone_keys_must_be_p256(lab):
    board, client = lab
    phone = FakePhone()
    code = board.phones.new_code()
    body = phone.pair_body(code)
    k = ec.generate_private_key(ec.SECP384R1())
    body["sign_pub"] = base64.b64encode(FakePhone.der(k)).decode()
    assert client.post("/phone/pair", json=body).status_code == 403
