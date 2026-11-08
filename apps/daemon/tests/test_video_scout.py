"""Video Scout: links, metadata, virality, matching, OCR dedupe, captions, media cleanup, rate limit, leaderboard,
the phone share route, and a full pipeline run on a generated fixture video with a fake model."""
import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import boardroom as br
import scout_routes
import video_scout as vs
from scout_catalogue import Catalogue
from tests.test_phones import FakePhone, paired

NOW = 1_760_000_000.0


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------------------------------- fixtures
YT_INFO = {
    "id": "aqz-KE-bpKQ", "extractor_key": "Youtube", "webpage_url": "https://www.youtube.com/shorts/aqz-KE-bpKQ",
    "title": "5 addons you NEED in Midnight #wow #addons", "uploader": "AddonGuy", "uploader_id": "@addonguy",
    "channel_follower_count": 120000, "view_count": 50000, "like_count": 4000, "comment_count": 600,
    "repost_count": None, "timestamp": NOW - 2 * 86400, "upload_date": "20251007", "duration": 6.0,
    "tags": ["World of Warcraft", "WeakAuras"],
    "description": "Links:\nhttps://wago.io/abc\nPlater profile in pinned comment #ui\n",
    "subtitles": {}, "automatic_captions": {},
}
TT_INFO = {
    "id": "7312345678901234567", "extractor_key": "TikTok", "title": "my ui", "uploader": "raidlead",
    "upload_date": "20251008", "view_count": "1200", "like_count": 300, "comment_count": 20, "repost_count": 10,
    "description": "new addon dropped #worldofwarcraft #elvui",
}


@pytest.fixture(scope="session")
def fixture_video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("fx") / "clip.mp4"
    vf = ("drawtext=text='Install WeakAuras now':fontsize=44:fontcolor=black:x=30:y=150:enable='lt(t,3)',"
          "drawtext=text='Plater Nameplates setup':fontsize=44:fontcolor=black:x=30:y=150:enable='gte(t,3)'")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=white:s=640x360:d=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-vf", vf, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                   check=True, timeout=120)
    return out


