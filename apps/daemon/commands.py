"""Deterministic voice/typed command router (Feature Bible).

Runs before the LLM orchestrator so everyday commands are instant, free and predictable:

  "Orion, watch what I do next" / "stop watching"        -> macro recorder
  "switch to OpenWoW mode" / "battle stations podcast"   -> Battle Stations
  "run that script that converts GLB models to M2"       -> Grimoire
  "log a realm idea: low-gravity CTF battleground"       -> Idea catcher
  "tell Alex on Telegram we're recording at 6"         -> Voice-to-reply (then "send it" / "cancel")
  "write the devlog"                                      -> Devlog compiler
  "grab the reconnect hook from Orion and adapt it for my vite app" -> cross-repo patch
  "start podcast mode" / "mark that" / "next topic: …"   -> Producer HUD
  "I drank a glass of water"                              -> Hydration log
  "from now on keep announcements casual"                 -> learned preference rule
  "harvest my projects"                                   -> Preload harvester
Anything else falls through to the orchestrator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from creative import parse_idea

PREFIX = re.compile(r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:orion[,:!.]?\s+)?(?:please\s+)?", re.I)


@dataclass
class Command:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)


def strip_prefix(text: str) -> str:
    return PREFIX.sub("", text.strip(), count=1).strip()


RULE_RE = re.compile(
    r"^(?:from now on|going forward|in (?:the )?future|(?:and )?for future reference|"
    r"i (?:always |really )?prefer|i want you to (?:always|never)|please (?:always|never)|(?:don'?t|do not) ever|"
    r"remember that i (?:like|prefer|want|hate|don'?t)|"
    r"(?:always|never) (?:use|keep|write|reply|answer|respond|sign|include|mention|add|call me|format|put|say|"
    r"post|send|draft|address|refer|start|end|open|close|ask|run|show|tell|cc)\b|"
    r"keep (?:my|your|all|the) (?:replies|messages|posts|answers|responses|announcements|devlogs|updates|drafts|"
    r"emails|summaries|tone)\b|stop (?:using|saying|adding) )(.+)?$",
    re.I | re.S,
)
WATCH_START = re.compile(r"^(?:watch (?:what|how) i do(?: next| this)?|start watching(?: me)?|record (?:a |this )?macro|"
                         r"learn (?:what|how) i do(?: this)?|watch me)\b", re.I)
WATCH_STOP = re.compile(r"^(?:stop watching|stop recording(?: the)?(?: macro)?|done watching|finish (?:the )?macro|"
                        r"end (?:the )?(?:macro|recording))\b", re.I)
BATTLE = re.compile(r"^(?:(?:switch|change|go|flip|shift)\s+(?:over\s+)?(?:to|into)\s+(?P<a>.+?)\s+mode|"
                    r"battle\s*stations?[:,]?\s+(?P<b>.+)|(?:load|start|enter|activate)\s+(?P<c>.+?)\s+mode)\.?$", re.I)
RUN = re.compile(r"^(?:run|execute|launch|fire off|kick off|do)\s+(?:the|my|that|this)?\s*(?P<q>.+)$", re.I)
VOICE_REPLY = re.compile(
    r"^(?:tell|message|text|msg|reply to|send|let)\s+(?P<name>[\w .'-]{2,40}?)\s+"
    r"(?:(?:on|via|in|over)\s+(?P<platform>telegram|discord|messenger|facebook|fb)\s+)?"
    r"(?:know\s+)?(?:that\s+|saying\s+|to say\s+|:\s*)?(?P<text>.+)$",
    re.I | re.S,
)
SEND_IT = re.compile(r"^(?:send it|send|yes,? send(?: it)?|go ahead(?: and send(?: it)?)?|ship it|do it)\W*$", re.I)
CANCEL = re.compile(r"^(?:cancel|don'?t send(?: it)?|never ?mind|scratch that|abort)\W*$", re.I)
DEVLOG = re.compile(r"^(?:write|draft|compile|make|generate|put together)\s+(?:the |a |my |today'?s )?"
                    r"(?:devlog|dev log|changelog|community update|update post)\b", re.I)
HARVEST_CODE = re.compile(
    r"^(?:grab|take|pull|copy|port|bring|steal|lift)\s+(?P<what>.+?)\s+from\s+(?:the\s+|my\s+)?(?P<src>.+?)\s+"
    r"(?:and\s+)?(?:adapt|put|patch|add|use|drop|wire)\s+(?:it\s+|them\s+)?(?:for|into|to|in)\s+(?:the\s+|my\s+)?(?P<dst>.+?)\.?$",
    re.I,
)
PRODUCER_START = re.compile(r"^(?:start|begin|enter|go into)\s+(?:the\s+)?(?:podcast|recording|show|producer|stream)"
                            r"(?:\s+mode)?(?:[:,]?\s+(?P<title>.+))?$", re.I)
PRODUCER_STOP = re.compile(r"^(?:stop|end|finish|wrap up|exit)\s+(?:the\s+)?(?:podcast|recording|show|producer|stream)"
                           r"(?:\s+mode)?\W*$", re.I)
HIGHLIGHT = re.compile(r"^(?:mark that|mark it|highlight(?: that)?|clip that|bookmark that|that'?s a highlight)"
                       r"(?:[:,]?\s+(?P<note>.+))?$", re.I)
TOPIC = re.compile(r"^(?:next|new)\s+topic[:,]?\s+(?P<title>.+)$", re.I)
WATER = re.compile(r"^(?:i\s+)?(?:just\s+)?(?:drank|had|finished|drink)\s+(?:a |another |one |my )?"
                   r"(?:full\s+)?(?:glass|bottle|cup|mug)\s+of\s+water|^log\s+(?:a\s+)?(?:glass of\s+)?water\b|"
                   r"^(?:i'?m\s+)?hydrated\b", re.I)
HARVEST = re.compile(r"^(?:harvest|scan|index)\s+(?:my\s+)?(?:machine|computer|pc|projects|repos|workspaces|scripts)\b", re.I)

PLATFORM_ALIASES = {"facebook": "messenger", "fb": "messenger"}


def parse(text: str, *, reply_pending: bool = False, recording: bool = False) -> Optional[Command]:
    raw = text.strip()
    t = strip_prefix(raw)
    if not t:
        return None

    if reply_pending:
        if SEND_IT.match(t):
            return Command("reply_send")
        if CANCEL.match(t):
            return Command("reply_cancel")
    if recording and WATCH_STOP.match(t):
        return Command("macro_stop")
    if WATCH_START.match(t):
        return Command("macro_start")
    if WATCH_STOP.match(t):
        return Command("macro_stop")

    idea = parse_idea(raw)
    if idea:
        return Command("idea", idea)

    m = PRODUCER_START.match(t)
    if m:
        return Command("producer_start", {"title": (m.group("title") or "").strip()})
    if PRODUCER_STOP.match(t):
        return Command("producer_stop")
    m = HIGHLIGHT.match(t)
    if m:
        return Command("highlight", {"note": (m.group("note") or "").strip()})
    m = TOPIC.match(t)
    if m:
        return Command("topic", {"title": m.group("title").strip()})

    if WATER.match(t):
        return Command("water")
    if HARVEST.match(t):
        return Command("harvest")
    if DEVLOG.match(t):
        return Command("devlog")

    m = HARVEST_CODE.match(t)
    if m:
        return Command("harvest_code", {"what": m.group("what").strip(), "source": m.group("src").strip(),
                                        "target": m.group("dst").strip(), "request": raw})

    m = BATTLE.match(t)
    if m:
        name = (m.group("a") or m.group("b") or m.group("c") or "").strip()
        if name:
            return Command("battle", {"name": name})

    m = VOICE_REPLY.match(t)
    if m and (m.group("platform") or re.match(r"^(?:tell|message|text|msg|let)\b", t, re.I)):
        name = m.group("name").strip()
        body = m.group("text").strip()
        if name.lower() not in {"me", "you", "orion", "it"} and len(body) >= 2:
            platform = (m.group("platform") or "").lower() or None
            return Command("voice_reply", {"name": name, "platform": PLATFORM_ALIASES.get(platform or "", platform),
                                           "text": body})

    m = RULE_RE.match(t)
    if m and len(t) >= 12 and not t.rstrip().endswith("?"):
        return Command("rule", {"text": t.rstrip(". ")})

    m = RUN.match(t)
    if m:
        return Command("grimoire", {"query": m.group("q").strip()})
    return None
