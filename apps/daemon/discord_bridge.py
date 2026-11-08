"""Autonomous Discord Prompt Detector (V2).

Passively triages community channels (#general, #realm-pitches, #bug-reports, #support by
default) on the same gateway connection as DiscordGameBot — no second login.

  Tier 1  local Ollama SLM, zero-cost binary classification:
          {needs_admin_approval: bool, category: prompt|realm_pitch|bug|question}
          (regex heuristic fallback when Ollama is offline)
  Tier 2  frontier model (OpenAI by default) drafts a reply in the admin's voice using
          the top-N past approved edits from LanceDB `user_alignment`.

Nothing is ever posted automatically: each draft becomes an APPROVAL_REQUIRED card in the
Planetary Context Window and is only sent after [✓ Approve] or [✎ Edit & Send].
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from alignment_drafter import ResponseDrafter
from config import settings

log = logging.getLogger("orion.discord_triage")

CATEGORIES = ("prompt", "realm_pitch", "bug", "question")

TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "needs_admin_approval": {"type": "boolean"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["needs_admin_approval", "category"],
}

TRIAGE_PROMPT = """You triage messages in a game community Discord for the server admin.
needs_admin_approval = true ONLY if the message is addressed to the admin/mods or clearly expects an
official response: a request to run/create something (prompt), a realm or content pitch (realm_pitch),
a bug report (bug), or a question about the game/server that members cannot answer themselves (question).
Banter, reactions, greetings, memes and member-to-member chat -> false.
Respond with JSON only.