class FakeFetcher:
    """Behaves like YtDlpFetcher, from fixture files: copies the clip (and captions) into the work folder."""

    def __init__(self, info, media=None, vtt=None, error=None, blocked=False):
        self.info, self.media, self.vtt, self.error, self.blocked = info, media, vtt, error, blocked
        self.calls, self.workdirs = [], []

    def available(self):
        return True

    async def fetch(self, url, workdir, *, want_video, comments=False, want_media=True):
        self.calls.append(url)
        self.workdirs.append(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        if self.error:
            return vs.FetchResult(error=self.error, blocked=self.blocked)
        res = vs.FetchResult(info=json.loads(json.dumps(self.info)))
        if self.media:
            res.media = workdir / ("media" + Path(self.media).suffix)
            shutil.copy(self.media, res.media)
        if self.vtt:
            res.subs = workdir / "media.en.vtt"
            res.subs.write_text(self.vtt)
        return res

    async def metadata(self, url, comments=False):
        info = dict(self.info)
        info["view_count"] = int(info.get("view_count") or 0) * 2
        return vs.FetchResult(info=info)

    async def expand(self, url, limit):
        return [f"https://youtu.be/{'a' * 10}{i}" for i in range(limit)], None


class NoVoice:
    def available(self):
        return False


class FakeVoice:
    def available(self):
        return True

    def transcribe(self, path):
        assert Path(path).exists()
        return [{"t": 0.5, "text": "today we look at weak auras and the Details meter"},
                {"t": 4.0, "text": "also DBM is still great"}]


async def fake_llm(prompt, schema):
    assert "WeakAuras" in prompt and "Install WeakAuras now" in prompt
    return {"bullets": ["Shows WeakAuras.", "Shows Plater.", "Recommends DBM."],
            "addons": [{"name": "WeakAuras", "what": "auras", "link": "https://wago.io/abc"},
                       {"name": "MadeUpAddon", "what": "invented by the model"}],
            "projects": [{"name": "OpenWoW", "what": "emulator"}], "links": ["https://wago.io/abc"],
            "claims": ["DBM is still great"], "verdict": "worth checking", "verdict_reason": "Three solid addons."}


def make_scout(tmp_path, fetcher, **kw):
    clock = [NOW]

    async def fake_sleep(s):   # waiting moves the fake clock on
        clock[0] += s
        await asyncio.sleep(0.01)   # and still lets the other tasks run, so the background re-check loop can't hog the loop
    kw.setdefault("transcriber", NoVoice())
    return vs.VideoScout(tmp_path, fetcher=fetcher, vault_dir=tmp_path / "vault", clock=lambda: clock[0],
                         sleep=fake_sleep, **kw)


# ------------------------------------------------------------------------------------------- pure parts
def test_links_and_keys():
    text = ("look https://www.youtube.com/shorts/aqz-KE-bpKQ, https://youtu.be/aqz-KE-bpKQ "
            "https://www.tiktok.com/@bob.x/video/7312345678901234567?lang=en https://www.instagram.com/reel/C1abcDEF/")
    links = vs.find_links(text)
    assert len(links) == 3   # the youtu.be link is the same video as the Short
    assert [vs.video_key(u) for u in links] == ["yt:aqz-KE-bpKQ", "tt:7312345678901234567", "ig:C1abcDEF"]
    assert vs.find_collections("https://www.youtube.com/@Bellular/videos") and not vs.find_links("https://www.youtube.com/@Bellular")
    assert vs.canonical_key({"extractor_key": "TikTok", "id": "123456"}, "url:x") == "tt:123456"


def test_metadata_parsing():
    m = vs.parse_metadata(YT_INFO)
    assert m["platform"] == "youtube" and m["views"] == 50000 and m["followers"] == 120000 and m["reposts"] is None
    assert m["upload_ts"] == NOW - 2 * 86400 and m["duration"] == 6.0
    assert m["hashtags"][:4] == ["world of warcraft", "weakauras", "ui", "wow"]
    t = vs.parse_metadata(TT_INFO)
    assert t["platform"] == "tiktok" and t["views"] == 1200 and t["followers"] is None
    assert t["upload_ts"] == 1759881600.0   # 2025-10-08 00:00 UTC
    assert "elvui" in t["hashtags"]


def test_virality_math():
    v = vs.virality(50000, 4000, 600, None, NOW - 2 * 86400, NOW, followers=100000, creator_median=10000,
                    history=[(NOW - 86400, 30000), (NOW, 50000)])
    assert v["views_per_day"] == 25000 and v["engagement"] == 0.092
    assert v["vs_followers"] == 0.5 and v["vs_creator"] == 5.0 and v["velocity"] == 20000
    assert v["label"] == "hot"
    import math
    assert v["weight"] == round(math.log10(25001) * (1 + 3 * 0.092) * 1.25, 3)
    fresh = vs.virality(100, upload_ts=NOW - 60, now=NOW)   # a minute old counts as an hour
    assert fresh["views_per_day"] == 2400
    assert vs.virality(None)["weight"] == 0 and vs.virality(0, upload_ts=NOW, now=NOW)["engagement"] is None


def test_fuzzy_matching_with_timestamps():
    terms = Catalogue(Path("/tmp") / f"scout-cat-{time.time_ns()}").terms()
    segs = [{"t": 1.0, "text": "first up, Weak Auras, then my plater nameplate profile", "source": "speech"},
            {"t": 5.0, "text": "WeakAuras again", "source": "speech"},          # within 15 s: folded
            {"t": 40.0, "text": "and WeakAura once more", "source": "speech"},
            {"t": 41.0, "text": "for more details see below, wa is cool", "source": "speech"},
            {"t": 42.0, "text": "I run DBM and Details!", "source": "screen"}]
    m = vs.match_terms(segs, terms)
    got = [(x["term"], x["t"], x["source"]) for x in m if x["kind"] == "addon"]
    assert ("WeakAuras", 1.0, "speech") in got and ("WeakAuras", 40.0, "speech") in got
    assert ("WeakAuras", 5.0, "speech") not in got
    assert ("Plater Nameplates", 1.0, "speech") in got
    assert ("Deadly Boss Mods", 42.0, "screen") in got and ("Details! Damage Meter", 42.0, "screen") in got
    assert not any(t == 41.0 for _, t, _ in got)   # lowercase "details" / "wa" are just words
    fuzzy = vs.match_terms([{"t": 3, "text": "the Questi addon", "source": "x"}],
                           [{"name": "Questie", "aliases": ["Questie"], "kind": "addon"}])
    assert fuzzy and 85 <= fuzzy[0]["score"] < 100


def test_ocr_dedupe_and_captions():
    frames = [(0.75, "Install WeakAuras now\n|||\n"), (2.25, "Install WeakAuras  now"), (3.75, "lnstall WeakAuras now"),
              (5.25, "Plater Nameplates setup\nab"), (6.75, "api_key=sk-abcdefghijklmnop1234")]
    lines = vs.dedupe_lines(frames, drop=lambda s: "api_key" in s)
    assert lines == [{"t": 0.75, "text": "Install WeakAuras now"}, {"t": 5.25, "text": "Plater Nameplates setup"}]
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello <c>there</c>\n\n"
           "00:00:03.000 --> 00:00:05.000\nhello there\nWeakAuras is great\n\n1:02:03.500 --> 1:02:04.000\nbye\n")
    assert vs.parse_vtt(vtt) == [{"t": 1.0, "text": "hello there"}, {"t": 3.0, "text": "WeakAuras is great"},
                                 {"t": 3723.5, "text": "bye"}]


