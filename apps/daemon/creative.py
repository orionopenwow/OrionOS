"""Creative Studio & Worldbuilding Forge (Feature Bible D4).

  * Instant Idea & Lore Catcher — "Orion, log a realm idea: low-gravity CTF battleground" /
    "save a podcast topic for The Jointe: ..." → tagged, appended to the right project's IDEAS.md
    (or ~/.orion/ideas/<topic>.md) and embedded into memory.
  * Asset Pipeline Automator — drop an image / audio / video / .glb on the viewport and get pills:
    [Remove Background] [Crop to Circular Discord Icon] [Compress for Web] [Convert GLB → WoW M2] …
  * Show & Podcast Producer — topic timer, one-key highlight timestamps, and silent on-screen
    context cards when you mention something Orion has notes on; session notes saved as markdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.creative")

IDEA_RE = re.compile(
    r"^\s*(?:hey\s+)?(?:orion[,:]?\s*)?(?:please\s+)?(?:log|save|capture|note|add|jot(?:\s+down)?|record)\s+"
    r"(?:a|an|this|the|my|new)?\s*(?P<kind>[\w\s-]{0,30}?)\s*(?P<noun>idea|topic|note|lore|concept|quest|pitch|bit|joke)"
    r"(?:\s+(?:for|in|to)\s+(?P<project>[\w\s'&.-]{2,40}?))?\s*[:,\-–—]\s*(?P<text>.+)$",
    re.I | re.S,
)
_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def parse_idea(text: str) -> Optional[dict[str, str]]:
    m = IDEA_RE.match(text.strip())
    if not m or len(m.group("text").strip()) < 3:
        return None
    kind = (m.group("kind") or "").strip().lower() or m.group("noun").lower()
    return {"kind": kind, "noun": m.group("noun").lower(), "project": (m.group("project") or "").strip(),
            "text": m.group("text").strip()}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "ideas"


class IdeaCatcher:
    def __init__(self, data_dir: Path, preload: Any) -> None:
        self.dir = data_dir / "ideas"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "index.jsonl"
        self.preload = preload

    def _target(self, idea: dict[str, str]) -> tuple[Path, str]:
        wanted = (idea["project"] or idea["kind"]).lower()
        for ws in self.preload.workspaces:
            names = {ws["name"].lower(), ws["id"].lower(), ws["id"].replace("-", " ").lower()}
            if wanted and ws["exists"] and any(wanted in n or n in wanted for n in names if len(n) > 2):
                return Path(ws["path"]) / "IDEAS.md", ws["name"]
        topic = idea["project"] or idea["kind"]
        return self.dir / f"{_slug(topic)}.md", topic.title()

    def log(self, idea: dict[str, str]) -> dict[str, Any]:
        path, project = self._target(idea)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        tag = idea["kind"] if idea["kind"] != idea["noun"] else idea["noun"]
        line = f"- [{stamp}] **{tag}** — {idea['text']}\n"
        new_file = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            if new_file:
                fh.write(f"# {project} — ideas\n\nCaptured by Orion.\n\n")
            fh.write(line)
        record = {"id": f"idea-{uuid.uuid4().hex[:8]}", "at": time.time(), "kind": tag, "project": project,
                  "text": idea["text"], "file": str(path)}
        with self.index.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        try:
            lines = self.index.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


# ------------------------------------------------------------------ asset pipeline
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
MODEL_EXT = {".glb", ".gltf", ".fbx", ".obj"}
MAX_UPLOAD = 300 * 1024 * 1024


def asset_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in MODEL_EXT:
        return "model"
    return "file"


class AssetPipeline:
    def __init__(self, data_dir: Path, grimoire: Any, runner: Any) -> None:
        self.inbox = data_dir / "assets" / "inbox"
        self.out = data_dir / "assets" / "out"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.grimoire = grimoire
        self.runner = runner
        self.assets: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _has(module: str) -> bool:
        try:
            __import__(module)
            return True
        except Exception:
            return False

    def _converter(self) -> Optional[dict[str, Any]]:
        """A Grimoire command whose description mentions GLB/glTF and M2, using {input}/{output} placeholders."""
        for e in self.grimoire.entries():
            text = f"{e['description']} {' '.join(e['voice_triggers'])} {e.get('command', '')}".lower()
            if ("glb" in text or "gltf" in text) and "m2" in text and "{input}" in e.get("command", ""):
                return e
        return None

    def actions_for(self, kind: str) -> list[dict[str, Any]]:
        ffmpeg = bool(shutil.which("ffmpeg"))
        if kind == "image":
            rembg = self._has("rembg")
            return [
                {"id": "remove_background", "label": "Remove Background", "enabled": rembg,
                 "reason": None if rembg else "pip install rembg (optional, ~150 MB model)"},
                {"id": "discord_icon", "label": "Crop to Circular Discord Icon", "enabled": True},
                {"id": "compress_web", "label": "Compress for Web", "enabled": True},
            ]
        if kind in ("audio", "video"):
            return [{"id": "compress_media", "label": "Compress for Web", "enabled": ffmpeg,
                     "reason": None if ffmpeg else "Install ffmpeg and add it to PATH"},
                    {"id": "normalize_audio", "label": "Normalize Loudness", "enabled": ffmpeg,
                     "reason": None if ffmpeg else "Install ffmpeg and add it to PATH"}]
        if kind == "model":
            conv = self._converter()
            return [{"id": "convert_m2", "label": "Convert GLB → WoW M2 Patch", "enabled": bool(conv),
                     "reason": None if conv else "Add a saved script mentioning GLB and M2 with {input} and "
                                                 "{output} placeholders"}]
        return []

    def store(self, filename: str, data: bytes) -> dict[str, Any]:
        safe = re.sub(r"[^\w.\- ]+", "_", Path(filename).name)[:120] or "asset"
        aid = f"asset-{uuid.uuid4().hex[:8]}"
        path = self.inbox / f"{aid}_{safe}"
        path.write_bytes(data)
        kind = asset_kind(safe)
        asset = {"id": aid, "name": safe, "path": str(path), "kind": kind, "bytes": len(data),
                 "actions": self.actions_for(kind), "outputs": []}
        self.assets[aid] = asset
        return asset

    def _out_path(self, asset: dict[str, Any], suffix: str) -> Path:
        stem = Path(asset["name"]).stem
        return self.out / f"{stem}{suffix}"

    async def run(self, asset_id: str, action: str) -> dict[str, Any]:
        asset = self.assets.get(asset_id)
        if not asset:
            raise ValueError("unknown asset")
        src = Path(asset["path"])
        if action == "discord_icon":
            out = await asyncio.to_thread(self._discord_icon, src, self._out_path(asset, "_discord.png"))
        elif action == "compress_web":
            out = await asyncio.to_thread(self._compress_image, src, self._out_path(asset, "_web.webp"))
        elif action == "remove_background":
            out = await asyncio.to_thread(self._remove_bg, src, self._out_path(asset, "_nobg.png"))
        elif action == "compress_media":
            ext = ".mp4" if asset["kind"] == "video" else ".mp3"
            out = self._out_path(asset, f"_web{ext}")
            args = (["-vcodec", "libx264", "-crf", "28", "-preset", "veryfast", "-acodec", "aac", "-b:a", "128k"]
                    if asset["kind"] == "video" else ["-b:a", "160k"])
            await self._ffmpeg(src, out, args)
        elif action == "normalize_audio":
            out = self._out_path(asset, "_normalized" + (".mp4" if asset["kind"] == "video" else ".mp3"))
            await self._ffmpeg(src, out, ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
        elif action == "convert_m2":
            conv = self._converter()
            if not conv:
                raise ValueError("no GLB → M2 converter configured in Scripts")
            if conv.get("requires_approval"):
                raise ValueError("the GLB → M2 converter is marked requires_approval — run it from Scripts")
            out = self._out_path(asset, ".m2")
            cmd = conv["command"].replace("{input}", f'"{src}"').replace("{output}", f'"{out}"')
            run = await self.runner.start(cmd, cwd=conv.get("cwd"), label=f"GLB → M2: {asset['name']}")
            while run.ended_at is None:
                await asyncio.sleep(0.5)
            if run.exit_code != 0:
                raise RuntimeError("converter failed — see the run output")
        else:
            raise ValueError(f"unknown action {action}")
        result = {"action": action, "path": str(out), "name": out.name, "bytes": out.stat().st_size if out.exists() else 0}
        asset["outputs"].append(result)
        return result

    @staticmethod
    def _discord_icon(src: Path, out: Path) -> Path:
        from PIL import Image, ImageDraw

        im = Image.open(src).convert("RGBA")
        side = min(im.size)
        left, top = (im.width - side) // 2, (im.height - side) // 2
        im = im.crop((left, top, left + side, top + side)).resize((512, 512), Image.LANCZOS)
        mask = Image.new("L", (2048, 2048), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 2047, 2047), fill=255)
        mask = mask.resize((512, 512), Image.LANCZOS)
        result = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        result.paste(im, (0, 0), mask)
        result.save(out, "PNG", optimize=True)
        return out

    @staticmethod
    def _compress_image(src: Path, out: Path) -> Path:
        from PIL import Image

        im = Image.open(src)
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        im.thumbnail((1920, 1920), Image.LANCZOS)
        im.save(out, "WEBP", quality=82, method=6)
        return out

    @staticmethod
    def _remove_bg(src: Path, out: Path) -> Path:
        from rembg import remove  # optional dependency

        out.write_bytes(remove(src.read_bytes()))
        return out

    async def _ffmpeg(self, src: Path, out: Path, args: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *args, str(out),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **_NO_WINDOW)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode("utf-8", "replace")[-400:])


# ------------------------------------------------------------------ producer HUD
class Producer:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "producer"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session: Optional[dict[str, Any]] = None
        self._last_card = 0.0

    def start(self, title: str = "") -> dict[str, Any]:
        self.session = {"title": title or f"Session {datetime.now():%Y-%m-%d %H:%M}", "started_at": time.time(),
                        "topics": [], "highlights": [], "cards": []}
        return self.session

    def elapsed(self) -> float:
        return time.time() - self.session["started_at"] if self.session else 0.0

    def topic(self, title: str) -> Optional[dict[str, Any]]:
        if not self.session:
            return None
        self.session["topics"].append({"title": title[:120], "at": self.elapsed()})
        return self.session

    def highlight(self, note: str = "") -> Optional[dict[str, Any]]:
        if not self.session:
            return None
        self.session["highlights"].append({"at": self.elapsed(), "note": note[:200]})
        return self.session

    def stop(self) -> Optional[Path]:
        if not self.session:
            return None
        s, self.session = self.session, None

        def ts(sec: float) -> str:
            sec = int(sec)
            return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

        path = self.dir / f"{datetime.fromtimestamp(s['started_at']):%Y-%m-%d_%H%M}_{_slug(s['title'])}.md"
        lines = [f"# {s['title']}", "", f"Duration: {ts(time.time() - s['started_at'])}", "", "## Topics"]
        lines += [f"- {ts(t['at'])} — {t['title']}" for t in s["topics"]] or ["- (none)"]
        lines += ["", "## Highlights"]
        lines += [f"- {ts(h['at'])}" + (f" — {h['note']}" if h["note"] else "") for h in s["highlights"]] or ["- (none)"]
        if s["cards"]:
            lines += ["", "## Context Orion surfaced"] + [f"- {ts(c['at'])} — {c['title']}" for c in s["cards"]]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def may_show_card(self) -> bool:
        return bool(self.session) and time.time() - self._last_card > 45

    def card_shown(self, title: str) -> None:
        self._last_card = time.time()
        if self.session:
            self.session["cards"].append({"at": self.elapsed(), "title": title[:120]})


__all__ = ["IdeaCatcher", "AssetPipeline", "Producer", "parse_idea", "asset_kind", "MAX_UPLOAD"]
