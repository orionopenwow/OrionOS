"""One-tap phone pairing from the HUD (phone_pairing_ui.py) and the pure-Python QR encoder (qr_svg.py)."""
import asyncio
import base64
import json
import re
import time
import xml.dom.minidom
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

import boardroom as br
import phone_pairing_ui as ppu
import phones as ph
import qr_svg

ROOT = Path(__file__).resolve().parents[3]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakePhone:
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

    def headers(self, method, path, body=b""):
        self.ts += 1
        msg = br.signing_message(method, path, str(self.ts), body)
        sig = base64.b64encode(self.read.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return {"X-Orion-Device": self.device_id, "X-Orion-Ts": str(self.ts), "X-Orion-Sig": sig,
                "Content-Type": "application/json"}


class FakeAlerts:
    def __init__(self):
        self.posted = []

    def post(self, kind, title, body="", **kw):
        self.posted.append((kind, title, body))


class Result:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode = out, rc


class FakeZeroconfModule:
    """Stands in for the optional zeroconf package."""
    def __init__(self):
        self.registered, self.unregistered = [], []
        mod = self

        class ServiceInfo:
            def __init__(self, type_, name, addresses, port, properties, server):
                self.type, self.name, self.addresses, self.port, self.properties = type_, name, addresses, port, properties

        class Zeroconf:
            def register_service(self, info):
                mod.registered.append(info)

            def unregister_service(self, info):
                mod.unregistered.append(info)

            def close(self):
                pass

        self.ServiceInfo, self.Zeroconf = ServiceInfo, Zeroconf


def make(tmp_path, platform="linux", runner=None, zc=None, popen=None):
    board = br.Boardroom(tmp_path)
    daemon = SimpleNamespace(board=board, alerts=FakeAlerts())
    calls = []

    def default_run(args, **kw):
        calls.append(args)
        return Result("", 1)

    ui = ppu.attach(daemon, platform=platform, run=runner or default_run, popen=popen or (lambda *a, **k: None),
                    zeroconf_mod=zc, route_ip=lambda: "192.168.1.20", port=9121)
    daemon.phone_link = ui
    ui.port_running = lambda: True           # never bind a real port in tests

    async def no_port():
        return True
    ui.ensure_port = no_port
    remote = TestClient(br.build_remote_app(board))
    app = FastAPI()
    app.include_router(ppu.make_router(lambda: daemon))
    return daemon, ui, remote, TestClient(app), calls


# ===================================================================================== QR
def test_qr_sizes_and_finder_patterns():
    m = qr_svg.matrix("hi", "M")
    assert len(m) == 21 and all(len(r) == 21 for r in m)        # version 1
    for (x, y) in ((0, 0), (14, 0), (0, 14)):
        assert all(m[y][x + i] for i in range(7)) and all(m[y + 6][x + i] for i in range(7))
        assert not m[y + 1][x + 1] and m[y + 3][x + 3]          # ring then centre
    url = "orion://pair?u=http://192.168.100.200:9121&c=ABCD-EFGH-JKMN-PQRS-TUVW-XYZ2-3456"
    assert len(qr_svg.matrix(url, "M")) == 4 * 5 + 17           # 79 bytes -> version 5 at level M
    assert len(qr_svg.matrix("x" * 2953, "L")) == 177           # version 40, full
    with pytest.raises(ValueError):
        qr_svg.matrix("x" * 2954, "L")


def test_qr_format_bits_both_copies_agree():
    for ecl in "LMQH":
        m = qr_svg.matrix("Orion " + ecl, ecl)
        s = len(m)
        a = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
        b = [m[8][s - 1 - i] for i in range(8)] + [m[s - 15 + i][8] for i in range(8, 15)]
        assert a == b and m[s - 8][8]                           # and the dark module


def test_qr_reed_solomon_known_value():
    # The standard's worked example (ISO 18004 annex I): "01234567" at version 1-M.
    data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
    ecc = qr_svg._rs_remainder(data, qr_svg._rs_divisor(10))
    assert ecc == [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55]


def test_qr_svg_is_valid_xml():
    s = qr_svg.svg("orion://pair?u=http://10.0.0.2:9121&c=AAAA")
    doc = xml.dom.minidom.parseString(s)
    assert doc.documentElement.tagName == "svg" and "<script" not in s


# ===================================================================================== words
def test_fingerprint_words():
    assert len(ppu.WORDS) == 256
    w = ppu.fingerprint_words("00FF-0102-0304-0506-0708")
    assert w.split() == [ppu.WORDS[0], ppu.WORDS[255], ppu.WORDS[1], ppu.WORDS[2], ppu.WORDS[3]]
    assert ppu.fingerprint_words("nope") == ""


def test_phone_word_list_matches():
    kt = (ROOT / "apps/mobile/app/src/main/java/com/orion/mobile/PairPc.kt").read_text(encoding="utf-8")
    m = re.search(r'WORDS\s*=\s*\(\s*((?:"[^"]*"\s*\+?\s*)+)\)', kt)
    assert m, "PairPc.kt word list not found"
    words = "".join(re.findall(r'"([^"]*)"', m.group(1))).split()
    assert words == ppu.WORDS


# ===================================================================================== the whole flow
def test_open_pair_confirm_hello_unpair(tmp_path):
    daemon, ui, remote, hud, _ = make(tmp_path)
    r = hud.post("/phones/pair/open").json()
    assert r["url"] == f"orion://pair?u=http://192.168.1.20:9121&c={r['code']}"
    assert r["qr_svg"].startswith("<svg") and r["expires"] > time.time()
    assert r["lan"]["ok"] is True

    st = hud.get("/phones/pair/status").json()
    assert st["open"] and st["pending"] == [] and st["phones"] == []

    phone = FakePhone()
    fp = remote.post("/phone/pair", json=phone.pair_body(r["code"])).json()["fingerprint"]
    st = hud.get("/phones/pair/status").json()
    assert st["pending"][0]["fingerprint"] == fp
    assert st["pending"][0]["words"] == ppu.fingerprint_words(fp) and len(st["pending"][0]["words"].split()) == 5

    # the phone can't do anything until you press Confirm
    body = json.dumps({"battery": 81}).encode()
    assert remote.post("/phone/hello", content=body, headers=phone.headers("POST", "/phone/hello", body)).status_code == 401

    assert hud.post("/phones/someone-else/confirm").status_code == 409
    assert hud.post(f"/phones/{phone.device_id}/confirm", json={"fingerprint": "0000-0000-0000-0000-0000"}).status_code == 409
    ok = hud.post(f"/phones/{phone.device_id}/confirm", json={"fingerprint": fp})
    assert ok.status_code == 200 and ok.json()["device_id"] == phone.device_id
    assert not hud.get("/phones/pair/status").json()["open"]

    body = json.dumps({"battery": 81, "charging": True, "network": "wifi", "app": "0.3"}).encode()
    h = remote.post("/phone/hello", content=body, headers=phone.headers("POST", "/phone/hello", body))
    assert h.status_code == 200 and h.json()["pc"]["id"] == ui.pc_id()
    p = hud.get("/phones").json()["phones"][0]
    assert p["name"] == "Test Pixel" and p["online"] and p["battery"] == 81 and p["network"] == "wifi"

    t = hud.post("/phones/test-card").json()
    assert t["ok"] and daemon.alerts.posted[0][1] == "Test from your PC"

    assert hud.post("/phones/all/unpair").status_code == 400
    assert hud.post(f"/phones/{phone.device_id}/unpair").json()["removed"] == 1
    assert hud.get("/phones").json()["phones"] == []
    assert hud.post("/phones/test-card").json()["ok"] is False


def test_close_forgets_waiting_phone(tmp_path):
    _, ui, remote, hud, _ = make(tmp_path)
    code = hud.post("/phones/pair/open").json()["code"]
    phone = FakePhone()
    remote.post("/phone/pair", json=phone.pair_body(code))
    st = hud.post("/phones/pair/close").json()
    assert not st["open"] and st["pending"] == []
    assert hud.post(f"/phones/{phone.device_id}/confirm").status_code == 409


def test_last_seen_goes_offline(tmp_path):
    _, ui, remote, hud, _ = make(tmp_path)
    code = hud.post("/phones/pair/open").json()["code"]
    phone = FakePhone()
    fp = remote.post("/phone/pair", json=phone.pair_body(code)).json()["fingerprint"]
    ui.confirm(phone.device_id, fp)
    assert ui.phones()[0]["online"] is False            # paired, never seen
    ui.clock = lambda: time.time()
    body = b"{}"
    remote.post("/phone/hello", content=body, headers=phone.headers("POST", "/phone/hello", body))
    assert ui.phones()[0]["online"] is True
    ui.clock = lambda: time.time() + 3600
    assert ui.phones()[0]["online"] is False


# ===================================================================================== addresses and reachability
def test_address_prefers_vbox_then_lan_then_tailscale(tmp_path, monkeypatch):
    monkeypatch.delenv("ORION_PHONE_URL", raising=False)

    def runner(args, **kw):
        if args[0] == "VBoxControl":
            return Result("Value: http://192.168.1.50:9121")
        return Result("", 1)
    _, ui, *_ = make(tmp_path / "a", runner=runner)
    assert ui.address()["url"] == "http://192.168.1.50:9121"

    _, ui, *_ = make(tmp_path / "b")
    assert ui.address() == {"url": "http://192.168.1.20:9121", "host": "192.168.1.20", "how": "lan",
                            "words": "Your phone must be on the same Wi-Fi as this PC."}

    def ts(args, **kw):
        return Result("100.101.102.103\n") if args[0] == "tailscale" else Result("", 1)
    _, ui, *_ = make(tmp_path / "c", runner=ts)
    ui.route_ip = lambda: None
    assert ui.address()["url"] == "http://100.101.102.103:9121"
    monkeypatch.setenv("ORION_PHONE_URL", "http://pc.example:9121/")
    assert ui.address()["url"] == "http://pc.example:9121"


def test_windows_firewall_check_and_open_access(tmp_path):
    state = {"rule": False, "cats": ["Public"]}

    def runner(args, **kw):
        if args[0] == "powershell":
            return Result(json.dumps(state))
        return Result("", 1)
    launched = []
    _, ui, _, hud, _ = make(tmp_path, platform="win32", runner=runner, popen=lambda a, **k: launched.append(a))
    lan = hud.get("/phones/lan").json()
    assert lan["ok"] is False and lan["fix"] == "open_access" and "Firewall" in lan["words"]
    state["rule"] = True
    lan = hud.get("/phones/lan").json()
    assert lan["ok"] is False and "public" in lan["words"]
    state["cats"] = ["Private"]
    assert hud.get("/phones/lan").json()["ok"] is True

    r = hud.post("/phones/open-access").json()
    assert r["ok"] and launched and launched[0][-1] == "-ThisPC" and launched[0][-2].endswith("Open-PhoneAccess.ps1")


def test_open_access_is_windows_only(tmp_path):
    _, ui, _, hud, _ = make(tmp_path)
    assert hud.post("/phones/open-access").json()["ok"] is False


def test_lab_inside_virtualbox_says_so(tmp_path):
    def runner(args, **kw):
        return Result("oracle") if args[0] == "systemd-detect-virt" else Result("", 1)
    _, ui, *_ = make(tmp_path, runner=runner)
    lan = ui.lan(fresh=True)
    assert lan["ok"] is False and lan["fix"] == "vbox_open_access" and "Open Phone Access" in lan["fix_words"]


def test_port_off_is_reported(tmp_path):
    _, ui, *_ = make(tmp_path)
    ui.port_running = lambda: False
    assert ui.lan(fresh=True)["ok"] is False


def test_ensure_port_remembers(tmp_path):
    board = br.Boardroom(tmp_path)
    started = []

    async def fake_start(simulator_every_s=0.0, bind="", port=9121):
        started.append((bind, port))
        board._server = object()
    board.start = fake_start
    ui = ppu.PhonePairingUI(SimpleNamespace(board=board), zeroconf_mod=None, port=9599)
    assert run(ui.ensure_port())
    assert started == [("0.0.0.0", 9599)] and board.store.get("phone_port_on") == "1"
    assert run(ui.ensure_port()) and len(started) == 1


# ===================================================================================== mDNS
def test_advertises_only_while_pairing_or_paired(tmp_path):
    zc = FakeZeroconfModule()
    _, ui, remote, hud, _ = make(tmp_path, zc=zc)
    assert ui.advertise() is False and not zc.registered       # nothing paired, pairing closed
    r = hud.post("/phones/pair/open").json()
    assert r["zeroconf"] is True
    info = zc.registered[-1]
    assert info.type == "_orion._tcp.local." and info.port == 9121 and info.properties["id"] == ui.pc_id()
    hud.post("/phones/pair/close")
    assert zc.unregistered                                     # closed, nobody paired: quiet again


def test_no_zeroconf_is_fine(tmp_path):
    _, ui, _, hud, _ = make(tmp_path, zc=None)
    assert hud.post("/phones/pair/open").json()["zeroconf"] is False


def test_pc_id_is_stable(tmp_path):
    _, ui, *_ = make(tmp_path)
    a = ui.pc_id()
    assert a == ui.pc_id() and re.fullmatch(r"[0-9a-f]{12}", a)