Channel: #{channel}
Author: {author}
Message:
{content}"""

_H_BUG = re.compile(r"\b(bug|crash|glitch|freeze|broken|stuck|error|exception|desync|fps drop|can'?t (?:log|load|join))\b", re.I)
_H_PITCH = re.compile(r"\b(pitch|idea|proposal|concept|what if we|realm|i(?:'d| would) love to see|suggestion)\b", re.I)
_H_PROMPT = re.compile(r"(^[!/]\w+|\b(can you (?:make|create|generate|build|add|run)|please (?:make|create|add|run)|generate|/imagine)\b)", re.I)
_H_QUESTION = re.compile(r"(\?\s*$|^\s*(how|what|when|where|why|who|is|are|can|does|do|will)\b)", re.I)
_H_ADDRESSED = re.compile(r"\b(admin|mods?|dev(?:s|elopers?)?|staff|team)\b", re.I)


def _norm_channel(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", (name or "").lower())


def heuristic_triage(content: str, channel: str) -> dict[str, Any]:
    text = content.strip()
    ch = _norm_channel(channel)
    if len(text) < 8 or (len(text.split()) < 4 and "?" not in text):
        return {"needs_admin_approval": False, "category": "question", "confidence": 0.9, "reason": "too short"}
    if _H_BUG.search(text) or "bug" in ch:
        cat, need = "bug", True
    elif _H_PITCH.search(text) or "pitch" in ch:
        cat, need = "realm_pitch", True
    elif _H_PROMPT.search(text):
        cat, need = "prompt", True
    elif _H_QUESTION.search(text):
        cat = "question"
        need = "support" in ch or bool(_H_ADDRESSED.search(text))
    else:
        cat, need = "question", False
    return {"needs_admin_approval": need, "category": cat, "confidence": 0.55, "reason": "heuristic"}


class DiscordPromptDetector:
    def __init__(
        self,
        orchestrator: Any,
        drafter: ResponseDrafter,
        on_approval_required: Callable[[dict[str, Any]], Awaitable[None]],
        channel_names: tuple[str, ...] = settings.discord_triage_channels,
        channel_ids: tuple[int, ...] = settings.discord_triage_channel_ids,
    ) -> None:
        self.orchestrator = orchestrator
        self.drafter = drafter
        self.on_approval_required = on_approval_required
        self.channel_names = {_norm_channel(c) for c in channel_names}
        self.channel_ids = set(channel_ids)
        self.client: Any = None  # discord.Client, attached by DiscordGameBot
        self.enabled = settings.discord_triage_enabled
        self.stats = {"seen": 0, "flagged": 0, "drafted": 0}

    def attach(self, client: Any) -> None:
        self.client = client

    def watches(self, message: Any) -> bool:
        if getattr(message, "guild", None) is None:  # DMs belong to the Comms hub, not triage
            return False
        if message.channel.id in self.channel_ids:
            return True
        name = _norm_channel(getattr(message.channel, "name", ""))
        return any(name == c or name.endswith(c) for c in self.channel_names)

    # ------------------------------------------------------------ hook
    async def on_discord_message(self, message: Any) -> None:
        """Registered as a DiscordGameBot message hook."""
        if not self.enabled or getattr(message.author, "bot", False) or not message.content:
            return
        if not self.watches(message):
            return
        self.stats["seen"] += 1
        channel = getattr(message.channel, "name", str(message.channel.id))
        author = getattr(message.author, "display_name", str(message.author))
        content = message.content[:4000]

        verdict = await self.classify(content, channel, author)
        if not verdict["needs_admin_approval"]:
            return
        self.stats["flagged"] += 1
        category = verdict["category"] if verdict["category"] in CATEGORIES else "question"

        context: list[str] = []
        try:
            async for prev in message.channel.history(limit=6, before=message):
                context.append(f"{getattr(prev.author, 'display_name', prev.author)}: {prev.content[:300]}")
            context.reverse()
        except Exception:
            pass

        drafted = await self.drafter.draft(
            platform="discord_server", category=category, channel=f"#{channel}",
            author=author, text=content, thread_context=context,
        )
        self.stats["drafted"] += 1
        await self.on_approval_required({
            "kind": "discord_prompt",
            "platform": "discord_server",
            "category": category,
            "channel": f"#{channel}",
            "guild": getattr(message.guild, "name", ""),
            "channel_id": message.channel.id,
            "message_id": message.id,
            "jump_url": getattr(message, "jump_url", ""),
            "author": author,
            "original": content,
            "draft": drafted["draft"],
            "system": drafted["system"],
            "user_prompt": drafted["user_prompt"],
            "triage": {**verdict, "category": category},
            "models": {"tier1": verdict.get("model"), "tier2": drafted["model"]},
            "examples_used": drafted["examples_used"],
        })

    async def classify(self, content: str, channel: str, author: str) -> dict[str, Any]:
        try:
            raw, model = await self.orchestrator.ollama_json(
                TRIAGE_PROMPT.format(channel=channel, author=author, content=content[:2000]),
                TRIAGE_SCHEMA, model=settings.triage_model or None, timeout=20.0,
            )
            verdict = {
                "needs_admin_approval": bool(raw.get("needs_admin_approval")),
                "category": raw.get("category") if raw.get("category") in CATEGORIES else "question",
                "confidence": float(raw.get("confidence") or 0.0),
                "reason": str(raw.get("reason") or "")[:200],
                "model": model,
            }
            return verdict
        except Exception as exc:
            log.debug("Tier-1 triage unavailable (%s); using heuristic", exc)
            return {**heuristic_triage(content, channel), "model": "heuristic"}

    # ------------------------------------------------------------ send
    async def send_reply(self, item: dict[str, Any], text: str) -> bool:
        if not self.client or not text.strip():
            return False
        try:
            channel = self.client.get_channel(item["channel_id"]) or await self.client.fetch_channel(item["channel_id"])
            target = None
            try:
                target = await channel.fetch_message(item["message_id"])
            except Exception:
                target = None
            chunks = [text[i:i + 1990] for i in range(0, len(text), 1990)]
            for idx, chunk in enumerate(chunks):
                if idx == 0 and target is not None:
                    await target.reply(chunk, mention_author=False)
                else:
                    await channel.send(chunk)
            return True
        except Exception as exc:
            log.error("Failed to send approved Discord reply: %s", exc)
            return False
