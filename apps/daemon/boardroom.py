"""Boardroom: signed sign-off, a paper-money ledger and the autonomy learner (V3 sections 2-3, v0.1).

Two machines, two jobs:

  orion-lab (OrionOS)        runs this daemon. Agents *propose* decisions here.
  orion-boardroom (VM 2)     holds the only signing key. The owner reads each decision there and signs
                             approve / edit / reject. This daemon acts only on a valid signature.

An agent on the lab can therefore never approve its own work: it has no key. Approving from the HUD is
refused for boardroom decisions.

Money in v0.1 is *paper money*. Incoming support is simulated, split into five buckets by the V3 waterfall,
and an approved payout only writes a ledger entry marked SIMULATED. No wallet, exchange or payment API is
touched anywhere in this module, and none should be added without a fresh design review.

Wire protocol (boardroom -> lab, port 9121, reachable only over Tailscale):
  every request carries   X-Orion-Ts: <unix seconds>   X-Orion-Sig: <base64 ed25519 signature>
  signed message          b"ORION-BOARDROOM-v1\\n" + METHOD + "\\n" + PATH + "\\n" + TS + "\\n" + sha256hex(body)
  GET  /boardroom/pending                      decisions waiting for a verdict (+ ledger and autonomy summary)
  POST /boardroom/verdict {id, verdict, packet_hash, note?, edits?}
  POST /boardroom/pair    {pubkey, proof}      unsigned; proof = HMAC-SHA256(pairing code, pubkey bytes)

`packet_hash` binds the signature to the exact decision the owner read, so a packet changed after it was
displayed cannot be approved with an old signature. Timestamps must be fresh and strictly increasing.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import random
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.boardroom")

SIG_DOMAIN = b"ORION-BOARDROOM-v1\n"
MAX_CLOCK_SKEW_S = 120
PAIRING_TTL_S = 600
MAX_BODY = 64 * 1024


# =====================================================================================  task catalogue
@dataclass(frozen=True)
class TaskType:
    key: str
    label: str
    domain: str          # money | code | infra | community | autonomy
    reversible: bool
    money: bool          # moves (paper) money or spends budget
    sensitive: bool = False  # touches auth, payments, secrets, or is irreversible in effect

    @property
    def max_level(self) -> int:
        """Hard limits from V3 §2 — not learnable."""
        if self.domain == "autonomy":
            return 0                      # promotions always need a fresh signature
        if self.money or self.sensitive:
            return 1                      # money never graduates past "suggest + still ask"
        if not self.reversible:
            return 1
        return 3


TASK_TYPES: dict[str, TaskType] = {t.key: t for t in (
    TaskType("payout.bounty", "Bounty payout", "money", reversible=False, money=True),
    TaskType("payout.hosting", "Hosting bill", "money", reversible=False, money=True),
    TaskType("merge.docs", "Merge docs-only PR", "code", reversible=True, money=False),
    TaskType("merge.code", "Merge code PR to main", "code", reversible=True, money=False, sensitive=True),
    TaskType("community.faq", "Post FAQ answer as Orion", "community", reversible=True, money=False),
    TaskType("infra.scale", "Scale server tier", "infra", reversible=True, money=True),
    TaskType("autonomy.promotion", "Raise an autonomy level", "autonomy", reversible=True, money=False),
    # Real (not simulated) actions on the lab itself, run by the setup agent (setup_agent.py).
    TaskType("setup.workspace", "Install workspace apps", "system", reversible=False, money=False),
    TaskType("setup.clone", "Get a project's code", "system", reversible=True, money=False),
    TaskType("setup.voice", "Install voice for Orion", "system", reversible=False, money=False),
    # OpenWoW, run by the OpenWoW agent (wow_agent.py) through the `wow` command.
    TaskType("wow.restart", "Restart the OpenWoW server", "game", reversible=True, money=False),
    TaskType("wow.update", "Update OpenWoW's code", "game", reversible=True, money=False, sensitive=True),
    TaskType("wow.build", "Build an OpenWoW release", "game", reversible=True, money=False, sensitive=True),
    # Crash healer (crash_healer.py): commit a tested fix on a new branch. Always asks.
    TaskType("heal.fix", "Apply a crash fix (new branch)", "code", reversible=True, money=False, sensitive=True),
)}

LEVELS = {0: "Ask", 1: "Suggest + still ask", 2: "Act + notify", 3: "Silent"}
# Clean (unedited) approvals in a row needed to *propose* each level.
PROMOTION_STREAK = {1: 10, 2: 30, 3: 100}

# V3 §3 waterfall, in cents. The last bucket takes the overflow.
BUCKETS: list[tuple[str, str, Optional[int]]] = [
    ("hosting", "Hosting", 60_00),
    ("api_fuel", "API fuel", 40_00),
    ("bounties", "Worker bounties", 150_00),
    ("upgrades", "Server upgrades", 300_00),
    ("good_causes", "Good causes", None),
]


def fingerprint(pub: bytes) -> str:
    h = hashlib.sha256(pub).hexdigest()[:20].upper()
    return "-".join(h[i:i + 4] for i in range(0, 20, 4))


def packet_hash(packet: dict[str, Any]) -> str:
    canon = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def signing_message(method: str, path: str, ts: str, body: bytes) -> bytes:
    return SIG_DOMAIN + f"{method.upper()}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}".encode()


def verify_signature(pub: bytes, message: bytes, sig_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(base64.b64decode(sig_b64, validate=True), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def fmt_usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


# =====================================================================================  storage
class Store:
    """SQLite, one connection guarded by a lock (the daemon calls in from threads and the event loop)."""

    def __init__(self, path: Path) -> None:
        import os
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path.parent, 0o700)  # holds the pairing code and the ledger: owner only
        except OSError:
            pass
        old_umask = os.umask(0o077)
        try:
            self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self.db.execute("PRAGMA user_version")  # create the file now, under the strict umask
        finally:
            os.umask(old_umask)
        for f in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, created REAL, task_type TEXT, summary TEXT, packet TEXT,
                packet_hash TEXT, status TEXT, verdict TEXT, note TEXT, edits TEXT,
                decided REAL, signer TEXT, level INTEGER, executed TEXT);
            CREATE TABLE IF NOT EXISTS autonomy (
                task_type TEXT PRIMARY KEY, level INTEGER DEFAULT 0, streak INTEGER DEFAULT 0,
                approvals INTEGER DEFAULT 0, edits INTEGER DEFAULT 0, rejections INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0, promotion_pending TEXT);
            CREATE TABLE IF NOT EXISTS ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, bucket TEXT,
                amount_cents INTEGER, memo TEXT, decision_id TEXT, prev_hash TEXT, hash TEXT);
            CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
            """)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.db.execute(sql, args)

    def get(self, k: str, default: Optional[str] = None) -> Optional[str]:
        rows = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return rows[0]["v"] if rows else default

    def put(self, k: str, v: Optional[str]) -> None:
        if v is None:
            self.x("DELETE FROM kv WHERE k=?", (k,))
        else:
            self.x("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


# =====================================================================================  paper ledger
class PaperLedger:
    """Append-only, hash-chained ledger of *simulated* money. Balances are derived, never stored."""

    def __init__(self, store: Store) -> None:
        self.s = store

    def _append(self, kind: str, bucket: str, cents: int, memo: str, decision_id: Optional[str] = None) -> dict:
        with self.s.lock:
            last = self.s.q("SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1")
            prev = last[0]["hash"] if last else "GENESIS"
            ts = time.time()
            body = json.dumps([prev, ts, kind, bucket, cents, memo, decision_id], separators=(",", ":"))
            h = hashlib.sha256(body.encode()).hexdigest()
            self.s.x("INSERT INTO ledger(ts, kind, bucket, amount_cents, memo, decision_id, prev_hash, hash) "
                     "VALUES(?,?,?,?,?,?,?,?)", (ts, kind, bucket, cents, memo, decision_id, prev, h))
        return {"ts": ts, "kind": kind, "bucket": bucket, "amount_cents": cents, "memo": memo, "hash": h}

    def balances(self) -> dict[str, int]:
        out = {b: 0 for b, _, _ in BUCKETS}
        for r in self.s.q("SELECT bucket, SUM(amount_cents) AS c FROM ledger GROUP BY bucket"):
            out[r["bucket"]] = int(r["c"] or 0)
        return out

    def receive(self, cents: int, source: str) -> list[dict]:
        """Waterfall an incoming (simulated) donation into the buckets, in order, up to each cap."""
        if cents <= 0:
            raise ValueError("amount must be positive")
        bal, left, entries = self.balances(), cents, []
        for key, _label, cap in BUCKETS:
            if left <= 0:
                break
            room = left if cap is None else max(0, cap - bal[key])
            take = min(room, left)
            if take > 0:
                entries.append(self._append("credit", key, take, f"SIMULATED donation via {source}"))
                left -= take
        return entries

    def debit(self, bucket: str, cents: int, memo: str, decision_id: str) -> dict:
        with self.s.lock:  # check and append atomically: no overdraft from concurrent callers
            have = self.balances().get(bucket, 0)
            if cents <= 0 or have < cents:
                raise ValueError(f"{bucket} has only {fmt_usd(have)}")
            return self._append("debit", bucket, -cents, f"SIMULATED payout: {memo}", decision_id)

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for r in self.s.q("SELECT * FROM ledger ORDER BY seq"):
            body = json.dumps([prev, r["ts"], r["kind"], r["bucket"], r["amount_cents"], r["memo"],
                               r["decision_id"]], separators=(",", ":"))
            if r["prev_hash"] != prev or hashlib.sha256(body.encode()).hexdigest() != r["hash"]:
                return False
            prev = r["hash"]
        return True

    def recent(self, limit: int = 12) -> list[dict]:
        return [dict(r) for r in self.s.q("SELECT * FROM ledger ORDER BY seq DESC LIMIT ?", (limit,))]

    def summary(self) -> dict[str, Any]:
        bal = self.balances()
        return {
            "simulated": True,
            "buckets": [{"key": k, "label": label, "cap_cents": cap, "balance_cents": bal[k]} for k, label, cap in BUCKETS],
            "total_cents": sum(bal.values()),
            "chain_ok": self.verify_chain(),
            "recent": self.recent(),
        }


# =====================================================================================  autonomy learner
class AutonomyLearner:
    def __init__(self, store: Store) -> None:
        self.s = store
        for key in TASK_TYPES:
            self.s.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (key,))

    def row(self, task_type: str) -> dict[str, Any]:
        rows = self.s.q("SELECT * FROM autonomy WHERE task_type=?", (task_type,))
        if not rows:
            # Task types registered after start-up (missions, rulebook, freeze...) have no row yet: add it, so
            # summary() doesn't crash (/boardroom/state was a 500 on a fresh install) and record() counts.
            self.s.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (task_type,))
            rows = self.s.q("SELECT * FROM autonomy WHERE task_type=?", (task_type,))
        if rows:
            return dict(rows[0])
        return {"task_type": task_type, "level": 0, "streak": 0, "approvals": 0, "edits": 0, "rejections": 0,
                "pinned": 0, "promotion_pending": None}

    def level(self, task_type: str) -> int:
        tt = TASK_TYPES[task_type]
        r = self.row(task_type)
        return 0 if r.get("pinned") else min(int(r["level"]), tt.max_level)

    def record(self, task_type: str, verdict: str) -> Optional[int]:
        """Update stats after a signed verdict. Returns a level worth proposing, if one is now earned."""
        if task_type == "autonomy.promotion":
            return None
        r = self.row(task_type)
        level = int(r["level"])
        if verdict == "approve":
            self.s.x("UPDATE autonomy SET approvals=approvals+1, streak=streak+1 WHERE task_type=?", (task_type,))
        else:
            col = "edits" if verdict == "edit" else "rejections"
            # Any edit or rejection resets the streak, drops the task back one level (V3 §2) and cancels
            # a promotion that was still waiting, so an old promotion can't be approved later.
            pending = r.get("promotion_pending")
            self.s.x(f"UPDATE autonomy SET {col}={col}+1, streak=0, level=MAX(level-1, 0), promotion_pending=NULL "
                     "WHERE task_type=?", (task_type,))
            if pending:
                self.s.x("UPDATE decisions SET status='cancelled', decided=?, note=? WHERE id=? AND status='pending'",
                         (time.time(), f"cancelled: a {task_type} decision was {verdict}ed", pending))
            return None
        r = self.row(task_type)
        nxt = level + 1
        tt = TASK_TYPES[task_type]
        if (nxt <= tt.max_level and not r.get("pinned") and not r.get("promotion_pending")
                and r["streak"] >= PROMOTION_STREAK.get(nxt, 10 ** 9)):
            return nxt
        return None

    def set_level(self, task_type: str, level: int) -> int:
        level = max(0, min(level, TASK_TYPES[task_type].max_level))
        self.s.x("UPDATE autonomy SET level=?, streak=0, promotion_pending=NULL WHERE task_type=?", (level, task_type))
        return level

    def mark_pending(self, task_type: str, decision_id: Optional[str]) -> None:
        self.s.x("UPDATE autonomy SET promotion_pending=? WHERE task_type=?", (decision_id, task_type))

    def summary(self) -> list[dict[str, Any]]:
        out = []
        for key, tt in TASK_TYPES.items():
            if tt.domain == "autonomy":
                continue
            r = self.row(key)
            lvl = self.level(key)
            nxt = lvl + 1 if lvl + 1 <= tt.max_level else None
            out.append({
                "task_type": key, "label": tt.label, "domain": tt.domain, "level": lvl,
                "level_name": LEVELS[lvl], "max_level": tt.max_level, "streak": r["streak"],
                "approvals": r["approvals"], "edits": r["edits"], "rejections": r["rejections"],
                "next_level_at": PROMOTION_STREAK.get(nxt) if nxt else None,
            })
        return out


