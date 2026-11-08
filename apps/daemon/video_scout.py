"""Video Scout (after-1.0 plan §10): send Orion a TikTok, YouTube (incl. Shorts) or Instagram Reels link and it
watches the video for you: transcript, on-screen text, keyword and addon mentions with timestamps, a short summary
and how fast the video is spreading. First use: scanning WoW videos for the best addons and newest projects.

Pipeline (all local, open source):
  fetch       yt-dlp: metadata JSON (views, likes, comments, reposts, upload date, followers, hashtags, description,
              duration), captions when the platform has them, and a small low-resolution copy of the video
  transcribe  captions first (fast); else faster-whisper if the voice add-on is installed; else the description
  on-screen   ffmpeg takes a frame every 1–2 s, Tesseract reads it, repeated lines are dropped
  comments    optional, where yt-dlp supports them
  match       RapidFuzz (or difflib) against the keyword list + addon catalogue, with timestamps
  summary     local Ollama by default (or the normal model router): 3–5 bullets + addons, projects, links, claims and a
              "worth checking" verdict. Prompt template: data_dir/scout/summary_prompt.txt (edit it freely)
  store       SQLite (videos, video_metrics, mentions); the transcript goes into the knowledge vault for search
  virality    views/day, engagement rate, views vs followers / the creator's usual, re-checked at 24 h and 72 h

Rules: read-only (it never posts, likes or follows); the downloaded media is deleted as soon as a video is
processed (only transcript, metadata and summary stay); a few videos a minute at most; results are cached; your
browser cookies are only used if you turn that on and point it at your own browser; when a platform blocks a
download the card says so plainly.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib.util
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

from scout_catalogue import Catalogue

log = logging.getLogger("orion.scout")

DAY = 86400.0
MAX_BATCH = 25
MAX_TRANSCRIPT_CHARS = 60_000

# --------------------------------------------------------------------------------------------- links
_YT = re.compile(r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?(?:[^\s#]*&)?v=|shorts/|live/|embed/)|"
                 r"youtu\.be/)([\w-]{11})[^\s<>\"')\]]*", re.I)
_TT = re.compile(r"https?://(?:www\.|m\.)?tiktok\.com/@[\w.-]+/video/(\d+)[^\s<>\"')\]]*", re.I)
_TT_SHORT = re.compile(r"https?://(?:vm|vt)\.tiktok\.com/[\w-]+/?|https?://(?:www\.)?tiktok\.com/t/[\w-]+/?", re.I)
_IG = re.compile(r"https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(?:reels?|p|tv)/([\w-]+)/?[^\s<>\"')\]]*", re.I)
_COLLECTION = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:playlist\?list=[\w-]+|@[\w.-]+(?:/(?:videos|shorts|streams))?|"
    r"channel/[\w-]+(?:/(?:videos|shorts))?|c/[\w.-]+)|tiktok\.com/@[\w.-]+/?(?=\s|$))[^\s<>\"')\]]*", re.I)
_YTSEARCH = re.compile(r"^ytsearch(\d{0,2}):(.+)$", re.I)


def find_links(text: str) -> list[str]:
    """Single-video links in a message (YouTube, Shorts, TikTok, Instagram Reels), in order, no repeats."""
    found: list[tuple[int, str]] = []
    for rx in (_YT, _TT, _TT_SHORT, _IG):
        for m in rx.finditer(text or ""):
            found.append((m.start(), m.group(0).rstrip(".,;:!?")))
    out: list[str] = []
    for _, url in sorted(found):
        if url not in out and video_key(url) not in [video_key(u) for u in out]:
            out.append(url)
    return out


def find_collections(text: str) -> list[str]:
    """Playlist / channel / profile links (expanded to a bounded number of videos)."""
    return [m.group(0).rstrip(".,;:!?") for m in _COLLECTION.finditer(text or "")]


def platform_of(url: str) -> str:
    u = url.lower()
    if "youtu" in u or u.startswith("ytsearch"):
        return "youtube"
    if "tiktok" in u:
        return "tiktok"
    if "instagram" in u:
        return "instagram"
    return "other"


def video_key(url: str) -> str:
    """Stable id for caching: yt:<id>, tt:<id>, ig:<code>, or url:<hash> for short links until fetched."""
    for rx, pre in ((_YT, "yt"), (_TT, "tt"), (_IG, "ig")):
        m = rx.match(url.strip())
        if m:
            return f"{pre}:{m.group(1)}"
    return "url:" + hashlib.sha1(url.strip().encode()).hexdigest()[:12]


def canonical_key(info: dict[str, Any], fallback: str) -> str:
    pre = {"youtube": "yt", "tiktok": "tt", "instagram": "ig"}.get(platform_of(str(info.get("extractor_key") or
                                                                                info.get("extractor") or "")))
    vid = str(info.get("id") or "").strip()
    return f"{pre}:{vid}" if pre and vid and re.fullmatch(r"[\w-]{3,40}", vid) else fallback


# --------------------------------------------------------------------------------------------- metadata
def _num(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_upload_ts(info: dict[str, Any]) -> Optional[float]:
    ts = info.get("timestamp") or info.get("release_timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return float(ts)
    d = str(info.get("upload_date") or info.get("release_date") or "")
    if re.fullmatch(r"\d{8}", d):
        import calendar
        return float(calendar.timegm((int(d[:4]), int(d[4:6]), int(d[6:8]), 0, 0, 0, 0, 0, 0)))
    return None


def parse_metadata(info: dict[str, Any], url: str = "") -> dict[str, Any]:
    """The fields Video Scout keeps from a yt-dlp info JSON (anything missing is None)."""
    desc = str(info.get("description") or "")
    tags = [str(t).lstrip("#").lower() for t in (info.get("tags") or []) if str(t).strip()]
    tags += [t.lower() for t in re.findall(r"#(\w{2,40})", desc + " " + str(info.get("title") or ""))]
    hashtags: list[str] = []
    for t in tags:
        if t not in hashtags:
            hashtags.append(t)
    return {
        "platform": platform_of(str(info.get("extractor_key") or info.get("extractor") or url)),
        "url": str(info.get("webpage_url") or info.get("original_url") or url),
        "title": str(info.get("title") or info.get("fulltitle") or "")[:300],
        "uploader": str(info.get("uploader") or info.get("channel") or info.get("creator") or "")[:120],
        "uploader_id": str(info.get("uploader_id") or info.get("channel_id") or "")[:120],
        "followers": _num(info.get("channel_follower_count") or info.get("uploader_follower_count")),
        "views": _num(info.get("view_count")),
        "likes": _num(info.get("like_count")),
        "comments": _num(info.get("comment_count")),
        "reposts": _num(info.get("repost_count")),
        "upload_ts": parse_upload_ts(info),
        "duration": float(info["duration"]) if isinstance(info.get("duration"), (int, float)) else None,
        "hashtags": hashtags[:40],
        "description": desc[:5000],
        "thumbnail": str(info.get("thumbnail") or ""),
        "has_captions": bool(info.get("subtitles") or info.get("automatic_captions")),
    }


# --------------------------------------------------------------------------------------------- virality
def virality(views: Optional[int], likes: Optional[int] = None, comments: Optional[int] = None,
             reposts: Optional[int] = None, upload_ts: Optional[float] = None, now: Optional[float] = None, *,
             followers: Optional[int] = None, creator_median: Optional[float] = None,
             history: Iterable[tuple[float, int]] = ()) -> dict[str, Any]:
    """How fast a video is spreading.

    views_per_day   views / days since upload (at least one hour counts)
    engagement      (likes + comments + reposts) / views
    vs_followers    views / the uploader's followers, when the platform says
    vs_creator      views / the median views of this creator's other scouted videos
    velocity        views gained per day between the two latest re-checks
    weight          log10(1 + views/day) × (1 + 3 × min(engagement, 0.25)), ×1.25 when well above the creator's
                    usual — what a mention from this video is worth on the leaderboard
    """
    now = time.time() if now is None else now
    out: dict[str, Any] = {"age_days": None, "views_per_day": None, "engagement": None, "vs_followers": None,
                           "vs_creator": None, "velocity": None, "weight": 0.0, "label": "unknown"}
    if views is None:
        return out
    if upload_ts:
        age = max((now - upload_ts) / DAY, 1 / 24)
        out["age_days"] = round(age, 2)
        out["views_per_day"] = round(views / age, 1)
    if views > 0:
        out["engagement"] = round(((likes or 0) + (comments or 0) + (reposts or 0)) / views, 4)
    if followers:
        out["vs_followers"] = round(views / followers, 3)
    if creator_median:
        out["vs_creator"] = round(views / creator_median, 2)
    pts = sorted((float(t), int(v)) for t, v in history if v is not None)
    if len(pts) >= 2:
        (t0, v0), (t1, v1) = pts[-2], pts[-1]
        if t1 - t0 >= 3600:
            out["velocity"] = round((v1 - v0) / ((t1 - t0) / DAY), 1)
    vpd = out["views_per_day"] if out["views_per_day"] is not None else float(views)
    eng = out["engagement"] or 0.0
    w = math.log10(1 + max(vpd, 0)) * (1 + 3 * min(eng, 0.25))
    if out["vs_creator"] and out["vs_creator"] >= 2:
        w *= 1.25
    out["weight"] = round(w, 3)
    if (out["vs_creator"] or 0) >= 3 or vpd >= 50_000:
        out["label"] = "hot"
    elif out["velocity"] is not None and out["views_per_day"] and out["velocity"] > 1.2 * out["views_per_day"]:
        out["label"] = "rising"
    elif vpd < 100:
        out["label"] = "quiet"
    else:
        out["label"] = "steady"
    return out


# --------------------------------------------------------------------------------------------- fuzzy text
try:
    from rapidfuzz import fuzz as _rf_fuzz

    def ratio(a: str, b: str) -> float:
        return float(_rf_fuzz.ratio(a, b))

    FUZZY_ENGINE = "rapidfuzz"
except ImportError:  # pragma: no cover - exercised when rapidfuzz isn't installed
    import difflib

    def ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a, b).ratio() * 100

    FUZZY_ENGINE = "difflib"

# Single-word names that are also everyday English: only matched written exactly as the name.
AMBIGUOUS = {"details", "recount", "pawn", "cell", "rarity", "dominos", "narcissus", "masque", "beta", "patch"}
_WORD = re.compile(r"[\w][\w.!+'’-]*")


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.+!'-]", " ", s.lower())).strip()


def _alias_hit(alias: str, text: str, words: list[str], threshold: float) -> float:
    """0 when not found, else 0..100 how well. Short or everyday names must match exactly."""
    a = alias.strip()
    if not a:
        return 0.0
    case_sensitive = (len(a) <= 4 and any(c.isupper() for c in a)) or a.lower() in AMBIGUOUS
    flags = 0 if case_sensitive else re.I
    if re.search(r"(?<![\w])" + re.escape(a) + r"(?![\w])", text, flags):
        return 100.0
    if case_sensitive or len(a) < 6:
        return 0.0
    n = len(a.split())
    target = a.lower()
    best = 0.0
    for size in {n, n + 1} if n > 1 else {1, 2}:
        for i in range(0, max(0, len(words) - size + 1)):
            chunk = " ".join(words[i:i + size]).lower()
            for cand in (chunk, chunk.replace(" ", "")):
                if abs(len(cand) - len(target)) > max(3, len(target) // 3):
                    continue
                r = ratio(cand, target)
                if r > best:
                    best = r
    return best if best >= threshold else 0.0


def match_terms(segments: list[dict[str, Any]], terms: list[dict[str, Any]], *, threshold: float = 88.0,
                window_s: float = 15.0) -> list[dict[str, Any]]:
    """Finds terms in timed text. segments: [{"t": seconds or None, "text": str, "source": str}].
    Returns mentions [{"term", "kind", "t", "source", "snippet", "score"}]; one per term per segment, and a repeat
    of the same term from the same source within window_s seconds is folded into the first."""
    out: list[dict[str, Any]] = []
    last: dict[tuple[str, str], float] = {}
    for seg in segments:
        text = str(seg.get("text") or "")
        if not text.strip():
            continue
        words = _WORD.findall(text)
        for term in terms:
            best = 0.0
            for alias in term.get("aliases") or [term["name"]]:
                best = max(best, _alias_hit(str(alias), text, words, threshold))
                if best >= 100:
                    break
            if not best:
                continue
            t = seg.get("t")
            k = (term["name"], str(seg.get("source", "")))
            if t is not None and k in last and 0 <= float(t) - last[k] < window_s:
                continue
            if t is not None:
                last[k] = float(t)
            out.append({"term": term["name"], "kind": term.get("kind", "keyword"), "t": t,
                        "source": seg.get("source", ""), "snippet": text.strip()[:200], "score": round(best, 1)})
    return out


# --------------------------------------------------------------------------------------------- captions
_VTT_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->")


def parse_vtt(text: str) -> list[dict[str, Any]]:
    """WebVTT/SRT to [{"t", "text"}]. Auto-captions repeat the previous line in each cue; only new lines are kept."""
    out: list[dict[str, Any]] = []
    recent: list[str] = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().split("\n")
        t = None
        body: list[str] = []
        for ln in lines:
            m = _VTT_TIME.search(ln)
            if m:
                h, mnt, s, ms = m.groups()
                t = int(h or 0) * 3600 + int(mnt) * 60 + int(s) + int(ms) / 1000
            elif t is not None:
                clean = re.sub(r"<[^>]+>", "", ln).strip()
                clean = re.sub(r"&nbsp;", " ", clean)
                if clean:
                    body.append(clean)
        if t is None:
            continue
        for ln in body:
            if ln in recent:
                continue
            recent = (recent + [ln])[-4:]
            out.append({"t": round(t, 2), "text": ln})
    return out


# --------------------------------------------------------------------------------------------- on-screen text
def _junk(line: str) -> bool:
    s = line.strip()
    if len(s) < 3:
        return True
    alnum = sum(c.isalnum() for c in s)
    return alnum / len(s) < 0.6 or not re.search(r"[A-Za-z]{2}", s)


def dedupe_lines(frames: list[tuple[float, str]], *, similar: float = 90.0,
                 drop: Optional[Callable[[str], bool]] = None) -> list[dict[str, Any]]:
    """OCR text per frame -> unique lines with the time they first appeared. Lines that are almost the same as one
    already seen (Tesseract reads the same caption a little differently frame to frame) count as repeats."""
    seen: list[str] = []
    out: list[dict[str, Any]] = []
    for t, text in frames:
        for raw in (text or "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if _junk(line) or (drop is not None and drop(line)):
                continue
            norm = _norm_text(line)
            if any(norm == s or ratio(norm, s) >= similar for s in seen[-300:]):
                continue
            seen.append(norm)
            out.append({"t": round(float(t), 2), "text": line})
    return out


def extract_frames(video: Path, outdir: Path, every_s: float = 1.5, max_frames: int = 240) -> list[tuple[float, Path]]:
    """One frame every `every_s` seconds (ffmpeg), scaled down so Tesseract stays quick."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    every_s = min(max(every_s, 0.5), 5.0)
    import subprocess
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-an",
                    "-vf", f"fps=1/{every_s},scale='min(1280,iw)':-2", "-frames:v", str(max_frames),
                    str(outdir / "f_%05d.png")], check=False, timeout=600, capture_output=True)
    frames = sorted(outdir.glob("f_*.png"))
    # the fps filter's first frame is centred on the first interval
    return [(round(i * every_s + every_s / 2, 2), p) for i, p in enumerate(frames)]