def test_plain_words_when_blocked():
    msg, blocked = vs.explain_error("ERROR: [TikTok] 123: Login required to view this content", "https://www.tiktok.com/@a/video/1")
    assert blocked and msg.startswith("TikTok blocked this download")
    msg, blocked = vs.explain_error("<urlopen error Tunnel connection failed: 403 Forbidden>", "https://youtu.be/x")
    assert not blocked and "Couldn't reach YouTube" in msg


def test_rate_limit():
    t = [0.0]
    waits = []

    async def sleep(s):
        waits.append(s)
        t[0] += s

    lim = vs.RateLimiter(3, clock=lambda: t[0], sleep=sleep)

    async def go():
        for _ in range(4):
            await lim.acquire()
            t[0] += 1
    run(go())
    assert waits == [57.0] and len(lim.starts) <= 3


def test_rank_mentions():
    rows = [{"video_id": "a", "term": "WeakAuras", "count": 9}, {"video_id": "b", "term": "Plater", "count": 1},
            {"video_id": "c", "term": "Plater", "count": 1}, {"video_id": "d", "term": "Plater", "count": 1},
            {"video_id": "e", "term": "ElvUI", "count": 1}]
    ranked = vs.rank_mentions(rows, {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": 4.0},
                              prev_scores={"WeakAuras": 3.0, "ElvUI": 1.0})
    assert [r["name"] for r in ranked] == ["ElvUI", "Plater", "WeakAuras"]   # viral video, then breadth
    trend = {r["name"]: r["trend"] for r in ranked}
    assert trend == {"ElvUI": "rising", "Plater": "new", "WeakAuras": "steady"}
    assert ranked[0]["best_video"] == "e"


# ------------------------------------------------------------------------------------------- the pipeline
def test_full_pipeline_on_fixture(tmp_path, fixture_video):
    if not vs.ocr_available():
        pytest.skip("tesseract not installed")
    fetcher = FakeFetcher(YT_INFO, media=fixture_video)
    events = []

    async def publish(e):
        events.append(e)
    scout = make_scout(tmp_path, fetcher, llm=fake_llm, transcriber=FakeVoice(), publish=publish)
    card = run(scout.process("https://www.youtube.com/shorts/aqz-KE-bpKQ"))
    assert card["status"] == "done" and card["id"] == "yt:aqz-KE-bpKQ"
    assert card["summary"]["verdict"] == "worth checking" and len(card["summary"]["bullets"]) == 3
    assert card["transcript_source"] == "voice"
    assert card["virality"]["views_per_day"] == 25000
    names = {m["name"] for m in card["top_mentions"]}
    assert {"WeakAuras", "Plater Nameplates", "Deadly Boss Mods", "Details! Damage Meter", "OpenWoW"} <= names
    assert "MadeUpAddon" not in names   # the model's guesses only count when the video backs them up
    d = scout.detail(card["id"])
    screen = [m for m in d["mentions"] if m["source"] == "screen"]
    assert any(m["term"] == "WeakAuras" and m["t"] < 3 for m in screen)
    assert any(m["term"] == "Plater Nameplates" and m["t"] >= 3 for m in screen)
    assert len([o for o in d["onscreen"] if "WeakAuras" in o["text"]]) == 1   # repeated frames read once
    assert d["metrics"][0]["views"] == 50000
    # media is gone, only the knowledge stays
    assert fetcher.workdirs and not fetcher.workdirs[0].exists()
    assert list(scout.tmp.iterdir()) == []
    vault = (tmp_path / "vault" / "video-scout" / "yt_aqz-KE-bpKQ.md").read_text()
    assert "today we look at weak auras" in vault and "Install WeakAuras now" in vault
    assert events and events[-1]["event"] == "SCOUT_VIDEO"
    # cached: a second request doesn't fetch again
    run(scout.process("https://youtu.be/aqz-KE-bpKQ"))
    assert len(fetcher.calls) == 1


def test_captions_shortcut_and_no_model(tmp_path):
    vtt = "WEBVTT\n\n00:00:02.000 --> 00:00:04.000\nI swapped to ElvUI and Plater\n"
    fetcher = FakeFetcher(TT_INFO, vtt=vtt)
    scout = make_scout(tmp_path, fetcher, llm=None)
    card = run(scout.process("https://www.tiktok.com/@raidlead/video/7312345678901234567"))
    assert card["status"] == "done" and card["transcript_source"] == "captions"
    assert {"ElvUI", "Plater Nameplates"} <= {m["name"] for m in card["top_mentions"]}
    assert card["summary"]["verdict_reason"].startswith("No AI model")
    assert card["virality"]["engagement"] == round(330 / 1200, 4)


def test_voice_addon_needed_and_media_deleted(tmp_path, fixture_video):
    fetcher = FakeFetcher(TT_INFO, media=fixture_video)
    scout = make_scout(tmp_path, fetcher, ocr=lambda p: "", ocr_ready=lambda: False)
    card = run(scout.process("https://www.tiktok.com/@raidlead/video/7312345678901234567"))
    assert any("Voice add-on needed" in n for n in card["notes"])
    assert card["transcript_source"] == "description"
    assert not fetcher.workdirs[0].exists()


def test_blocked_platform_card_says_so(tmp_path):
    fetcher = FakeFetcher(TT_INFO, error="TikTok blocked this download (it wants a login).", blocked=True)
    scout = make_scout(tmp_path, fetcher)
    card = run(scout.process("https://www.tiktok.com/@raidlead/video/7312345678901234567"))
    assert card["status"] == "blocked" and "TikTok blocked" in card["error"]
    assert "couldn't read" in vs.reply_text(card)
    assert not fetcher.workdirs[0].exists()


def test_leaderboard_exports_and_ideas(tmp_path):
    vtt = "WEBVTT\n\n00:00:02.000 --> 00:00:04.000\nWeakAuras and ElvUI\n"
    scout = make_scout(tmp_path, FakeFetcher(TT_INFO, vtt=vtt))
    run(scout.process("https://www.tiktok.com/@raidlead/video/7312345678901234567"))
    old = dict(YT_INFO, id="old00000000", timestamp=NOW - 30 * 86400)
    scout.fetcher = FakeFetcher(old, vtt="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nMasque\n")
    run(scout.process("https://youtu.be/old00000000"))
    lb = scout.leaderboard(7)
    assert lb["videos"] == 1
    assert [a["name"] for a in lb["addons"]][:2] == ["ElvUI", "WeakAuras"] or \
        {a["name"] for a in lb["addons"]} >= {"ElvUI", "WeakAuras"}
    assert "Masque" not in {a["name"] for a in lb["addons"]}   # uploaded a month ago
    assert all(a["trend"] == "new" for a in lb["addons"])
    assert "Masque" in {a["name"] for a in scout.leaderboard(0)["addons"]}
    md = scout.export_markdown()
    assert "| 1 |" in md and "Top WoW addons" in md
    assert scout.export_csv().splitlines()[0].startswith("kind,rank,name")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "IDEAS.md").write_text("# Ideas\n")
    scout.send_to_ideas(proj)
    assert (proj / "IDEAS.md").read_text().startswith("# Ideas\n\n## Top WoW addons")
    assert "WeakAuras" in scout.leaderboard_text()


