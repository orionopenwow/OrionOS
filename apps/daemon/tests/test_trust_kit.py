"""Toolkit 22/23/25: Tripwires, Share Scrubber, Promise Keeper, plus the phone health route, the core feed from
the health monitor and the read-only MCP tools. Offline: temporary folders, fake comms, fake OCR, a local
listener on 127.0.0.1 only. Fake secrets are joined from pieces at runtime ([fake-secrets])."""
import asyncio
import io
import json
import os
import shutil
import stat
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import alerts
import boardroom as br
import health_monitor as hm
import promise_keeper as pk
import share_scrubber as ss
import tripwires as tw
import trust_kit


def run(coro):
    return asyncio.run(coro)


def fake_openai() -> str:
    return "sk-" + "proj-" + "Q7w" + "E9r" + "T2y" + "U4i" + "O6p" + "A8s" + "D0f" + "G1h"


@pytest.fixture()
def center(tmp_path):
    c = alerts.AlertCenter(tmp_path / "alerts")
    alerts.install(c)
    yield c
    alerts.install(None)


# =====================================================================================  Share Scrubber
TEXT = ("Traceback (most recent call last):\n"
        '  File "/home/sam/projects/game/server.py", line 12\n'
        "  C:\\Users\\Sam\\AppData\\Local\\orion\\log.txt\n"
        "mail: sam.doe@example.org  peer 203.0.113.7  local 127.0.0.1  v6 2001:db8::7 at 12:30:45\n"
        "ssh raid-box then db.internal.lan:3306\n")


def scrubber(tmp_path):
    cfg = tmp_path / "ssh_config"
    cfg.write_text("Host raid-box\n  HostName db.internal.lan\n  User sam\nHost *\n  ServerAliveInterval 30\n")
    return ss.Scrubber(ssh_config=cfg)


def test_text_scrub_covers_everything(tmp_path):
    s = scrubber(tmp_path)
    key = fake_openai()
    clean, c = s.report(TEXT + f"OPENAI_API_KEY={key}\n")
    assert key not in clean and "[redacted]" in clean
    assert "sam.doe@example.org" not in clean and "[email]" in clean
    assert "/home/sam" not in clean and "~/projects/game/server.py" in clean
    assert "C:\\Users\\Sam" not in clean and "%USERPROFILE%\\AppData" in clean
    assert "203.0.113.7" not in clean and "2001:db8::7" not in clean
    assert "127.0.0.1" in clean and "12:30:45" in clean            # loopback and times stay
    assert "raid-box" not in clean and "db.internal.lan" not in clean and clean.count("[server]") == 2
    assert c.emails == 1 and c.paths == 2 and c.ips == 2 and c.servers == 2 and c.secrets >= 1
    assert c.words().startswith("Took out") and "2 IP addresses" in c.words()


