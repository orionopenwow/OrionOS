"""A steadier phone link (phone_link.py, phones.py replay window, boardroom phone port restarts, alerts resume)."""
import asyncio
import base64
import json
import socket
import time
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import alerts as al
import boardroom as br
import phone_link
import phones as ph


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Phone:
    def __init__(self, device_id="pixel-link-0001"):
        self.device_id = device_id
        self.read = ec.generate_private_key(ec.SECP256R1())
        self.sign = ec.generate_private_key(ec.SECP256R1())

    @staticmethod
    def der(k):
        return k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def pair_body(self, code):
        return {"device_id": self.device_id, "name": "Link Pixel",
                "read_pub": base64.b64encode(self.der(self.read)).decode(),
                "sign_pub": base64.b64encode(self.der(self.sign)).decode(),
                "proof": ph.pairing_proof(code, self.der(self.read), self.der(self.sign))}

    def headers(self, key, method, path, body=b"", ts=None, idem=None):
        ts = str(ts if ts is not None else int(time.time() * 1000))
        sig = base64.b64encode(key.sign(br.signing_message(method, path, ts, body), ec.ECDSA(hashes.SHA256()))).decode()
        h = {"X-Orion-Device": self.device_id, "X-Orion-Ts": ts, "X-Orion-Sig": sig, "Content-Type": "application/json"}
        if idem:
            h["X-Orion-Idem"] = idem
        return h


@pytest.fixture()
def lab(tmp_path):
    phone_link._idem_wait.clear()
    phone_link._inflight.clear()
    phone_link._stats.clear()
    board = br.Boardroom(tmp_path)
    board.ledger.receive(300_00, "test")
    client = TestClient(br.build_remote_app(board))
    phone = Phone()
    code = board.phones.new_code()
    r = client.post("/phone/pair", json=phone.pair_body(code))
    board.phones.confirm(r.json()["fingerprint"])
    return board, client, phone


def now_ms():
    return int(time.time() * 1000)


# ------------------------------------------------------------------ timestamps: order, replay, clocks
def test_out_of_order_requests_are_accepted_but_copies_are_not(lab):
    board, client, phone = lab
    t = now_ms()
    late = phone.headers(phone.read, "GET", "/phone/pending", ts=t)
    early = phone.headers(phone.read, "GET", "/phone/pending", ts=t + 5)
    assert client.get("/phone/pending", headers=early).status_code == 200
    assert client.get("/phone/pending", headers=late).status_code == 200      # arrived second: still fine
    r = client.get("/phone/pending", headers=late)                             # the same request again: no
    assert r.status_code == 401 and "replayed" in r.json()["error"]
    old = phone.headers(phone.read, "GET", "/phone/pending", ts=t + 5 - ph.REORDER_MS - 1)
    assert client.get("/phone/pending", headers=old).status_code == 401       # older than the window


def test_replay_guard_keys_on_the_message_not_the_signature_encoding(lab):
    board, client, phone = lab
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
    h = phone.headers(phone.read, "GET", "/phone/pending")
    assert client.get("/phone/pending", headers=h).status_code == 200
    r_, s_ = decode_dss_signature(base64.b64decode(h["X-Orion-Sig"]))
    n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551   # P-256 order: (r, n-s) also verifies
    h2 = {**h, "X-Orion-Sig": base64.b64encode(encode_dss_signature(r_, n - s_)).decode()}
    assert client.get("/phone/pending", headers=h2).status_code == 401


def test_a_phone_paired_before_the_window_stays_strict_at_first(lab):
    board, client, phone = lab
    phones = json.loads(board.store.get("phones"))
    rec = phones[phone.device_id]
    rec.pop("seen", None)
    rec.pop("floor", None)
    rec["last_ts"] = now_ms()
    board.store.put("phones", json.dumps(phones))
    stale = phone.headers(phone.read, "GET", "/phone/pending", ts=rec["last_ts"] - 10)
    assert client.get("/phone/pending", headers=stale).status_code == 401
    fresh = phone.headers(phone.read, "GET", "/phone/pending", ts=rec["last_ts"] + 10)
    assert client.get("/phone/pending", headers=fresh).status_code == 200