def test_velocity_rechecks(tmp_path):
    clock = [NOW]

    async def fake_sleep(s):
        clock[0] += s
    scout = vs.VideoScout(tmp_path, fetcher=FakeFetcher(YT_INFO), transcriber=NoVoice(), clock=lambda: clock[0],
                          sleep=fake_sleep)
    run(scout.process("https://youtu.be/aqz-KE-bpKQ"))
    assert run(scout.recheck_due()) == 0
    clock[0] += 86400 + 5
    assert run(scout.recheck_due()) == 1
    d = scout.detail("yt:aqz-KE-bpKQ")
    assert [m["views"] for m in d["metrics"]] == [50000, 100000]
    assert d["virality"]["velocity"] == pytest.approx(50000 / ((86400 + 5) / 86400), rel=1e-3)
    clock[0] += 2 * 86400
    assert run(scout.recheck_due()) == 1 and run(scout.recheck_due()) == 0


def test_chat_commands_and_batches(tmp_path):
    scout = make_scout(tmp_path, FakeFetcher(YT_INFO))

    async def go():
        a = await scout.chat("scout https://www.youtube.com/shorts/aqz-KE-bpKQ", "user")
        b = await scout.chat("open https://www.youtube.com/shorts/aqz-KE-bpKQ", "user")
        c = await scout.chat("what's the weather", "user")
        d = await scout.chat("scout search 3 best wow addons", "user")
        e = await scout.chat("scout top", "user")
        await scout.queue.join()
        await scout.queue.join()
        f = await scout.chat("https://youtu.be/aqz-KE-bpKQ", "user")
        await scout.stop()
        return a, b, c, d, e, f
    a, b, c, d, e, f = run(go())
    assert a.startswith("Scouting 1 video") and b is None and c is None
    assert "up to 3 videos" in d and e.startswith("No addon mentions")
    assert f.startswith("Already scouted")
    links = tmp_path / "links.txt"
    links.write_text("https://youtu.be/aqz-KE-bpKQ\nnot a link\nhttps://www.instagram.com/reel/C1abcDEF/\n")
    assert len(vs.read_link_file(links)) == 2


