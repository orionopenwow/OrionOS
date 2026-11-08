"""Nightly review ("dreaming"), after-1.0 plan §1: once a day Orion looks back over the day's events, approvals and
edits and proposes new skills, rules and shortcuts as cards in the Queue. Nothing changes until you accept.

Opt-in: Settings > Skills > "Nightly review" (desktop_skills_hub.Settings "nightly_review", off by default).
Runs from self_running (start/stop/decide_card), at review_hour local time (3 by default), at most once a day,
never while Orion is frozen.

What it looks at (all already on this machine; nothing is sent anywhere):
  - the light activity log (skills/activity/*.jsonl): repeated sequences of steps -> "make this a skill?"
  - the approval queue: drafts you edited the same way (always shorter / longer) or kept rejecting for one
    channel -> a standing rule ("Keep drafts for #general short")
  - apps you switched to many times -> a one-word quick command that opens it

Accepting a card:
  skill     -> a skill draft appears in the Skills card (you still name it, check it and save it; typed values
               are never in the log, so they become blanks to fill in)
  rule      -> added to your standing rules (memory_core.add_rule, source "nightly review")
  shortcut  -> added to your quick commands (~/.config/orion/quick.json)
Rejecting a card means it is never proposed again.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.skill_dreams")

CARD_KIND = "skill_dream"
TICK_S = 1800.0
DAY = 86400
MAX_CARDS = 5
MIN_APP_SWITCHES = 6


def _pid(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


class DreamState:
    """skills/dreams.json: the last night it ran and every proposal it made (so nothing is proposed twice)."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "skills" / "dreams.json"
        self.lock = threading.Lock()

    def get(self) -> dict[str, Any]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("last_day", "")
                d.setdefault("proposals", {})
                return d
        except (OSError, ValueError):
            pass
        return {"last_day": "", "proposals": {}}

    def update(self, fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.lock:
            d = self.get()
            fn(d)
            props = d["proposals"]
            if len(props) > 400:  # keep the newest
                for k in sorted(props, key=lambda k: props[k].get("at", 0))[:-400]:
                    props.pop(k, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.path)
            return d


# ====================================================================================== finding proposals
def skill_proposals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from skills_recorder import spot_patterns, suggestion_card
    sigs = [str(r.get("sig") or "") for r in rows if r.get("sig")]
    out = []
    for pat in spot_patterns(sigs, min_len=3, min_count=3)[:3]:
        card = suggestion_card(pat)
        # one example run of the sequence, from the log (identity only)
        steps: list[dict[str, Any]] = []
        n = len(pat["sequence"])
        for i in range(len(rows) - n + 1):
            if [r.get("sig") for r in rows[i:i + n]] == pat["sequence"]:
                steps = [r.get("step") or {} for r in rows[i:i + n]]
                break
        out.append({"type": "skill", "id": "skill-" + _pid(*pat["sequence"]),
                    "title": "Make this a skill?",
                    "text": f"You did these {pat['length']} steps {pat['count']} times: " + "; ".join(card["steps"]),
                    "args": {"steps": steps, "lines": card["steps"]}})
    return out


def rule_proposals(items: list[dict[str, Any]], since: float) -> list[dict[str, Any]]:
    edited: dict[str, list[float]] = defaultdict(list)
    rejected: Counter = Counter()
    for it in items:
        if float(it.get("resolved_at") or it.get("created_at") or 0) < since:
            continue
        where = str(it.get("channel") or it.get("platform") or "")
        if not where or it.get("kind") in (CARD_KIND, "skill_step"):
            continue
        draft, final = str(it.get("draft") or ""), str(it.get("final") or "")
        if it.get("status") == "sent" and final and draft and final.strip() != draft.strip() and len(draft) > 20:
            edited[where].append(len(final) / max(1, len(draft)))
        if it.get("status") == "rejected" and it.get("kind") not in ("self_evolve",):
            rejected[(str(it.get("kind")), where)] += 1
    out = []
    for where, ratios in edited.items():
        if len(ratios) < 2:
            continue
        avg = sum(ratios) / len(ratios)
        if avg <= 0.75:
            text = f"Keep drafts for {where} shorter — you cut them down {len(ratios)} times today."
            rule = f"When drafting for {where}, keep it short: the user usually trims drafts there."
        elif avg >= 1.3:
            text = f"Put more detail in drafts for {where} — you added to them {len(ratios)} times today."
            rule = f"When drafting for {where}, include more detail: the user usually adds to drafts there."
        else:
            continue
        out.append({"type": "rule", "id": "rule-" + _pid(where, "short" if avg <= 0.75 else "long"),
                    "title": "A new standing rule?", "text": text, "args": {"rule": rule}})
    for (kind, where), n in rejected.items():
        if n >= 3:
            out.append({"type": "rule", "id": "rule-" + _pid(kind, where, "reject"), "title": "A new standing rule?",
                        "text": f"You said no to {n} {kind.replace('_', ' ')} drafts for {where} today. "
                                "Stop drafting those unless you ask?",
                        "args": {"rule": f"Don't draft {kind.replace('_', ' ')} replies for {where} unless the user "
                                         "asks for one."}})
    return out


def shortcut_proposals(rows: list[dict[str, Any]], existing: dict[str, Any]) -> list[dict[str, Any]]:
    apps: Counter = Counter()
    for r in rows:
        st = r.get("step") or {}
        if st.get("action") == "focus" and st.get("app"):
            apps[str(st["app"])] += 1
    out = []
    have = {json.dumps(v) for v in existing.values()}
    for app, n in apps.most_common(3):
        if n < MIN_APP_SWITCHES:
            break
        short = app.rsplit(".", 1)[-1].lower()
        word = re.sub(r"[^a-z0-9]", "", short)[:20]
        if not word or word in existing or json.dumps([f"app:{short}"]) in have:
            continue
        out.append({"type": "shortcut", "id": "shortcut-" + _pid(app), "title": "A new shortcut?",
                    "text": f"You switched to {app} {n} times today. Say or type “{word}” to open it?",
                    "args": {"name": word, "steps": [f"app:{short}"]}})
    return out


def build(daemon: Any, data_dir: Path, now: Optional[float] = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    since = now - DAY
    from desktop_skills_hub import ActivityLog
    rows = ActivityLog(data_dir).since(since)
    props = skill_proposals(rows)
    approvals = getattr(daemon, "approvals", None)
    try:
        items = list(approvals.items.values()) if approvals is not None else []
    except Exception:
        items = []
    props += rule_proposals(items, since)
    quick = getattr(daemon, "quick", None)
    try:
        existing = quick.commands() if quick is not None else {}
    except Exception:
        existing = {}
    props += shortcut_proposals(rows, existing)
    return props


# ====================================================================================== the nightly run
async def dream(daemon: Any, data_dir: Path, state: DreamState, now: Optional[float] = None) -> list[dict[str, Any]]:
    """Make tonight's cards. Returns the new proposals (already-proposed ones are skipped)."""
    now = time.time() if now is None else now
    seen = state.get()["proposals"]
    fresh = [p for p in build(daemon, data_dir, now) if p["id"] not in seen][:MAX_CARDS]
    approvals = getattr(daemon, "approvals", None)
    cards = []
    for p in fresh:
        if approvals is None:
            break
        card = approvals.add(CARD_KIND, platform="Orion", channel="Nightly review", author="Orion",
                             category="normal", title=p["title"], original=p["text"],
                             draft="Approve to accept, Reject and it won't be suggested again. Nothing changes "
                                   "until you accept.",
                             dream_id=p["id"], dream_type=p["type"], dream_args=p["args"])
        cards.append(card)
        try:
            await daemon.bus.publish({"event": "APPROVAL_REQUIRED", "item": card})
        except Exception:
            pass
    day = datetime.fromtimestamp(now).date().isoformat()

    def mark(d: dict[str, Any]) -> None:
        d["last_day"] = day
        for p, c in zip(fresh, cards):
            d["proposals"][p["id"]] = {"at": now, "type": p["type"], "text": p["text"], "card": c["id"],
                                       "state": "open"}
    state.update(mark)
    return fresh


def due(settings: dict[str, Any], state: DreamState, now: float) -> bool:
    if not settings.get("nightly_review"):
        return False
    dt = datetime.fromtimestamp(now)
    if dt.hour < int(settings.get("review_hour", 3)):
        return False
    return state.get()["last_day"] != dt.date().isoformat()


async def accept(daemon: Any, item: dict[str, Any]) -> str:
    typ, args = item.get("dream_type"), item.get("dream_args") or {}
    if typ == "skill":
        hub = getattr(daemon, "skills", None)
        if hub is None:
            return "The Skill Recorder isn't running."
        steps = []
        for i, st in enumerate(args.get("steps") or []):
            st = json.loads(json.dumps(st))
            if st.get("action") == "type":
                st["value"] = "{{text_" + str(i + 1) + "}}"
                st.pop("light", None)
            steps.append(st)
        if steps and all(s.get("surface") == "desktop" for s in steps):
            from desktop_skill import build_draft
            draft = build_draft(steps, source="nightly-review")
            for v in sorted({m for s in draft["steps"] for m in re.findall(r"\{\{(text_\d+)\}\}", json.dumps(s))}):
                draft["variables"].append({"name": v, "label": "Text to type", "kind": "text", "example": "",
                                           "used_in": [s["id"] for s in draft["steps"] if "{{" + v + "}}" in json.dumps(s)]})
            hub.drafts[draft["draft_id"]] = draft
            await daemon.bus.publish({"event": "SKILL_DRAFT", "draft": draft})
            return "A skill draft is in the Skills card: name it, check it and save it."
        return "Record it once (press Record in the Skills card) and I'll turn it into a skill."
    if typ == "rule":
        mem = getattr(daemon, "memory", None)
        if mem is None or not hasattr(mem, "add_rule"):
            return "Memory isn't running, so the rule wasn't added."
        await mem.add_rule(str(args.get("rule", ""))[:300], source="nightly review")
        return "Added to your standing rules."
    if typ == "shortcut":
        quick = getattr(daemon, "quick", None)
        if quick is None:
            from quick_commands import QuickCommands
            quick = QuickCommands()
        quick.save_command(str(args.get("name", "")), [str(s) for s in args.get("steps") or []])
        return f"Say or type “{args.get('name')}” to use it."
    return "Noted."


async def decide_card(daemon: Any, item: dict[str, Any], decision: str, state: DreamState) -> None:
    import freeze
    yes = decision in ("approve", "edit")
    if yes and freeze.is_frozen():
        await daemon.bus.log(freeze.MESSAGE, "warn")
        return
    if daemon.approvals.claim(item["id"], "sending" if yes else "rejected") is None:
        return
    try:
        msg = await accept(daemon, item) if yes else "Won't suggest that again."
    except Exception as exc:
        log.exception("nightly review accept failed")
        msg = f"That didn't work: {exc}"
    pid = str(item.get("dream_id", ""))
    state.update(lambda d: d["proposals"].setdefault(pid, {}).update(state="accepted" if yes else "dismissed",
                                                                      result=msg[:200]))
    updated = daemon.approvals.update(item["id"], status="approved" if yes else "rejected", result=msg[:300])
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})
    await daemon.bus.log(msg)


