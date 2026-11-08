"""Promise Keeper (toolkit tool 25): never forget what you said you'd do.

Orion reads only your OWN sent messages in the unified messages (comms_hub.py: Discord, Messenger, Telegram, and
texts when a bridge brings them in) and notices commitments: "I'll send you the build tomorrow", "I'll fix that
bug this weekend". Each one becomes a quiet task with its due date and a link back to the message, and you get
one nudge before it's due. It never messages anyone, and it never reads other people's messages for this.

How a promise is spotted:
  1. Rules find candidate sentences ("I'll ...", "I will ...", "I'm going to ...", "let me ..."), skipping
     questions, "if" clauses, "I'll never" and things that aren't actions ("I'll be late").
  2. When the local model (Ollama) is answering, it confirms each candidate and says what the promise is.
     Without it the rules decide alone.
  3. The date comes from dateparser when it is installed, else from a small built-in parser (today, tonight,
     tomorrow, this weekend, next week, on Friday, by Friday, in 3 days, end of the week...).

Where you see them: the "Promises" part of the morning brief, "Orion, what did I promise this week?", and
GET /promises. POST /promises/{id}/done or /dismiss closes one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.promises")

SCAN_EVERY_S = 120.0
LOOKBACK_S = 14 * 86400        # only messages from the last two weeks become promises
NUDGE_BEFORE_S = 3 * 3600
MAX_SEEN = 8000
MAX_PROMISES = 500

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_RE = r"(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"

COMMIT_RE = re.compile(
    r"\b(?:i'?ll|i will|i'?m going to|i am going to|i'?m gonna|i shall|let me|i promise to|i can get|"
    r"will do)\b\s*(?P<rest>.*)", re.I)
NOT_ACTION = re.compile(
    r"^(?:not|never|be\b|see\b|think\b|guess\b|bet\b|probably not|have to\b|need to\b|pass\b|leave it|let you "
    r"down|miss\b|regret\b|die\b|take that as|assume\b|wait and see|keep that in mind|remember that)", re.I)
TIME_WORDS = re.compile(
    rf"\b(?:by |on |this |next |before |until |at |in )?(?:today|tonight|tomorrow|tmrw|tmr|this (?:morning|"
    rf"afternoon|evening|weekend|week)|next week|the weekend|end of (?:the )?(?:day|week)|eod|eow|{DAY_RE}|"
    rf"in (?:a|an|one|two|three|\d+) (?:hours?|days?|weeks?)|later(?: today)?|soon|asap|"
    rf"\d{{1,2}}(?::\d{{2}})? ?(?:am|pm)|\d{{1,2}}:\d{{2}})\b", re.I)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def candidates(text: str) -> list[dict[str, str]]:
    """Sentences that look like a promise, with the promised action."""
    out = []
    for s in _sentences(text):
        if s.endswith("?") or re.match(r"^\s*(if|when|unless|maybe if)\b", s, re.I):
            continue
        m = COMMIT_RE.search(s)
        if not m:
            continue
        before = s[:m.start()].lower()
        if re.search(r"\b(if|unless|maybe|might|whether)\b", before):
            continue
        rest = m.group("rest").strip(" ,.!;:-")
        if re.match(r"^(?:try to|try and|definitely|totally|also|just|probably|quickly|go ahead and)\s+", rest, re.I):
            rest = re.sub(r"^(?:try to|try and|definitely|totally|also|just|probably|quickly|go ahead and)\s+", "",
                          rest, flags=re.I)
        if not rest or NOT_ACTION.match(rest) or len(rest.split()) < 1:
            continue
        if "will do" in m.group(0).lower() and not rest:
            continue
        out.append({"sentence": s, "action": rest})
    return out


def _at(d: datetime, hour: int, minute: int = 0) -> datetime:
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _clock(text: str) -> Optional[tuple[int, int]]:
    m = re.search(r"\b(?:at|by|before)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.I) or \
        re.search(r"\b(?:at|by|before)\s+(\d{1,2}):(\d{2})()\b", text, re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return (h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def parse_due(text: str, base: datetime) -> Optional[datetime]:
    """A small date parser for promises. None when there's no date."""
    t = text.lower()
    clock = _clock(t)

    def day(d: datetime, default_hour: int) -> datetime:
        return _at(d, *(clock or (default_hour, 0)))

    if re.search(r"\b(tonight|this evening)\b", t):
        return day(base, 21)
    if re.search(r"\bthis afternoon\b", t):
        return day(base, 17)
    if re.search(r"\bthis morning\b", t):
        return day(base, 12)
    if re.search(r"\b(today|later today|eod|end of (the )?day|asap)\b", t):
        return day(base, 18)
    if re.search(r"\b(tomorrow|tmrw|tmr)\b", t):
        return day(base + timedelta(days=1), 17)
    m = re.search(r"\bin (a|an|one|two|three|\d+) (hour|day|week)s?\b", t)
    if m:
        n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(m.group(1)) or int(m.group(1))
        if m.group(2) == "hour":
            return base + timedelta(hours=n)
        return day(base + timedelta(days=n * (7 if m.group(2) == "week" else 1)), 17)
    if re.search(r"\b(this weekend|the weekend|over the weekend)\b", t):
        ahead = (6 - base.weekday()) % 7      # Sunday
        return day(base + timedelta(days=ahead), 18)
    if re.search(r"\b(end of (the )?week|eow|this week)\b", t):
        ahead = (4 - base.weekday()) % 7      # Friday
        return day(base + timedelta(days=ahead), 17)
    if re.search(r"\bnext week\b", t):
        monday = base + timedelta(days=7 - base.weekday())
        return day(monday + timedelta(days=4), 17)
    m = re.search(rf"\b(?:on |by |this |next |before )?{DAY_RE}\b", t)
    if m:
        name = m.group(1)[:3]
        idx = next(i for i, d in enumerate(DAYS) if d.startswith(name))
        ahead = (idx - base.weekday()) % 7 or 7
        if "next " + m.group(1) in t and ahead < 7:
            ahead += 7
        return day(base + timedelta(days=ahead), 17)
    if clock:
        d = _at(base, *clock)
        return d if d > base else d + timedelta(days=1)
    return None