# ------------------------------------------------------------------------------------------- HTTP + phone
@pytest.fixture()
def hub(tmp_path):
    scout = make_scout(tmp_path / "s", FakeFetcher(YT_INFO))
    h = scout_routes.ScoutHub(scout)
    scout_routes.install(h)
    return h


def test_http_routes(hub):
    app = FastAPI()
    app.include_router(scout_routes.router)
    with TestClient(app) as c:
        assert c.post("/scout", json={"url": "hello"}).status_code == 400
        run(hub.scout.process("https://youtu.be/aqz-KE-bpKQ"))
        r = c.post("/scout", json={"urls": ["https://youtu.be/aqz-KE-bpKQ"]})
        assert r.status_code == 200 and r.json()["cached"][0]["id"] == "yt:aqz-KE-bpKQ"
        assert c.get("/scout/videos").json()["videos"][0]["status"] == "done"
        assert c.get("/scout/video/yt:aqz-KE-bpKQ").json()["metrics"]
        assert c.get("/scout/video/nope").status_code == 404
        assert "text/csv" in c.get("/scout/leaderboard?format=csv").headers["content-type"]
        assert c.get("/scout/leaderboard").json()["videos"] == 1
        assert c.post("/scout/settings", json={"use_cookies": "yes"}).status_code == 400
        assert c.post("/scout/settings", json={"per_minute": 50}).json()["settings"]["per_minute"] == 10
        assert c.get("/scout/settings").json()["settings"]["use_cookies"] is False