def tesseract_ocr(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image, ImageOps
        with Image.open(path) as img:
            g = ImageOps.grayscale(img)
            return pytesseract.image_to_string(g, timeout=20)
    except Exception as exc:
        log.debug("OCR failed on %s: %s", path.name, exc)
        return ""


def ocr_available() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def has_video_stream(path: Path) -> bool:
    probe = shutil.which("ffprobe")
    if not probe:
        return path.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")
    import subprocess
    r = subprocess.run([probe, "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    return "video" in r.stdout


# --------------------------------------------------------------------------------------------- fetching
@dataclass
class FetchResult:
    info: dict[str, Any] = field(default_factory=dict)
    media: Optional[Path] = None
    subs: Optional[Path] = None
    error: Optional[str] = None
    blocked: bool = False


def explain_error(stderr: str, url: str) -> tuple[str, bool]:
    """yt-dlp's error text -> (a plain sentence, whether the platform blocked us)."""
    name = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}.get(platform_of(url), "The site")
    e = stderr.lower()
    if any(s in e for s in ("tunnel connection failed", "name or service not known", "timed out", "network is unreachable",
                            "temporary failure in name resolution", "connection refused", "urlopen error")):
        return f"Couldn't reach {name} from this computer (no internet, or a firewall/proxy is in the way).", False
    if any(s in e for s in ("login required", "sign in", "log in", "cookies", "rate-limit", "rate limit",
                            "confirm you", "not a bot", "requested content is not available", "http error 403",
                            "http error 429", "blocked")):
        return (f"{name} blocked this download (it wants a login or thinks Orion is a bot). You can let Orion use "
                f"your own browser's cookies in Video Scout settings, or try again later.", True)
    if "private" in e:
        return f"This {name} video is private.", True
    if "not available in your country" in e or "geo" in e:
        return f"{name} doesn't allow this video in your country.", True
    if "unsupported url" in e:
        return "Video Scout can't read that link (TikTok, YouTube, Shorts and Instagram Reels work).", False
    if "video unavailable" in e or "removed" in e or "does not exist" in e:
        return f"That {name} video isn't available any more.", False
    last = [ln for ln in stderr.strip().splitlines() if ln.strip()][-1:] or ["unknown error"]
    return f"Couldn't fetch the video: {last[0][:200]}", False


class YtDlpFetcher:
    """Runs yt-dlp as a separate process (read-only: it only downloads)."""

    def __init__(self, settings: Callable[[], dict[str, Any]]) -> None:
        self.settings = settings

    @staticmethod
    def command() -> Optional[list[str]]:
        exe = shutil.which("yt-dlp")
        if exe:
            return [exe]
        if importlib.util.find_spec("yt_dlp") is not None:
            return [sys.executable, "-m", "yt_dlp"]
        return None

    def available(self) -> bool:
        return self.command() is not None

    def _common(self) -> list[str]:
        s = self.settings()
        args = ["--no-progress", "--no-color", "--socket-timeout", "30", "--retries", "2", "--ignore-config"]
        cookies = str(s.get("cookies_file") or "").strip()
        browser = str(s.get("cookies_from_browser") or "").strip()
        if s.get("use_cookies"):
            if cookies and Path(os.path.expanduser(cookies)).is_file():
                args += ["--cookies", os.path.expanduser(cookies)]
            elif browser and re.fullmatch(r"[a-z]+(:[\w .-]+)?", browser):
                args += ["--cookies-from-browser", browser]
        return args

    async def _run(self, args: list[str], timeout: float) -> tuple[int, str, str]:
        cmd = self.command()
        if cmd is None:
            return 127, "", "yt-dlp is not installed"
        proc = await asyncio.create_subprocess_exec(*cmd, *self._common(), *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", "timed out"
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def metadata(self, url: str, *, comments: bool = False) -> FetchResult:
        args = ["-J", "--skip-download", "--no-playlist"]
        if comments:
            n = int(self.settings().get("max_comments", 40))
            args += ["--write-comments", "--extractor-args", f"youtube:max_comments={n},all,{n},0;comment_sort=top"]
        rc, out, err = await self._run(args + ["--", url], 180)
        if rc != 0 or not out.strip():
            msg, blocked = explain_error(err or out, url)
            return FetchResult(error=msg, blocked=blocked)
        try:
            return FetchResult(info=json.loads(out))
        except ValueError:
            return FetchResult(error="yt-dlp returned something Video Scout couldn't read.")

    async def fetch(self, url: str, workdir: Path, *, want_video: bool, comments: bool = False,
                    want_media: bool = True) -> FetchResult:
        if not self.available():
            return FetchResult(error="yt-dlp isn't installed on this computer. Install the Video Scout add-on "
                                     "(pip install yt-dlp) and try again.")
        workdir.mkdir(parents=True, exist_ok=True)
        meta = await self.metadata(url, comments=comments)
        if meta.error:
            return meta
        info = meta.info
        max_min = float(self.settings().get("max_duration_min", 60))
        info_path = workdir / "info.json"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        too_long = isinstance(info.get("duration"), (int, float)) and info["duration"] > max_min * 60
        has_ffmpeg = bool(shutil.which("ffmpeg"))
        if want_video:
            fmt = ("bv*[height<=480]+ba/b[height<=480]/wv*+ba/w" if has_ffmpeg else "b[height<=480]/w")
        else:
            fmt = "ba/b[height<=360]/w"
        args = ["--load-info-json", str(info_path), "-o", str(workdir / "media.%(ext)s"),
                "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en,-live_chat", "--sub-format", "vtt/best",
                "--max-filesize", "400M", "--no-playlist"]
        if too_long or not want_media:
            args.append("--skip-download")
        else:
            args += ["-f", fmt]
        rc, out, err = await self._run(args, 900)
        subs = sorted(workdir.glob("media*.vtt")) or sorted(workdir.glob("media*.srt"))
        media = [p for p in workdir.glob("media.*") if p.suffix.lower() not in (".vtt", ".srt", ".json", ".part", ".ytdl")]
        res = FetchResult(info=info, media=media[0] if media else None, subs=subs[0] if subs else None)
        if rc != 0 and not res.media and not res.subs and not too_long and want_media:
            msg, blocked = explain_error(err or out, url)
            res.error, res.blocked = (None, False)
            res.info["_scout_note"] = f"Only the details could be read: {msg}"
        if too_long:
            res.info["_scout_note"] = f"Longer than {int(max_min)} minutes, so Orion read the captions and details only."
        return res

    async def expand(self, url: str, limit: int) -> tuple[list[str], Optional[str]]:
        """A playlist, channel or ytsearchN:query -> up to `limit` video links."""
        rc, out, err = await self._run(["-J", "--flat-playlist", "--playlist-end", str(limit), "--", url], 180)
        if rc != 0 or not out.strip():
            return [], explain_error(err or out, url)[0]
        try:
            data = json.loads(out)
        except ValueError:
            return [], "yt-dlp returned something Video Scout couldn't read."
        urls: list[str] = []
        for e in data.get("entries") or []:
            if not isinstance(e, dict):
                continue
            u = e.get("url") or e.get("webpage_url") or ""
            if u and not u.startswith("http") and e.get("ie_key", "").lower().startswith("youtube"):
                u = f"https://www.youtube.com/watch?v={u}"
            if u.startswith("http"):
                urls.append(u)
            if len(urls) >= limit:
                break
        return urls, None


# --------------------------------------------------------------------------------------------- transcription
class WhisperTranscriber:
    """faster-whisper, the same optional voice add-on the daemon already uses."""

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def transcribe(self, path: Path) -> list[dict[str, Any]]:
        from faster_whisper import WhisperModel
        with self._lock:
            if self._model is None:
                self._model = WhisperModel(os.getenv("ORION_WHISPER_MODEL", "base.en"), device="cpu", compute_type="int8")
            segs, _ = self._model.transcribe(str(path), beam_size=1, vad_filter=True)
            return [{"t": round(s.start, 2), "text": s.text.strip()} for s in segs if s.text.strip()]


# --------------------------------------------------------------------------------------------- summary
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bullets": {"type": "array", "items": {"type": "string"}},
        "addons": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "what": {"type": "string"}, "link": {"type": "string"}}}},
        "projects": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "what": {"type": "string"}, "link": {"type": "string"}}}},
        "links": {"type": "array", "items": {"type": "string"}},
        "claims": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["worth checking", "maybe", "skip"]},
        "verdict_reason": {"type": "string"},
    },
    "required": ["bullets", "verdict"],
}

