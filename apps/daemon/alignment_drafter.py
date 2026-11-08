"""Tier-2 response drafting that clones the user's voice.

Every draft pulls the top-N most similar past approved/edited replies from the LanceDB
`user_alignment` table and shows them to the frontier model as few-shot examples
(Speed 1: instant few-shot memory). The *clean* system + user prompt (without the
few-shot block) is what gets stored when the admin decides, so the nightly fine-tuning
export (Speed 2) contains exactly the triples the model should learn.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from config import settings

log = logging.getLogger("orion.drafter")

_PLATFORM_ROLE = {
    "discord_server": (
        "You are drafting replies as the admin of the '{game}' Discord community. "
        "You answer member prompts, realm pitches, bug reports and questions."
    ),
    "discord": "You are drafting a personal Discord direct-message reply as the user.",
    "telegram": "You are drafting a personal Telegram reply as the user.",
    "messenger": "You are drafting a personal Facebook Messenger reply as the user.",
}

_CATEGORY_HINT = {
    "prompt": "The member is asking the admin to run or create something. Confirm scope, set expectations.",
    "realm_pitch": "The member is pitching a realm / content idea. Respond with genuine, specific feedback and a clear next step.",
    "bug": "The member is reporting a bug. Thank them, ask for any missing repro detail, and state what happens next.",
    "question": "The member asked a question. Answer directly; if unsure, say what you will check.",
    "dm": "Reply naturally to this personal message.",
}

BASE_RULES = (
    "Write ONLY the message text that will be sent — no preamble, no quotes, no sign-off unless the user's "
    "past replies use one. Match the user's tone, length, punctuation, emoji habits and decision-making "
    "exactly as shown in their past approved replies. Never invent facts, dates, or promises."
)


class ResponseDrafter:
    def __init__(self, orchestrator: Any, memory: Any) -> None:
        self.orchestrator = orchestrator
        self.memory = memory

    async def _rules(self, text: str) -> list[str]:
        try:
            return await self.memory.rules_for(text)
        except Exception:
            return []

    @staticmethod
    def system_prompt(platform: str, category: str) -> str:
        role = _PLATFORM_ROLE.get(platform, _PLATFORM_ROLE["discord"]).format(game=settings.discord_game_name)
        hint = _CATEGORY_HINT.get(category, _CATEGORY_HINT["dm"])
        return f"{role}\n{hint}\n{BASE_RULES}"

    @staticmethod
    def user_prompt(*, platform: str, channel: str, author: str, text: str,
                    thread_context: Optional[list[str]] = None) -> str:
        parts = [f"Platform: {platform}", f"Channel: {channel}", f"From: {author}"]
        if thread_context:
            parts.append("Recent conversation (oldest first):\n" + "\n".join(thread_context[-8:]))
        parts.append(f"Message to reply to:\n{text.strip()[:4000]}")
        return "\n".join(parts)

    async def draft(self, *, platform: str, category: str, channel: str, author: str, text: str,
                    thread_context: Optional[list[str]] = None) -> dict[str, Any]:
        system = self.system_prompt(platform, category)
        user = self.user_prompt(platform=platform, channel=channel, author=author, text=text,
                                thread_context=thread_context)
        examples: list[dict] = []
        try:
            examples = await self.memory.alignment_examples(text, top_k=settings.alignment_examples, category=category)
        except Exception as exc:
            log.debug("alignment lookup failed: %s", exc)

        few_shot = ""
        if examples:
            blocks = []
            for i, ex in enumerate(examples, 1):
                tag = "edited by user" if ex["decision"] == "edited" else "approved as-is"
                blocks.append(f"### Example {i} ({ex['category']}, {tag})\n{ex['user_prompt'][:1200]}\n"
                              f"--> User's final reply:\n{ex['final_response'][:1200]}")
            few_shot = ("\n\nThe user's own past replies to similar messages. Clone this voice and judgement:\n\n"
                        + "\n\n".join(blocks))

        rules = await self._rules(text)
        rules_block = ("\n\nThe user's standing rules (always follow):\n" + "\n".join(f"- {r}" for r in rules)
                       if rules else "")
        try:
            draft, model = await self.orchestrator.frontier_text(system + rules_block + few_shot, user, max_tokens=700)
            draft = draft.strip().strip('"').strip()
        except Exception as exc:
            log.warning("Draft generation failed: %s", exc)
            draft, model = "", None
        return {
            "draft": draft,
            "model": model,
            "system": system,
            "user_prompt": user,
            "examples_used": len(examples),
        }

    async def record(self, *, platform: str, category: str, system: str, user_prompt: str,
                     draft: str, final: str, decision: str) -> Optional[dict[str, Any]]:
        """decision: approved | edited | rejected. 'approved' is auto-upgraded to 'edited' if text changed."""
        if decision == "approved" and final.strip() != draft.strip():
            decision = "edited"
        try:
            return await self.memory.record_alignment(
                platform=platform, category=category, system=system, user_prompt=user_prompt,
                draft=draft, final_response=final, decision=decision,
            )
        except Exception as exc:
            log.warning("Could not record alignment example: %s", exc)
            return None


# ---------------------------------------------------------------------------- quick-reply suggestions
SUGGEST_SYSTEM = (
    "You suggest quick replies the user could send in a personal chat. Write EXACTLY as the user writes: copy "
    "their casing, length, punctuation, slang and emoji habits from their own messages shown below. Give 3 "
    "different options with different intents (e.g. agree/confirm, ask a short follow-up, decline or defer). "
    "Each under 120 characters. Never invent facts, times or promises the user didn't make. "
    'Respond with JSON only: {"replies": ["...", "...", "..."]}'
)

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def style_profile(samples: list[str]) -> dict[str, Any]:
    """Cheap fingerprint of how the user types (used offline and to steer the model)."""
    texts = [s.strip() for s in samples if s and s.strip()]
    if not texts:
        return {"lower": False, "end_punct": True, "emoji": False, "avg_len": 40}
    lower = sum(1 for t in texts if t == t.lower()) / len(texts)
    punct = sum(1 for t in texts if t[-1] in ".!?") / len(texts)
    emoji = sum(1 for t in texts if _EMOJI.search(t)) / len(texts)
    return {"lower": lower > 0.6, "end_punct": punct > 0.5, "emoji": emoji > 0.3,
            "avg_len": int(sum(len(t) for t in texts) / len(texts))}


def offline_suggestions(text: str, profile: dict[str, Any]) -> list[str]:
    """Model-free fallback: intent-aware stock replies, re-cased to match the user's style."""
    t = text.lower()
    if "?" in t and any(w in t for w in ("can you", "could you", "are you", "do you", "will you", "want to", "wanna")):
        base = ["Yeah, I can do that", "What time works for you?", "Can't right now, later?"]
    elif "?" in t:
        base = ["Good question, let me check", "Not sure yet, I'll get back to you", "Yeah"]
    elif any(w in t for w in ("thanks", "thank you", "ty", "appreciate")):
        base = ["Anytime", "Of course!", "Glad it helped"]
    else:
        base = ["Sounds good", "Got it, thanks", "Nice, tell me more"]
    out = []
    for b in base:
        s = b.lower() if profile.get("lower") else b
        if not profile.get("end_punct"):
            s = s.rstrip(".!")
        elif s[-1] not in ".!?":
            s += "!" if "!" in s else ""
        out.append(s)
    return out