def test_every_answer_carries_the_pc_time_and_modest_skew_is_fine(lab):
    board, client, phone = lab
    r = client.get("/boardroom/hello")
    assert abs(int(r.headers["X-Orion-Time"]) - now_ms()) < 5000
    ahead = phone.headers(phone.read, "GET", "/phone/pending", ts=now_ms() + 200_000)   # 3+ minutes ahead
    assert client.get("/phone/pending", headers=ahead).status_code == 200


def test_too_much_skew_gets_plain_words_and_the_pc_time(lab):
    board, client, phone = lab
    r = client.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending",
                                                           ts=now_ms() - 7 * 60_000))
    assert r.status_code == 401
    err = r.json()["error"]
    assert "clock" in err and "7 minutes behind" in err and "automatic date and time" in err
    assert "X-Orion-Time" in r.headers


def test_the_local_api_key_does_not_touch_the_phone_port(lab, monkeypatch):
    """The daemon makes a new API key every start (api_token.py); phones sign instead and must keep working."""
    import api_token
    monkeypatch.setattr(api_token, "_token", "x" + uuid.uuid4().hex)
    board, client, phone = lab
    assert client.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending")).status_code == 200
    restarted = TestClient(br.build_remote_app(br.Boardroom(board.data_dir)))   # a restart: same paired phone
    monkeypatch.setattr(api_token, "_token", "y" + uuid.uuid4().hex)
    assert restarted.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending",
                                                                 ts=now_ms() + 3)).status_code == 200


# ------------------------------------------------------------------ approvals: never lost, never twice
def _verdict(board, client, phone, idem):
    d = run(board.propose("payout.bounty", "pay 25", {"bucket": "bounties", "amount_cents": 25_00, "memo": "m"}, "CFO"))
    body = json.dumps({"id": d["id"], "verdict": "approve", "packet_hash": d["packet_hash"], "idem": idem}).encode()
    return d, body


def test_a_resent_approval_gets_the_same_answer_and_applies_once(lab):
    board, client, phone = lab
    idem = uuid.uuid4().hex
    d, body = _verdict(board, client, phone, idem)
    h = phone.headers(phone.sign, "POST", "/phone/verdict", body, idem=idem)
    first = client.post("/phone/verdict", content=body, headers=h)
    assert first.status_code == 200 and first.json()["decision"]["status"] == "approved"
    again = client.post("/phone/verdict", content=body, headers=h)       # the answer was lost: same bytes again
    assert again.status_code == 200 and again.json() == first.json() and again.headers.get("X-Orion-Replayed") == "1"
    resigned = client.post("/phone/verdict", content=body,                # or signed afresh with the same key
                           headers=phone.headers(phone.sign, "POST", "/phone/verdict", body, ts=now_ms() + 7, idem=idem))
    assert resigned.status_code == 200 and resigned.json() == first.json()
    debits = [e for e in board.ledger.recent(100) if e["kind"] == "debit"]
    assert len(debits) == 1


def test_idempotent_answers_survive_a_restart(lab):
    board, client, phone = lab
    idem = uuid.uuid4().hex
    d, body = _verdict(board, client, phone, idem)
    h = phone.headers(phone.sign, "POST", "/phone/verdict", body, idem=idem)
    first = client.post("/phone/verdict", content=body, headers=h).json()
    restarted = TestClient(br.build_remote_app(br.Boardroom(board.data_dir)))
    assert restarted.post("/phone/verdict", content=body, headers=h).json() == first


def test_the_idem_key_only_counts_when_signed_and_on_the_same_route(lab):
    board, client, phone = lab
    idem = uuid.uuid4().hex
    d, body = _verdict(board, client, phone, idem)
    client.post("/phone/verdict", content=body, headers=phone.headers(phone.sign, "POST", "/phone/verdict", body, idem=idem))
    # a header that isn't in the signed body is ignored: this is just a replay
    plain = json.dumps({"id": d["id"], "verdict": "approve", "packet_hash": d["packet_hash"]}).encode()
    h = phone.headers(phone.sign, "POST", "/phone/verdict", plain, ts=now_ms() + 3, idem=idem)
    assert client.post("/phone/verdict", content=plain, headers=h).status_code == 409   # already approved
    # the same key on another route doesn't hand back the verdict's answer
    other = json.dumps({"id": "nope", "idem": idem}).encode()
    r = client.post("/phone/card/done", content=other,
                    headers=phone.headers(phone.read, "POST", "/phone/card/done", other, ts=now_ms() + 9, idem=idem))
    assert r.status_code in (404, 409) and "decision" not in r.text