DEFAULT_PROMPT = """You are Orion's Video Scout. Someone sent you a short video to watch for them. Their main interest:
World of Warcraft addons, UI setups, and new WoW projects (addons, tools, private-server/emulator projects).

Video: {title}
By: {uploader} on {platform} · {stats}
Hashtags: {hashtags}

Description:
{description}

Addons and keywords spotted (with seconds into the video):
{mentions}

Text shown on screen:
{onscreen}

Transcript ({transcript_source}):
{transcript}

Reply with JSON only:
- "bullets": 3 to 5 short plain sentences saying what the video shows or claims
- "addons": addons named or shown, each {{"name", "what" (one line), "link" (if given, else "")}}
- "projects": other projects/tools named, same shape
- "links": any URLs or site names the video points to (wago, curseforge, github ...)
- "claims": concrete claims worth verifying (e.g. "X replaces Y", "works in Classic")
- "verdict": "worth checking", "maybe" or "skip" for someone hunting good addons and new projects
- "verdict_reason": one short sentence
Only use what is in the material above; don't invent addons or links."""

LLM = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def render_prompt(template: str, values: dict[str, str]) -> str:
    out = template.replace("{{", "\x00").replace("}}", "\x01")
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out.replace("\x00", "{").replace("\x01", "}")


def clean_summary(data: Any) -> dict[str, Any]:
    d = data if isinstance(data, dict) else {}

    def strs(v: Any, n: int) -> list[str]:
        return [str(x).strip()[:300] for x in (v or []) if isinstance(x, (str, int, float)) and str(x).strip()][:n]

    def items(v: Any) -> list[dict[str, str]]:
        out = []
        for x in (v or [])[:20]:
            if isinstance(x, str) and x.strip():
                x = {"name": x}
            if isinstance(x, dict) and str(x.get("name", "")).strip():
                out.append({"name": str(x["name"]).strip()[:80], "what": str(x.get("what") or "").strip()[:200],
                            "link": str(x.get("link") or "").strip()[:300]})
        return out

    verdict = str(d.get("verdict") or "maybe").lower().strip()
    if verdict not in ("worth checking", "maybe", "skip"):
        verdict = "worth checking" if "worth" in verdict else "skip" if "skip" in verdict else "maybe"
    return {"bullets": strs(d.get("bullets"), 5), "addons": items(d.get("addons")), "projects": items(d.get("projects")),
            "links": strs(d.get("links"), 15), "claims": strs(d.get("claims"), 10), "verdict": verdict,
            "verdict_reason": str(d.get("verdict_reason") or "").strip()[:300]}