class NightlyReview:
    def __init__(self, daemon: Any, data_dir: Path, every_s: float = TICK_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.daemon = daemon
        self.data_dir = Path(data_dir)
        self.state = DreamState(self.data_dir)
        self.every_s = every_s
        self.clock = clock
        self._task: Optional[asyncio.Task] = None

    def settings(self) -> dict[str, Any]:
        from desktop_skills_hub import Settings
        return Settings(self.data_dir).get()

    async def tick(self) -> Optional[list[dict[str, Any]]]:
        import freeze
        now = self.clock()
        if freeze.is_frozen() or not due(self.settings(), self.state, now):
            return None
        return await dream(self.daemon, self.data_dir, self.state, now)

    async def _loop(self) -> None:
        hub = getattr(self.daemon, "skills", None)
        try:  # the opt-in "notice repeated tasks" watcher comes back after a restart
            if hub is not None and getattr(hub, "desktop", None) is not None:
                hub.desktop.start_watcher()
        except Exception:
            log.debug("activity watcher didn't start", exc_info=True)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("nightly review tick failed")
            await asyncio.sleep(self.every_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="nightly-review")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        hub = getattr(self.daemon, "skills", None)
        if hub is not None and getattr(hub, "desktop", None) is not None:
            hub.desktop.stop_watcher()