def test_ssh_config_hosts(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("# mine\nHost web1 web1-alias *.lan\n HostName 10.0.0.5\nHost github.com\n HostName github.com\n")
    assert ss.ssh_hosts(cfg) == ["web1-alias", "web1"]             # no wildcards, IPs or public forges
    assert ss.ssh_hosts(tmp_path / "missing") == []


def test_message_mode_is_gentle(tmp_path):
    s = scrubber(tmp_path)
    msg = "did you change the passwords? mail me at sam@example.org, it's in /home/sam/notes"
    out = ss._scrub_value(msg, False)
    assert "passwords?" in out and "sam@example.org" in out and "~/notes" in out
    assert fake_openai() not in ss._scrub_value("here " + fake_openai(), False)
    assert s.scrub_text("nothing to see") == "nothing to see"


def test_approval_queue_scrubs_outgoing(tmp_path):
    from approval_queue import ApprovalQueue
    q = ApprovalQueue(tmp_path / "approvals.json")
    item = q.add("comms_reply", draft_reply="token is " + fake_openai(),
                 attachments=["log at /home/sam/x.log from 198.51.100.4 by sam@example.org"])
    assert fake_openai() not in item["draft_reply"]
    att = item["attachments"][0]
    assert "/home/sam" not in att and "198.51.100.4" not in att and "sam@example.org" not in att
    assert fake_openai() not in (tmp_path / "approvals.json").read_text()


def _picture(lines):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 60 + 50 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        d.text((20, 20 + 50 * i), line, fill="black", font=font)
    return img


def test_image_blur_with_fake_ocr(tmp_path):
    s = scrubber(tmp_path)
    img = _picture(["hello world"])
    words = [{"text": "hello", "x": 20, "y": 20, "w": 80, "h": 30},
             {"text": "sam@example.org", "x": 120, "y": 20, "w": 240, "h": 30},
             {"text": "password:", "x": 380, "y": 20, "w": 140, "h": 30},
             {"text": "hunter2x", "x": 530, "y": 20, "w": 120, "h": 30}]
    out, n = s.scrub_image(img, ocr=lambda _i: words)
    assert n == 2                                                   # the email, and the word after "password:"
    assert out.crop((120, 20, 360, 50)).tobytes() != img.crop((120, 20, 360, 50)).tobytes()
    assert out.crop((20, 20, 100, 50)).tobytes() == img.crop((20, 20, 100, 50)).tobytes()


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract isn't installed")
def test_image_blur_with_real_ocr(tmp_path):
    s = scrubber(tmp_path)
    img = _picture(["Contact sam.doe@example.org now", "Server 203.0.113.77 is up"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data, n = s.scrub_png_bytes(buf.getvalue())
    assert n >= 2
    from guide_cards import _ocr_words
    from PIL import Image
    again = " ".join(w["text"] for w in _ocr_words(Image.open(io.BytesIO(data))))
    assert "example.org" not in again and "203.0.113.77" not in again


def test_clipboard_text_and_image(tmp_path):
    s = scrubber(tmp_path)
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        if argv[:2] == ["wl-paste", "--list-types"]:
            return SimpleNamespace(stdout=b"text/plain;charset=utf-8\nUTF8_STRING\n", returncode=0)
        if argv[:2] == ["wl-paste", "--no-newline"]:
            return SimpleNamespace(stdout=b"ssh sam@203.0.113.9", returncode=0)
        return SimpleNamespace(stdout=b"", returncode=0)
    msg = s.clean_clipboard(run=fake_run)
    copied = [inp for argv, inp in calls if argv[0] == "wl-copy"]
    assert copied and b"203.0.113.9" not in copied[0] and "clean copy" in msg

    img = _picture(["x"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    calls.clear()

    def fake_run_img(argv, **kw):
        calls.append((argv, kw.get("input")))
        if argv[:2] == ["wl-paste", "--list-types"]:
            return SimpleNamespace(stdout=b"image/png\n", returncode=0)
        if argv[:2] == ["wl-paste", "--type"]:
            return SimpleNamespace(stdout=buf.getvalue(), returncode=0)
        return SimpleNamespace(stdout=b"", returncode=0)
    msg = s.clean_clipboard(run=fake_run_img, ocr=lambda _i: [{"text": "10.1.2.3", "x": 1, "y": 1, "w": 30, "h": 20}])
    assert "blurred 1" in msg
    assert any(argv[:3] == ["wl-copy", "--type", "image/png"] for argv, _ in calls)


def test_scrub_file_and_cli(tmp_path, monkeypatch):
    f = tmp_path / "crash.log"
    f.write_text("at /home/sam/app.py from 192.0.2.10\n")
    s = ss.Scrubber(hosts=[])
    out, words = s.scrub_file(f)
    assert out.name == "crash-clean.log" and "/home/sam" not in out.read_text() and "IP address" in words
    monkeypatch.setattr(ss, "_DEFAULT", s)
    monkeypatch.setattr(ss, "_notify", lambda *a: None)
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b""))
    assert ss.main(["file", str(f)]) == 0


def test_guide_cards_blur_emails(tmp_path):
    from guide_cards import GuideCards
    img = _picture(["hi"])
    words = [{"text": "sam@example.org", "x": 10, "y": 10, "w": 200, "h": 30}]
    seen = []
    orig = GuideCards._blur_secrets

    def spy(im, boxes):
        seen.extend(boxes)
        return orig(im, boxes)
    cards = GuideCards(tmp_path, grab=lambda: img, title=lambda: "Editor", ocr=lambda _i: words)
    cards._blur_secrets = spy
    cards.create("Click Save")
    assert seen and seen[0]["text"] == "sam@example.org"


def test_crash_text_is_scrubbed(tmp_path):
    from crash_healer import CrashHealer
    rec = {"language": "python", "command": "python /home/sam/app.py", "error_type": "ValueError",
           "message": "cannot reach 198.51.100.20", "trace": "File /home/sam/app.py line 3"}
    text = CrashHealer._crash_text(SimpleNamespace(), rec)
    assert "/home/sam" not in text and "198.51.100.20" not in text and "ValueError" in text


# =====================================================================================  Tripwires
@pytest.fixture()
def wires(tmp_path, center):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "orion-repo"
    repo.mkdir()
    t = tw.Tripwires(tmp_path / "data", home=home, repo_root=repo, default_callback="http://127.0.0.1:9120")
    return SimpleNamespace(t=t, home=home, repo=repo, center=center, tmp=tmp_path)


def _tokens(st):
    return list(st["planted"].keys())


def test_plant_builds_unique_bait_secret_shield_lets_through(wires):
    import secret_shield
    res = wires.t.plant()
    assert len(res["planted"]) == 4 and not res["skipped"]
    st = wires.t._load()
    toks = _tokens(st)
    assert len(set(toks)) == 4 and all(tw.TOKEN_RE.fullmatch(x) for x in toks)
    for token, p in st["planted"].items():
        f = Path(p["file"])
        text = f.read_text()
        assert token in text and f"http://127.0.0.1:9120/tripwire/{token}" in text
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
        assert secret_shield.scan_text(text, "bait.txt") == []           # Orion's own decoys pass
    assert (wires.home / "Documents/passwords-old.txt").exists()
    # Without the marker the same bait would be flagged: the allow list is what lets it through.
    cloud = next(Path(p["file"]) for p in st["planted"].values() if p["kind"] == "cloud_key").read_text()
    assert secret_shield.scan_text(cloud.replace("orntw", "qzpxv").replace("ORNTW", "QZPXV"), "x.txt")
    # Planting twice adds nothing new.
    assert wires.t.plant()["planted"] == []


def test_never_in_public_repo_or_over_real_files(wires):
    wires.t.set_config({"baits": [{"kind": "passwords", "path": str(wires.repo / "passwords.txt")},
                                  {"kind": "desktop"}]})
    (wires.home / "Desktop").mkdir()
    (wires.home / "Desktop/server-logins.txt").write_text("mine")
    res = wires.t.plant()
    assert res["planted"] == [] and len(res["skipped"]) == 2
    assert not (wires.repo / "passwords.txt").exists()
    assert (wires.home / "Desktop/server-logins.txt").read_text() == "mine"
    assert "public repo" in res["skipped"][0]


@pytest.mark.skipif(not shutil.which("git"), reason="git isn't installed")
def test_git_branch_bait_and_removal(wires):
    repo = wires.tmp / "site"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README").write_text("hi\n")
    wires.t.set_config({"baits": [{"kind": "git_branch"}], "repo": str(repo)})
    res = wires.t.plant()
    assert len(res["planted"]) == 1, res
    out = subprocess.run(["git", "-C", str(repo), "show", f"{tw.GIT_BRANCH}:.env.production"],
                         capture_output=True, text=True).stdout
    token = _tokens(wires.t._load())[0]
    assert token in out and "DEPLOY_PASSWORD=" in out
    assert (repo / "README").read_text() == "hi\n" and not (repo / ".env.production").exists()
    assert wires.t.remove()["removed"] == 1
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", tw.GIT_BRANCH])
    assert r.returncode != 0
    # Orion's own repo is refused.
    subprocess.run(["git", "init", "-q", str(wires.repo)], check=True)
    wires.t.set_config({"repo": str(wires.repo)})
    assert "public repo" in wires.t.plant()["skipped"][0]


def test_hit_through_daemon_route_alerts_urgently(wires):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    wires.t.plant()
    st = wires.t._load()
    token = next(t for t, p in st["planted"].items() if p["kind"] == "passwords")
    app = FastAPI()
    app.include_router(tw.make_router(lambda: wires.t, lambda: None))
    c = TestClient(app)
    r = c.get(f"/tripwire/{token}/admin", headers={"User-Agent": "curl/8.5"})
    assert r.status_code == 403
    feed = wires.center.feed()["alerts"]
    assert len(feed) == 1 and feed[0]["level"] == "urgent" and "curl/8.5" in feed[0]["body"]
    assert "passwords file" in feed[0]["body"] and token not in feed[0]["body"]
    c.get(f"/tripwire/{token}/admin")                                       # quiet period: counted, not re-alerted
    assert len(wires.center.feed()["alerts"]) == 1
    assert wires.t._load()["planted"][token]["hits"] == 2
    c.get("/tripwire/orntw0000000000000000/x")                               # not ours: no alarm
    assert len(wires.center.feed()["alerts"]) == 1
    pub = c.get("/tripwires").json()
    assert pub["armed"] and pub["hits"][0]["path"] == "/tripwire/<token>/admin"
    assert token not in json.dumps(pub)                                      # the state never shows token values


def test_fake_cloud_key_in_a_header_trips(wires):
    wires.t.plant()
    st = wires.t._load()
    token, p = next((t, p) for t, p in st["planted"].items() if p["kind"] == "cloud_key")
    key_id = "AKIA" + token[:16].upper()
    assert key_id in Path(p["file"]).read_text()
    assert wires.t.record_hit(path="/s3/bucket", ip="203.0.113.5", user_agent="aws-cli/2.15",
                              headers={"Authorization": f"AWS4-HMAC-SHA256 Credential={key_id}/20261016/s3"})
    assert "cloud key" in wires.center.feed()["alerts"][0]["body"]


def test_listener_and_inbox(wires):
    wires.t.plant()
    token = _tokens(wires.t._load())[0]
    port = wires.t.start_listener("127.0.0.1", 0)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/tripwire/{token}/login",
                                     headers={"User-Agent": "python-probe"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 403
    finally:
        wires.t.stop_listener()
    assert wires.center.feed()["alerts"][0]["level"] == "urgent"
    # A listener running without the daemon writes hits to a file the daemon picks up later.
    other = tw.Tripwires(wires.tmp / "data", home=wires.home)
    rec = other.inbox_writer()
    assert rec(path=f"/tripwire/{token}/x", ip="198.51.100.1", user_agent="ua", method="GET", headers={}, body=b"")
    assert not rec(path="/nothing", ip="1.1.1.1", user_agent="", method="GET", headers={}, body=b"")
    assert wires.t.ingest_inbox() == 1 and wires.t.ingest_inbox() == 0


def test_remove_only_untouched_bait(wires):
    wires.t.plant()
    st = wires.t._load()
    files = [Path(p["file"]) for p in st["planted"].values()]
    files[0].write_text("I edited this")
    res = wires.t.remove()
    assert res["removed"] == 3 and len(res["kept"]) == 1
    assert files[0].exists() and not any(f.exists() for f in files[1:])
    assert wires.t._load()["planted"] == {}


def test_config_validation(wires):
    with pytest.raises(tw.TripwireError):
        wires.t.set_config({"callback": "ftp://evil"})
    with pytest.raises(tw.TripwireError):
        wires.t.set_config({"listener": {"port": 80}})
    cfg = wires.t.set_config({"callback": "http://10.0.0.2:9135", "listener": {"enabled": True, "port": 9135}})
    assert cfg["callback"] == "http://10.0.0.2:9135" and cfg["listener"]["enabled"]


def test_planting_goes_through_a_sensitive_approval(wires):
    board = br.Boardroom(wires.tmp / "board")
    tw.register_task_type()
    assert br.TASK_TYPES[tw.TASK].max_level == 1
    started = []
    board.executors[tw.TASK] = tw.make_executor(wires.t, lambda: started.append(1))
    res = run(tw.propose(board, wires.t, "the server"))
    assert res["ok"] and res["status"] == "pending"
    assert not (wires.home / "Documents/passwords-old.txt").exists()     # nothing until you approve
    d = board.get(res["decision"])
    out = board.executors[tw.TASK](d)
    assert len(out["planted"]) == 4 and started == [1]


# =====================================================================================  Promise Keeper
BASE = datetime(2026, 10, 14, 10, 0)     # a Wednesday


@pytest.mark.parametrize("text,action,due", [
    ("I'll send you the build tomorrow", "send you the build tomorrow", datetime(2026, 10, 15, 17, 0)),
    ("I'll fix that bug this weekend.", "fix that bug this weekend", datetime(2026, 10, 18, 18, 0)),
    ("I'm going to upload the logs on Friday at 3pm", "upload the logs on Friday at 3pm", datetime(2026, 10, 16, 15, 0)),
    ("sure, I'll try to call Sam tonight", "call Sam tonight", datetime(2026, 10, 14, 21, 0)),
    ("I'll push the patch next week", "push the patch next week", datetime(2026, 10, 23, 17, 0)),
    ("let me check and get back to you in 2 days", "check and get back to you in 2 days", datetime(2026, 10, 16, 17, 0)),
])
def test_promise_rules_and_dates(text, action, due):
    c = pk.candidates(text)
    assert len(c) == 1 and c[0]["action"] == action
    assert pk.parse_due(c[0]["sentence"], BASE) == due


@pytest.mark.parametrize("text", ["ok I will be late", "Will you send it?", "If it breaks I'll fix it",
                                  "haha I'll never do that", "I think it works", "maybe I'll see"])
def test_not_promises(text):
    assert pk.candidates(text) == []


def test_tidy_and_no_date():
    assert pk.tidy_action("send you the build tomorrow", "Sam") == "Send Sam the build"
    assert pk.parse_due("I'll look at it", BASE) is None


class FakeComms:
    def __init__(self, msgs):
        self.threads = {"discord:1": {"messages": msgs}}
        self.senders = {}

    async def reply(self, *a, **k):
        raise AssertionError("Promise Keeper must never send anything")


def _msg(mid, text, direction="out", ts=None):
    return {"id": mid, "text": text, "direction": direction, "timestamp": ts or BASE.timestamp(),
            "thread_name": "Sam", "thread_key": "discord:1", "platform": "discord",
            "thread_url": "https://discord.com/channels/@me/1"}


def test_scan_only_own_messages_and_nudge_once(tmp_path):
    now = [BASE.timestamp()]
    nudges = []
    comms = FakeComms([_msg("m1", "I'll send you the build tomorrow"),
                       _msg("m2", "I'll send you my notes tomorrow", direction="in"),
                       _msg("m3", "lol ok")])
    keeper = pk.PromiseKeeper(tmp_path, comms=comms, notify=lambda *a, **k: nudges.append(a), clock=lambda: now[0])
    assert run(keeper.scan()) == 1
    assert run(keeper.scan()) == 0                                            # each message only once
    [p] = keeper.promises()
    assert p["task"] == "Send Sam the build" and p["to"] == "Sam" and p["how"] == "rules"
    assert p["link"]["message_id"] == "m1" and p["link"]["url"].startswith("https://discord.com")
    assert keeper.tick() == 0 and nudges == []                                # quiet task: no alert when made
    now[0] = datetime(2026, 10, 15, 14, 30).timestamp()                       # 2.5 h before it's due
    assert keeper.tick() == 1 and keeper.tick() == 0                          # exactly one nudge
    assert "Send Sam the build" in nudges[0][2] or "send Sam the build" in nudges[0][2]
    assert "Promises: Send Sam the build (Sam, today 17:00)." == keeper.brief_section()
    assert keeper.week_words().startswith("This week you promised 1 thing")
    keeper.close(p["id"])
    assert keeper.promises() == [] and keeper.brief_section() == ""


class FakeLLM:
    def __init__(self, verdict):
        self.verdict = verdict
        self.prompts = []

    async def ollama_available(self):
        return True

    async def ollama_json(self, prompt, schema, **kw):
        self.prompts.append(prompt)
        return self.verdict, "fake"


def test_local_model_confirms_or_rejects(tmp_path):
    msg = _msg("m1", "I'll get the raid gear sorted on Saturday")
    no = pk.PromiseKeeper(tmp_path / "a", llm=FakeLLM({"promise": False, "what": "", "when": ""}))
    assert run(no.consider(msg)) == []
    yes = pk.PromiseKeeper(tmp_path / "b", llm=FakeLLM({"promise": True, "what": "sort the raid gear",
                                                         "when": "Saturday"}))
    [p] = run(yes.consider(msg))
    assert p["task"] == "Sort the raid gear" and p["how"] == "model"
    assert datetime.fromtimestamp(p["due"]).weekday() == 5


def test_morning_brief_has_promises(tmp_path):
    from quick_commands import QuickCommands
    q = QuickCommands(config=tmp_path / "quick.json")

    async def part():
        return "Promises: Send Sam the build (Sam, today 17:00)."

    async def broken():
        raise RuntimeError("x")
    q.brief_parts += [part, broken]
    text = run(q.morning_brief())
    assert text.startswith("Good morning.") and text.endswith("Promises: Send Sam the build (Sam, today 17:00).")


def test_promise_routes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    keeper = pk.PromiseKeeper(tmp_path, comms=FakeComms([_msg("m1", "I'll fix it this weekend")]),
                              clock=lambda: BASE.timestamp())   # fixed clock: the message is dated BASE
    app = FastAPI()
    app.include_router(pk.make_router(lambda: keeper))
    c = TestClient(app)
    assert c.post("/promises/scan").json() == {"added": 1}
    pid = c.get("/promises").json()["promises"][0]["id"]
    assert c.post(f"/promises/{pid}/done").json()["promise"]["status"] == "done"
    assert c.post("/promises/nope/done").status_code == 404
    assert c.post(f"/promises/{pid}/explode").status_code == 400


# =====================================================================================  glue
class FakeBoard:
    def __init__(self, tmp):
        self.real = br.Boardroom(Path(tmp) / "board")
        self.store, self.executors, self.remote_hooks = self.real.store, {}, []

    async def propose(self, *a, **k):
        return await self.real.propose(*a, **k)

    def get(self, i):
        return self.real.get(i)


@pytest.fixture()
def kitd(tmp_path, center):
    from quick_commands import QuickCommands
    d = SimpleNamespace(board=FakeBoard(tmp_path), quick=QuickCommands(config=tmp_path / "q.json"),
                        comms=FakeComms([]), orchestrator=None, bus=None, loop=None,
                        health=SimpleNamespace(last={"status": "warn", "words": "Mostly fine.", "at": 5,
                                                     "turbulence": 0.15,
                                                     "checks": [{"label": "Disk space", "status": "warn",
                                                                 "reason": "Low."},
                                                                {"label": "Network", "status": "ok",
                                                                 "reason": "ok"}]}))
    k = trust_kit.TrustKit(d, data_dir=tmp_path / "data", repo_root=tmp_path / "repo", port=9120)
    return SimpleNamespace(d=d, k=k, tmp=tmp_path)


def test_quick_phrases_and_brief_wiring(kitd):
    q = kitd.d.quick
    m = q.match("Orion, set tripwires on the server")
    assert m and m[1] == ["toolkit:tripwires:set:server"]
    out = run(q.run_steps(m[1]))
    assert "Asked your phone to set 4 tripwires on the server" in out
    assert q.match("what did I promise this week")[1] == ["toolkit:promises:week"]
    assert run(q.run_steps(["toolkit:promises:week"])).startswith("You haven't promised")
    assert q.match("share clean")[1] == ["toolkit:share-clean:clipboard"]
    assert q.match("remove the tripwires") and q.match("are my tripwires ok")
    assert kitd.k.brief_promises in q.brief_parts
    assert kitd.d.board.executors[tw.TASK]
    trust_kit.TrustKit(kitd.d, data_dir=kitd.tmp / "data", repo_root=kitd.tmp / "repo", port=9120)
    assert len([p for p in q.plugins if p.name == "tripwires"]) == 1       # installing twice doesn't duplicate


def test_phone_health_route(kitd):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import freeze
    freeze.set_dir(kitd.tmp / "fz")
    try:
        app = FastAPI()
        seen = []

        async def phone_auth(request, need):
            seen.append(need)
            return {"name": "Pixel", "fingerprint": "AB"}, None

        async def json_body(request):
            return {}
        assert kitd.k.phone_hook in kitd.d.board.remote_hooks
        kitd.k.phone_hook(app, phone_auth, json_body)
        h = TestClient(app).get("/phone/health").json()["health"]
        assert seen == ["read"]
        assert h["status"] == "warn" and h["words"] == "Mostly fine." and h["frozen"] is False
        assert h["problems"] == [{"label": "Disk space", "status": "warn", "reason": "Low."}]
    finally:
        freeze.set_dir(None)


def test_health_monitor_feeds_the_core(tmp_path):
    import core_signals as cs
    sysr, proc = tmp_path / "sys", tmp_path / "proc"
    bat = sysr / "class/power_supply/BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("42\n")
    (bat / "status").write_text("Discharging\n")
    proc.mkdir()
    (proc / "loadavg").write_text(f"{(os.cpu_count() or 1) * 0.5:.2f} 0.4 0.3 1/100 999\n")
    marker = tmp_path / "reboot-required"
    marker.write_text("")
    p = hm.Probes(sys_root=sysr, proc_root=proc)
    vals = hm.core_feed(p, (str(marker),))
    assert vals == {"cpu": pytest.approx(0.5, abs=0.01), "battery": 42.0, "plugged": False, "update_ready": True}
    got = []
    mon = hm.HealthMonitor(SimpleNamespace(bus=SimpleNamespace(clients={}), _tasks=[]), probes=p, cfg=SimpleNamespace(),
                           feed=got.append)
    mon.run_checks = lambda: []
    run(mon.check_now())
    assert got and "update_ready" in got[0]
    assert hm.HealthMonitor(None, probes=p).feed is None                     # tests' fake systems don't feed
    saved = dict(cs._external)
    try:
        hm.send_to_core({"cpu": 0.5, "battery": 42.0, "plugged": False, "update_ready": True})
        assert cs.external("battery") == 42.0 and cs.external("update_ready") == 1.0
    finally:
        cs._external.clear()
        cs._external.update(saved)


def test_mcp_tools_read_only():
    class Backend:
        async def call(self, method, path, body=None, timeout=None):
            assert method == "GET"
            if path == "/health/status":
                return 200, {"words": "Mostly fine.", "checks": [
                    {"label": "Disk space", "status": "warn", "reason": "Low.", "fix_words": "Free up space."}]}
            return 200, {"words": "2 snapshots.", "undo_to": "11", "snapshots": [
                {"id": "11", "date": "2026-10-10", "kind_words": "Before an update", "why": "apt"},
                {"id": "12", "date": "2026-10-11", "kind": "manual"}]}
    tools = SimpleNamespace(backend=Backend(), out=lambda t: t)
    by = {t.name: t for t in trust_kit.mcp_tools(tools)}
    assert set(by) == {"health_status", "snapshots_list"} and all(t.risk == "read" for t in by.values())
    h = run(by["health_status"].handler({}))
    assert "Disk space: warn. Low. Fix: Free up space." in h
    s = run(by["snapshots_list"].handler({"limit": 5}))
    assert s.index("- 12") < s.index("- 11") and "Undo point before the last update: 11." in s


def test_tools_are_in_the_catalogue(tmp_path):
    from mcp_tools import McpConfig, OrionTools
    t = OrionTools(cfg=McpConfig(roots=[tmp_path]), backend=SimpleNamespace(), default_project=str(tmp_path))
    assert "health_status" in t.tools and "snapshots_list" in t.tools