def plain_summary(meta: dict[str, Any], mentions: list[dict[str, Any]], vir: dict[str, Any],
                  transcript_text: str) -> dict[str, Any]:
    """When no model is reachable: a summary built only from what was found."""
    addons = top_terms(mentions, "addon")
    bullets = []
    if meta.get("title"):
        bullets.append(f"\"{meta['title']}\" by {meta.get('uploader') or 'unknown'}.")
    if addons:
        bullets.append("Addons mentioned: " + ", ".join(f"{a['name']} ({a['count']}×)" for a in addons[:6]) + ".")
    kws = top_terms(mentions, "keyword")
    if kws:
        bullets.append("Topics: " + ", ".join(k["name"] for k in kws[:6]) + ".")
    first = (meta.get("description") or transcript_text or "").strip().split("\n")[0][:200]
    if first:
        bullets.append(first)
    links = re.findall(r"https?://[^\s)>\"]+", meta.get("description") or "")[:10]
    verdict = "worth checking" if addons and vir.get("weight", 0) >= 2 else "maybe" if addons or kws else "skip"
    return {"bullets": bullets[:5] or ["Nothing much to go on (no captions, voice add-on or description)."],
            "addons": [{"name": a["name"], "what": "", "link": ""} for a in addons], "projects": [], "links": links,
            "claims": [], "verdict": verdict,
            "verdict_reason": "No AI model was reachable, so this is just what Orion spotted."}


def top_terms(mentions: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for m in mentions:
        if m.get("kind") != kind:
            continue
        a = agg.setdefault(m["term"], {"name": m["term"], "count": 0, "first_t": None})
        a["count"] += 1
        if m.get("t") is not None and (a["first_t"] is None or m["t"] < a["first_t"]):
            a["first_t"] = m["t"]
    return sorted(agg.values(), key=lambda a: (-a["count"], a["name"].lower()))


# --------------------------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """At most `per_minute` starts in any 60 seconds; acquire() waits for a free slot."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        self.per_minute = max(1, int(per_minute))
        self.clock, self.sleep = clock, sleep
        self.starts: list[float] = []
        self._lock = asyncio.Lock()

    def wait_time(self) -> float:
        now = self.clock()
        self.starts = [t for t in self.starts if now - t < 60]
        if len(self.starts) < self.per_minute:
            return 0.0
        return 60 - (now - self.starts[0])

    async def acquire(self) -> float:
        waited = 0.0
        async with self._lock:
            while True:
                w = self.wait_time()
                if w <= 0:
                    self.starts.append(self.clock())
                    return waited
                waited += w
                await self.sleep(w)


# --------------------------------------------------------------------------------------------- leaderboard math
def rank_mentions(rows: list[dict[str, Any]], weights: dict[str, float],
                  prev_scores: Optional[dict[str, float]] = None) -> list[dict[str, Any]]:
    """rows: [{"video_id", "term", "count"}] -> ranked terms. Each video adds sqrt(min(count, 9)) × (1 + its
    virality weight), so ten videos naming an addon once beat one video naming it ten times, and a viral video
    counts for more. Trend vs. the previous period: new / rising / steady / falling."""
    prev_scores = prev_scores or {}
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(r["term"], {"name": r["term"], "score": 0.0, "videos": 0, "mentions": 0, "best_video": None,
                                       "_best": -1.0})
        w = 1 + float(weights.get(r["video_id"], 0.0))
        part = math.sqrt(min(int(r["count"]), 9)) * w
        a["score"] += part
        a["videos"] += 1
        a["mentions"] += int(r["count"])
        if part > a["_best"]:
            a["_best"], a["best_video"] = part, r["video_id"]
    out = []
    for a in agg.values():
        prev = prev_scores.get(a["name"], 0.0)
        a["score"] = round(a["score"], 2)
        a["trend"] = ("new" if prev <= 0 else "rising" if a["score"] >= 1.5 * prev else
                      "falling" if a["score"] < 0.67 * prev else "steady")
        a.pop("_best")
        out.append(a)
    out.sort(key=lambda a: (-a["score"], -a["videos"], a["name"].lower()))
    return out


# --------------------------------------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY, url TEXT, platform TEXT, title TEXT, uploader TEXT, uploader_id TEXT, followers INTEGER,
  upload_ts REAL, duration REAL, hashtags TEXT, description TEXT, status TEXT, error TEXT, notes TEXT,
  transcript_source TEXT, transcript TEXT, onscreen TEXT, summary TEXT, virality TEXT, source TEXT,
  created_ts REAL, updated_ts REAL, checked_24 INTEGER DEFAULT 0, checked_72 INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS videos_url ON videos(url);
CREATE TABLE IF NOT EXISTS video_metrics (
  video_id TEXT, ts REAL, views INTEGER, likes INTEGER, comments INTEGER, reposts INTEGER, followers INTEGER);
CREATE INDEX IF NOT EXISTS metrics_vid ON video_metrics(video_id, ts);
CREATE TABLE IF NOT EXISTS mentions (
  video_id TEXT, term TEXT, kind TEXT, t REAL, source TEXT, snippet TEXT, score REAL);
CREATE INDEX IF NOT EXISTS mentions_vid ON mentions(video_id);
CREATE INDEX IF NOT EXISTS mentions_term ON mentions(term);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "per_minute": 3,            # videos started per minute, at most
    "ocr": True,                # read on-screen text (downloads a small 480p copy, deleted afterwards)
    "ocr_every_s": 1.5,
    "comments": False,          # also read top comments where the platform allows it
    "max_comments": 40,
    "use_cookies": False,       # never by default
    "cookies_file": "",         # a cookies.txt exported from YOUR browser
    "cookies_from_browser": "",  # or e.g. "firefox" / "chrome" (yt-dlp reads your own browser's cookies)
    "llm": "local",             # local = Ollama first; router = Orion's normal model choice
    "max_batch": 10,            # videos taken from one playlist / channel / search
    "max_duration_min": 60,
    "auto_scout_links": True,   # a TikTok/YouTube/Reels link pasted into chat is scouted
}


