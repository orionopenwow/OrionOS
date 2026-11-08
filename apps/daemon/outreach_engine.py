"""Orion Social Media & Creator Outreach Engine (`outreach_engine.py`).

Strictly follows `[no-messaging]` and `[approval-rules]`:
  - Never posts or sends a message without explicit human sign-off.
  - Runs every draft through the Anti-AI Copywriting Filter (`humanize_copy` & `score_human_voice`)
    so posts read like a real indie systems programmer, never like ChatGPT.
  - Queues all social posts (TikTok, Instagram Reels, YouTube, Reddit, Discord) and creator outreach
    messages into `ApprovalQueue` (`~/.orion/approvals.json`).
  - Supports one-key batch scheduling with human-paced jitter so accounts stay 100% compliant.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from approval_queue import ApprovalQueue

log = logging.getLogger("orion.outreach")

try:   # routes resolve their annotations from module globals (this file uses `from __future__ import annotations`)
    from fastapi import Request
except ImportError:   # pragma: no cover - the engine works without FastAPI
    Request = Any  # type: ignore

# ==============================================================================
# 1. ANTI-AI COPYWRITING FILTER ("NEVER SOUND LIKE A BOT")
# ==============================================================================
BANNED_AI_REPLACEMENTS: dict[str, str] = {
    r"\bdelve(?:s|d|ing)?\s+into\b": "look at",
    r"\bdelve(?:s|d|ing)?\b": "dig",
    r"\btapestry\b": "stack",
    r"\btestament\s+to\b": "proof of",
    r"\bgame-changer\b": "useful tool",
    r"\bgame\s+changer\b": "useful tool",
    r"\brevolutionary\b": "fast",
    r"\brevolutionize(?:s|d|ing)?\b": "speed up",
    r"\bunleash(?:es|ed|ing)?\b": "run",
    r"\bunlock(?:s|ed|ing)?\s+the\s+power\s+of\b": "use",
    r"\belevate(?:s|d|ing)?\b": "improve",
    r"\bsymphony\b": "mix",
    r"\bbeacon\b": "signal",
    r"\blandscape\b": "space",
    r"\bseamless(?:ly)?\b": "clean",
    r"\bparadigm\s+shift\b": "change",
    r"\bbuckle\s+up\b": "here is the build",
    r"\bdive\s+in(?:to)?\b": "check",
    r"\bsupercharge(?:s|d|ing)?\b": "speed up",
    r"\bgroundbreaking\b": "new",
    r"\bin\s+conclusion,?\b": "",
    r"\boverall,?\s+it\s+is\s+clear\s+that\b": "",
    r"\bever\s+wondered\s+how\b": "here is how",
}

HYPE_EMOJI_PATTERN = re.compile(r"[🚀🔥✨💥🌟🎉]{2,}")


def humanize_copy(text: str, casual_lowercase: bool = False) -> str:
    """Strips AI clichés, hype emoji chains, and robotic transitions from draft copy."""
    cleaned = text.strip()
    for pattern, repl in BANNED_AI_REPLACEMENTS.items():
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)

    # Replace excessive em-dashes (more than 1 per paragraph) with normal punctuation
    cleaned = cleaned.replace(" — ", " - ")
    cleaned = HYPE_EMOJI_PATTERN.sub("", cleaned)
    # Collapse double spaces left by removals
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    if casual_lowercase:
        # Lowercase sentence starts on short-form captions while keeping acronyms like WebGPU, DUST, Lua, VM
        lines = []
        for line in cleaned.splitlines():
            if line and not line.startswith(("http", "#", "-", "*")):
                line = line[0].lower() + line[1:]
            lines.append(line)
        cleaned = "\n".join(lines)
    return cleaned.strip()


def score_human_voice(text: str) -> dict[str, Any]:
    """Scores copy from 0 to 100 on human indie-dev authenticity and flags any remaining AI tells."""
    score = 100
    flags: list[str] = []
    lower = text.lower()

    for pattern in BANNED_AI_REPLACEMENTS:
        if re.search(pattern, lower):
            score -= 15
            flags.append(f"Contains AI cliché matching `{pattern}`")

    if HYPE_EMOJI_PATTERN.search(text):
        score -= 15
        flags.append("Contains hype emoji spam (🚀🔥✨)")

    if text.count("—") > 1:
        score -= 10
        flags.append("Too many em-dashes (classic LLM punctuation tell)")

    # Reward concrete technical specifics (numbers, file extensions, git commands)
    if re.search(r"(\d+\s*(?:ms|s|mb|gb|fps|dust|%|hours|tries)|\.lua|\.glb|\.m2|\.vdi|\.json|git\s+reset|webgpu|ed25519)", lower):
        score = min(100, score + 5)
    else:
        score -= 10
        flags.append("Add at least one concrete number, file extension, or command to ground the post")

    return {
        "score": max(0, score),
        "passed": score >= 80 and len(flags) == 0,
        "flags": flags,
    }


# ==============================================================================
# 2. CURATED CREATOR ARCHETYPES & SLOW-ROLLOUT TEMPLATES
# ==============================================================================
DEFAULT_CREATOR_LIST: list[dict[str, Any]] = [
    {
        "id": "cr-mod-01",
        "handle": "@example_balatro_modder",
        "niche": "Balatro & Lua Steamodded Creators",
        "platform": "tiktok",
        "followers_est": "85K",
        "gift_dust": 1000,
        "hook": "Live F9 hotkey that writes single-file Steamodded jokers + git revert on Ctrl+Z",
    },
    {
        "id": "cr-wow-02",
        "handle": "@example_classic_modder",
        "niche": "WoW 1.12 / Classic+ & Custom Mount Modders",
        "platform": "youtube",
        "followers_est": "140K",
        "gift_dust": 1000,
        "hook": "Drag-and-drop .glb to 1.12 .m2 mount porter + browser WebGPU client",
    },
    {
        "id": "cr-ai-03",
        "handle": "@example_homelab_builder",
        "niche": "Local AI, Linux SwayFX & Self-Hosted OS Builders",
        "platform": "reddit",
        "followers_est": "210K",
        "gift_dust": 1000,
        "hook": "Two-VM OrionOS architecture: agent lab has zero keys, isolated boardroom signs approvals",
    },
    {
        "id": "cr-indie-04",
        "handle": "@example_shader_dev",
        "niche": "Three.js / WebGPU / Bevy Game Devs",
        "platform": "reels",
        "followers_est": "62K",
        "gift_dust": 1000,
        "hook": "3 lines of window.orion gives any indie game a mod loader + 60/25/10/5 creator tip splitter",
    },
]

READY_CAMPAIGN_POSTS: list[dict[str, Any]] = [
    {
        "week": 1,
        "platform": "reddit",
        "target_channel": "r/LocalLLaMA",
        "title": "I built a two-VM Linux setup (OrionOS) where my local agent runs in one VM with zero keys, and a second 'boardroom' VM signs off on actions",
        "body": (
            "I've been working on a desktop agent + custom Ubuntu/SwayFX image called OrionOS, and the main thing "
            "that always bugged me about autonomous agents is giving them API keys, git push rights, or wallet access "
            "on the same machine they execute code on.\n\n"
            "So I split it into two VirtualBox/KVM machines over Tailscale:\n"
            "- `orion-lab`: boots straight into the HUD, runs the Python daemon, Ollama router, and rootless Docker "
            "sandboxes. Has zero private keys and a per-user `nftables` firewall that only lets traffic out through a "
            "local Squid allow-list.\n"
            "- `orion-boardroom`: stripped-down console with no browser. Holds the single `ed25519` signing key.\n\n"
            "When the lab wants to spend compute credits (Stardust), merge a patch, or run a game mod script, it sends "
            "a canonical JSON packet hash to the boardroom. I approve one 'Fuel Tank' session receipt for 2 hours and "
            "let it cook.\n\n"
            "Repo and `.vdi` build scripts are open if anyone wants to poke holes in the firewall rules."
        ),
        "media_hint": "os/image/out/vm/orionos-1.0.0.vdi architecture diagram + terminal verification output",
    },
    {
        "week": 2,
        "platform": "tiktok",
        "target_channel": "@orion_forge",
        "title": "18s Demo: Live Voice-to-Addon in WoW 1.12 + 1-Click Git Revert",
        "body": (
            "bound a hotkey over 1.12 that writes and commits lua addons into their own git repo so if it hallucinates "
            "i just hit revert (`git reset --hard HEAD~1`). took 4 tries to stop it from writing xml files lol. "
            "costs about 3 cents of stardust compute. link in bio if you want the adapter."
        ),
        "script_beats": [
            "0-3s: Split screen WoW 1.12 on left, Orion cosmic orb on right.",
            "3-9s: Speak into mic: 'make a small bar that tracks my five-second mana rule and flashes purple'.",
            "9-14s: Floating kinetic text appears over Orion, commits OrionForge_ManaTick.lua, type /reload.",
            "14-18s: Bar works live in-game; click Revert button to show 1-second undo.",
        ],
        "media_hint": "1080x1920 vertical capture of Orion HUD + WoW 1.12 windowed borderless",
    },
    {
        "week": 3,
        "platform": "reels",
        "target_channel": "@orion_forge",
        "title": "Dragging a .glb model into Orion and riding it as a 1.12 mount 30s later",
        "body": (
            "dropped a rigged .glb into the model porter and it rebuilt the 1.12 .m2 bone weights and textures automatically. "
            "whenever someone forks a mount or mod in forge, the original artist gets 10% and the base modder gets 25% of "
            "the stardust split automatically."
        ),
        "media_hint": "1080x1920 vertical capture of Model Porter .glb drop -> WebGPU preview -> in-game mount",
    },
    {
        "week": 4,
        "platform": "youtube",
        "target_channel": "OrionOS Devlog",
        "title": "How Orion Forge Mods Games Without Touching Binaries (Clean-Room Adapters + Git Sandbox)",
        "body": (
            "Walkthrough of the 48-hour Forge loop inside OrionOS 1.0:\n"
            "00:00 - Why DLL injection breaks games and triggers anti-cheat\n"
            "00:48 - Clean-room adapters (`game_context.md` + `FrameXML.log` error watcher)\n"
            "02:15 - Live WoW 1.12 & Balatro modding with one Fuel Tank session receipt\n"
            "03:30 - How `forge.json` splits Stardust (60% remixer / 25% original / 10% asset / 5% burn)"
        ),
        "media_hint": "4-minute 1440p desktop recording with raw mic audio",
    },
    {
        "week": 5,
        "platform": "discord",
        "target_channel": "#announcements",
        "title": "Orion Caretaker Update — Herald Nightly Playtest & Workshop Royalty Splits",
        "body": (
            "*[Orion - Automated Realm Caretaker]*\n"
            "**Nightly Herald Report + Forge Build 1.0.4**\n"
            "- Herald finished the 03:00 Elwynn pathfinding run (0 crashes, 14 quests verified in headless WebGPU).\n"
            "- Model Porter now fixes inverted normals on `.glb` mounts before writing `.m2` bones.\n"
            "- 3 new community addons are live in the Workshop. If you fork someone's addon in Forge, keep their "
            "`parent_mod_id` in `forge.json` so their 25% Stardust royalty routes automatically."
        ),
        "media_hint": "Webhook embed with Herald status badge",
    },
]


class OutreachEngine:
    """Manages human-toned social media drafts, creator outreach lists, and Approval Queue staging."""

    def __init__(self, approvals: ApprovalQueue, storage_dir: Path) -> None:
        self.approvals = approvals
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.creators_file = self.storage_dir / "outreach_creators.json"
        self.schedule_file = self.storage_dir / "outreach_scheduled.json"
        self._ensure_creators()

    def _ensure_creators(self) -> None:
        if not self.creators_file.exists():
            self.creators_file.write_text(json.dumps(DEFAULT_CREATOR_LIST, indent=2), encoding="utf-8")

    def list_creators(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.creators_file.read_text(encoding="utf-8"))
        except Exception:
            return list(DEFAULT_CREATOR_LIST)

    def add_creator(
        self,
        handle: str,
        niche: str,
        platform: str,
        followers_est: str = "10K+",
        gift_dust: int = 1000,
        hook: str = "",
    ) -> dict[str, Any]:
        creators = self.list_creators()
        entry = {
            "id": f"cr-{uuid.uuid4().hex[:6]}",
            "handle": handle,
            "niche": niche,
            "platform": platform.lower(),
            "followers_est": followers_est,
            "gift_dust": gift_dust,
            "hook": hook or "Automatic 25% Stardust royalty whenever players fork your mods in Orion Forge",
        }
        creators.append(entry)
        self.creators_file.write_text(json.dumps(creators, indent=2), encoding="utf-8")
        return entry

    def draft_post(
        self,
        platform: str,
        target_channel: str,
        title: str,
        body: str,
        week: int = 1,
        media_hint: str = "",
        script_beats: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Humanizes a social post, scores its voice authenticity, and places it in the Approval Queue."""
        casual = platform.lower() in ("tiktok", "reels", "shorts")
        clean_body = humanize_copy(body, casual_lowercase=casual)
        voice_check = score_human_voice(clean_body)

        item = self.approvals.add(
            kind="social_post",
            platform=platform.lower(),
            target_channel=target_channel,
            title=title,
            draft_reply=clean_body,
            week=week,
            media_hint=media_hint,
            script_beats=script_beats or [],
            voice_score=voice_check["score"],
            voice_flags=voice_check["flags"],
            disclosure="Orion (AI Caretaker)" if platform.lower() == "discord" else "Human CreatorApproved",
        )
        return item

    def draft_creator_outreach(self, creator_id: str, specific_video_note: str = "your recent mod showcase") -> dict[str, Any]:
        """Drafts a zero-fluff creator DM/email and queues it in the Approval Queue (`[no-messaging]`)."""
        creators = {c["id"]: c for c in self.list_creators()}
        cr = creators.get(creator_id)
        if not cr:
            raise ValueError(f"Creator {creator_id} not found")

        raw_body = (
            f"hey {cr['handle']} - watched {specific_video_note}.\n\n"
            f"built an open-source overlay called Orion Forge ({cr['hook']}). every mod carries a `forge.json` "
            f"manifest with the original author's ID, so whenever someone remixes or tips a mod built on top of yours, "
            f"the ledger automatically routes 25% of the Stardust credits straight to you.\n\n"
            f"loaded an account with {cr['gift_dust']} free Stardust for you if you ever want to test it on your own "
            f"projects - no strings attached, never have to post about it unless you actually like it. want me to send "
            f"the invite link?"
        )
        clean_body = humanize_copy(raw_body, casual_lowercase=True)
        voice_check = score_human_voice(clean_body)

        return self.approvals.add(
            kind="creator_outreach",
            platform=cr["platform"],
            target_channel=cr["handle"],
            creator_id=cr["id"],
            niche=cr["niche"],
            gift_dust=cr["gift_dust"],
            title=f"quick tool for your mods ({cr['handle']})",
            draft_reply=clean_body,
            voice_score=voice_check["score"],
            voice_flags=voice_check["flags"],
        )

    def seed_campaign_queue(self) -> dict[str, Any]:
        """Queues the 6-week slow rollout posts and initial creator outreach drafts into ApprovalQueue."""
        existing_titles = {i.get("title") for i in self.approvals.pending()}
        queued_posts = []
        for p in READY_CAMPAIGN_POSTS:
            if p["title"] not in existing_titles:
                queued_posts.append(
                    self.draft_post(
                        platform=p["platform"],
                        target_channel=p["target_channel"],
                        title=p["title"],
                        body=p["body"],
                        week=p["week"],
                        media_hint=p.get("media_hint", ""),
                        script_beats=p.get("script_beats"),
                    )
                )

        queued_creators = []
        for cr in self.list_creators():
            expected_title = f"quick tool for your mods ({cr['handle']})"
            if expected_title not in existing_titles:
                queued_creators.append(self.draft_creator_outreach(cr["id"]))

        return {
            "queued_posts": len(queued_posts),
            "queued_creator_outreach": len(queued_creators),
            "total_pending_approvals": len(self.approvals.pending()),
        }

    def commit_approved_item(self, item_id: str, base_schedule_ts: Optional[float] = None) -> dict[str, Any]:
        """Marks a queued post as approved by the human owner and assigns a jittered publish slot."""
        claimed = self.approvals.claim(item_id, to_status="approved_scheduled")
        if not claimed:
            raise ValueError(f"Approval item {item_id} is not pending")

        # Add randomized human pacing jitter (45s to 180s offset) to prevent rate-limit / bot flags
        jitter_seconds = random.randint(45, 180)
        scheduled_for = (base_schedule_ts or time.time()) + jitter_seconds

        updated = self.approvals.update(
            item_id,
            status="approved_scheduled",
            scheduled_for=scheduled_for,
            jitter_seconds=jitter_seconds,
            approved_by="human_owner_enter_key",
        )

        # Persist to scheduled manifest for the publisher runner
        scheduled_list: list[dict[str, Any]] = []
        if self.schedule_file.exists():
            try:
                scheduled_list = json.loads(self.schedule_file.read_text(encoding="utf-8"))
            except Exception:
                scheduled_list = []
        scheduled_list.append(updated)
        self.schedule_file.write_text(json.dumps(scheduled_list, indent=2, default=str), encoding="utf-8")
        return updated or claimed

    def snapshot(self) -> dict[str, Any]:
        pending = [
            i for i in self.approvals.pending() if i.get("kind") in ("social_post", "creator_outreach")
        ]
        scheduled: list[dict[str, Any]] = []
        if self.schedule_file.exists():
            try:
                scheduled = json.loads(self.schedule_file.read_text(encoding="utf-8"))
            except Exception:
                scheduled = []
        return {
            "pending_outreach_approvals": pending,
            "scheduled_items": scheduled[-30:],
            "creators": self.list_creators(),
        }


def make_router(get_daemon):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse
    from config import settings

    router = APIRouter()

    def _engine() -> OutreachEngine:
        d = get_daemon()
        return OutreachEngine(d.approvals, Path(settings.data_dir))

    @router.get("/outreach/state")
    async def outreach_state() -> dict[str, Any]:
        return _engine().snapshot()

    @router.post("/outreach/seed")
    async def outreach_seed() -> dict[str, Any]:
        return _engine().seed_campaign_queue()

    @router.post("/outreach/draft")
    async def outreach_draft(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine().draft_post(
                platform=str(body.get("platform") or "reddit"),
                target_channel=str(body.get("target_channel") or "r/LocalLLaMA"),
                title=str(body["title"]),
                body=str(body["body"]),
                week=int(body.get("week") or 1),
                media_hint=str(body.get("media_hint") or ""),
                script_beats=body.get("script_beats"),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/outreach/commit")
    async def outreach_commit(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine().commit_approved_item(
                item_id=str(body["item_id"]),
                base_schedule_ts=body.get("base_schedule_ts"),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return router