def test_two_copies_at_once_wait_for_one_answer(lab):
    board, client, phone = lab
    idem = uuid.uuid4().hex
    d, body = _verdict(board, client, phone, idem)
    board.slow_executors.add("payout.bounty")
    import concurrent.futures
    t = now_ms()
    hs = [phone.headers(phone.sign, "POST", "/phone/verdict", body, ts=t + i, idem=idem) for i in range(3)]
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        outs = list(ex.map(lambda h: client.post("/phone/verdict", content=body, headers=h), hs))
    assert [o.status_code for o in outs] == [200, 200, 200]
    assert len({o.text for o in outs}) == 1
    assert len([e for e in board.ledger.recent(100) if e["kind"] == "debit"]) == 1


def test_too_many_requests_from_one_phone_get_a_polite_429(lab):
    board, client, phone = lab
    phone_link._inflight[phone.device_id] = phone_link.PER_PHONE_MAX
    r = client.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending"))
    assert r.status_code == 429 and r.headers["Retry-After"] == "1"
    phone_link._inflight.clear()
    assert client.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending",
                                                              ts=now_ms() + 2)).status_code == 200


# ------------------------------------------------------------------ alerts resume
def test_a_backlog_bigger_than_one_page_is_not_skipped(tmp_path):
    c = al.AlertCenter(tmp_path)
    for i in range(7):
        c.post("system", f"a{i}")
    got, since = [], 0
    for _ in range(5):
        out = c.feed(since, limit=3)
        got += [a["seq"] for a in out["alerts"]]
        since = out["last"]
        if not out.get("more"):
            break
    assert got == list(range(1, 8))


def test_a_phone_ahead_of_the_pc_is_reset_not_stuck(tmp_path):
    c = al.AlertCenter(tmp_path)
    c.post("system", "fresh")
    out = c.feed(since=500)
    assert out["reset"] and out["last"] == 1 and [a["title"] for a in out["alerts"]] == ["fresh"]
    out = run(c.wait_feed(500, 5))   # long-poll answers at once instead of waiting forever
    assert out["reset"]


# ------------------------------------------------------------------ status, checks, watching
def test_link_info_words():
    phone_link._stats.clear()
    t = 1_000_000.0
    assert phone_link.link_info("p", t - 5, {"rtt_ms": 40}, now=t)["words"] == "Connected"
    assert phone_link.link_info("p", t - 5, {"rtt_ms": 40}, now=t)["quality"] == "good"
    assert phone_link.link_info("p", t - 5, {"rtt_ms": 900}, now=t)["quality"] == "poor"
    assert phone_link.link_info("p", t - 300, {}, now=t)["words"] == "Reconnecting…"
    assert phone_link.link_info("p", t - 3600, {}, now=t)["state"] == "away"
    assert phone_link.link_info("p", None, {}, now=t)["state"] == "never"


def test_same_subnet():
    assert phone_link.same_subnet("192.168.1.20", "192.168.1.77") is True
    assert phone_link.same_subnet("192.168.1.20", "192.168.50.7") is False
    assert phone_link.same_subnet("192.168.1.20", "100.101.1.2") is None
    assert phone_link.same_subnet(None, "10.0.0.1") is None


def test_hello_facts_are_checked():
    f = phone_link.hello_facts({"rtt_ms": 120, "fails": -1, "pinned": True, "via": "tailnet", "reconnects": "x"})
    assert f == {"rtt_ms": 120, "pinned": True, "via": "tailnet"}


class FakeUI:
    def __init__(self, board, ip="192.168.1.20"):
        self.board = board
        self.port = 9121
        self.ip = ip
        self.advertised = 0
        self._zc = None
        self.zc_mod = None
        self._lan_cache = None
        self.lan_ok = True

    def route_ip(self):
        return self.ip

    def run(self, *a, **k):
        return SimpleNamespace(stdout="", returncode=1)

    def port_running(self):
        return True

    def lan(self, fresh=False):
        if self.lan_ok:
            return {"ok": True, "words": "Fine.", "fix_words": ""}
        return {"ok": False, "words": "Windows Firewall stops your phone reaching this PC.", "fix_words": "Press Let my phone in."}

    def advertise(self):
        self.advertised += 1
        return False

    def _facts(self):
        return {"p1": {"pinned": True}}