class Store:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.db.execute(sql, args)

    def many(self, sql: str, rows: list[tuple]) -> None:
        with self.lock:
            self.db.executemany(sql, rows)


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _l(v: Optional[str], default: Any) -> Any:
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


@dataclass
class Job:
    url: str
    source: str
    reply: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None
    force: bool = False
    expand: bool = False


# --------------------------------------------------------------------------------------------- the scout
class VideoScout:
    def __init__(self, data_dir: Path, *, fetcher: Any = None, transcriber: Any = None, llm: Optional[LLM] = None,
                 ocr: Optional[Callable[[Path], str]] = None, publish: Optional[Callable[[dict], Awaitable[None]]] = None,
                 vault_dir: Optional[Path] = None, redact: Optional[Callable[[str], bool]] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 ocr_ready: Optional[Callable[[], bool]] = None) -> None:
        self.dir = Path(data_dir) / "scout"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tmp = self.dir / "tmp"
        shutil.rmtree(self.tmp, ignore_errors=True)   # media left by a crash never lingers
        self.tmp.mkdir(exist_ok=True)
        self.settings_path = self.dir / "settings.json"
        self.prompt_path = self.dir / "summary_prompt.txt"
        if not self.prompt_path.exists():
            self.prompt_path.write_text(DEFAULT_PROMPT, encoding="utf-8")
        self.store = Store(self.dir / "scout.db")
        self.catalogue = Catalogue(self.dir, clock=clock)
        self.fetcher = fetcher or YtDlpFetcher(self.settings)
        self.transcriber = transcriber if transcriber is not None else WhisperTranscriber()
        self.llm = llm
        self.ocr = ocr or tesseract_ocr
        self.ocr_ready = ocr_ready or ocr_available
        self.publish = publish
        self.vault_dir = vault_dir
        if redact is None:
            try:
                from harvester import looks_secret as redact
            except Exception:  # pragma: no cover
                redact = None
        self.redact = redact
        self.clock, self.sleep = clock, sleep
        self.limiter = RateLimiter(self.settings()["per_minute"], clock=clock, sleep=sleep)
        self.queue: asyncio.Queue[Job] | None = None
        self.inflight: set[str] = set()
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
        except (OSError, ValueError):
            pass
        return dict(DEFAULT_SETTINGS)

    def set_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        cur = self.settings()
        for k, v in changes.items():
            if k not in DEFAULT_SETTINGS:
                raise ValueError(f"unknown setting '{k}'")
            want = type(DEFAULT_SETTINGS[k])
            if want is bool and not isinstance(v, bool):
                raise ValueError(f"{k} is on/off")
            if want in (int, float):
                v = want(v)
                if v <= 0:
                    raise ValueError(f"{k} must be above zero")
            if want is str:
                v = str(v).strip()[:300]
            cur[k] = v
        cur["per_minute"] = min(int(cur["per_minute"]), 10)
        cur["max_batch"] = min(int(cur["max_batch"]), MAX_BATCH)
        if cur["llm"] not in ("local", "router"):
            raise ValueError("llm is local or router")
        self.settings_path.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        self.limiter.per_minute = int(cur["per_minute"])
        return cur

    def status(self) -> dict[str, Any]:
        return {"fetcher": bool(getattr(self.fetcher, "available", lambda: True)()),
                "voice": bool(getattr(self.transcriber, "available", lambda: False)()),
                "ocr": bool(self.ocr_ready()), "fuzzy": FUZZY_ENGINE, "llm": self.settings()["llm"],
                "queued": self.queue.qsize() if self.queue else 0, "working": sorted(self.inflight),
                "github_token": bool(os.getenv("ORION_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"))}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._tasks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.queue = asyncio.Queue()
        self._tasks = [loop.create_task(self._worker(), name="scout-worker"),
                       loop.create_task(self._recheck_loop(), name="scout-recheck")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []

    async def _worker(self) -> None:
        assert self.queue is not None
        while True:
            job = await self.queue.get()
            try:
                if job.expand:
                    await self._expand(job)
                else:
                    card = await self.process(job.url, job.source, force=job.force)
                    if job.reply:
                        try:
                            await job.reply(card)
                        except Exception:
                            log.exception("scout reply failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Video Scout job failed for %s", job.url)
            finally:
                self.inflight.discard(job.url)
                self.queue.task_done()

    async def _recheck_loop(self) -> None:
        while True:
            try:
                await self.recheck_due()
                if self.catalogue.refresh_due() and (os.getenv("ORION_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")):
                    await self.catalogue.refresh_from_github()
            except asyncio.CancelledError:
                raise
            except OSError as exc:   # offline / GitHub unreachable: one line, not a traceback every 15 minutes
                log.warning("Video Scout re-check skipped (no connection): %s", exc)
            except Exception as exc:
                if type(exc).__module__.startswith(("httpx", "httpcore")):
                    log.warning("Video Scout re-check skipped (no connection): %s", exc)
                else:
                    log.exception("Video Scout re-check failed")
            await self.sleep(900)

    # ------------------------------------------------------------------ submitting
    def submit(self, urls: list[str], source: str, *, reply: Optional[Callable[[dict], Awaitable[None]]] = None,
               force: bool = False) -> dict[str, Any]:
        """Queue links. Single videos already scouted come back from the cache (unless force)."""
        self.start()
        queued, cached, collections = [], [], []
        for raw in urls[:MAX_BATCH * 4]:
            url = raw.strip()
            if not url:
                continue
            if _YTSEARCH.match(url) or (find_collections(url) and not find_links(url)):
                collections.append(url)
                self._put(Job(url, source, reply, force, expand=True))
                continue
            key = video_key(url)
            row = self._find(key, url)
            if row and row["status"] == "done" and not force:
                cached.append(self.card(row["id"]))
                continue
            if url in self.inflight:
                queued.append(url)
                continue
            if len(queued) >= MAX_BATCH:
                break
            self._put(Job(url, source, reply, force))
            queued.append(url)
        return {"queued": queued, "cached": cached, "collections": collections}

    def _put(self, job: Job) -> None:
        if self.queue is None:
            raise RuntimeError("Video Scout isn't running")
        self.inflight.add(job.url)
        self.queue.put_nowait(job)

    async def _expand(self, job: Job) -> None:
        limit = int(self.settings()["max_batch"])
        url = job.url
        m = _YTSEARCH.match(url)
        if m:
            url = f"ytsearch{min(int(m.group(1) or limit), limit)}:{m.group(2).strip()}"
        urls, err = await self.fetcher.expand(url, limit)
        if err or not urls:
            if job.reply:
                await job.reply({"status": "error", "error": err or "Nothing to scout there.", "url": job.url})
            return
        self.inflight.discard(job.url)
        for u in urls[:limit]:
            key = video_key(u)
            row = self._find(key, u)
            if row and row["status"] == "done" and not job.force:
                continue
            if u not in self.inflight:
                self._put(Job(u, job.source, None, job.force))
        if job.reply:
            await job.reply({"status": "expanded", "url": job.url, "count": len(urls)})

    def _find(self, key: str, url: str) -> Optional[sqlite3.Row]:
        rows = self.store.q("SELECT * FROM videos WHERE id=? OR url=? ORDER BY updated_ts DESC LIMIT 1", (key, url))
        return rows[0] if rows else None

    # ------------------------------------------------------------------ chat
    async def chat(self, text: str, source: str, reply: Optional[Callable[[dict], Awaitable[None]]] = None) -> Optional[str]:
        """The deterministic `scout ...` command and pasted links. Returns Orion's answer, or None if not for us."""
        t = (text or "").strip()
        m = re.match(r"^(?:hey |ok )?(?:orion[,:]?\s+)?scout\b[:,]?\s*(.*)$", t, re.I | re.S)
        if not m:
            links = find_links(t)
            if not links or not self.settings().get("auto_scout_links", True):
                return None
            rest = t
            for u in links:
                rest = rest.replace(u, " ")
            if re.match(r"^\W*(please\s+)?(open|play|launch|send|share|reply|tell)\b", rest, re.I):
                return None   # "open <link>" / "send <link> to Sam" are for other parts of Orion
            return self._queued_reply(self.submit(links, source, reply=reply))
        rest = m.group(1).strip()
        low = rest.lower()
        if not rest or low in ("help", "?"):
            return ("Video Scout: send me a TikTok, YouTube/Shorts or Instagram Reels link and I'll watch it for you. "
                    "Also: 'scout search <words>', 'scout file <path to a list of links>', a playlist or channel link, "
                    "'scout top' for the top WoW addons this week.")
        if low in ("top", "leaderboard", "board", "top addons", "this week"):
            return self.leaderboard_text()
        if low in ("status", "settings"):
            st = self.status()
            return (f"Video Scout: downloader {'ready' if st['fetcher'] else 'missing (pip install yt-dlp)'}, "
                    f"voice {'ready' if st['voice'] else 'add-on needed (captions and descriptions only)'}, "
                    f"on-screen text {'ready' if st['ocr'] else 'needs ffmpeg + Tesseract'}; "
                    f"{st['queued']} waiting.")
        force = False
        if low.startswith(("again ", "refresh ")):
            force, rest = True, rest.split(None, 1)[1]
            low = rest.lower()
        sm = re.match(r"^search\s+(?:(\d{1,2})\s+)?(.+)$", rest, re.I | re.S)
        if sm:
            n = min(int(sm.group(1) or self.settings()["max_batch"]), int(self.settings()["max_batch"]))
            self.submit([f"ytsearch{n}:{sm.group(2).strip()}"], source, reply=reply, force=force)
            return f"Searching YouTube for \"{sm.group(2).strip()}\" and scouting up to {n} videos."
        fm = re.match(r"^file\s+(.+)$", rest, re.I)
        if fm:
            try:
                urls = read_link_file(Path(os.path.expanduser(fm.group(1).strip().strip('"'))))
            except ValueError as exc:
                return str(exc)
            if not urls:
                return "I didn't find any video links in that file."
            return self._queued_reply(self.submit(urls, source, reply=reply, force=force))
        urls = find_links(rest) + find_collections(rest)
        if not urls:
            return "I need a TikTok, YouTube/Shorts or Instagram Reels link to scout (or 'scout search <words>')."
        return self._queued_reply(self.submit(urls, source, reply=reply, force=force))

    @staticmethod
    def _queued_reply(r: dict[str, Any]) -> str:
        parts = []
        if r["queued"]:
            n = len(r["queued"])
            parts.append(f"Scouting {n} video{'s' if n != 1 else ''}. The card shows up in Video Scout when it's done "
                         "(usually a minute or two each).")
        if r["collections"]:
            parts.append(f"Opening {len(r['collections'])} playlist/channel/search and scouting its videos.")
        for c in r["cached"][:3]:
            parts.append(f"Already scouted \"{c.get('title') or c['url']}\": {verdict_line(c)}")
        return " ".join(parts) or "Nothing new to scout."

    # ------------------------------------------------------------------ the pipeline
    async def process(self, url: str, source: str = "user", *, force: bool = False) -> dict[str, Any]:
        key = video_key(url)
        row = self._find(key, url)
        if row and row["status"] == "done" and not force:
            return self.card(row["id"])
        now = self.clock()
        if row is None:
            self.store.x("INSERT OR REPLACE INTO videos (id, url, platform, status, source, created_ts, updated_ts) "
                         "VALUES (?,?,?,?,?,?,?)", (key, url, platform_of(url), "working", source, now, now))
        else:
            key = row["id"]
            self.store.x("UPDATE videos SET status='working', error=NULL, updated_ts=? WHERE id=?", (now, key))
        await self._emit(key)
        await self.limiter.acquire()
        work = self.tmp / uuid.uuid4().hex[:12]
        st = self.settings()
        try:
            res = await self.fetcher.fetch(url, work, want_video=bool(st["ocr"]) and self.ocr_ready(),
                                           comments=bool(st["comments"]))
            if res.error:
                self.store.x("UPDATE videos SET status=?, error=?, updated_ts=? WHERE id=?",
                             ("blocked" if res.blocked else "error", res.error, self.clock(), key))
                return await self._emit(key)
            key = self._rekey(key, canonical_key(res.info, key))
            card = await self._analyse(key, url, source, res, work, st)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Video Scout failed on %s", url)
            self.store.x("UPDATE videos SET status='error', error=?, updated_ts=? WHERE id=?",
                         (f"Something went wrong while scouting: {exc}"[:300], self.clock(), key))
            card = self.card(key)
        finally:
            shutil.rmtree(work, ignore_errors=True)   # the video and audio never outlive the scouting
        await self._emit(key)
        return card

    def _rekey(self, old: str, new: str) -> str:
        if new == old:
            return old
        if self.store.q("SELECT 1 FROM videos WHERE id=?", (new,)):
            self.store.x("DELETE FROM videos WHERE id=?", (old,))
        else:
            self.store.x("UPDATE videos SET id=? WHERE id=?", (new, old))
        return new

    async def _analyse(self, key: str, url: str, source: str, res: FetchResult, work: Path,
                       st: dict[str, Any]) -> dict[str, Any]:
        meta = parse_metadata(res.info, url)
        notes: list[str] = []
        if res.info.get("_scout_note"):
            notes.append(str(res.info["_scout_note"]))
        now = self.clock()

        # ---- transcript: captions, else whisper, else the description
        segments: list[dict[str, Any]] = []
        tsource = "none"
        if res.subs and res.subs.exists():
            segments = parse_vtt(res.subs.read_text(encoding="utf-8", errors="replace"))
            tsource = "captions" if segments else tsource
        if not segments and res.media is not None:
            if self.transcriber is not None and self.transcriber.available():
                try:
                    segments = await asyncio.to_thread(self.transcriber.transcribe, res.media)
                    tsource = "voice"
                except Exception as exc:
                    notes.append(f"Couldn't transcribe the audio ({exc}).")
            else:
                notes.append("Voice add-on needed to transcribe speech (no captions on this video). "
                             "Used the description and on-screen text instead.")
        if not segments and meta["description"]:
            tsource = "description" if tsource == "none" else tsource
        transcript_text = " ".join(s["text"] for s in segments)[:MAX_TRANSCRIPT_CHARS]
        __import__("injection_firewall").note(transcript_text, f"a video transcript ({meta.get('title') or url})"[:120])  # Toolkit

        # ---- on-screen text
        onscreen: list[dict[str, Any]] = []
        if st["ocr"] and res.media is not None:
            if not self.ocr_ready():
                notes.append("On-screen text needs ffmpeg and Tesseract.")
            elif await asyncio.to_thread(has_video_stream, res.media):
                frames = await asyncio.to_thread(extract_frames, res.media, work / "frames", float(st["ocr_every_s"]))
                texts = []
                for t, p in frames:
                    texts.append((t, await asyncio.to_thread(self.ocr, p)))
                onscreen = dedupe_lines(texts, drop=self.redact)

        # ---- comments
        comments = [c for c in (res.info.get("comments") or []) if isinstance(c, dict)][:int(st["max_comments"])]

        # ---- matching
        segs = ([{"t": s["t"], "text": s["text"], "source": "speech" if tsource == "voice" else "captions"}
                 for s in segments] +
                [{"t": o["t"], "text": o["text"], "source": "screen"} for o in onscreen] +
                [{"t": None, "text": meta["title"], "source": "title"}] +
                [{"t": None, "text": ln, "source": "description"} for ln in meta["description"].splitlines()
                 if ln.strip() and not (self.redact and self.redact(ln))] +
                [{"t": None, "text": "#" + " #".join(meta["hashtags"]), "source": "hashtags"}] +
                [{"t": None, "text": str(c.get("text") or "")[:500], "source": "comment"} for c in comments])
        mentions = match_terms(segs, self.catalogue.terms())

        # ---- metrics + virality
        self.store.x("INSERT INTO video_metrics VALUES (?,?,?,?,?,?,?)",
                     (key, now, meta["views"], meta["likes"], meta["comments"], meta["reposts"], meta["followers"]))
        vir = self._virality(key, meta, now)

        # ---- summary
        summary = await self._summarise(meta, mentions, onscreen, transcript_text, tsource, vir, notes)
        known = {m["term"].lower() for m in mentions}
        names = {a["name"].lower(): a["name"] for a in self.catalogue.addons()}
        for a in summary["addons"]:
            n = names.get(a["name"].lower(), a["name"])
            if n.lower() not in known and (n.lower() in names or n.lower() in (transcript_text + " " + meta["description"] + " " +
                                                                               " ".join(o["text"] for o in onscreen)).lower()):
                mentions.append({"term": n, "kind": "addon", "t": None, "source": "summary", "snippet": a["what"],
                                 "score": 100.0})
                known.add(n.lower())
        for p in summary["projects"]:
            if p["name"].lower() not in known:
                mentions.append({"term": p["name"], "kind": "project", "t": None, "source": "summary",
                                 "snippet": p["what"], "score": 100.0})
                known.add(p["name"].lower())

        # ---- store
        self.store.x("DELETE FROM mentions WHERE video_id=?", (key,))
        self.store.many("INSERT INTO mentions VALUES (?,?,?,?,?,?,?)",
                        [(key, m["term"], m["kind"], m["t"], m["source"], m["snippet"], m["score"]) for m in mentions])
        self.store.x(
            "UPDATE videos SET url=?, platform=?, title=?, uploader=?, uploader_id=?, followers=?, upload_ts=?, duration=?, "
            "hashtags=?, description=?, status='done', error=NULL, notes=?, transcript_source=?, transcript=?, onscreen=?, "
            "summary=?, virality=?, updated_ts=? WHERE id=?",
            (meta["url"] or url, meta["platform"], meta["title"], meta["uploader"], meta["uploader_id"], meta["followers"],
             meta["upload_ts"], meta["duration"], _j(meta["hashtags"]), meta["description"], _j(notes), tsource,
             _j(segments[:4000]), _j(onscreen[:1000]), _j(summary), _j(vir), self.clock(), key))
        self._to_vault(key)
        return self.card(key)

    def _virality(self, key: str, meta: dict[str, Any], now: float) -> dict[str, Any]:
        hist = [(r["ts"], r["views"]) for r in self.store.q(
            "SELECT ts, views FROM video_metrics WHERE video_id=? AND views IS NOT NULL ORDER BY ts", (key,))]
        median = None
        if meta.get("uploader_id") or meta.get("uploader"):
            others = [r["views"] for r in self.store.q(
                "SELECT m.views FROM video_metrics m JOIN videos v ON v.id=m.video_id WHERE v.id<>? AND "
                "(v.uploader_id=? OR (v.uploader_id='' AND v.uploader=?)) AND m.views IS NOT NULL "
                "AND m.ts=(SELECT MAX(ts) FROM video_metrics WHERE video_id=v.id)",
                (key, meta.get("uploader_id") or "\x00", meta.get("uploader") or "\x00"))]
            if len(others) >= 2:
                median = statistics.median(others)
        return virality(meta["views"], meta["likes"], meta["comments"], meta["reposts"], meta["upload_ts"], now,
                        followers=meta["followers"], creator_median=median, history=hist)

    async def _summarise(self, meta: dict[str, Any], mentions: list[dict[str, Any]], onscreen: list[dict[str, Any]],
                         transcript: str, tsource: str, vir: dict[str, Any], notes: list[str]) -> dict[str, Any]:
        if self.llm is None:
            return plain_summary(meta, mentions, vir, transcript)
        ment = "\n".join(f"- {m['term']} ({m['kind']}) at {fmt_t(m['t'])} in {m['source']}: {m['snippet'][:90]}"
                         for m in mentions[:60]) or "(none)"
        stats = ", ".join(f"{k} {human(meta[k])}" for k in ("views", "likes", "comments", "reposts") if meta[k] is not None)
        try:
            template = self.prompt_path.read_text(encoding="utf-8")
        except OSError:
            template = DEFAULT_PROMPT
        prompt = render_prompt(template, {
            "title": meta["title"] or "(no title)", "uploader": meta["uploader"] or "unknown",
            "platform": meta["platform"], "stats": stats or "no stats",
            "hashtags": " ".join("#" + h for h in meta["hashtags"][:20]) or "(none)",
            "description": meta["description"][:2000] or "(none)", "mentions": ment,
            "onscreen": "\n".join(f"[{fmt_t(o['t'])}] {o['text']}" for o in onscreen[:120]) or "(none)",
            "transcript_source": tsource, "transcript": transcript[:12000] or "(none)",
        })
        try:
            data = await asyncio.wait_for(self.llm(prompt, SUMMARY_SCHEMA), 240)
            out = clean_summary(data)
            if out["bullets"]:
                return out
        except Exception as exc:
            log.info("Video Scout summary model unavailable: %s", exc)
        notes.append("No AI model answered, so the summary is just what Orion spotted.")
        return plain_summary(meta, mentions, vir, transcript)

    def _to_vault(self, key: str) -> None:
        if self.vault_dir is None:
            return
        try:
            d = self.detail(key)
            if not d:
                return
            folder = Path(self.vault_dir) / "video-scout"
            folder.mkdir(parents=True, exist_ok=True)
            lines = [f"# {d.get('title') or d['url']}", "", f"Video Scout · {d['platform']} · {d.get('uploader') or ''}",
                     f"Link: {d['url']}", ""]
            s = d.get("summary") or {}
            lines += [f"- {b}" for b in s.get("bullets", [])]
            if s.get("verdict"):
                lines += ["", f"Verdict: {s['verdict']} — {s.get('verdict_reason', '')}"]
            ad = [m["name"] for m in d.get("top_mentions", []) if m["kind"] == "addon"]
            if ad:
                lines += ["", "Addons: " + ", ".join(ad)]
            if d.get("transcript"):
                lines += ["", f"## Transcript ({d.get('transcript_source')})", "",
                          " ".join(s["text"] for s in d["transcript"])[:MAX_TRANSCRIPT_CHARS]]
            if d.get("onscreen"):
                lines += ["", "## On screen", ""] + [f"- [{fmt_t(o['t'])}] {o['text']}" for o in d["onscreen"][:300]]
            safe = re.sub(r"[^\w.-]", "_", key)
            (folder / f"{safe}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            log.exception("Couldn't save the transcript to the vault")

    async def _emit(self, key: str) -> dict[str, Any]:
        card = self.card(key)
        if self.publish is not None:
            try:
                await self.publish({"event": "SCOUT_VIDEO", "video": card})
            except Exception:
                pass
        return card

    # ------------------------------------------------------------------ re-checks (velocity)
    async def recheck_due(self) -> int:
        now = self.clock()
        n = 0
        for col, after in (("checked_24", DAY), ("checked_72", 3 * DAY)):
            rows = self.store.q(f"SELECT id, url FROM videos WHERE status='done' AND {col}=0 AND created_ts<=?",
                                (now - after,))
            for r in rows[:20]:
                await self.limiter.acquire()
                res = await self.fetcher.metadata(r["url"])
                self.store.x(f"UPDATE videos SET {col}=1 WHERE id=?", (r["id"],))
                if res.error:
                    continue
                meta = parse_metadata(res.info, r["url"])
                self.store.x("INSERT INTO video_metrics VALUES (?,?,?,?,?,?,?)",
                             (r["id"], self.clock(), meta["views"], meta["likes"], meta["comments"], meta["reposts"],
                              meta["followers"]))
                vir = self._virality(r["id"], {**meta, "upload_ts": meta["upload_ts"]}, self.clock())
                self.store.x("UPDATE videos SET virality=?, updated_ts=? WHERE id=?", (_j(vir), self.clock(), r["id"]))
                await self._emit(r["id"])
                n += 1
        return n

    # ------------------------------------------------------------------ reading
    def card(self, key: str) -> dict[str, Any]:
        rows = self.store.q("SELECT * FROM videos WHERE id=?", (key,))
        if not rows:
            return {"id": key, "status": "missing"}
        r = rows[0]
        m = self.store.q("SELECT * FROM video_metrics WHERE video_id=? ORDER BY ts DESC LIMIT 1", (key,))
        last = dict(m[0]) if m else {}
        mentions = [dict(x) for x in self.store.q("SELECT term, kind, t, source FROM mentions WHERE video_id=?", (key,))]
        tops = []
        for kind in ("addon", "project", "keyword"):
            tops += [{**a, "kind": kind} for a in top_terms(mentions, kind)]
        return {
            "id": r["id"], "url": r["url"], "platform": r["platform"], "title": r["title"] or "",
            "uploader": r["uploader"] or "", "followers": r["followers"], "upload_ts": r["upload_ts"],
            "duration": r["duration"], "status": r["status"], "error": r["error"], "notes": _l(r["notes"], []),
            "hashtags": _l(r["hashtags"], []), "transcript_source": r["transcript_source"],
            "stats": {k: last.get(k) for k in ("views", "likes", "comments", "reposts")},
            "virality": _l(r["virality"], {}), "summary": _l(r["summary"], None), "top_mentions": tops[:30],
            "scouted_ts": r["created_ts"], "updated_ts": r["updated_ts"],
        }

    def detail(self, key: str) -> Optional[dict[str, Any]]:
        rows = self.store.q("SELECT * FROM videos WHERE id=?", (key,))
        if not rows:
            return None
        r = rows[0]
        return {**self.card(key), "description": r["description"] or "", "transcript": _l(r["transcript"], []),
                "onscreen": _l(r["onscreen"], []),
                "mentions": [dict(x) for x in self.store.q("SELECT term, kind, t, source, snippet, score FROM mentions "
                                                           "WHERE video_id=? ORDER BY t IS NULL, t", (key,))],
                "metrics": [dict(x) for x in self.store.q("SELECT ts, views, likes, comments, reposts, followers FROM "
                                                          "video_metrics WHERE video_id=? ORDER BY ts", (key,))]}

    def cards(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.q("SELECT id FROM videos ORDER BY updated_ts DESC LIMIT ?", (max(1, min(limit, 500)),))
        return [self.card(r["id"]) for r in rows]

    # ------------------------------------------------------------------ leaderboard
    def leaderboard(self, days: float = 7, now: Optional[float] = None, limit: int = 25) -> dict[str, Any]:
        now = self.clock() if now is None else now
        since = now - days * DAY if days > 0 else 0.0
        prev_since = since - days * DAY if days > 0 else 0.0

        def window(lo: float, hi: float) -> tuple[list[dict[str, Any]], dict[str, float]]:
            vids = self.store.q("SELECT id, virality FROM videos WHERE status='done' AND "
                                "COALESCE(upload_ts, created_ts) >= ? AND COALESCE(upload_ts, created_ts) < ?", (lo, hi))
            weights = {v["id"]: float(_l(v["virality"], {}).get("weight") or 0) for v in vids}
            if not weights:
                return [], {}
            marks = ",".join("?" * len(weights))
            rows = [dict(r) for r in self.store.q(
                f"SELECT video_id, term, kind, COUNT(*) AS count FROM mentions WHERE video_id IN ({marks}) "
                f"GROUP BY video_id, term, kind", tuple(weights))]
            return rows, weights

        rows, weights = window(since, now + 1)
        prev_rows, prev_weights = window(prev_since, since) if days > 0 else ([], {})
        out: dict[str, Any] = {"days": days, "since": since, "videos": len(weights)}
        for kind, label in (("addon", "addons"), ("project", "projects"), ("keyword", "keywords")):
            prev = {a["name"]: a["score"] for a in rank_mentions([r for r in prev_rows if r["kind"] == kind], prev_weights)}
            ranked = rank_mentions([r for r in rows if r["kind"] == kind], weights, prev if days > 0 else None)
            for a in ranked:
                if a["best_video"]:
                    c = self.card(a["best_video"])
                    a["best_video"] = {"id": c["id"], "title": c.get("title"), "url": c.get("url")}
            out[label] = ranked[:limit]
        out["new_on_github"] = [{"name": a["name"], "url": a.get("url", "")} for a in self.catalogue.new_since(since)][:limit]
        return out

    def leaderboard_text(self, days: float = 7) -> str:
        lb = self.leaderboard(days)
        if not lb["addons"]:
            return "No addon mentions in scouted videos from the last week yet. Send me some WoW videos."
        top = "; ".join(f"{i + 1}. {a['name']} ({a['videos']} video{'s' if a['videos'] != 1 else ''}"
                        f"{', ' + a['trend'] if a['trend'] in ('new', 'rising') else ''})"
                        for i, a in enumerate(lb["addons"][:5]))
        return f"Top WoW addons this week, from {lb['videos']} videos: {top}."

    def export_markdown(self, days: float = 7) -> str:
        lb = self.leaderboard(days)
        when = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        lines = [f"## Top WoW addons — week to {when} (Video Scout, {lb['videos']} videos)", "",
                 "| # | Addon | Score | Videos | Mentions | Trend | Best video |", "|---|---|---|---|---|---|---|"]
        for i, a in enumerate(lb["addons"], 1):
            bv = a.get("best_video") or {}
            link = f"[{(bv.get('title') or 'video')[:50].replace('|', '/')}]({bv.get('url')})" if bv.get("url") else ""
            lines.append(f"| {i} | {a['name']} | {a['score']} | {a['videos']} | {a['mentions']} | {a['trend']} | {link} |")
        if lb["projects"]:
            lines += ["", "**Projects mentioned:** " + ", ".join(f"{p['name']} ({p['trend']})" for p in lb["projects"][:15])]
        if lb["new_on_github"]:
            lines += ["", "**New on GitHub:** " + ", ".join(
                f"[{p['name']}]({p['url']})" if p["url"] else p["name"] for p in lb["new_on_github"][:15])]
        return "\n".join(lines) + "\n"

    def export_csv(self, days: float = 7) -> str:
        lb = self.leaderboard(days)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["kind", "rank", "name", "score", "videos", "mentions", "trend", "best_video_url"])
        for kind in ("addons", "projects", "keywords"):
            for i, a in enumerate(lb[kind], 1):
                w.writerow([kind[:-1], i, a["name"], a["score"], a["videos"], a["mentions"], a["trend"],
                            (a.get("best_video") or {}).get("url", "")])
        return buf.getvalue()

    def send_to_ideas(self, project_dir: Path, days: float = 7) -> Path:
        """Appends this week's board to <project>/IDEAS.md (creates it if needed)."""
        project_dir = Path(os.path.expanduser(str(project_dir))).resolve()
        if not project_dir.is_dir():
            raise ValueError("That project folder doesn't exist.")
        path = project_dir / "IDEAS.md"
        md = self.export_markdown(days)
        prefix = "" if not path.exists() else ("\n" if path.read_text(encoding="utf-8").endswith("\n") else "\n\n")
        with path.open("a", encoding="utf-8") as f:
            f.write(prefix + md)
        return path


# --------------------------------------------------------------------------------------------- small helpers
def read_link_file(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"I can't find {path}.")
    if path.suffix.lower() not in (".txt", ".csv", ".md", ".list", ".tsv", "") or path.stat().st_size > 1_000_000:
        raise ValueError("A link list should be a small .txt, .csv or .md file.")
    text = path.read_text(encoding="utf-8", errors="replace")
    out = find_links(text) + find_collections(text)
    out += [ln.strip() for ln in text.splitlines() if _YTSEARCH.match(ln.strip())]
    return out


def fmt_t(t: Optional[float]) -> str:
    if t is None:
        return "-"
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def human(n: Optional[float]) -> str:
    if n is None:
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(int(n))


def verdict_line(card: dict[str, Any]) -> str:
    if card.get("status") in ("blocked", "error"):
        return card.get("error") or "couldn't be scouted."
    s = card.get("summary") or {}
    addons = [m["name"] for m in card.get("top_mentions", []) if m["kind"] == "addon"][:5]
    v = card.get("virality") or {}
    bits = [s.get("verdict", "done")]
    if addons:
        bits.append("addons: " + ", ".join(addons))
    if v.get("views_per_day") is not None:
        bits.append(f"{human(v['views_per_day'])} views/day")
    return "; ".join(bits) + "."


def reply_text(card: dict[str, Any]) -> str:
    """A finished card as a short message (Telegram, phone alert, chat)."""
    title = card.get("title") or card.get("url", "")
    if card.get("status") in ("blocked", "error"):
        return f"Video Scout couldn't read {title}: {card.get('error')}"
    s = card.get("summary") or {}
    lines = [f"Scouted: {title}", verdict_line(card)]
    lines += [f"• {b}" for b in s.get("bullets", [])[:5]]
    return "\n".join(lines)
