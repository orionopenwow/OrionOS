"""Orion Mobile v0.2: learning alerts, next-step cards, chat/voice routes, quick commands."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import alerts as al
import boardroom as br
import phone_api
from guide_cards import GuideCards
from quick_commands import QuickCommands, normalize
from tests.test_phones import FakePhone, paired

DAY = 86400


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- alerts that learn
def test_ignored_alerts_step_down_and_say_so(tmp_path):
    clk = Clock()
    c = al.AlertCenter(tmp_path, clock=clk)
    ids = [c.post("openwow", f"crash {i}")["id"] for i in range(12)]
    clk.t += 2 * 3600
    changes = c.feedback([{"id": i, "action": "dismissed", "at": clk.t} for i in ids])
    assert changes == [{"type": "openwow", "from": "normal", "to": "digest", "why": "you open 0% of them"}]
    assert c.policy("openwow")["level"] == "digest"
    told = c.feed()["alerts"][-1]
    assert told["type"] == "system" and "OpenWoW alerts are now Digest" in told["title"]
    # cooldown: no second change for 3 days even with more ignored alerts
    more = [c.post("openwow", "x")["id"] for _ in range(12)]
    clk.t += 2 * 3600
    assert c.feedback([{"id": i, "action": "dismissed"} for i in more]) == []


def test_fast_opens_step_up(tmp_path):
    clk = Clock()
    c = al.AlertCenter(tmp_path, clock=clk)
    posted = []
    for i in range(12):
        posted.append((c.post("messages", f"m{i}")["id"], clk.t))
        clk.t += 600
    clk.t += 3600
    changes = c.feedback([{"id": i, "action": "opened", "at": ts + 30} for i, ts in posted])
    assert changes and changes[0]["to"] == "urgent"


def test_approvals_never_below_normal_and_locks_hold(tmp_path):
    clk = Clock()
    c = al.AlertCenter(tmp_path, clock=clk)
    c.set("approval", "normal")
    with pytest.raises(ValueError):
        c.set("approval", "digest")
    clk.t += 4 * DAY
    ids = [c.post("approval", "a")["id"] for _ in range(15)]
    clk.t += 2 * 3600
    assert c.feedback([{"id": i, "action": "dismissed"} for i in ids]) == []
    assert c.policy("approval")["level"] == "normal"
    c.set("life", locked=True)
    ids = [c.post("life", "l")["id"] for _ in range(15)]
    clk.t += 2 * 3600
    assert c.feedback([{"id": i, "action": "dismissed"} for i in ids]) == []


def test_quiet_hours_turn_normal_into_digest_but_not_approvals(tmp_path):
    clk = Clock(time.mktime((2027, 1, 1, 12, 0, 0, 0, 0, -1)))
    c = al.AlertCenter(tmp_path, clock=clk)
    first = c.post("life", "start")["id"]
    for day in range(8):                      # you open things around noon every day, never at 3am
        a = c.post("life", "noon")["id"]
        c.feedback([{"id": a, "action": "opened", "at": clk.t + 10}])
        clk.t += DAY
    assert 3 in c.quiet_hours() and 12 not in c.quiet_hours()
    clk.t = time.mktime((2027, 1, 9, 3, 0, 0, 0, 0, -1))
    assert c.post("life", "3am")["level"] == "digest"
    assert c.post("approval", "3am")["level"] == "urgent"


def test_long_poll_wakes_on_new_alert(tmp_path):
    c = al.AlertCenter(tmp_path)

    async def go():
        waiter = asyncio.create_task(c.wait_feed(0, 5))
        await asyncio.sleep(0.05)
        c.post("system", "hello")
        t0 = time.time()
        out = await waiter
        return out, time.time() - t0
    out, took = asyncio.new_event_loop().run_until_complete(go())
    assert [a["title"] for a in out["alerts"]] == ["hello"] and took < 1


# ---------------------------------------------------------------- next-step cards
def screen():
    img = Image.new("RGB", (1600, 900), (20, 20, 40))
    return img


def test_card_crops_rings_and_clicks_only_when_asked(tmp_path):
    clicks = []
    cards = GuideCards(tmp_path, grab=screen, title=lambda: "Discord", click=lambda x, y: clicks.append((x, y)),
                       ocr=lambda img: [{"text": "Continue", "x": 800, "y": 450, "w": 80, "h": 20}])
    m = cards.create("Click Continue", target=(820, 460))
    assert m["image"] and m["withheld"] is None
    img = Image.open(__import__("io").BytesIO(cards.image(m["id"])))
    assert img.width < 1600   # cropped around the target
    assert clicks == []
    cards.do(m["id"])
    assert clicks == [(820, 460)]
    with pytest.raises(ValueError):
        cards.do(m["id"])  # once only


def test_sign_in_screens_send_no_picture(tmp_path):
    cards = GuideCards(tmp_path, grab=screen, title=lambda: "Discord - Log In", click=lambda x, y: None,
                       ocr=lambda img: [{"text": "Password", "x": 1, "y": 1, "w": 5, "h": 5}])
    m = cards.create("Type your password (you do this one)")
    assert m["image"] is False and "sign-in" in m["withheld"]
    assert cards.image(m["id"]) is None


def test_secrets_on_screen_are_blurred(tmp_path):
    def grab():
        img = Image.new("RGB", (400, 200), (255, 255, 255))
        for x in range(100, 300):
            for y in range(80, 100):
                img.putpixel((x, y), (0, 0, 0) if (x // 3) % 2 else (255, 255, 255))
        return img
    cards = GuideCards(tmp_path, grab=grab, title=lambda: "Terminal", click=lambda x, y: None,
                       ocr=lambda img: [{"text": "sk-" + "ant-api03-abcdefghijklmnopqrstu", "x": 100, "y": 80, "w": 200, "h": 20}])
    m = cards.create("Look at this")
    img = Image.open(__import__("io").BytesIO(cards.image(m["id"]))).convert("L")
    # the striped "secret" became a smooth grey blur
    row = [img.getpixel((x, 90)) for x in range(130, 270)]
    assert max(row) - min(row) < 80


def test_old_pictures_disappear(tmp_path):
    clk = Clock()
    cards = GuideCards(tmp_path, grab=screen, title=lambda: "x", click=lambda x, y: None, ocr=lambda i: [], clock=clk)
    m = cards.create("step")
    clk.t += 25 * 3600
    assert cards.image(m["id"]) is None and cards.open_cards() == []


# ---------------------------------------------------------------- quick commands
def fake_run(log):
    async def run(*args, timeout=60):
        log.append(args)
        if args[-1] == "status":
            return 0, "OpenWoW: running since today"
        return 0, f"ok {' '.join(args)}"
    return run


def test_quick_commands_match_plain_speech(tmp_path):
    q = QuickCommands(config=tmp_path / "q.json", run=fake_run([]))
    assert normalize("Hey Orion, start the server please!") == "start the server"
    assert q.match("Orion, start the server") == ("wow start", ["wow:start"])
    assert q.match("stream")[1] == ["app:discord", "wow:start"]
    assert q.match("wow update") is None          # update asks the phone, never a quick command
    assert q.match("what's the weather") is None


def test_quick_commands_refuse_unsafe_steps(tmp_path):
    q = QuickCommands(config=tmp_path / "q.json", run=fake_run([]))
    for bad in (["wow:update"], ["wow:build"], ["app:discord; rm -rf ~"], ["shell:rm"]):
        with pytest.raises(ValueError):
            q.save_command("oops", bad)
    q.save_command("game night", ["app:discord", "wow:start"])
    assert "game night" in q.commands()


def test_run_steps_and_brief(tmp_path):
    log = []
    board = br.Boardroom(tmp_path / "b")
    q = QuickCommands(config=tmp_path / "q.json", run=fake_run(log), board=board)
    out = asyncio.new_event_loop().run_until_complete(q.run_steps(["app:discord", "say:status"]))
    assert "Opening discord." in out and "Nothing waiting for your approval." in out
    assert log[0][:2] == ("swaymsg", "exec")
    t = time.mktime((2027, 1, 1, 8, 5, 0, 0, 0, -1))
    assert q.brief_due(t, 0) and not q.brief_due(t, t - 60)
    assert not q.brief_due(time.mktime((2027, 1, 1, 7, 0, 0, 0, 0, -1)), 0)


# ---------------------------------------------------------------- the routes, with a fake phone
class FakeHub:
    def __init__(self, tmp_path):
        self.alerts = al.AlertCenter(tmp_path / "a")
        self.clicks = []
        self.cards = GuideCards(tmp_path / "c", grab=screen, title=lambda: "x",
                                click=lambda x, y: self.clicks.append((x, y)), ocr=lambda i: [])
        self.quick = QuickCommands(config=tmp_path / "q.json", run=fake_run([]))
        self.said = []

    async def converse(self, text, source):
        self.said.append((text, source))
        return f"you said {text}"

    async def transcribe(self, audio):
        return "start the server" if audio else ""

    async def speak(self, text):
        return b"RIFFfake"


@pytest.fixture()
def lab2(tmp_path):
    board = br.Boardroom(tmp_path / "board")
    hub = FakeHub(tmp_path)
    board.remote_hooks.append(phone_api.register(hub))
    client = TestClient(br.build_remote_app(board))
    phone = FakePhone()
    paired(board, client, phone)
    return board, hub, client, phone


def post(client, phone, key, path, data):
    body = json.dumps(data).encode()
    return client.post(path, content=body, headers=phone.headers(key, "POST", path, body))


def test_unsigned_requests_get_nothing(lab2):
    board, hub, client, phone = lab2
    for path in ("/phone/feed", "/phone/cards", "/phone/quick"):
        assert client.get(path).status_code == 401
    assert client.post("/phone/chat", json={"text": "hi"}).status_code == 401


def test_feed_feedback_and_chat(lab2):
    board, hub, client, phone = lab2
    hub.alerts.post("openwow", "OpenWoW stopped")
    r = client.get("/phone/feed", headers=phone.headers(phone.read, "GET", "/phone/feed"))
    assert r.status_code == 200 and r.json()["alerts"][0]["title"] == "OpenWoW stopped"
    aid = r.json()["alerts"][0]["id"]
    assert post(client, phone, phone.read, "/phone/feedback", {"events": [{"id": aid, "action": "opened"}]}).status_code == 200
    r = post(client, phone, phone.read, "/phone/chat", {"text": "status", "speak": True})
    assert r.json()["reply"] == "you said status" and r.json()["audio_b64"]
    assert hub.said == [("status", "phone")]


def test_voice_route(lab2):
    board, hub, client, phone = lab2
    audio = b"\x00\x01" * 1000
    r = client.post("/phone/voice", content=audio,
                    headers={**phone.headers(phone.read, "POST", "/phone/voice", audio), "Content-Type": "audio/mp4"})
    assert r.status_code == 200 and r.json()["heard"] == "start the server"


def test_orion_does_it_needs_the_fingerprint_key(lab2):
    board, hub, client, phone = lab2
    m = hub.cards.create("Click Join", target=(500, 300))
    assert post(client, phone, phone.read, "/phone/card/do", {"id": m["id"]}).status_code == 401
    assert hub.clicks == []
    r = post(client, phone, phone.sign, "/phone/card/do", {"id": m["id"]})
    assert r.status_code == 200 and hub.clicks == [(500, 300)]
    path = f"/phone/card-image/{m['id']}"
    img = client.get(path, headers=phone.headers(phone.read, "GET", path))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_proposals_post_an_approval_alert(tmp_path):
    board = br.Boardroom(tmp_path / "b")
    c = al.AlertCenter(tmp_path / "a")
    board.on_proposed.append(lambda d: c.post("approval", "Orion needs your approval", d["packet"]["summary"], ref=d["id"]))
    asyncio.new_event_loop().run_until_complete(board.propose("merge.docs", "Merge the FAQ fix", {}, "CTO"))
    a = c.feed()["alerts"][0]
    assert a["type"] == "approval" and a["level"] == "urgent" and a["ref"].startswith("dec-")