# =====================================================================================  boardroom
Publish = Callable[[dict[str, Any]], Awaitable[None]]


class Boardroom:
    def __init__(self, data_dir: Path, publish: Optional[Publish] = None, approvals: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.store = Store(self.data_dir / "boardroom.db")
        self.ledger = PaperLedger(self.store)
        from phones import PhoneRegistry
        self.phones = PhoneRegistry(self.store)
        self.learner = AutonomyLearner(self.store)
        self.publish = publish
        self.approvals = approvals  # mirror decisions into the HUD's approval queue (read-only there)
        self._sim_task: Optional[asyncio.Task] = None
        self._server: Any = None
        self._server_task: Optional[asyncio.Task] = None
        self._bind, self._port, self._stopping = "", 0, False   # [phonelink] for restarts and rebinding
        # task_type -> function(decision) -> result dict, for decisions that do real work once signed
        self.executors: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        # [review-fix 5] executors that take long (git merges...): run in a worker thread so the event loop keeps
        # answering; the verdict still waits for them, so approval works exactly the same.
        self.slow_executors: set[str] = set()
        self._executing: set[str] = set()
        self.remote_hooks: list[Callable[..., None]] = []     # extra phone routes (phone_api.register)
        self.on_proposed: list[Callable[[dict[str, Any]], None]] = []  # e.g. post a phone alert
        self.on_decided: list[Callable[[dict[str, Any]], None]] = []   # after a verdict (e.g. clean up on reject)

    # ----------------------------------------------------------------- pairing & keys
    @property
    def pubkey(self) -> Optional[bytes]:
        v = self.store.get("boardroom_pubkey")
        return base64.b64decode(v) if v else None

    def new_pairing_code(self) -> str:
        """One-time code shown on the lab; typed on the boardroom. Valid for 10 minutes. ~130 bits, so a
        sniffed pairing request can't be brute-forced back to the code inside the window."""
        if self.pubkey:
            raise ValueError("A boardroom is already paired. Unpair it first (orion-ctl unpair-boardroom).")
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
        raw = "".join(secrets.choice(alphabet) for _ in range(28))
        code = "-".join(raw[i:i + 4] for i in range(0, 28, 4))
        self.store.put("pairing", json.dumps({"code": code, "expires": time.time() + PAIRING_TTL_S, "fails": 0}))
        self.store.put("pending_pubkey", None)
        return code

    @staticmethod
    def _norm_code(code: str) -> str:
        return "".join(ch for ch in code.upper() if ch.isalnum())

    def pair(self, pub_b64: str, proof_hex: str) -> str:
        """Step 1 (from the boardroom): register a *pending* key. It signs nothing until the owner compares
        fingerprints and confirms on the lab (confirm_pending), which defeats a man in the middle."""
        raw = self.store.get("pairing")
        if self.pubkey or not raw:
            raise PermissionError("pairing is not open")
        pairing = json.loads(raw)
        if time.time() > pairing["expires"]:
            self.store.put("pairing", None)
            raise PermissionError("pairing code expired")
        try:
            pub = base64.b64decode(pub_b64, validate=True)
        except ValueError:
            raise ValueError("not an ed25519 public key")
        if len(pub) != 32:
            raise ValueError("not an ed25519 public key")
        want = hmac.new(self._norm_code(pairing["code"]).encode(), pub, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, proof_hex):
            pairing["fails"] = int(pairing.get("fails", 0)) + 1
            if pairing["fails"] >= 5:
                self.store.put("pairing", None)
                raise PermissionError("too many wrong codes; pairing closed. Make a new code on the lab.")
            self.store.put("pairing", json.dumps(pairing))
            raise PermissionError("wrong pairing code")
        self.store.put("pending_pubkey", base64.b64encode(pub).decode())
        fp = fingerprint(pub)
        log.warning("Boardroom key %s is waiting for confirmation on the lab", fp)
        return fp

    def pending_fingerprint(self) -> Optional[str]:
        v = self.store.get("pending_pubkey")
        return fingerprint(base64.b64decode(v)) if v else None

    def confirm_pending(self, fp: str) -> str:
        """Step 2 (on the lab, by the owner): accept the pending key if its fingerprint is the one the
        boardroom screen shows."""
        v = self.store.get("pending_pubkey")
        if not v or not self.store.get("pairing"):
            raise PermissionError("no key is waiting for confirmation")
        pub = base64.b64decode(v)
        if not hmac.compare_digest(self._norm_code(fingerprint(pub)), self._norm_code(fp)):
            raise PermissionError("fingerprint doesn't match the waiting key")
        self.store.put("boardroom_pubkey", v)
        self.store.put("pending_pubkey", None)
        self.store.put("pairing", None)
        # last_ts is never lowered: requests captured before a re-pair stay unreplayable.
        log.warning("Boardroom paired. Key fingerprint %s", fingerprint(pub))
        return fingerprint(pub)

    def unpair(self) -> None:
        self.store.put("boardroom_pubkey", None)
        self.store.put("pending_pubkey", None)
        self.store.put("pairing", None)

    def check_request(self, method: str, path: str, ts: Optional[str], sig: Optional[str], body: bytes) -> None:
        pub = self.pubkey
        if pub is None:
            raise PermissionError("no boardroom paired")
        if not ts or not sig:
            raise PermissionError("unsigned request")
        try:
            t = int(ts)
        except ValueError:
            raise PermissionError("bad timestamp")
        if abs(time.time() - t) > MAX_CLOCK_SKEW_S:
            raise PermissionError("stale request (check both clocks)")
        if not verify_signature(pub, signing_message(method, path, ts, body), sig):
            raise PermissionError("bad signature")
        with self.store.lock:  # replay guard: timestamps strictly increase
            if t <= int(self.store.get("last_ts", "0") or 0):
                raise PermissionError("replayed request")
            self.store.put("last_ts", str(t))

    # ----------------------------------------------------------------- decisions
    def _decision(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["packet"] = json.loads(d["packet"])
        for k in ("edits", "executed"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d

    def get(self, decision_id: str) -> Optional[dict[str, Any]]:
        rows = self.store.q("SELECT * FROM decisions WHERE id=?", (decision_id,))
        return self._decision(rows[0]) if rows else None

    def pending(self) -> list[dict[str, Any]]:
        return [self._decision(r) for r in self.store.q(
            "SELECT * FROM decisions WHERE status='pending' ORDER BY created")]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [self._decision(r) for r in self.store.q(
            "SELECT * FROM decisions WHERE status!='pending' ORDER BY COALESCE(decided, created) DESC LIMIT ?", (limit,))]

    async def propose(self, task_type: str, summary: str, details: dict[str, Any], proposer: str,
                      risk: str = "low", simulated: bool = True) -> dict[str, Any]:
        """An agent asks for a decision. Depending on the learned level it waits, or runs and notifies."""
        tt = TASK_TYPES[task_type]
        level = self.learner.level(task_type)
        packet = {
            "id": f"dec-{uuid.uuid4().hex[:10]}",
            "task_type": task_type, "label": tt.label, "domain": tt.domain,
            "summary": summary, "details": details, "proposer": proposer, "risk": risk,
            "money": tt.money, "simulated": simulated, "created": round(time.time(), 3),
            "autonomy_level": level, "recommendation": "approve" if level >= 1 else None,
        }
        h = packet_hash(packet)
        auto = level >= 2 and not __import__("freeze").is_frozen()  # reversible, non-money, non-sensitive; never while frozen
        self.store.x("INSERT INTO decisions(id, created, task_type, summary, packet, packet_hash, status, level) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     (packet["id"], packet["created"], task_type, summary, json.dumps(packet), h,
                      "pending", level))
        if auto:
            executed = self._execute(self.get(packet["id"]), {})
            self.store.x("UPDATE decisions SET status=?, verdict='auto', decided=?, executed=? WHERE id=?",
                         ("auto_executed", time.time(), json.dumps(executed), packet["id"]))
        decision = self.get(packet["id"])
        await self._mirror(decision)
        await self._emit({"event": "BOARDROOM_DECISION", "decision": decision})
        for hook in self.on_proposed:
            try:
                hook(decision)
            except Exception:
                log.exception("on_proposed hook failed")
        return decision

    async def verdict(self, decision_id: str, verdict: str, sent_hash: str, note: str = "",
                      edits: Optional[dict[str, Any]] = None, signer: str = "") -> dict[str, Any]:
        """Apply a verdict that has already passed check_request()."""
        if verdict not in ("approve", "edit", "reject"):
            raise ValueError("verdict must be approve, edit or reject")
        d = self.get(decision_id)
        if d is None:
            raise KeyError(decision_id)
        if d["status"] != "pending":
            raise ValueError(f"decision is already {d['status']}")
        # Recompute from the packet that will actually be executed: the stored hash alone could be stale.
        actual = packet_hash(d["packet"])
        if not (hmac.compare_digest(actual, d["packet_hash"]) and hmac.compare_digest(actual, sent_hash or "")):
            raise PermissionError("decision changed since it was shown; reload and read it again")
        edits = edits or {}
        executed: Optional[dict[str, Any]] = None
        status = "rejected"
        if verdict in ("approve", "edit"):
            if decision_id in self._executing:   # a second verdict while a slow executor runs: refuse, never twice
                raise ValueError("decision is already being carried out")
            self._executing.add(decision_id)
            try:
                if d["task_type"] in self.slow_executors:
                    executed = await asyncio.to_thread(self._execute, d, edits if verdict == "edit" else {})
                else:
                    executed = self._execute(d, edits if verdict == "edit" else {})
                status = "approved" if verdict == "approve" else "edited"
            except ValueError as exc:
                executed, status = {"error": str(exc)}, "failed"
            finally:
                self._executing.discard(decision_id)
        self.store.x("UPDATE decisions SET status=?, verdict=?, note=?, edits=?, decided=?, signer=?, executed=? "
                     "WHERE id=? AND status='pending'",
                     (status, verdict, note, json.dumps(edits) if edits else None, time.time(), signer,
                      json.dumps(executed) if executed else None, decision_id))
        if d["task_type"] == "autonomy.promotion":
            target = d["packet"]["details"]["task_type"]
            if status in ("approved", "edited"):
                pass  # _execute already applied the new level
            elif status == "failed":
                self.store.x("UPDATE autonomy SET promotion_pending=NULL WHERE task_type=?", (target,))
            else:  # declined: forget the streak so it isn't re-proposed on the next approval
                self.store.x("UPDATE autonomy SET promotion_pending=NULL, streak=0 WHERE task_type=?", (target,))
        elif status != "failed":
            promo = self.learner.row(d["task_type"]).get("promotion_pending")
            earned = self.learner.record(d["task_type"], verdict)
            if promo and (pd := self.get(promo)) and pd["status"] == "cancelled":
                await self._mirror(pd)
            if earned is not None:
                await self._propose_promotion(d["task_type"], earned)
        d = self.get(decision_id)
        for hook in self.on_decided:
            try:
                hook(d)
            except Exception:
                log.exception("on_decided hook failed")
        await self._mirror(d)
        await self._emit({"event": "BOARDROOM_DECISION", "decision": d})
        return d

    async def _propose_promotion(self, task_type: str, level: int) -> None:
        tt = TASK_TYPES[task_type]
        r = self.learner.row(task_type)
        dec = await self.propose(
            "autonomy.promotion",
            f"Let Orion move “{tt.label}” to level {level} ({LEVELS[level]})",
            {"task_type": task_type, "from_level": self.learner.level(task_type), "to_level": level,
             "clean_approvals_in_a_row": r["streak"],
             "what_changes": ("Orion will pre-fill its recommendation, but you still sign every one."
                              if level == 1 else
                              "Orion will do these on its own and tell you afterwards. You can undo them."
                              if level == 2 else "Orion will do these silently and only log them.")},
            proposer="Autonomy learner", risk="medium")
        self.learner.mark_pending(task_type, dec["id"])

    def _execute(self, d: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
        """Carry out an approved decision. Everything here is simulated in v0.1."""
        details = {**d["packet"]["details"], **{k: v for k, v in edits.items() if k in ("amount_cents", "memo")}}
        tt = d["task_type"]
        if tt.startswith("payout."):
            cents = int(details["amount_cents"])
            if cents <= 0 or cents > int(d["packet"]["details"]["amount_cents"]):
                raise ValueError("an edit may lower a payout, never raise it")
            entry = self.ledger.debit(details["bucket"], cents, details.get("memo", d["summary"]), d["id"])
            return {"simulated": True, "ledger_entry": entry["hash"], "amount": fmt_usd(cents),
                    "note": "Paper money only: no wallet or payment service was contacted."}
        if tt == "autonomy.promotion":
            if self.learner.level(details["task_type"]) != int(details["from_level"]):
                raise ValueError("this promotion is out of date (the level changed since it was proposed)")
            lvl = self.learner.set_level(details["task_type"], int(details["to_level"]))
            return {"task_type": details["task_type"], "level": lvl}
        if tt in self.executors:
            return self.executors[tt](d)
        return {"simulated": True, "note": f"Simulated: {TASK_TYPES[tt].label} recorded, no external action."}

    # ----------------------------------------------------------------- state / UI
    def state(self) -> dict[str, Any]:
        pub = self.pubkey
        return {
            "paired": pub is not None,
            "phones": self.phones.list(),
            "fingerprint": fingerprint(pub) if pub else None,
            "pending": self.pending(),
            "recent": self.recent(),
            "ledger": self.ledger.summary(),
            "autonomy": self.learner.summary(),
        }

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.publish:
            try:
                await self.publish(event)
                await self.publish({"event": "BOARDROOM_STATE", "state": self.state()})
            except Exception:
                log.debug("boardroom publish failed", exc_info=True)

    async def _mirror(self, d: dict[str, Any]) -> None:
        """Show the decision in the HUD's approval queue. The HUD can read it but not decide it."""
        if self.approvals is None or d is None:
            return
        status = {"pending": "pending", "rejected": "rejected", "cancelled": "rejected"}.get(d["status"], "sent")
        p = d["packet"]
        fields = dict(
            platform="boardroom", category="boardroom", channel=f"Boardroom · {p['label']}",
            author=p["proposer"], title=p["summary"], original=_describe(p),
            draft="Sign this on the boardroom VM. The HUD cannot approve boardroom decisions.",
            decision={"approved": "approved", "edited": "edited", "rejected": "rejected"}.get(d["status"]),
            error=(d.get("executed") or {}).get("error") if d["status"] == "failed" else None,
        )
        if self.approvals.get(d["id"]) is None:
            self.approvals.add("boardroom", id=d["id"], **fields)
            if status != "pending":
                self.approvals.update(d["id"], status=status)
        else:
            self.approvals.update(d["id"], status=status, **fields)
        item = self.approvals.get(d["id"])
        if self.publish and item:
            await self.publish({"event": "APPROVAL_REQUIRED" if status == "pending" else "APPROVAL_RESOLVED",
                                "item": item})

    # ----------------------------------------------------------------- simulator (stand-in agents)
    async def simulate(self, kind: str = "random") -> dict[str, Any]:
        """Stand-ins for the V3 CFO/CTO/Community agents until those exist."""
        kinds = ["donation", "bounty", "docs", "faq", "hosting"]
        kind = random.choice(kinds) if kind == "random" else kind
        n = random.randint(100, 999)
        if kind == "donation":
            cents = random.choice([5_00, 10_00, 25_00, 50_00, 100_00])
            source = random.choice(["Ko-fi", "Solana USDC", "Bitcoin"])
            entries = self.ledger.receive(cents, source)
            await self._emit({"event": "BOARDROOM_LEDGER", "entries": entries})
            return {"kind": "donation", "amount": fmt_usd(cents), "source": source, "entries": len(entries)}
        if kind in ("bounty", "hosting"):
            bucket = "bounties" if kind == "bounty" else "hosting"
            bal = self.ledger.balances()[bucket]
            if bal < 5_00:
                return await self.simulate("donation")
            cents = min(bal, random.choice([10_00, 20_00, 25_00, 40_00]) if kind == "bounty" else 30_00)
            who = f"contributor-{random.randint(1, 40):02d}"
            if kind == "bounty":
                summary = f"Pay {fmt_usd(cents)} bounty to {who} for issue #{n} (simulated)"
                memo = f"bounty issue #{n} to {who}"
                task = "payout.bounty"
            else:
                summary = f"Pay {fmt_usd(cents)} hosting bill (simulated)"
                memo, task = "monthly VPS bill", "payout.hosting"
            return await self.propose(task, summary, {"bucket": bucket, "amount_cents": cents, "memo": memo,
                                                      "payee": who if kind == "bounty" else "VPS provider"},
                                      proposer="CFO agent (simulated)", risk="high")
        if kind == "docs":
            return await self.propose("merge.docs", f"Merge docs-only PR #{n}: fix typos in README (simulated)",
                                      {"pr": n, "files": ["README.md"], "lines_changed": random.randint(2, 30)},
                                      proposer="CTO agent (simulated)", risk="low")
        if kind == "faq":
            return await self.propose("community.faq", f"Answer a Discord FAQ about realm downtime (simulated #{n})",
                                      {"channel": "#help", "draft": "The realm restarts daily at 04:00 UTC for backups."},
                                      proposer="Community agent (simulated)", risk="low")
        raise ValueError(f"unknown simulation {kind!r}")

    async def _sim_loop(self, every_s: float) -> None:
        await asyncio.sleep(5)
        if self.ledger.summary()["total_cents"] == 0:
            await self.simulate("donation")
        while True:
            try:
                await self.simulate("random")
            except Exception:
                log.exception("boardroom simulator tick failed")
            await asyncio.sleep(every_s)

    # ----------------------------------------------------------------- lifecycle / remote server
    async def start(self, simulator_every_s: float = 0.0, bind: str = "", port: int = 9121) -> None:
        if simulator_every_s > 0:
            self._sim_task = asyncio.create_task(self._sim_loop(simulator_every_s))
        if bind:
            self._bind, self._port, self._stopping = bind, port, False
            self._server_task = asyncio.create_task(self._serve_forever())   # [phonelink]
            for _ in range(40):   # wait (briefly) until it is listening, so callers can rely on it
                if self._server is not None and getattr(self._server, "started", False):
                    break
                await asyncio.sleep(0.05)
            log.info("Boardroom endpoint listening on %s:%d", bind, port)

    async def _serve_forever(self) -> None:
        """[phonelink] Keep the phone port up. uvicorn calls sys.exit() when it can't bind (the port still held
        after sleep/wake or a quick restart); that SystemExit would end the whole daemon. Catch it here and try
        again with a growing pause, and start again after a rebind (address change)."""
        import uvicorn
        import phone_link
        app = build_remote_app(self)
        pause = 1.0
        while not self._stopping:
            server = uvicorn.Server(uvicorn.Config(app, host=self._bind, port=self._port, log_level="warning",
                                                   **phone_link.server_config_kw()))
            server.install_signal_handlers = lambda: None  # the main server owns signals
            self._server = server
            began = time.monotonic()
            try:
                await server.serve()
            except asyncio.CancelledError:
                raise
            except (SystemExit, OSError, Exception) as exc:   # noqa: B014 - SystemExit is what uvicorn raises
                log.warning("Phone port %s:%d stopped (%s); trying again", self._bind, self._port, exc)
            if self._stopping:
                break
            if getattr(server, "_orion_rebind", False):
                pause = 0.2
            elif time.monotonic() - began > 60:
                pause = 1.0
            phone_link.note_restart()
            await asyncio.sleep(pause)
            pause = min(pause * 2, 30.0)

    async def rebind(self) -> None:
        """[phonelink] Bound to one address and that address changed: listen again."""
        srv = self._server
        if srv is not None:
            srv._orion_rebind = True
            srv.should_exit = True

    async def stop(self) -> None:
        if self._sim_task:
            self._sim_task.cancel()
        self._stopping = True
        if self._server:
            self._server.should_exit = True


def _describe(p: dict[str, Any]) -> str:
    d = p["details"]
    lines = [p["summary"]]
    if "amount_cents" in d:
        lines.append(f"Amount: {fmt_usd(int(d['amount_cents']))} from {d.get('bucket')} (paper money)")
    if p.get("recommendation"):
        lines.append(f"Orion recommends: {p['recommendation']} (autonomy level {p['autonomy_level']})")
    lines.append(f"Risk: {p['risk']} · proposed by {p['proposer']}")
    return "\n".join(lines)


def build_remote_app(board: Boardroom):
    """The only routes the boardroom VM can reach. Mounted on its own port; no HUD or WebSocket here."""
    app = FastAPI(title="Orion boardroom endpoint", docs_url=None, redoc_url=None, openapi_url=None)

    import phone_link   # [phonelink] server time, per-phone limits, idempotent approvals
    phone_link.install(app, board)   # [phonelink]

    async def _body(request: Request, limit: int = MAX_BODY) -> Optional[bytes]:
        try:
            if int(request.headers.get("content-length") or 0) > limit:
                return None
        except ValueError:
            return None
        body = await phone_link.read_body(request)   # [phonelink] time limit: half-open connections
        return body if body is not None and len(body) <= limit else None

    async def _json(request: Request) -> Optional[dict]:
        body = await _body(request)
        try:
            data = json.loads(body) if body else None
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def _auth(request: Request) -> Optional[JSONResponse]:
        body = await _body(request)
        if body is None:
            return JSONResponse({"error": "request too large"}, status_code=413)
        try:
            board.check_request(request.method, request.url.path, request.headers.get("x-orion-ts"),
                                request.headers.get("x-orion-sig"), body)
        except PermissionError as exc:
            log.warning("Boardroom request refused: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=401)
        return None

    @app.get("/boardroom/hello")
    async def hello() -> dict[str, Any]:
        return {"orion": "lab", "paired": board.pubkey is not None}

    @app.post("/boardroom/pair")
    async def pair(request: Request):
        data = await _json(request)
        if data is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        try:
            fp = board.pair(str(data.get("pubkey", "")), str(data.get("proof", "")))
        except (PermissionError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        await board._emit({"event": "BOARDROOM_PAIR_PENDING", "fingerprint": fp})
        return {"fingerprint": fp, "status": "waiting for confirmation on the lab"}

    # ---------------------------------------------------------------- Orion Mobile (phones)
    async def _phone(request: Request, need: str, limit: int = MAX_BODY):
        body = await _body(request, limit)
        if body is None:
            return None, JSONResponse({"error": "request too large"}, status_code=413)
        try:
            who = board.phones.check(request.headers.get("x-orion-device"), request.method, request.url.path,
                                     request.headers.get("x-orion-ts"), request.headers.get("x-orion-sig"),
                                     body, need)
        except PermissionError as exc:
            import phones as _ph   # [phonelink] a resend of an approval whose answer was lost: same answer again
            if isinstance(exc, _ph.ReplayError):
                again = phone_link.idem_replayed(request, board.store, request.headers.get("x-orion-device") or "", body)
                if again is not None:
                    return None, again
            log.warning("Phone request refused: %s", exc)
            return None, JSONResponse({"error": str(exc)}, status_code=401)
        cached = await phone_link.idem_begin(request, board.store, who["device_id"], body)   # [phonelink]
        if cached is not None:   # [phonelink]
            return who, cached   # [phonelink]
        return who, None

    @app.post("/phone/pair")
    async def phone_pair(request: Request):
        data = await _json(request)
        if data is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        try:
            fp = board.phones.pair(str(data.get("device_id", "")), str(data.get("name", "")),
                                   str(data.get("read_pub", "")), str(data.get("sign_pub", "")),
                                   str(data.get("proof", "")))
        except (PermissionError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        await board._emit({"event": "PHONE_PAIR_PENDING", "fingerprint": fp})
        out = {"fingerprint": fp, "status": "waiting for confirmation on the lab"}
        try:  # [review-fix 7] the PC's own key, for the phone to pin (proof made from the pairing code)
            import pc_identity
            code = json.loads(board.store.get("phone_pairing") or "{}").get("code", "")
            if code:
                out.update(await asyncio.to_thread(pc_identity.pair_extras, board, code))
        except Exception as exc:
            log.warning("PC key not added to the pairing answer: %s", exc)
        return out

    @app.get("/phone/pending")
    async def phone_pending(request: Request):
        who, err = await _phone(request, "read")
        if err is not None:
            return err
        st = board.state()
        # The phone hashes packet_canon itself and displays what it parsed from that same text, so a
        # fingerprint approval always covers exactly what was shown (no JSON canonicalisation on the phone).
        for d in st["pending"]:
            d["packet_canon"] = json.dumps(d["packet"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return {"phone": who, "pending": st["pending"], "recent": st["recent"][:10],
                "treasury": [{"label": b["label"], "balance_cents": b["balance_cents"]} for b in st["ledger"]["buckets"]],
                "chain_ok": st["ledger"]["chain_ok"], "autonomy": st["autonomy"]}

    @app.post("/phone/verdict")
    async def phone_verdict(request: Request):
        who, err = await _phone(request, "sign")
        if err is not None:
            return err
        data = await _json(request)
        if data is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        try:
            d = await board.verdict(str(data.get("id", "")), str(data.get("verdict", "")),
                                    str(data.get("packet_hash", "")), str(data.get("note", ""))[:500],
                                    data.get("edits") if isinstance(data.get("edits"), dict) else None,
                                    signer=f"phone:{who['name']} {who['fingerprint']}")
        except KeyError:
            return JSONResponse({"error": "unknown decision"}, status_code=404)
        except (ValueError, PermissionError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"decision": d}

    # More phone routes (alerts, chat, voice, cards) are added by the daemon: see phone_api.py.
    for hook in board.remote_hooks:
        hook(app, _phone, _json)

    @app.get("/boardroom/pending")
    async def pending(request: Request):
        if (err := await _auth(request)) is not None:
            return err
        st = board.state()
        return {"pending": st["pending"], "ledger": st["ledger"], "autonomy": st["autonomy"],
                "recent": st["recent"][:8]}

    @app.post("/boardroom/verdict")
    async def verdict(request: Request):
        if (err := await _auth(request)) is not None:
            return err
        data = await _json(request)
        if data is None:
            return JSONResponse({"error": "bad request"}, status_code=400)
        try:
            d = await board.verdict(str(data.get("id", "")), str(data.get("verdict", "")),
                                    str(data.get("packet_hash", "")), str(data.get("note", ""))[:500],
                                    data.get("edits") if isinstance(data.get("edits"), dict) else None,
                                    signer=fingerprint(board.pubkey or b""))
        except KeyError:
            return JSONResponse({"error": "unknown decision"}, status_code=404)
        except (ValueError, PermissionError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"decision": d}

    return app


# =====================================================================================  admin CLI (lab side)
def _admin(argv: list[str]) -> int:
    """Pairing is done here, not over HTTP: only the OrionOS session user can open its own data dir,
    so an agent process on the same machine can't pair a key of its own or unpair yours."""
    from config import settings

    board = Boardroom(settings.data_dir)
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "pair-code":
        try:
            code = board.new_pairing_code()
        except ValueError as exc:
            print(exc)
            return 1
        print(code)
        return 0
    if cmd == "pending":
        print(board.pending_fingerprint() or "")
        return 0
    if cmd == "confirm":
        try:
            print(board.confirm_pending(argv[2] if len(argv) > 2 else ""))
        except PermissionError as exc:
            print(exc)
            return 1
        return 0
    if cmd == "phone-code":
        print(board.phones.new_code())
        return 0
    if cmd == "phone-pending":
        print(board.phones.pending_fingerprint() or "")
        return 0
    if cmd == "phone-confirm":
        try:
            print(json.dumps(board.phones.confirm(argv[2] if len(argv) > 2 else "")))
        except PermissionError as exc:
            print(exc)
            return 1
        return 0
    if cmd == "phone-signkey":   # [H9] the paired phone's approval key, for root to pin (orion-ctl pair-phone)
        p = json.loads(board.store.get("phones", "{}") or "{}").get(argv[2] if len(argv) > 2 else "")
        print(p["sign"] if p else "")
        return 0 if p else 1
    if cmd == "phone-list":
        for p in board.phones.list():
            print(f"{p['device_id']}  {p['name']:<20} {p['fingerprint']}")
        return 0
    if cmd == "phone-remove":
        print(f"removed {board.phones.remove(argv[2] if len(argv) > 2 else '')} phone(s)")
        return 0
    if cmd == "unpair":
        board.unpair()
        print("Boardroom unpaired. Pair again with: orion-ctl pair-boardroom")
        return 0
    if cmd == "fingerprint":
        pub = board.pubkey
        print(fingerprint(pub) if pub else "not paired")
        return 0
    print("usage: python boardroom.py pair-code | pending | confirm <fp> | unpair | fingerprint | "
          "phone-code | phone-pending | phone-confirm <fp> | phone-list | phone-remove <id|all>")
    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_admin(sys.argv))
