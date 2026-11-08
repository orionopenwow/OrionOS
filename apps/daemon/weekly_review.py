"""Weekly self-review (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 8).

Every Sunday (local time, from REVIEW_HOUR) Orion puts up ONE card about the past seven days:
  - what broke         problems the health monitor saw (from the self-repair log), updates that were held back
  - what Orion fixed   self-repairs from the change history
  - what it updated    apps, models, security updates, system versions
  - what it would like to change next   proposals you accept or dismiss one by one: raise a level after a long
                       run of yeses (that still asks your phone), look into a problem that keeps coming back,
                       firmware that's waiting...
  - the AI cost        from the compute meter (an estimate)
Your phone gets a quiet (digest) note. Ask "how did this week go?" in chat for the same summary.

Accepting a proposal never changes anything by itself beyond the normal gates: raising a level still asks your
phone; looking into a problem only produces a diagnosis card.

HTTP (local):
    GET  /self/review                         the newest review (or a preview of this week when there's none)
    POST /self/review/now                      make this week's review now
    POST /self/review/{rid}/items/{iid}       {"accept": true|false}
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
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import self_levels

log = logging.getLogger("orion.weekly_review")

CARD_KIND = "self_review"
REVIEW_HOUR = 10
TICK_S = 1800.0
WEEK = 7 * 86400
UPDATE_KINDS = {"app_update", "runtime_update", "model_update", "security_update", "system_version",
                "app_install", "firmware"}
CHAT_RE = re.compile(r"^(orion,? )?(how (did|was|has) (this|the|my|your) week( been| go| gone)?|"
                     r"(give me |show me )?(the |your )?weekly (review|report|summary)|"
                     r"what did (you|orion) (do|fix|change|update) this week)\??$")


def _money(x: float) -> str:
    return f"${x:.2f}" if x >= 0.01 else "under a cent"


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


class Reviews:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "weekly_reviews.json"
        self.lock = threading.Lock()

    def all(self) -> list[dict[str, Any]]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def save_all(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-26:], indent=1, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, review: dict[str, Any]) -> None:
        with self.lock:
            items = self.all()
            items.append(review)
            self.save_all(items)

    def get(self, rid: str) -> Optional[dict[str, Any]]:
        return next((r for r in self.all() if r.get("id") == rid), None)

    def update(self, rid: str, fn: Callable[[dict[str, Any]], None]) -> Optional[dict[str, Any]]:
        with self.lock:
            items = self.all()
            for r in items:
                if r.get("id") == rid:
                    fn(r)
                    self.save_all(items)
                    return r
        return None

    def latest(self) -> Optional[dict[str, Any]]:
        items = self.all()
        return items[-1] if items else None


# =====================================================================================  building one
def _cost(daemon: Any, since: float) -> dict[str, Any]:
    meter = getattr(getattr(daemon, "orchestrator", None), "meter", None)
    if meter is None:
        return {"cost": None, "words": "The compute meter isn't running, so there's no cost to show."}
    try:
        day0 = date.fromtimestamp(since).isoformat()
        total = 0.0
        with meter._lock:
            days = {d: rows for d, rows in meter.days.items() if d > day0}
        for rows in days.values():
            total += meter._period(rows)["cost"]
        return {"cost": round(total, 4), "words": f"AI cost this week: about {_money(total)} (an estimate)."}
    except Exception:
        log.exception("couldn't read the compute meter")
        return {"cost": None, "words": "Couldn't read the compute meter."}


def build(daemon: Any, now: Optional[float] = None, repair: Any = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    since = now - WEEK
    try:
        import changes
        recent = [c for c in changes.current().list(1000) if float(c.get("ts") or 0) >= since]
    except Exception:
        recent = []
    seen = (repair.summary(since)["seen"] if repair is not None else [])
    counts = Counter(s.get("label") or s.get("check") for s in seen)
    broke = [f"{label} ({n}×)" if n > 1 else str(label) for label, n in counts.most_common(8)]
    broke += [c["what"] for c in recent if c.get("kind") == "system_test" and c.get("result") == "failed"]
    broke += [c["what"] for c in recent if c.get("kind") == "model_update" and c.get("result") == "rolled back"]
    fixed = [c["what"].replace("Self-repair: ", "") for c in recent
             if c.get("kind") == "repair" and c.get("result") in ("fixed", "ran")]
    updated = [c["what"] for c in recent if (c.get("kind") in UPDATE_KINDS or c.get("source") == "get-apps")
               and "failed" not in str(c.get("result"))]
    items: list[dict[str, Any]] = []

    def item(text: str, typ: str, **args: Any) -> None:
        items.append({"id": uuid.uuid4().hex[:8], "text": text, "type": typ, "args": args, "state": "open"})

    try:
        for row in self_levels.current().table():
            if row["raisable"] and not row["fingerprint"] and row["level"] < row["max"] \
                    and row["streak"] >= max(5, self_levels.STREAK_TO_SUGGEST // 2):
                nxt = row["level"] + 1
                item(f"You said yes to {row['streak']} “{row['label']}” in a row. Let Orion "
                     f"{self_levels.LEVEL_NAMES[nxt].lower()} for these? (asks your phone)", "raise_level",
                     kind=row["kind"], level=nxt)
    except Exception:
        pass
    by_check = Counter(s.get("check") for s in seen)
    for s in seen:
        cid = s.get("check")
        if by_check.get(cid, 0) >= 3 and not any(i["args"].get("check", {}).get("id") == cid for i in items):
            item(f"{s.get('label')} kept coming back ({by_check[cid]}×). Let Orion look into it?", "diagnose",
                 check={"id": cid, "label": s.get("label"), "status": "fail", "reason": s.get("reason"),
                        "fix": s.get("fix")})
    try:
        import self_updates
        st = self_updates.load_state()
        if st.get("firmware"):
            item(f"{len(st['firmware'])} firmware update(s) are waiting. Open the firmware updater?", "firmware")
        offer = st.get("offer") or {}
        if offer.get("passed"):
            item(f"OrionOS {offer.get('version')} passed the self-test and is waiting for you.", "note")
        if st.get("security_waiting"):
            item(f"{st['security_waiting']} security update(s) are waiting for your OK.", "note")
    except Exception:
        pass
    if repair is not None:
        for fid in (repair.summary(since).get("blocked") or {}):
            item(f"A fix ({fid.replace('_', ' ')}) made things worse, so Orion stopped trying it by itself.", "note")
    cost = _cost(daemon, since)
    today = date.fromtimestamp(now)
    return {"id": f"rev-{uuid.uuid4().hex[:8]}", "week": week_key(today), "at": now, "since": since,
            "broke": broke[:12], "fixed": fixed[:12], "updated": updated[:15], "items": items, "cost": cost}


def words(r: dict[str, Any]) -> str:
    def part(title: str, xs: list[str], none: str) -> str:
        return f"{title}: " + ("; ".join(xs[:6]) + (f" and {len(xs) - 6} more" if len(xs) > 6 else "") if xs
                              else none) + "."
    lines = [part("What broke", r.get("broke") or [], "nothing"),
             part("What Orion fixed", r.get("fixed") or [], "nothing needed fixing"),
             part("What it updated", r.get("updated") or [], "nothing"),
             (r.get("cost") or {}).get("words") or ""]
    open_items = [i for i in r.get("items") or [] if i.get("state") == "open"]
    if open_items:
        lines.append("It would like to: " + " | ".join(i["text"] for i in open_items[:5]))
    return "\n".join(x for x in lines if x)


# =====================================================================================  acting on proposals
async def act_on_item(daemon: Any, r: dict[str, Any], it: dict[str, Any], accept: bool, repair: Any = None) -> str:
    if not accept:
        return "Dismissed."
    typ, args = it.get("type"), it.get("args") or {}
    if typ == "raise_level":
        res = await self_levels.request_change(daemon, str(args.get("kind")), int(args.get("level", 1)),
                                               by="Weekly review", why=it.get("text", ""))
        return str(res.get("message", ""))
    if typ == "diagnose" and repair is not None:
        check = dict(args.get("check") or {})
        repair._update(lambda s: s["diagnosed"].pop(check.get("id"), None))
        res = await repair.diagnose(check)
        return "Orion looked into it; the result is a card." if res else "Orion couldn't look into it right now."
    if typ == "firmware":
        import self_updates
        await self_updates.firmware_job(daemon)
        return "The firmware card is up."
    return "Noted."


async def decide_item(daemon: Any, reviews: Reviews, rid: str, iid: str, accept: bool, repair: Any = None) -> dict[str, Any]:
    r = reviews.get(rid)
    if r is None:
        raise KeyError(rid)
    it = next((i for i in r.get("items") or [] if i.get("id") == iid), None)
    if it is None:
        raise KeyError(iid)
    if it.get("state") != "open":
        return {"ok": True, "message": f"Already {it['state']}."}
    import freeze
    if accept and freeze.is_frozen():
        return {"ok": False, "message": freeze.MESSAGE}
    msg = await act_on_item(daemon, r, it, accept, repair)

    def mark(rv: dict[str, Any]) -> None:
        for i in rv.get("items") or []:
            if i.get("id") == iid:
                i["state"] = "accepted" if accept else "dismissed"
                i["result"] = msg[:200]
    reviews.update(rid, mark)
    return {"ok": True, "message": msg}


async def decide_card(daemon: Any, item: dict[str, Any], decision: str, reviews: Reviews, repair: Any = None) -> None:
    """The whole card: Approve accepts every open proposal, Reject dismisses them all."""
    if daemon.approvals.claim(item["id"], "sending" if decision != "reject" else "rejected") is None:
        return
    rid = str(item.get("review_id", ""))
    r = reviews.get(rid)
    msgs = []
    for it in (r or {}).get("items") or []:
        if it.get("state") == "open":
            try:
                res = await decide_item(daemon, reviews, rid, it["id"], decision != "reject", repair)
                msgs.append(res["message"])
            except Exception:
                log.exception("review item failed")
    updated = daemon.approvals.update(item["id"], status="rejected" if decision == "reject" else "approved",
                                      result=" ".join(msgs)[:300] or "Seen.")
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})


# =====================================================================================  the Sunday card
async def make_review(daemon: Any, reviews: Reviews, repair: Any = None, now: Optional[float] = None) -> dict[str, Any]:
    r = build(daemon, now, repair)
    text = words(r)
    reviews.add(r)
    approvals = getattr(daemon, "approvals", None)
    if approvals is not None:
        open_n = len(r["items"])
        card = approvals.add(CARD_KIND, platform="Orion", channel="Weekly review", author="Orion", category="normal",
                             title="Your week with Orion", original=text,
                             draft=("Approve = yes to everything it would like to do. Reject = no to all of it. You "
                                    "can also answer each one separately." if open_n else "Approve = seen."),
                             review_id=r["id"])
        reviews.update(r["id"], lambda rv: rv.__setitem__("card", card["id"]))
        try:
            await daemon.bus.publish({"event": "APPROVAL_REQUIRED", "item": card})
        except Exception:
            pass
    try:
        import alerts
        alerts.emit("system", "Your week with Orion", text[:480], ref=f"review:{r['id']}", level="digest")
    except TypeError:     # an older alerts module without levels
        import alerts
        alerts.emit("system", "Your week with Orion", text[:480], ref=f"review:{r['id']}")
    except Exception:
        pass
    return r


def due(reviews: Reviews, now: float) -> bool:
    dt = datetime.fromtimestamp(now)
    if dt.weekday() != 6 or dt.hour < REVIEW_HOUR:
        return False
    last = reviews.latest()
    return not last or last.get("week") != week_key(dt.date())


class WeeklyReview:
    def __init__(self, daemon: Any, data_dir: Path, repair: Any = None, every_s: float = TICK_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.daemon = daemon
        self.reviews = Reviews(data_dir)
        self.repair = repair
        self.every_s = every_s
        self.clock = clock
        self._task: Optional[asyncio.Task] = None

    async def tick(self) -> Optional[dict[str, Any]]:
        now = self.clock()
        if due(self.reviews, now):
            return await make_review(self.daemon, self.reviews, self.repair, now)
        return None

    def current(self) -> dict[str, Any]:
        last = self.reviews.latest()
        if last and self.clock() - float(last.get("at") or 0) < WEEK:
            return {**last, "words": words(last)}
        r = build(self.daemon, self.clock(), self.repair)
        return {**r, "preview": True, "words": words(r)}

    def chat(self, text: str) -> Optional[str]:
        t = " ".join(re.sub(r"[^\w\s?,']", " ", (text or "").lower()).split())
        if not CHAT_RE.match(t):
            return None
        r = self.current()
        head = "This week so far:" if r.get("preview") else "Here's the week:"
        return f"{head}\n{r['words']}"

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("weekly review tick failed")
            await asyncio.sleep(self.every_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="weekly-review")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
