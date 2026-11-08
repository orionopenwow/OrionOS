"""Proactive Context Observer for Orion.

Monitors active desktop context (code files in IDEs, browser articles/docs,
game engines, terminal sessions) and autonomously generates actionable
insights, auto-summaries, and rapid-response suggestions.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.proactive")

_CODE_EXTENSIONS = re.compile(
    r"\.(py|ts|tsx|js|jsx|rs|go|cpp|c|cs|java|html|css|json|yaml|toml|sql|shader|gd|lua)\b",
    re.I,
)
_IDE_NAMES = re.compile(
    r"\b(visual studio code|vs ?code|\bcode\b|cursor|pycharm|intellij|webstorm|rider|clion|goland|neovim|\bvim\b|sublime|zed)\b",
    re.I,
)
_BROWSER_NAMES = re.compile(
    r"\b(chrome|firefox|brave|edge|safari|arc|opera)\b",
    re.I,
)
_GAME_DEV_NAMES = re.compile(
    r"\b(unity|unreal engine|godot|blender|maya|aseprite|fmod|wwise)\b",
    re.I,
)
_DOCS_NAMES = re.compile(
    r"\b(github|stackoverflow|arxiv|wikipedia|docs|documentation|api|mdn|medium|substack|dev\.to)\b",
    re.I,
)


class ProactiveObserver:
    def __init__(
        self,
        analyze_fn: Callable[[str, str], Awaitable[dict[str, Any]]],
        emit_fn: Callable[[dict[str, Any]], Awaitable[None]],
        cooldown_s: float = 60.0,
    ) -> None:
        self.analyze_fn = analyze_fn
        self.emit_fn = emit_fn
        self.cooldown_s = cooldown_s
        self.enabled = True
        self._last_trigger_time = 0.0
        self._last_context_signature = ""

    def classify_context(self, window_title: str, text: str) -> str:
        """Classifies the environment the user is currently focused on."""
        title_lower = window_title.lower()

        if _IDE_NAMES.search(title_lower) or _CODE_EXTENSIONS.search(title_lower):
            return "code"
        if _GAME_DEV_NAMES.search(title_lower):
            return "game_dev"
        if _DOCS_NAMES.search(title_lower):
            return "browser_docs"
        if _BROWSER_NAMES.search(title_lower):
            return "browser_article"
        return "general"

    async def inspect_context(self, window_title: str, text: str, source: str = "screen") -> None:
        """Inspects current active window and text to offer proactive insights if appropriate."""
        if not self.enabled:
            return

        now = time.monotonic()
        if now - self._last_trigger_time < self.cooldown_s:
            return

        text = text.strip()
        if len(text) < 40:
            return

        context_type = self.classify_context(window_title, text)
        # Avoid re-triggering for identical window and short hash
        signature = f"{window_title}::{len(text)}::{hash(text[:300])}"
        if signature == self._last_context_signature:
            return

        self._last_context_signature = signature
        self._last_trigger_time = now

        log.info("Proactive observer triggered for context '%s' (%s)", context_type, window_title)

        try:
            # Generate tailored prompt based on context
            if context_type == "code":
                instruction = (
                    "The user is coding in an IDE. Inspect the visible code/error. "
                    "Offer ONE crisp proactive insight: spot an edge case, suggest a concise refactoring, "
                    "or give a 1-line explanation of the active function. Be concise and practical."
                )
            elif context_type == "game_dev":
                instruction = (
                    "The user is working in a game development / 3D tool. "
                    "Notice visible assets, shaders, hierarchy or logs. "
                    "Provide a proactive tip or checklist item for game performance, architecture, or bug prevention."
                )
            elif context_type in ("browser_docs", "browser_article"):
                instruction = (
                    "The user is reading an article or documentation. "
                    "Extract the top 2-3 key takeaways as bullet points and provide an auto-summary."
                )
            else:
                instruction = (
                    "Notice what the user is working on and offer a helpful, non-intrusive proactive summary."
                )

            analysis = await self.analyze_fn(f"Active Window: {window_title}\n\nVisible Text:\n{text[:2500]}", instruction)

            summary = analysis.get("summary", "")
            if not summary or "offline" in str(analysis.get("tier", "")):
                return

            actions = analysis.get("actions", [])

            insight_event = {
                "event": "PROACTIVE_INSIGHT",
                "id": f"insight-{uuid.uuid4().hex[:8]}",
                "timestamp": time.time(),
                "context_type": context_type,
                "window_title": window_title,
                "title": f"Insight: {window_title[:35]}",
                "summary": summary,
                "actions": actions,
            }

            await self.emit_fn(insight_event)
        except Exception as exc:
            log.warning("Proactive observer failed: %s", exc)