def test_diagnose_names_the_first_thing_to_fix(tmp_path):
    phone_link._stats.clear()
    phone_link._addr_cache = None
    ui = FakeUI(br.Boardroom(tmp_path))
    out = phone_link.diagnose(ui, "p1", "192.168.1.77")
    assert out["ok"] is True and out["addresses"]["lan"] == "http://192.168.1.20:9121"
    ui.lan_ok = False
    out = phone_link.diagnose(ui, "p1", "192.168.1.77")
    assert out["ok"] is False and out["say"] == "Press Let my phone in."
    ui.lan_ok = True
    phone_link._addr_cache = None
    out = phone_link.diagnose(ui, "p1", "192.168.77.5")
    assert not out["ok"] and "same Wi-Fi" in out["say"]


def test_watch_announces_again_when_the_address_changes(tmp_path):
    phone_link._state.update({"ip": None, "firewall": None})
    ui = FakeUI(br.Boardroom(tmp_path))
    ui.lan_ok = False
    w = phone_link.LinkWatch(ui)
    assert run(w.tick())["changed"] is False
    assert phone_link.status()["firewall"]["ok"] is False      # checked once at start, status only
    ui.ip = "192.168.1.44"
    assert run(w.tick())["changed"] is True
    assert ui.advertised == 2 and phone_link.status()["ip"] == "192.168.1.44"


def test_phone_port_survives_a_busy_port_and_comes_up_when_free(tmp_path):
    """uvicorn exits the process when it can't bind; the phone port must retry instead of ending the daemon."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)

    async def scenario():
        board = br.Boardroom(tmp_path)
        await board.start(bind="127.0.0.1", port=port)
        await asyncio.sleep(0.5)
        assert not getattr(board._server, "started", False)
        blocker.close()
        for _ in range(100):
            await asyncio.sleep(0.1)
            if getattr(board._server, "started", False):
                break
        up = getattr(board._server, "started", False)
        await board.stop()
        await asyncio.wait_for(board._server_task, 10)
        return up

    assert run(scenario()) is True


def test_soak_short():
    """The simulated phone through restarts, an address change, a clock jump, drops and sleep (phone_soak.py)."""
    import logging
    import phone_soak
    logging.getLogger("orion").setLevel(logging.CRITICAL)
    res = phone_soak.run(short=True)
    assert phone_soak.check(res) == [], res
    assert all(v < 15 for v in res["recovery_s"].values()), res["recovery_s"]


def test_phone_link_check_and_hello_addresses_over_http(tmp_path):
    """The phone's "Fix the connection" asks the PC for its checks; hello tells the phone the PC's addresses."""
    import phone_pairing_ui as ppu
    phone_link._addr_cache = None
    board = br.Boardroom(tmp_path)
    ui = ppu.attach(SimpleNamespace(board=board, alerts=None), zeroconf_mod=None, platform="linux-test",
                    run=lambda *a, **k: SimpleNamespace(stdout="100.101.102.103\n", returncode=0),
                    route_ip=lambda: "192.168.1.20", port=9121)
    ui.data_dir = tmp_path
    client = TestClient(br.build_remote_app(board))
    phone = Phone()
    code = board.phones.new_code()
    board.phones.confirm(client.post("/phone/pair", json=phone.pair_body(code)).json()["fingerprint"])
    body = json.dumps({"nonce": "n" * 32, "rtt_ms": 35, "pinned": True, "via": "lan"}).encode()
    r = client.post("/phone/hello", content=body, headers=phone.headers(phone.read, "POST", "/phone/hello", body))
    assert r.status_code == 200, r.text
    assert r.json()["addresses"] == {"lan": "http://192.168.1.20:9121", "tailnet": "http://100.101.102.103:9121"}
    link = ui.phones()[0]["link"]
    assert link["state"] == "connected" and link["quality"] == "good"
    r = client.post("/phone/link-check", content=b"{}",
                    headers=phone.headers(phone.read, "POST", "/phone/link-check", b"{}", ts=now_ms() + 5))
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()["checks"]]
    assert {"port", "firewall", "address", "key"} <= set(ids)