async def suggest_replies(drafter: "ResponseDrafter", *, platform: str, author: str, text: str,
                          thread_context: list[str], style_samples: list[str]) -> dict[str, Any]:
    profile = style_profile(style_samples)
    examples: list[dict] = []
    try:
        examples = await drafter.memory.alignment_examples(text, top_k=3)
    except Exception:
        pass
    rules = await drafter._rules(text)
    parts = [f"Platform: {platform}. Message from {author}:\n{text.strip()[:1500]}"]
    if thread_context:
        parts.append("Recent conversation:\n" + "\n".join(thread_context[-6:]))
    if style_samples:
        parts.append("How the user writes (their own recent messages):\n" + "\n".join(f"- {s[:160]}" for s in style_samples[-15:]))
    if examples:
        parts.append("Replies the user approved before:\n" + "\n".join(f"- {e['final_response'][:200]}" for e in examples))
    system = SUGGEST_SYSTEM + ("\nAlso follow: " + "; ".join(rules[:8]) if rules else "")
    try:
        raw, model = await drafter.orchestrator.frontier_text(system, "\n\n".join(parts), json_mode=True,
                                                              max_tokens=300, temperature=0.7, timeout=25.0)
        start, end = raw.find("{"), raw.rfind("}")
        replies = json.loads(raw[start:end + 1]).get("replies", []) if start >= 0 else []
        replies = [str(r).strip().strip('"')[:160] for r in replies if str(r).strip()][:3]
        if replies:
            return {"replies": replies, "model": model, "profile": profile}
    except Exception as exc:
        log.debug("suggestion model unavailable: %s", exc)
    return {"replies": offline_suggestions(text, profile), "model": "offline-style", "profile": profile}