def test_phone_share_route_needs_a_paired_phone(tmp_path, hub):
    board = br.Boardroom(tmp_path / "board")
    board.remote_hooks.append(hub.phone_hook)
    phone = FakePhone()
    with TestClient(br.build_remote_app(board)) as client:
        paired(board, client, phone)
        body = json.dumps({"text": "check this https://youtu.be/aqz-KE-bpKQ"}).encode()
        assert client.post("/phone/scout", content=body, headers={"Content-Type": "application/json"}).status_code == 401
        stranger = FakePhone(phone.device_id)
        bad = client.post("/phone/scout", content=body, headers=stranger.headers(stranger.read, "POST", "/phone/scout", body))
        assert bad.status_code == 401
        r = client.post("/phone/scout", content=body, headers=phone.headers(phone.read, "POST", "/phone/scout", body))
        assert r.status_code == 200, r.text
        assert r.json()["queued"] == 1 and "alert" in r.json()["reply"]
        none = json.dumps({"text": "no links here"}).encode()
        assert client.post("/phone/scout", content=none,
                           headers=phone.headers(phone.read, "POST", "/phone/scout", none)).status_code == 400


def test_github_refresh_is_optional_and_polite(tmp_path, monkeypatch):
    cat = Catalogue(tmp_path, clock=lambda: NOW)
    assert run(cat.refresh_from_github(token=""))["ok"] is False

    class R:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            return self._d

    class Http:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None, headers=None):
            self.calls += 1
            assert headers["Authorization"] == "Bearer t0k" and "extension:toc" in params["q"]
            if self.calls == 1:
                return R(200, {"items": [{"name": "ShinyNewAddon_Mainline.toc",
                                          "repository": {"name": "shiny", "html_url": "https://github.com/x/shiny"}},
                                         {"name": "WeakAuras.toc", "repository": {"name": "WeakAuras2"}}]})
            return R(403, {})

    import scout_catalogue

    async def fast(s):
        return None
    monkeypatch.setattr(scout_catalogue.asyncio, "sleep", fast)
    r = run(cat.refresh_from_github(Http(), token="t0k", pages=3))
    assert r["added"] == ["ShinyNewAddon"]
    assert [a["name"] for a in cat.new_since(NOW - 1)] == ["ShinyNewAddon"]
    assert not cat.refresh_due()


def test_hub_chat_hooks(tmp_path):
    class Bus:
        def __init__(self):
            self.events = []

        async def publish(self, e, **k):
            self.events.append(e)

    class D:
        bus = Bus()
        telegram = None

    scout = make_scout(tmp_path, FakeFetcher(YT_INFO))
    h = scout_routes.ScoutHub(scout, D())

    async def go():
        assert await h.chat("https://youtu.be/aqz-KE-bpKQ", "voice") is None   # speech never carries links
        assert await h.chat("https://youtu.be/aqz-KE-bpKQ", "discord") is None
        assert await h.handle_prompt("what time is it", "user") is False
        assert await h.handle_prompt("scout https://youtu.be/aqz-KE-bpKQ", "user") is True
        await scout.queue.join()
        await scout.stop()
    run(go())
    said = [e["response"]["summary"] for e in D.bus.events if e.get("event") == "ORCHESTRATOR_RESPONSE"]
    assert said[0].startswith("Scouting 1 video") and said[-1].startswith("Scouted: 5 addons")


def test_main_wires_video_scout():
    import main
    assert isinstance(main.daemon.scout, scout_routes.ScoutHub)
    assert main.app.url_path_for("scout_leaderboard") == "/scout/leaderboard"
