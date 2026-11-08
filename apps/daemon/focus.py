"""Focus: the few things that matter right now, for the middle of the command console (and the phone's home).

At most five cards, most important first. Each card is one of:

  now        the current task: the running mission, else a coding agent at work (progress, what it waits on)
  message    an unread message that matters (sender, app, a one-line gist; Open / Draft reply, never Send)
  approval   things waiting for your OK (the count and the top one; opening goes to the queue, it never approves)
  needs_you  a stuck agent, a mission that needs you, a failing health check or Test Center check, a phone gone quiet
  next_up    the next thing due: a promise you made (Promise Keeper), or wind-down starting soon

How importance is learned (all on this computer, nothing sent anywhere, no keys read):

  * Every card kind has a starting importance (a running task and things waiting for your OK start high).
  * Messages get a per-sender, per-app and per-kind weight between -1 and +1. Opening a message or drafting a
    reply raises that sender's weight a little; "Not important" lowers it a lot; a message card left untouched for
    six hours lowers it a little. Contacts on your VIP list start high.
  * Your reply habits count too: Orion looks at how quickly you usually answer each person in your message
    history ("You usually reply to Maya within minutes").
  * Weights drift slowly back to where they started (half every 30 days), so old habits fade. New messages also
    count for more than old ones (a message loses half its pull every 12 hours).
  * "Pin" keeps a card at the top until you unpin it. "Not important" hides that card until something new happens.

Every card says why it is there in plain words. Weights are kept in <data dir>/focus.json.

Nothing here sends a message, approves anything or changes another part of Orion: it only reads, ranks and
remembers what you tapped. "Draft reply" writes a draft with the local model (or your own phrases when it is not
running); sending stays a tap in Messages.

HTTP (daemon port, with Orion's key):
  GET  /focus                    {"cards": [...], "all_clear": bool, "line": str, "generated_at": float}
  POST /focus/feedback           {"id", "action": "open|reply|draft|dismiss|pin|unpin"} -> {"ok": true}
  POST /focus/draft              {"id"} (a message card) -> {"draft": str, "thread": str}   never sent
Phone: GET /phone/home carries the same list under "focus" (phone_pair.py).
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.focus")

MAX_CARDS = 5
MAX_MESSAGES = 3          # leave room for the other kinds
MAX_NEEDS_YOU = 2
MESSAGE_MIN_SCORE = 22.0  # a message has to matter at least this much to take a place
HALF_LIFE_DAYS = 30.0     # learned weights drift back to their start
MESSAGE_HALF_LIFE_H = 12.0
IGNORE_AFTER_S = 6 * 3600
HIDE_FOR_S = 7 * 86400    # "Not important" hides that card (until something new changes its id)
MAX_SEEN = 600
MAX_PEOPLE = 2000
GIST_MAX = 110
VIP_START = 0.6

BASE = {"now": 70.0, "approval": 62.0, "needs_you": 58.0, "message": 40.0, "next_up": 32.0}
STEP = {"open": 0.15, "reply": 0.25, "draft": 0.2, "dismiss": -0.35, "ignored": -0.1}
ACTIONS = ("open", "reply", "draft", "dismiss", "pin", "unpin")
APP_LABEL = {"telegram": "Telegram", "discord": "Discord", "messenger": "Messenger", "phone": "Phone"}
ALL_CLEAR = "All clear. Nothing needs you right now."

_KV = re.compile(r"(?i)\b(pass(?:word)?|passwd|secret|token|api[_-]?key|apikey|bearer|private[_-]?key|pin)\b"
                 r"(\s*(?:is|:|=)\s*)(\S+)")
_LONG = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{28,}\b")


def redact(text: str) -> str:
    """Hide anything that looks like a password or key in a gist."""
    text = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[hidden]", text)
    return _LONG.sub("[hidden]", text)


def gist(text: str, limit: int = GIST_MAX) -> str:
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    line = redact(" ".join(line.split()))
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def ago_words(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{s // 60} minutes ago"
    if s < 7200:
        return "an hour ago"
    if s < 86400:
        return f"{s // 3600} hours ago"
    return "yesterday" if s < 2 * 86400 else f"{s // 86400} days ago"


def in_words(seconds: float) -> str:
    s = int(seconds)
    if s <= 0:
        return "now" if s > -3600 else "overdue"
    if s < 3600:
        return f"in {max(1, s // 60)} minutes"
    if s < 7200:
        return "in about an hour"
    return f"in {s // 3600} hours" if s < 86400 else f"in {s // 86400} days"


def reply_words(seconds: float) -> str:
    if seconds < 15 * 60:
        return "within minutes"
    if seconds < 3600:
        return "within the hour"
    if seconds < 6 * 3600:
        return "within a few hours"
    return "the same day" if seconds < 86400 else "after a day or more"


def _person(name: str) -> str:
    return " ".join(str(name or "").lower().split())[:120]


# ===================================================================================== the model
class FocusModel:
    """Learned weights and card memory, kept in a small JSON file. Thread-safe."""

    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(data_dir) / "focus.json"
        self.clock = clock
        self._lock = threading.RLock()
        self.data = self._load()

    # ------------------------------------------------------------------ storage
    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "senders": {}, "apps": {}, "kinds": {}, "pins": {}, "hidden": {}, "seen": {}}

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        data = self._empty()
        if isinstance(raw, dict):
            for k in data:
                if isinstance(raw.get(k), dict):
                    data[k] = raw[k]
        return data

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as exc:
                log.warning("Could not save focus weights: %s", exc)

    # ------------------------------------------------------------------ weights
    def weight(self, table: str, key: str, prior: float = 0.0) -> float:
        """The weight now: what was learned, drifting back to `prior` (half every HALF_LIFE_DAYS)."""
        with self._lock:
            e = self.data[table].get(key)
            if not isinstance(e, dict):
                return prior
            w = float(e.get("w", prior))
            age_d = max(0.0, self.clock() - float(e.get("at", self.clock()))) / 86400.0
            return prior + (w - prior) * 0.5 ** (age_d / HALF_LIFE_DAYS)

    def entry(self, table: str, key: str) -> dict[str, Any]:
        with self._lock:
            e = self.data[table].get(key)
            return dict(e) if isinstance(e, dict) else {}

    def nudge(self, table: str, key: str, step: float, prior: float = 0.0, count: Optional[str] = None) -> float:
        with self._lock:
            w = self.weight(table, key, prior)
            e = self.data[table].setdefault(key, {})
            e["w"] = round(max(-1.0, min(1.0, w + step)), 4)
            e["at"] = self.clock()
            if count:
                e[count] = int(e.get(count, 0)) + 1
            if len(self.data[table]) > MAX_PEOPLE:   # forget the stalest people first
                for k in sorted(self.data[table], key=lambda k: self.data[table][k].get("at", 0))[:200]:
                    self.data[table].pop(k, None)
            return e["w"]

    # ------------------------------------------------------------------ pins, hides, seen
    def pinned(self, cid: str) -> bool:
        return cid in self.data["pins"]

    def hidden(self, cid: str) -> bool:
        until = self.data["hidden"].get(cid)
        return isinstance(until, (int, float)) and until > self.clock()

    def first_seen(self, cid: str) -> float:
        with self._lock:
            e = self.data["seen"].setdefault(cid, {"at": self.clock()})
            if len(self.data["seen"]) > MAX_SEEN:
                for k in sorted(self.data["seen"], key=lambda k: self.data["seen"][k].get("at", 0))[:100]:
                    self.data["seen"].pop(k, None)
            return float(e.get("at", self.clock()))

    def mark_ignored(self, cid: str) -> bool:
        """True the first time a card counts as ignored (it is only counted once)."""
        with self._lock:
            e = self.data["seen"].setdefault(cid, {"at": self.clock()})
            if e.get("ignored") or e.get("acted"):
                return False
            e["ignored"] = True
            return True

    def mark_acted(self, cid: str) -> None:
        with self._lock:
            self.data["seen"].setdefault(cid, {"at": self.clock()})["acted"] = True

    def tidy(self) -> None:
        now = self.clock()
        with self._lock:
            self.data["hidden"] = {k: v for k, v in self.data["hidden"].items()
                                   if isinstance(v, (int, float)) and v > now}


# ===================================================================================== the dashboard
class Focus:
    def __init__(self, daemon: Any, data_dir: Optional[Path] = None, clock: Callable[[], float] = time.time) -> None:
        self.d = daemon
        if data_dir is None:
            from config import settings
            data_dir = settings.data_dir
        self.model = FocusModel(Path(data_dir), clock)
        self.clock = clock
        self._last: dict[str, dict[str, Any]] = {}   # card id -> card, from the last ranking

    # ------------------------------------------------------------------ ranking
    def cards(self, limit: int = MAX_CARDS) -> dict[str, Any]:
        limit = max(1, min(MAX_CARDS, int(limit or MAX_CARDS)))
        cands: list[dict[str, Any]] = []
        for part in (self._now, self._messages, self._approvals, self._needs_you, self._next_up):
            try:
                cands += part()
            except Exception:
                log.debug("focus: %s skipped", part.__name__, exc_info=True)
        self._learn_from_ignored(cands)
        for c in cands:
            if self.model.pinned(c["id"]):
                c["pinned"] = True
                c["score"] = round(c["score"] + 100.0, 1)
                c["why"] = "You pinned this."
        self._last = {c["id"]: c for c in cands}
        cands = [c for c in cands if c.get("pinned") or not self.model.hidden(c["id"])]
        cands = [c for c in cands if c.get("pinned") or c["kind"] != "message" or c["score"] >= MESSAGE_MIN_SCORE]
        cands.sort(key=lambda c: -c["score"])
        out: list[dict[str, Any]] = []
        per = {"message": 0, "needs_you": 0}
        caps = {"message": MAX_MESSAGES, "needs_you": MAX_NEEDS_YOU}
        for c in cands:
            k = c["kind"]
            if k in caps and not c.get("pinned"):
                if per[k] >= caps[k]:
                    continue
                per[k] += 1
            out.append(c)
            if len(out) >= limit:
                break
        self.model.tidy()
        self.model.save()
        return {"cards": out, "all_clear": not out, "line": ALL_CLEAR if not out else "",
                "generated_at": self.clock()}

    def _learn_from_ignored(self, cands: list[dict[str, Any]]) -> None:
        now = self.clock()
        for c in cands:
            if c["kind"] != "message":
                continue
            seen = self.model.first_seen(c["id"])
            if now - seen >= IGNORE_AFTER_S and self.model.mark_ignored(c["id"]):
                m = c["meta"]
                self.model.nudge("senders", m["person"], STEP["ignored"], self._prior(m), count="ignored")

    def _prior(self, meta: dict[str, Any]) -> float:
        return VIP_START if meta.get("vip") else 0.0

    # ------------------------------------------------------------------ now
    def _now(self) -> list[dict[str, Any]]:
        mc = getattr(self.d, "mission_control", None)
        missions = []
        if mc is not None:
            try:
                missions = [m for m in mc.list() if m.get("state") == "running"]
            except Exception:
                missions = []
        if missions:
            m = max(missions, key=lambda x: x.get("started") or x.get("created") or 0)
            phase = str(m.get("phase") or "working")
            step = {"working": (1, "Working on it"), "saving": (2, "Saving its changes"),
                    "checking": (3, "Checking the work (build, tests, review)")}.get(phase, (1, "Working on it"))
            waiting = "Your OK on the result, once the checks pass." if phase == "checking" else ""
            if m.get("paused"):
                waiting = "You paused it."
            cost, budget = m.get("cost_usd"), m.get("budget_usd")
            money = ""
            if isinstance(cost, (int, float)) and isinstance(budget, (int, float)) and budget:
                money = f" ${cost:.2f} of ${budget:.2f} spent."
            who = str(m.get("agent_label") or m.get("agent") or "An agent")
            return [self._card("now", f"now:mission:{m.get('id')}", str(m.get("title") or "A mission")[:120],
                               f"{who}: {step[1]}.{money}".strip(), "This is what Orion is working on right now.",
                               BASE["now"], open_={"window": "missions"},
                               meta={"progress": round(step[0] / 4, 2), "step": step[1], "waiting_on": waiting})]
        for a in self._agents():
            if a.get("state") == "working":
                name = str(a.get("name") or a.get("tool") or "An agent")[:60]
                return [self._card("now", f"now:agent:{a.get('id')}", f"{name} is working",
                                   str(a.get("status") or "Working")[:140], "A coding agent is busy right now.",
                                   BASE["now"] - 8, open_={"window": "agents"},
                                   meta={"progress": None, "step": str(a.get("status") or ""), "waiting_on": ""})]
        return []

    # ------------------------------------------------------------------ messages
    def _messages(self) -> list[dict[str, Any]]:
        hub = getattr(self.d, "comms", None)
        threads = list(getattr(hub, "threads", {}).values()) if hub is not None else []
        now = self.clock()
        out = []
        for t in threads:
            if not int(t.get("unread") or 0) or t.get("muted"):
                continue
            msgs = list(t.get("messages") or [])
            last_in = next((m for m in reversed(msgs) if m.get("direction") == "in"), None)
            if last_in is None:
                continue
            sender = str(last_in.get("author") or t.get("thread_name") or "Someone")[:80]
            person = _person(sender)
            app = str(t.get("platform") or "messages")
            vip = bool(t.get("vip") or last_in.get("vip"))
            meta = {"person": person, "vip": vip}
            w_sender = self.model.weight("senders", person, self._prior(meta))
            w_app = self.model.weight("apps", app)
            w_kind = self.model.weight("kinds", "message")
            habit = reply_habit(msgs)
            ts = float(last_in.get("timestamp") or now)
            fresh = max(0.25, 0.5 ** (max(0.0, now - ts) / 3600.0 / MESSAGE_HALF_LIFE_H))
            unread = int(t.get("unread") or 0)
            habit_bonus = 0.0
            if habit:
                habit_bonus = 20.0 * habit["rate"] * (1.0 if habit["median_s"] < 3600 else 0.6)
            score = (BASE["message"] + 30 * w_sender + 10 * w_app + 15 * w_kind + habit_bonus
                     + (12 if vip else 0)) * fresh + min(unread, 5) * 2
            why = self._message_why(sender, vip, habit, w_sender, self.model.entry("senders", person), ts, now)
            first = sender.split()[0] if sender.split() else sender
            title = f"{sender} on {APP_LABEL.get(app, app.title())}"
            detail = gist(last_in.get("text", ""))
            cid = f"msg:{t.get('key')}:{last_in.get('id') or int(ts)}"
            out.append(self._card("message", cid, title, detail, why, score, open_={"window": "messages",
                                  "thread": t.get("key")}, actions=["open", "draft"],
                                  meta={"sender": sender, "first_name": first, "app": app,
                                        "app_label": APP_LABEL.get(app, app.title()), "gist": detail,
                                        "unread": unread, "thread": t.get("key"), "ts": ts, "vip": vip,
                                        "person": person}))
        return out

    def _message_why(self, sender: str, vip: bool, habit: Optional[dict[str, Any]], w: float,
                     e: dict[str, Any], ts: float, now: float) -> str:
        first = sender.split()[0] if sender.split() else sender
        if habit and habit["rate"] >= 0.5:
            return f"You usually reply to {first} {reply_words(habit['median_s'])}."
        if vip:
            return f"{first} is on your VIP list."
        if int(e.get("open", 0)) + int(e.get("draft", 0)) + int(e.get("reply", 0)) >= 2 and w > 0.2:
            return f"You usually open {first}'s messages."
        if habit and habit["rate"] > 0:
            return f"You sometimes reply to {first}."
        return f"New from {first}, {ago_words(now - ts)}."

    # ------------------------------------------------------------------ approvals
    def _approvals(self) -> list[dict[str, Any]]:
        q = getattr(self.d, "approvals", None)
        items = [i for i in (q.pending() if q is not None else []) if i.get("status", "pending") == "pending"]
        if not items:
            return []
        rank = {"high": 0, "medium": 1, "low": 2}

        def risk(i: dict[str, Any]) -> str:
            r = str(i.get("risk") or "").lower()
            return r if r in rank else ("high" if i.get("category") == "urgent" else "medium")

        items.sort(key=lambda i: (rank[risk(i)], -(float(i.get("created_at") or 0))))
        top = items[0]
        title = " ".join(str(top.get("title") or top.get("channel") or str(top.get("kind") or "").replace("_", " ")
                             or "Something").split())[:140]
        if _KV.search(title):
            title = "Needs your OK (details in the queue)"
        n = len(items)
        head = "1 thing is waiting for your OK" if n == 1 else f"{n} things are waiting for your OK"
        w_kind = self.model.weight("kinds", "approval")
        score = BASE["approval"] + min(n, 5) * 2 + (8 if risk(top) == "high" else 0) + 15 * w_kind
        why = "Orion won't do these until you say yes." + (" The top one is high risk." if risk(top) == "high" else "")
        return [self._card("approval", f"approval:{top.get('id')}", head, f"Top: {title}", why, score,
                           open_={"window": "queue"}, meta={"count": n, "top_id": str(top.get("id") or ""),
                                                            "top_title": title, "risk": risk(top)})]

    # ------------------------------------------------------------------ needs you
    def _agents(self) -> list[dict[str, Any]]:
        tk = getattr(self.d, "agent_toolkit", None)
        try:
            if tk is not None and hasattr(tk, "agents"):
                return [a for a in tk.agents() if isinstance(a, dict)]
        except Exception:
            pass
        return []

    def _needs_you(self) -> list[dict[str, Any]]:
        out = []
        w_kind = self.model.weight("kinds", "needs_you")
        base = BASE["needs_you"] + 15 * w_kind
        mc = getattr(self.d, "mission_control", None)
        if mc is not None:
            for m in mc.list():
                if m.get("state") in ("needs_you", "failed"):
                    if m.get("state") == "failed" and self.clock() - float(m.get("finished") or 0) > 86400:
                        continue
                    word = "needs you" if m["state"] == "needs_you" else "didn't finish"
                    out.append(self._card("needs_you", f"needs:mission:{m.get('id')}:{m['state']}",
                                          f"Mission {word}: {str(m.get('title') or '')[:90]}",
                                          str(m.get("summary") or m.get("why") or "Open it to see what happened.")[:160],
                                          "A mission stopped and is waiting for you.", base + 4,
                                          open_={"window": "missions"}))
        for a in self._agents():
            if a.get("state") in ("stuck", "failed"):
                name = str(a.get("name") or a.get("tool") or "An agent")[:60]
                reason = str(a.get("reason") or a.get("status") or "")[:140]
                word = "is stuck" if a["state"] == "stuck" else "hit an error"
                out.append(self._card("needs_you", f"needs:agent:{a.get('id')}:{a['state']}", f"{name} {word}",
                                      reason or "Open Agents to see it.",
                                      f"{name} has stopped making progress." if a["state"] == "stuck"
                                      else f"{name} ran into a problem.", base + 2, open_={"window": "agents"}))
        mon = getattr(self.d, "health", None)
        last = getattr(mon, "last", None) or {}
        if last.get("status") in ("warn", "fail"):
            words = redact(" ".join(str(last.get("words") or "Something needs a look.").split()))[:160]
            out.append(self._card("needs_you", f"needs:health:{last.get('status')}",
                                  "Something on this computer needs a look", words,
                                  "Orion's health check found a problem." if last["status"] == "fail"
                                  else "Orion's health check found something to watch.",
                                  base + (6 if last["status"] == "fail" else -6), open_={"window": "analytics"}))
        tc = getattr(self.d, "test_center", None)
        if tc is not None and hasattr(tc, "view"):
            try:
                view = tc.view()
                fails = [it for g in view.get("groups", []) for it in g.get("items", []) if it.get("status") == "fail"]
            except Exception:
                fails = []
            if fails:
                n = len(fails)
                head = "1 check didn't work" if n == 1 else f"{n} checks didn't work"
                out.append(self._card("needs_you", f"needs:tests:{n}:{fails[0].get('id')}", head,
                                      f"First: {str(fails[0].get('title') or '')[:120]}",
                                      "The Test Center found something that isn't working.", base - 4,
                                      open_={"window": "tests"}))
        link = getattr(self.d, "phone_link", None)
        if link is not None and hasattr(link, "phones"):
            try:
                phones = link.phones()
            except Exception:
                phones = []
            now = self.clock()
            for p in phones[:1]:
                seen = p.get("last_seen")
                if isinstance(seen, (int, float)) and 3600 < now - seen < 86400:
                    out.append(self._card("needs_you", f"needs:phone:{p.get('device_id')}",
                                          f"{str(p.get('name') or 'Your phone')[:40]} hasn't connected",
                                          f"Last seen {ago_words(now - seen)}.",
                                          "Your phone usually stays in touch with this computer.", base - 26,
                                          open_={"window": "phone"}))
        return out

    # ------------------------------------------------------------------ next up
    def _next_up(self) -> list[dict[str, Any]]:
        now = self.clock()
        best: Optional[dict[str, Any]] = None
        kit = getattr(self.d, "trust_kit", None)
        pk = getattr(kit, "promises", None)
        if pk is not None:
            try:
                due = [p for p in pk.promises("open") if p.get("due") and float(p["due"]) - now < 86400]
            except Exception:
                due = []
            if due:
                p = min(due, key=lambda x: float(x["due"]))
                left = float(p["due"]) - now
                to = str(p.get("to") or "").strip()
                what = str(p.get("action") or p.get("text") or "Something you promised")[:120]
                best = self._card("next_up", f"next:promise:{p.get('id')}", what,
                                  f"Due {in_words(left)}" + (f" (you told {to})" if to else "") + ".",
                                  "You said you'd do this.",
                                  BASE["next_up"] + (14 if left < 3 * 3600 else 0) + 15 * self.model.weight("kinds", "next_up"),
                                  open_={"window": "life"}, meta={"due": float(p["due"])})
        ext = getattr(self.d, "ext", None)
        if best is None and ext is not None and hasattr(ext, "circadian"):
            try:
                c = ext.circadian()
                starts = c.get("wind_down_starts")
                ts = _to_ts(starts)
                if c.get("phase") == "normal" and ts and 0 < ts - now < 3 * 3600:
                    best = self._card("next_up", f"next:winddown:{int(ts) // 3600}", "Wind-down",
                                      f"Starts {in_words(ts - now)}.", "Your evening routine is coming up.",
                                      BASE["next_up"] - 4, open_={"window": "life"}, meta={"due": ts})
            except Exception:
                pass
        return [best] if best else []

    # ------------------------------------------------------------------ cards
    @staticmethod
    def _card(kind: str, cid: str, title: str, detail: str, why: str, score: float, *,
              open_: Optional[dict[str, Any]] = None, actions: Optional[list[str]] = None,
              meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return {"id": cid[:200], "kind": kind, "title": title, "detail": detail, "why": why,
                "score": round(float(score), 1), "pinned": False, "open": open_ or {},
                "actions": actions or ["open"], "meta": meta or {}}

    # ------------------------------------------------------------------ feedback
    def feedback(self, cid: str, action: str) -> dict[str, Any]:
        """Learn from what you did with a card. Never acts on anything else in Orion."""
        if action not in ACTIONS:
            raise ValueError("Unknown action. Use open, reply, draft, dismiss, pin or unpin.")
        cid = str(cid or "")[:200]
        if not cid:
            raise ValueError("Which card?")
        card = self._last.get(cid) or {"kind": cid.split(":", 1)[0].replace("msg", "message"), "meta": {}}
        kind = card.get("kind") or "message"
        meta = card.get("meta") or {}
        m = self.model
        if action == "pin":
            m.data["pins"][cid] = self.clock()
        elif action == "unpin":
            m.data["pins"].pop(cid, None)
        else:
            m.mark_acted(cid)
            step = STEP[action]
            if action == "dismiss":
                m.data["hidden"][cid] = self.clock() + HIDE_FOR_S
                m.data["pins"].pop(cid, None)
            if kind == "message" and meta.get("person"):
                m.nudge("senders", meta["person"], step, self._prior(meta), count=action)
                if meta.get("app"):
                    m.nudge("apps", str(meta["app"]), step / 3)
            m.nudge("kinds", str(kind), step / 3)
        m.save()
        return {"ok": True}

    # ------------------------------------------------------------------ drafts (never sent)
    async def draft(self, cid: str) -> dict[str, Any]:
        card = self._last.get(str(cid or "")) if cid else None
        if card is None or card["kind"] != "message":
            raise ValueError("That message isn't on the dashboard any more.")
        key = card["meta"].get("thread")
        hub = getattr(self.d, "comms", None)
        last = hub.last_inbound(key) if hub is not None and hasattr(hub, "last_inbound") else None
        text = str((last or {}).get("text") or "")
        context = hub.context_for(key) if hub is not None and hasattr(hub, "context_for") else []
        out = ""
        drafter = getattr(self.d, "drafter", None)
        orch = getattr(drafter, "orchestrator", None) or getattr(self.d, "orchestrator", None)
        if orch is not None and text:
            try:
                from alignment_drafter import ResponseDrafter
                local = ResponseDrafter(_LocalOnly(orch), getattr(drafter, "memory", None))
                res = await local.draft(platform=str(card["meta"].get("app") or "messages"), category="dm",
                                        channel=str(card["meta"].get("sender") or ""),
                                        author=str(card["meta"].get("sender") or ""), text=text,
                                        thread_context=list(context)[:-1])
                out = str(res.get("draft") or "")
            except Exception as exc:
                log.info("Focus draft: local model not available (%s); using your own phrases", exc)
        if not out.strip():
            out = self._plain(text)
        self.feedback(card["id"], "draft")
        return {"draft": redact(out.strip())[:1500], "thread": key}

    def _plain(self, text: str) -> str:
        hub = getattr(self.d, "comms", None)
        try:
            samples = hub.style_samples() if hub is not None else []
        except Exception:
            samples = []
        try:
            from alignment_drafter import offline_suggestions, style_profile
            return offline_suggestions(text, style_profile(samples))[0]
        except Exception:
            return "Got it, I'll get back to you soon."

    # ------------------------------------------------------------------ phone
    def for_phone(self) -> list[dict[str, Any]]:
        res = self.cards()
        return [{"id": c["id"], "kind": c["kind"], "title": c["title"][:120], "detail": c["detail"][:160],
                 "why": c["why"][:120], "pinned": c["pinned"]} for c in res["cards"]]


# ===================================================================================== helpers
def reply_habit(msgs: list[dict[str, Any]], limit: int = 200) -> Optional[dict[str, Any]]:
    """How often and how fast you answer in this thread: {"rate": 0..1, "median_s": float, "n": int} or None.

    Runs of their messages count as one; your next message after a run is the reply."""
    msgs = [m for m in msgs[-limit:] if isinstance(m, dict)]
    runs, delays = 0, []
    i = 0
    while i < len(msgs):
        if msgs[i].get("direction") != "in" or msgs[i].get("backfill"):
            i += 1
            continue
        j = i
        while j + 1 < len(msgs) and msgs[j + 1].get("direction") == "in":
            j += 1
        start = float(msgs[j].get("timestamp") or 0)
        runs += 1
        if j + 1 < len(msgs) and msgs[j + 1].get("direction") == "out":
            delays.append(max(0.0, float(msgs[j + 1].get("timestamp") or start) - start))
        i = j + 1
    done = runs - 1 if runs and not (msgs and msgs[-1].get("direction") == "out") else runs   # the newest run is still open
    if done < 2:
        return None
    delays.sort()
    median = delays[len(delays) // 2] if delays else math.inf
    return {"rate": round(min(1.0, len(delays) / done), 2), "median_s": median, "n": done}


def _to_ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            from datetime import datetime
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


class _LocalOnly:
    """Drafts with the local model (Ollama) only; no cloud model is ever asked."""

    def __init__(self, orch: Any) -> None:
        self.orch = orch

    async def frontier_text(self, system: str, user: str, **kw: Any) -> tuple[str, str]:
        orch = self.orch
        if not await orch.ollama_available():
            raise RuntimeError("the local model isn't running")
        body = {"model": orch.cfg.ollama_model, "system": system, "prompt": user, "stream": False,
                "options": {"temperature": kw.get("temperature", 0.4)}}
        r = await orch.http.post(f"{orch.cfg.ollama_url}/api/generate", json=body, timeout=120.0)
        r.raise_for_status()
        text = str(r.json().get("response") or "")
        if not text.strip():
            raise RuntimeError("the local model gave no draft")
        return text, orch.cfg.ollama_model


# ===================================================================================== wiring
from pydantic import BaseModel  # noqa: E402


class FeedbackBody(BaseModel):
    id: str
    action: str


class DraftBody(BaseModel):
    id: str


_focus: dict[int, Focus] = {}


def attach(daemon: Any, data_dir: Optional[Path] = None) -> Focus:
    f = _focus.get(id(daemon))
    if f is None:
        f = Focus(daemon, data_dir)
        _focus.clear()
        _focus[id(daemon)] = f
    return f


def for_phone(daemon: Any, data_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """The same top-5 list for GET /phone/home."""
    return attach(daemon, data_dir).for_phone()


def make_router(get_daemon: Callable[[], Any]):
    import asyncio

    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()
    @router.get("/focus")
    async def get_focus(limit: int = MAX_CARDS) -> dict[str, Any]:
        return await asyncio.to_thread(attach(get_daemon()).cards, limit)

    @router.post("/focus/feedback")
    async def post_feedback(body: FeedbackBody):
        try:
            return attach(get_daemon()).feedback(body.id, body.action)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/focus/draft")
    async def post_draft(body: DraftBody):
        try:
            return await attach(get_daemon()).draft(body.id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return router