def parse_due_any(text: str, base: datetime) -> Optional[datetime]:
    due = parse_due(text, base)
    if due is not None:
        return due
    try:
        from dateparser.search import search_dates   # optional
    except Exception:
        return None
    try:
        found = search_dates(text, settings={"RELATIVE_BASE": base, "PREFER_DATES_FROM": "future"})
    except Exception:
        return None
    for _, d in found or []:
        if d > base:
            return d.replace(tzinfo=None)
    return None


def tidy_action(action: str, to: str = "") -> str:
    a = TIME_WORDS.sub("", action)
    if to and len(to) <= 40:
        a = re.sub(r"\b(?:you|ya|u)\b", lambda _m: to, a, flags=re.I)
        a = re.sub(r"\byour\b", lambda _m: to + "'s", a, flags=re.I)
    a = re.sub(r"\s{2,}", " ", a).strip(" ,.!;:-")
    a = re.sub(r"\b(for sure|no worries|don'?t worry|haha|lol)\b", "", a, flags=re.I).strip(" ,.!;:-")
    return (a[:1].upper() + a[1:])[:160] if a else ""


class PromiseKeeper:
    def __init__(self, data_dir: Path, comms: Any = None, llm: Any = None,
                 notify: Optional[Callable[..., None]] = None, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(data_dir) / "promises.json"
        self.comms = comms
        self.llm = llm
        self.notify = notify or _alert
        self.clock = clock
        self._lock = threading.RLock()
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ state
    def _load(self) -> dict[str, Any]:
        try:
            st = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                st.setdefault("seen", [])
                st.setdefault("promises", [])
                return st
        except (OSError, ValueError):
            pass
        return {"seen": [], "promises": []}

    def _save(self, st: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        st["seen"] = st["seen"][-MAX_SEEN:]
        open_ = [p for p in st["promises"] if p["status"] == "open"]
        closed = [p for p in st["promises"] if p["status"] != "open"]
        st["promises"] = (closed + open_)[-MAX_PROMISES:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ spotting
    async def _model_check(self, sentence: str, thread: str) -> Optional[dict[str, Any]]:
        """{"promise": bool, "what": str, "when": str} from the local model, or None when it isn't answering."""
        llm = self.llm
        if llm is None or not hasattr(llm, "ollama_json"):
            return None
        try:
            if hasattr(llm, "ollama_available") and not await llm.ollama_available():
                return None
            schema = {"type": "object", "properties": {"promise": {"type": "boolean"}, "what": {"type": "string"},
                                                       "when": {"type": "string"}},
                      "required": ["promise", "what", "when"]}
            data, _ = await llm.ollama_json(
                f"Message I sent to {thread}: \"{sentence[:400]}\"\n\nIs this me committing to do something "
                "specific later? If yes, say what in a few words starting with a verb, and when (words from the "
                "message, or empty).", schema,
                system="You spot commitments in the user's own chat messages. Reply only with the JSON asked for.",
                timeout=20.0)
        except Exception:
            return None
        return data if isinstance(data, dict) and "promise" in data else None

    async def consider(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        """Turn one of your sent messages into promises (none for most messages)."""
        if msg.get("direction") != "out":
            return []
        made = []
        sent = float(msg.get("timestamp") or self.clock())
        base = datetime.fromtimestamp(sent)
        thread = str(msg.get("thread_name") or msg.get("thread_id") or "someone")
        for c in candidates(str(msg.get("text") or "")):
            what, when_text = c["action"], c["sentence"]
            verdict = await self._model_check(c["sentence"], thread)
            if verdict is not None:
                if not verdict.get("promise"):
                    continue
                what = str(verdict.get("what") or what)
                when_text = f"{verdict.get('when') or ''} {c['sentence']}"
            due = parse_due_any(when_text, base)
            task = tidy_action(what, thread)
            if not task:
                continue
            made.append(self._add(task, due, msg, c["sentence"], thread, "model" if verdict else "rules"))
        return made

    def _add(self, task: str, due: Optional[datetime], msg: dict[str, Any], sentence: str, thread: str,
             how: str) -> dict[str, Any]:
        now = self.clock()
        due_ts = due.timestamp() if due else None
        nudge = None
        if due_ts is not None:
            nudge = due_ts - NUDGE_BEFORE_S
            if nudge <= now:
                nudge = now + max(60.0, (due_ts - now) / 2) if due_ts > now else None
        p = {"id": "pr-" + uuid.uuid4().hex[:8], "task": task, "to": thread, "platform": msg.get("platform", ""),
             "said": sentence[:300], "sent_at": float(msg.get("timestamp") or now), "due": due_ts,
             "nudge_at": nudge, "nudged": False, "status": "open", "created": now, "how": how,
             "link": {"thread_key": msg.get("thread_key") or "", "message_id": msg.get("id") or "",
                      "url": msg.get("thread_url") or f"orion://comms/{msg.get('thread_key') or ''}#{msg.get('id') or ''}"}}
        with self._lock:
            st = self._load()
            st["promises"].append(p)
            self._save(st)
        return p

    async def scan(self) -> int:
        """Look through your recent sent messages for new promises. Returns how many were added."""
        comms = self.comms
        if comms is None:
            return 0
        cutoff = self.clock() - LOOKBACK_S
        with self._lock:
            seen = set(self._load()["seen"])
        new_msgs = []
        for t in list(getattr(comms, "threads", {}).values()):
            for m in list(t.get("messages") or []):
                if m.get("direction") != "out" or float(m.get("timestamp") or 0) < cutoff:
                    continue
                mid = str(m.get("id") or m.get("dedupe_key") or "")
                if mid and mid not in seen:
                    new_msgs.append(m)
        added = 0
        for m in sorted(new_msgs, key=lambda x: float(x.get("timestamp") or 0)):
            try:
                added += len(await self.consider(m))
            finally:
                with self._lock:
                    st = self._load()
                    st["seen"].append(str(m.get("id") or m.get("dedupe_key")))
                    self._save(st)
        return added

    def tick(self) -> int:
        """Send the one nudge for promises that are nearly due. Returns how many nudges went out."""
        now = self.clock()
        due_now = []
        with self._lock:
            st = self._load()
            for p in st["promises"]:
                if p["status"] == "open" and not p["nudged"] and p.get("nudge_at") and p["nudge_at"] <= now:
                    p["nudged"] = True
                    due_now.append(dict(p))
            if due_now:
                self._save(st)
        for p in due_now:
            try:
                self.notify("life", "A promise is due soon",
                            f"You told {p['to']} you'd {p['task'][:1].lower() + p['task'][1:]}"
                            f" ({when_words(p['due'], now)}).", ref=f"promise:{p['id']}")
            except Exception:
                log.exception("promise nudge failed")
        return len(due_now)

    # ------------------------------------------------------------------ reading
    def promises(self, status: Optional[str] = "open", since: Optional[float] = None) -> list[dict[str, Any]]:
        ps = self._load()["promises"]
        if status:
            ps = [p for p in ps if p["status"] == status]
        if since is not None:
            ps = [p for p in ps if p["sent_at"] >= since]
        return sorted(ps, key=lambda p: (p["due"] is None, p["due"] or 0, p["sent_at"]))

    def close(self, pid: str, status: str = "done") -> dict[str, Any]:
        if status not in ("done", "dismissed", "open"):
            raise ValueError("status is done, dismissed or open")
        with self._lock:
            st = self._load()
            for p in st["promises"]:
                if p["id"] == pid:
                    p["status"] = status
                    p["closed_at"] = self.clock()
                    self._save(st)
                    return p
        raise KeyError(pid)

    def week_words(self) -> str:
        now = self.clock()
        start = now - 7 * 86400
        ps = [p for p in self._load()["promises"] if p["sent_at"] >= start and p["status"] != "dismissed"]
        if not ps:
            return "You haven't promised anything in your messages this week."
        ps.sort(key=lambda p: (p["due"] is None, p["due"] or 0))
        lines = []
        for p in ps[:12]:
            mark = "done" if p["status"] == "done" else when_words(p["due"], now)
            lines.append(f"{p['task']} ({p['to']}, {mark})")
        head = f"This week you promised {len(ps)} thing{'s' if len(ps) != 1 else ''}: "
        return head + "; ".join(lines) + "."

    def brief_section(self) -> str:
        """The "Promises" part of the morning brief (empty when there's nothing open)."""
        now = self.clock()
        ps = [p for p in self.promises("open") if p["due"] is None or p["due"] <= now + 2 * 86400]
        if not ps:
            return ""
        items = [f"{p['task']} ({p['to']}, {when_words(p['due'], now)})" for p in ps[:5]]
        more = f" and {len(ps) - 5} more" if len(ps) > 5 else ""
        return "Promises: " + "; ".join(items) + more + "."

    # ------------------------------------------------------------------ the loop
    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await self.scan()
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("promise scan failed")
            await asyncio.sleep(SCAN_EVERY_S)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="promise-keeper")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


def when_words(due: Optional[float], now: float) -> str:
    if due is None:
        return "no date"
    d, n = datetime.fromtimestamp(due), datetime.fromtimestamp(now)
    if due < now:
        return "overdue since " + ("today" if d.date() == n.date() else d.strftime("%a"))
    days = (d.date() - n.date()).days
    hm = d.strftime("%H:%M")
    if days == 0:
        return f"today {hm}"
    if days == 1:
        return f"tomorrow {hm}"
    if days < 7:
        return d.strftime("%A")
    return d.strftime("%d %b")


def _alert(kind: str, title: str, body: str = "", ref: Optional[str] = None) -> None:
    import alerts
    alerts.emit(kind, title, body, ref=ref)


# =====================================================================================  quick commands
WEEK_RE = re.compile(r"^(what (did|have) i promise( (this|last) week| lately| recently)?|what are my promises|"
                     r"(my |show (me )?my |list my )?promises( this week)?|what do i owe (people|anyone))$")


# =====================================================================================  HTTP
def make_router(get_pk: Callable[[], PromiseKeeper]):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/promises", tags=["toolkit"])

    @router.get("")
    async def list_promises(status: str = "open") -> Any:
        pk = get_pk()
        return {"promises": pk.promises(status or None), "week": pk.week_words(), "brief": pk.brief_section()}

    @router.post("/scan")
    async def scan() -> Any:
        return {"added": await get_pk().scan()}

    @router.post("/{pid}/{action}")
    async def act(pid: str, action: str) -> Any:
        status = {"done": "done", "dismiss": "dismissed", "reopen": "open"}.get(action)
        if status is None:
            return JSONResponse({"error": "use done, dismiss or reopen"}, status_code=400)
        try:
            return {"promise": get_pk().close(pid, status)}
        except KeyError:
            return JSONResponse({"error": "no such promise"}, status_code=404)

    return router
