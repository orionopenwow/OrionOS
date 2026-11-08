"""Discord Game Bug Tracker & Interactive Support Bot for Orion.

Collects bug reports for games, performs in-line Gemini/Orchestrator search
diagnoses, captures and embeds desktop/game screengrabs, and equips Discord
messages with interactive action buttons:
  - [📋 Paste / Send Fix]
  - [💬 Give Response]
  - [✨ Gemini Search]
  - [✅ Mark Resolved]
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.discord_game_bot")

_BUG_KEYWORDS = re.compile(
    r"\b(bug|crash|glitch|freeze|error|exception|broken|stuck|fps drop|render issue|physics|desync|reproduce)\b|^!(bug|report)\b",
    re.I,
)


def _safe_pred(pred: Callable[[Any], bool], message: Any) -> bool:
    try:
        return bool(pred(message))
    except Exception:
        return False


class DiscordGameBot:
    def __init__(
        self,
        token: str,
        channel_ids: tuple[int, ...],
        game_name: str = "My Game",
        on_bug_received: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
        gemini_search_fn: Optional[Callable[[str], Awaitable[str]]] = None,
        screengrab_fn: Optional[Callable[[], Optional[bytes]]] = None,
    ) -> None:
        self.token = token.strip()
        self.channel_ids = set(channel_ids)
        self.game_name = game_name
        self.on_bug_received = on_bug_received
        self.gemini_search_fn = gemini_search_fn
        self.screengrab_fn = screengrab_fn

        self.bugs: dict[str, dict[str, Any]] = {}
        # V2: extra listeners sharing this gateway connection (prompt triage, DM comms hub).
        self.message_hooks: list[Callable[[Any], Awaitable[None]]] = []
        self.ready_hooks: list[Callable[[Any], None]] = []
        # Messages for which any predicate is true are left to the hooks only (no automatic public reply).
        self.skip_predicates: list[Callable[[Any], bool]] = []
        self._client: Any = None
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        if not self.token:
            log.info("Discord game bot disabled (no DISCORD_TOKEN)")
            return False
        try:
            import discord
        except ImportError:
            log.warning("discord.py not installed; Discord game bot cannot run")
            return False

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        bot = self

        class GiveResponseModal(discord.ui.Modal, title=f"Response for Bug"):
            def __init__(self, bug_id: str, original_msg: discord.Message) -> None:
                super().__init__(title=f"Reply to #{bug_id}")
                self.bug_id = bug_id
                self.original_msg = original_msg
                self.response_input = discord.ui.TextInput(
                    label="Developer Response",
                    style=discord.TextStyle.paragraph,
                    placeholder="Describe fix, status update, or workaround for player...",
                    required=True,
                    max_length=1500,
                )
                self.add_item(self.response_input)

            async def on_submit(self, interaction: discord.Interaction) -> None:
                resp_text = self.response_input.value
                reply_embed = discord.Embed(
                    title=f"🛠️ Developer Response • #{self.bug_id}",
                    description=resp_text,
                    color=0x3DFFA8,
                )
                reply_embed.set_footer(text=f"Sent by {interaction.user.display_name} via Orion")
                await interaction.response.send_message(embed=reply_embed)
                if bot.bug_exists(self.bug_id):
                    bot.bugs[self.bug_id]["developer_response"] = resp_text

        class BugActionView(discord.ui.View):
            def __init__(self, bug_id: str, fix_snippet: str = "") -> None:
                super().__init__(timeout=None)
                self.bug_id = bug_id
                self.fix_snippet = fix_snippet

            @discord.ui.button(label="📋 Paste Fix / Snippet", style=discord.ButtonStyle.secondary, custom_id="paste_fix")
            async def paste_fix_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
                bug = bot.bugs.get(self.bug_id, {})
                snippet = bug.get("fix_snippet") or self.fix_snippet or "No fix snippet generated yet."
                await interaction.response.send_message(
                    f"**Ready-to-Paste Fix for `#{self.bug_id}`:**\n```python\n{snippet}\n```",
                    ephemeral=False,
                )

            @discord.ui.button(label="💬 Give Response", style=discord.ButtonStyle.primary, custom_id="give_response")
            async def give_response_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
                modal = GiveResponseModal(self.bug_id, interaction.message)
                await interaction.response.send_modal(modal)

            @discord.ui.button(label="✨ Gemini Search", style=discord.ButtonStyle.success, custom_id="gemini_search")
            async def gemini_search_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
                await interaction.response.defer()
                bug = bot.bugs.get(self.bug_id, {})
                search_query = bug.get("description", "")
                if bot.gemini_search_fn and search_query:
                    search_res = await bot.gemini_search_fn(search_query)
                    embed = discord.Embed(
                        title=f"✨ Gemini Search Diagnosis • #{self.bug_id}",
                        description=search_res[:4000],
                        color=0x00E5FF,
                    )
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send("Gemini Search unavailable or query empty.", ephemeral=True)

            @discord.ui.button(label="✅ Mark Resolved", style=discord.ButtonStyle.secondary, custom_id="mark_resolved")
            async def mark_resolved_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
                if bot.bug_exists(self.bug_id):
                    bot.bugs[self.bug_id]["status"] = "resolved"
                button.disabled = True
                button.label = "Resolved ✅"
                await interaction.response.edit_message(view=self)
                await interaction.followup.send(f"Bug `#{self.bug_id}` has been marked as **Resolved**!")

        @client.event
        async def on_ready() -> None:
            log.info("Discord Game Bot online as %s (monitoring %s)", client.user, bot.game_name)
            for hook in bot.ready_hooks:
                try:
                    hook(client)
                except Exception:
                    log.exception("Discord ready hook failed")

        def _run_hooks(message: "discord.Message") -> None:
            for hook in bot.message_hooks:
                task = asyncio.create_task(hook(message))
                task.add_done_callback(
                    lambda t: log.error("Discord message hook failed: %s", t.exception())
                    if not t.cancelled() and t.exception() else None
                )

        @client.event
        async def on_message(message: "discord.Message") -> None:
            if message.author == client.user or not message.content:
                return
            _run_hooks(message)
            if message.guild is None:
                return  # DMs are routed to the Comms hub, never treated as public bug reports
            if any(_safe_pred(p, message) for p in bot.skip_predicates):
                return  # handled by the approval-queue triage instead

            channel_name = getattr(message.channel, "name", "")
            is_bug_channel = (
                message.channel.id in bot.channel_ids
                or "bug" in channel_name.lower()
                or "report" in channel_name.lower()
                or "support" in channel_name.lower()
            )

            is_bug_content = bool(_BUG_KEYWORDS.search(message.content))
            if not (is_bug_channel or is_bug_content):
                return

            # Parse and register bug
            bug_id = f"BUG-{uuid.uuid4().hex[:6].upper()}"
            content = message.content.strip()
            author = message.author.display_name

            log.info("Collecting bug report %s from %s in #%s", bug_id, author, channel_name)

            # Perform in-line Gemini search diagnosis if available
            diagnosis = "Analyzing bug with Gemini..."
            fix_snippet = "# Reviewing telemetry and game logs"
            if bot.gemini_search_fn:
                try:
                    diagnosis = await bot.gemini_search_fn(f"Game bug diagnosis for '{bot.game_name}': {content}")
                    # Extract code block if present
                    code_match = re.search(r"```(?:\w+)?\s*(.*?)\s*```", diagnosis, re.DOTALL)
                    if code_match:
                        fix_snippet = code_match.group(1)
                except Exception as exc:
                    diagnosis = f"Diagnosis error: {exc}"

            bug_data = {
                "id": bug_id,
                "game": bot.game_name,
                "channel": f"#{channel_name}",
                "channel_id": message.channel.id,
                "message_id": message.id,
                "author": author,
                "description": content,
                "status": "open",
                "diagnosis": diagnosis,
                "fix_snippet": fix_snippet,
                "timestamp": time.time(),
            }
            bot.bugs[bug_id] = bug_data

            # Notify desktop daemon and HUD
            if bot.on_bug_received:
                await bot.on_bug_received(bug_data)

            # Capture in-line screengrab if requested
            screengrab_file: Optional[discord.File] = None
            if bot.screengrab_fn:
                try:
                    img_bytes = bot.screengrab_fn()
                    if img_bytes:
                        screengrab_file = discord.File(io.BytesIO(img_bytes), filename="screengrab.png")
                except Exception as exc:
                    log.debug("Screengrab error: %s", exc)

            # Create rich interactive Discord embed
            embed = discord.Embed(
                title=f"🎮 {bot.game_name} • Bug Report #{bug_id}",
                description=f"**Reported by:** `{author}`\n**Issue:**\n{content[:1000]}",
                color=0xC084FC,
            )
            embed.add_field(
                name="✨ Gemini Search Diagnosis",
                value=diagnosis[:1000] if diagnosis else "No automated diagnosis available.",
                inline=False,
            )
            embed.set_footer(text=f"Orion Game Intelligence • Status: Open • Click buttons below to respond")

            if screengrab_file:
                embed.set_image(url="attachment://screengrab.png")

            view = BugActionView(bug_id=bug_id, fix_snippet=fix_snippet)

            if screengrab_file:
                await message.reply(embed=embed, file=screengrab_file, view=view)
            else:
                await message.reply(embed=embed, view=view)

        self._client = client
        self._task = asyncio.create_task(client.start(self.token), name="orion-discord-game-bot")
        return True

    def bug_exists(self, bug_id: str) -> bool:
        return bug_id in self.bugs

    def get_all_bugs(self) -> list[dict[str, Any]]:
        return list(self.bugs.values())

    async def send_response_to_discord(self, bug_id: str, response_text: str) -> bool:
        """Allows Orion HUD to send a response directly to the Discord thread/channel."""
        if not self._client or not self.is_running:
            return False
        bug = self.bugs.get(bug_id)
        if not bug:
            return False
        try:
            import discord
            channel = self._client.get_channel(bug["channel_id"])
            if channel:
                reply_embed = discord.Embed(
                    title=f"🛠️ Developer Response • #{bug_id}",
                    description=response_text,
                    color=0x3DFFA8,
                )
                reply_embed.set_footer(text=f"Sent via Orion Desktop Agent HUD")
                await channel.send(embed=reply_embed)
                bug["developer_response"] = response_text
                return True
        except Exception as exc:
            log.error("Failed to post response to Discord for bug %s: %s", bug_id, exc)
        return False

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
        if self._task is not None:
            self._task.cancel()
        self._client = self._task = None
