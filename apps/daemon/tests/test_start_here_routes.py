"""Start Here.cmd's daemon helpers: sharing the phone app on the Wi-Fi (phone_app_share.py) and POST /hud/open."""
import asyncio
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import boardroom as br
import hud_open
import phone_app_share as pas
import qr_svg

ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakeLink:
    def __init__(self):
        self.ports = 0

    def address(self):
        return {"url": "http://192.168.1.20:9121", "host": "192.168.1.20", "how": "lan", "words": ""}

    async def ensure_port(self):
        self.ports += 1
        return True

    def lan(self, fresh=False):
        return {"ok": True, "words": "fine", "fix": None, "fix_words": ""}


def make(tmp_path, with_apk=True):
    repo = tmp_path / "repo"
    out = repo / "apps/mobile/app/build/outputs/apk/debug"
    out.mkdir(parents=True)
    (repo / "secret.txt").write_text("not for phones")
    if with_apk:
        (out / "app-debug.apk").write_bytes(b"PK\x03\x04 fake apk")
    board = br.Boardroom(tmp_path / "data")
    clock = Clock()
    daemon = SimpleNamespace(board=board, phone_link=FakeLink())
    daemon.app_share = pas.attach(daemon, repo=repo, clock=clock)
    remote = TestClient(br.build_remote_app(board))
    app = FastAPI()
    app.include_router(pas.make_router(lambda: daemon))
    return daemon, clock, remote, TestClient(app), repo


def test_share_window_opens_serves_and_closes_by_itself(tmp_path):
    daemon, clock, remote, hud, repo = make(tmp_path)
    assert remote.get("/phone/app.apk").status_code == 404            # closed until you share
    st = hud.get("/phones/app-share").json()
    assert st["open"] is False and st["apk"] is True and st["url"] == ""

    r = hud.post("/phones/app-share", json={"minutes": 15}).json()
    assert r["open"] and r["url"] == "http://192.168.1.20:9121/phone/app.apk"
    assert r["until"] == clock.t + 15 * 60 and daemon.phone_link.ports == 1
    assert r["qr_svg"].startswith("<svg") and r["lan"]["ok"] is True
    assert r["qr_rows"] and set("".join(r["qr_rows"])) <= {"0", "1"}
    assert r["qr_rows"][2][2:-2] == "".join("1" if v else "0" for v in qr_svg.matrix(r["url"], "M")[0])

    got = remote.get("/phone/app.apk")
    assert got.status_code == 200 and got.content == b"PK\x03\x04 fake apk"
    assert got.headers["content-type"] == pas.APK_TYPE
    assert 'filename="Orion-Mobile.apk"' in got.headers["content-disposition"]
    assert remote.head("/phone/app.apk").status_code == 200
    assert hud.get("/phones/app-share").json()["downloads"] >= 1

    clock.t += 15 * 60 + 1                                            # time's up
    assert remote.get("/phone/app.apk").status_code == 404
    assert hud.get("/phones/app-share").json()["open"] is False


def test_close_now_and_minutes_are_clamped(tmp_path):
    daemon, clock, remote, hud, _ = make(tmp_path)
    assert hud.post("/phones/app-share", json={"minutes": 9999}).json()["until"] == clock.t + 60 * 60
    assert hud.post("/phones/app-share", json={"minutes": "x"}).json()["until"] == clock.t + 15 * 60
    assert hud.post("/phones/app-share", json={"minutes": -3}).json()["until"] == clock.t + 60
    assert hud.post("/phones/app-share/close").json()["open"] is False
    assert remote.get("/phone/app.apk").status_code == 404


def test_only_the_apk_is_ever_served(tmp_path):
    daemon, clock, remote, hud, repo = make(tmp_path)
    hud.post("/phones/app-share", json={"minutes": 5})
    for bad in ("/phone/app.apk/../secret.txt", "/phone/app.apk/x", "/phone/secret.txt", "/phone/../secret.txt",
                "/phone/app.apk%2F..%2Fsecret.txt", "/phone/apps/mobile/app/build/outputs/apk/debug/app-debug.apk",
                "/secret.txt", "/"):
        r = remote.get(bad)
        assert r.status_code in (401, 404, 405), bad
        assert b"not for phones" not in r.content, bad
    assert remote.post("/phone/app.apk").status_code == 405
    q = remote.get("/phone/app.apk?path=../secret.txt")                # parameters are ignored, still only the APK
    assert q.content == b"PK\x03\x04 fake apk"


def test_newest_apk_wins_and_links_out_are_refused(tmp_path):
    daemon, clock, remote, hud, repo = make(tmp_path)
    top = repo / "Orion-Mobile.apk"
    top.write_bytes(b"newer")
    old = repo / "apps/mobile/app/build/outputs/apk/debug/app-debug.apk"
    os.utime(old, (time.time() - 100, time.time() - 100))
    assert pas.find_apk(repo) == top.resolve()
    outside = tmp_path / "elsewhere.apk"
    outside.write_bytes(b"outside")
    top.unlink()
    try:
        (repo / "apps/mobile/app/build/outputs/apk/link.apk").symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    assert pas.find_apk(repo) == old.resolve()


def test_no_apk_means_no_share(tmp_path):
    daemon, clock, remote, hud, _ = make(tmp_path, with_apk=False)
    r = hud.post("/phones/app-share", json={"minutes": 15})
    assert r.status_code == 404 and "built" in r.json()["error"]
    assert hud.get("/phones/app-share").json()["apk"] is False
    assert remote.get("/phone/app.apk").status_code == 404


def test_qr_text_draws_light_squares():
    t = pas.qr_text("http://192.168.1.20:9121/phone/app.apk")
    lines = t.split("\n")
    assert lines[0] == "█" * len(lines[0])                      # the quiet border is light: full blocks
    assert set(t) <= {"█", "▀", "▄", " ", "\n"}


# ===================================================================================== /hud/open
class FakeBus:
    def __init__(self):
        self.sent = []
        self.clients = {"a": SimpleNamespace(kind="ui")}

    async def publish(self, event, record=True, **kw):
        self.sent.append((event, record))


def test_hud_open_broadcasts_the_event():
    daemon = SimpleNamespace(bus=FakeBus())
    app = FastAPI()
    app.include_router(hud_open.make_router(lambda: daemon))
    c = TestClient(app)
    r = c.post("/hud/open", json={"what": "phone"})
    assert r.status_code == 200 and r.json() == {"ok": True, "what": "phone", "window": True}
    assert daemon.bus.sent == [({"event": "HUD_OPEN", "what": "phone"}, False)]
    for bad in ({"what": ""}, {"what": "../x"}, {"what": "Phone<script>"}, {}, {"what": "x" * 40}):
        assert c.post("/hud/open", json=bad).status_code == 400
    assert len(daemon.bus.sent) == 1


def test_hud_open_without_bus_says_starting():
    app = FastAPI()
    app.include_router(hud_open.make_router(lambda: SimpleNamespace()))
    assert TestClient(app).post("/hud/open", json={"what": "phone"}).status_code == 503


def test_main_registers_both_with_marked_lines():
    src = (ROOT / "apps/daemon/main.py").read_text(encoding="utf-8")
    marked = [l for l in src.splitlines() if "[start-here]" in l]
    assert len(marked) == 3
    assert any("phone_app_share" in l and "attach" in l for l in marked)
    assert any("hud_open" in l for l in marked)
