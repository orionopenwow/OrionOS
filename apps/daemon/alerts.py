"""Phone alerts that learn how you want to be told (release plan, Step 4b).

Every alert has a type: approval, openwow, messages, life, system. Each type sits at a level:

  urgent   sound + vibration
  normal   a regular notification
  digest   silent, bundled into one notification

Orion watches what you do with each alert on the phone (opened, how fast, swiped away) and moves a type up or
down one level at a time, at most every 3 days, telling you each time. You can lock any type. Approvals never go
below normal. It also learns quiet hours (hours of the day you never open anything): during them normal alerts
arrive as digest, except approvals.

Storage: data_dir/alerts.db (owner-only). Nothing leaves the machine except to your paired phone.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import statistics
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

TYPES = ("approval", "openwow", "messages", "life", "system")
LEVELS = ("digest", "normal", "urgent")  # low → high
DEFAULTS = {"approval": "urgent", "openwow": "normal", "messages": "normal", "life": "normal", "system": "normal"}
FLOOR = {"approval": "normal"}
LABELS = {"approval": "Approvals", "openwow": "OpenWoW", "messages": "Messages", "life": "Life", "system": "System"}

MIN_SAMPLES = 10
WINDOW = 20                  # look at the most recent N alerts of a type
CHANGE_COOLDOWN_S = 3 * 86400
FAST_OPEN_S = 300
QUIET_MIN_DAYS = 7
DAY = 86400

_center: Optional["AlertCenter"] = None


def emit(kind: str, title: str, body: str = "", ref: Optional[str] = None, card: Optional[str] = None,
         level: Optional[str] = None) -> None:
    """Fire-and-forget from anywhere in the daemon (agents call this next to their desktop notification).
    level may make an alert quieter (e.g. "digest" for the weekly review); only "urgent" can make it louder
    (tripwires: someone used planted bait)."""
    if _center is not None:
        try:
            _center.post(kind, title, body, ref=ref, card=card, level=level)
        except Exception:
            pass


class AlertCenter:
    def __init__(self, data_dir: Path, clock=time.time) -> None:
        self.clock = clock
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(data_dir, 0o700)
        path = data_dir / "alerts.db"
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS alerts(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, ts REAL,
                type TEXT, title TEXT, body TEXT, level TEXT, ref TEXT, card TEXT);
            CREATE TABLE IF NOT EXISTS events(alert_id TEXT, action TEXT, ts REAL, latency REAL,
                PRIMARY KEY(alert_id, action));
            CREATE TABLE IF NOT EXISTS policy(type TEXT PRIMARY KEY, level TEXT, locked INTEGER DEFAULT 0,
                changed REAL DEFAULT 0);
        """)
        self._waiters: list[asyncio.Future] = []

    # ------------------------------------------------------------------ helpers
    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.db.execute(sql, args)

    def policy(self, kind: str) -> dict[str, Any]:
        r = self._q("SELECT * FROM policy WHERE type=?", (kind,))
        if r:
            return dict(r[0])
        return {"type": kind, "level": DEFAULTS[kind], "locked": 0, "changed": 0}

    def _set_policy(self, kind: str, level: str, locked: Optional[bool] = None) -> None:
        p = self.policy(kind)
        lk = p["locked"] if locked is None else int(bool(locked))
        self._x("INSERT INTO policy(type, level, locked, changed) VALUES(?,?,?,?) ON CONFLICT(type) DO UPDATE "
                "SET level=excluded.level, locked=excluded.locked, changed=excluded.changed",
                (kind, level, lk, self.clock()))

    # ------------------------------------------------------------------ quiet hours
    def quiet_hours(self) -> list[int]:
        now = self.clock()
        rows = self._q("SELECT ts FROM events WHERE action='opened' AND ts>?", (now - 14 * DAY,))
        first = self._q("SELECT MIN(ts) AS t FROM alerts")
        if not rows or not first or first[0]["t"] is None or now - first[0]["t"] < QUIET_MIN_DAYS * DAY:
            return []
        active = {time.localtime(r["ts"]).tm_hour for r in rows}
        return [h for h in range(24) if h not in active]

    def effective_level(self, kind: str) -> str:
        level = self.policy(kind)["level"]
        if kind != "approval" and level == "normal" and time.localtime(self.clock()).tm_hour in self.quiet_hours():
            return "digest"
        return level

    # ------------------------------------------------------------------ posting and reading
    def post(self, kind: str, title: str, body: str = "", ref: Optional[str] = None,
             card: Optional[str] = None, level: Optional[str] = None) -> dict[str, Any]:
        if kind not in TYPES:
            kind = "system"
        lvl = self.effective_level(kind)
        if level == "urgent":
            lvl = "urgent"   # tripwires: always loud
        elif level in LEVELS and LEVELS.index(level) < LEVELS.index(lvl):
            lvl = level   # quieter only
        a = {"id": uuid.uuid4().hex[:16], "ts": self.clock(), "type": kind, "title": title[:120],
             "body": body[:500], "level": lvl, "ref": ref, "card": card}
        self._x("INSERT INTO alerts(id, ts, type, title, body, level, ref, card) VALUES(?,?,?,?,?,?,?,?)",
                (a["id"], a["ts"], kind, a["title"], a["body"], a["level"], ref, card))
        self._wake()
        return a

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, []
        for f in waiters:
            try:
                f.get_loop().call_soon_threadsafe(lambda f=f: f.done() or f.set_result(True))
            except RuntimeError:
                pass  # its loop is closed

    def feed(self, since: int = 0, limit: int = 50) -> dict[str, Any]:
        """Alerts after `since` (the phone's last seen number). Numbers only ever grow, so a phone that was away
        (PC asleep, daemon restarted) resumes exactly where it stopped: nothing lost, nothing twice.
        [phonelink] "last" is the number of the last alert *returned*, so a backlog bigger than `limit` arrives
        over several calls instead of being skipped ("more": true). A phone that is ahead of the PC (alerts.db
        replaced) is told "reset" with the last hour's alerts, so it can't stay stuck waiting for numbers that
        will never come."""
        top = self._q("SELECT MAX(seq) AS s FROM alerts")[0]["s"] or 0
        reset = since > top
        if reset:
            rows = self._q("SELECT * FROM alerts WHERE ts>? ORDER BY seq LIMIT ?", (self.clock() - 3600, limit))
        else:
            rows = self._q("SELECT * FROM alerts WHERE seq>? ORDER BY seq LIMIT ?", (since, limit))
        more = len(rows) == limit and rows[-1]["seq"] < top
        last = rows[-1]["seq"] if more else top
        out: dict[str, Any] = {"alerts": [dict(r) for r in rows], "last": last, "policy": self.summary()}
        if more:
            out["more"] = True
        if reset:
            out["reset"] = True
        return out

    async def wait_feed(self, since: int, timeout: float) -> dict[str, Any]:
        """Long-poll: return as soon as there's something newer than `since`, or after `timeout` seconds."""
        out = self.feed(since)
        if out["alerts"] or out.get("reset") or timeout <= 0:
            return out
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            pass
        return self.feed(since)

    def summary(self) -> list[dict[str, Any]]:
        return [{"type": t, "label": LABELS[t], "level": self.policy(t)["level"],
                 "locked": bool(self.policy(t)["locked"]), "floor": FLOOR.get(t, "digest")} for t in TYPES]

    # ------------------------------------------------------------------ feedback and learning
    def feedback(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """events: [{"id": alert id, "action": "opened"|"dismissed", "at": seconds}]. Returns policy changes."""
        touched = set()
        for e in events[:200]:
            action = e.get("action")
            if action not in ("opened", "dismissed"):
                continue
            r = self._q("SELECT ts, type FROM alerts WHERE id=?", (str(e.get("id", "")),))
            if not r:
                continue
            at = float(e.get("at") or self.clock())
            at = min(max(at, r[0]["ts"]), self.clock())
            self._x("INSERT OR IGNORE INTO events(alert_id, action, ts, latency) VALUES(?,?,?,?)",
                    (str(e["id"]), action, at, at - r[0]["ts"]))
            touched.add(r[0]["type"])
        return [c for c in (self.relearn(t) for t in sorted(touched)) if c]

    def relearn(self, kind: str) -> Optional[dict[str, Any]]:
        p = self.policy(kind)
        now = self.clock()
        if p["locked"] or now - (p["changed"] or 0) < CHANGE_COOLDOWN_S:
            return None
        rows = self._q(
            "SELECT a.id, a.ts, "
            " (SELECT latency FROM events e WHERE e.alert_id=a.id AND e.action='opened') AS opened, "
            " (SELECT 1 FROM events e WHERE e.alert_id=a.id AND e.action='dismissed') AS dismissed "
            "FROM alerts a WHERE a.type=? AND a.ts>? AND a.ts<? ORDER BY a.ts DESC LIMIT ?",
            (kind, max(now - 30 * DAY, p["changed"] or 0), now - 3600, WINDOW))
        if len(rows) < MIN_SAMPLES:
            return None
        n = len(rows)
        opened = [r["opened"] for r in rows if r["opened"] is not None]
        dismissed = sum(1 for r in rows if r["dismissed"] and r["opened"] is None)
        rate = len(opened) / n
        i = LEVELS.index(p["level"])
        new, why = i, ""
        if rate < 0.2 or dismissed / n > 0.6:
            new, why = i - 1, f"you open {round(rate * 100)}% of them"
        elif rate > 0.7 and opened and statistics.median(opened) < FAST_OPEN_S:
            new, why = i + 1, f"you open {round(rate * 100)}% of them, usually within " \
                              f"{max(1, round(statistics.median(opened) / 60))} min"
        floor = LEVELS.index(FLOOR.get(kind, "digest"))
        new = max(floor, min(new, len(LEVELS) - 1))
        if new == i:
            return None
        level = LEVELS[new]
        self._set_policy(kind, level)
        change = {"type": kind, "from": p["level"], "to": level, "why": why}
        self.post("system", f"{LABELS[kind]} alerts are now {level.capitalize()}",
                  f"Because {why}. Change or lock it in Orion > Alerts.")
        return change

    def set(self, kind: str, level: Optional[str] = None, locked: Optional[bool] = None) -> dict[str, Any]:
        if kind not in TYPES:
            raise ValueError("unknown alert type")
        p = self.policy(kind)
        if level is not None:
            if level not in LEVELS:
                raise ValueError("level must be urgent, normal or digest")
            if LEVELS.index(level) < LEVELS.index(FLOOR.get(kind, "digest")):
                raise ValueError(f"{LABELS[kind]} can't go below {FLOOR[kind]}")
        self._set_policy(kind, level or p["level"], locked)
        return self.policy(kind)

    def prune(self, keep_days: int = 60) -> None:
        cut = self.clock() - keep_days * DAY
        self._x("DELETE FROM events WHERE alert_id IN (SELECT id FROM alerts WHERE ts<?)", (cut,))
        self._x("DELETE FROM alerts WHERE ts<?", (cut,))


def install(center: AlertCenter) -> None:
    global _center
    _center = center
