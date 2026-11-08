"""Stardust ($DUST) Engine — Ledger, Fuel Tanks, Forge Game Modding, Creator Tipping & Tokenomics.

Implements the complete architecture from `docs/STARDUST_PLAN.md`:
  1. Hash-chained SQLite ledger (`dust_accounts`, `dust_tanks`, `dust_events`, `dust_mods`, `dust_expenses`)
  2. Fuel Tank session allowances (one Boardroom approval opens a time/amount-bounded tank for Forge)
  3. Automatic Creator Royalty Splitter (60% remixer / 25% original author / 10% asset creator / 5% burn)
  4. Direct Creator Tipping across all game projects (97% creator / 3% burn)
  5. Clean-Room Game Inspector & Git-Backed Forge Mod Manager (auto-commit per prompt + 1-click git revert)
  6. Complete Tokenomics (1,000,000,000 DUST), Airdrop Merkle Calculator & 4-Tier Founder Financial Roadmap
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import subprocess
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.stardust")

try:   # routes resolve their annotations from module globals (this file uses `from __future__ import annotations`)
    from fastapi import Request
except ImportError:   # pragma: no cover - the engine works without FastAPI
    Request = Any  # type: ignore

# ==============================================================================
# TOKENOMICS CONSTANTS (1,000,000,000 $DUST HARD CAP)
# ==============================================================================
TOTAL_SUPPLY_DUST = 1_000_000_000
USD_CENTS_PER_DUST = 1  # Phase 0-1 compute peg: 100 DUST = $1.00 USD (1 DUST = $0.01)

TOKENOMICS_BUCKETS: dict[str, dict[str, Any]] = {
    "creator_modder_rewards": {
        "share_pct": 40.0,
        "tokens": 400_000_000,
        "vesting": "48-month emission tied to verified mod usage, forks, and Herald playtest bounties",
    },
    "compute_ecosystem_treasury": {
        "share_pct": 25.0,
        "tokens": 250_000_000,
        "vesting": "Multisig locked; max 2%/month unlocked only via signed Boardroom decision",
    },
    "founder_creator_allocation": {
        "share_pct": 15.0,
        "tokens": 150_000_000,
        "vesting": "10% Launch Recoup tranche + 12-month cliff, 36-month linear vesting across 4 tiers",
    },
    "community_airdrop": {
        "share_pct": 10.0,
        "tokens": 100_000_000,
        "vesting": "50% at Phase 3 TGE via Merkle proof; 50% over 6 months of active Forge usage",
    },
    "liquidity_reserves": {
        "share_pct": 10.0,
        "tokens": 100_000_000,
        "vesting": "Protocol-owned DEX liquidity on Base; LP tokens locked 24 months",
    },
}

# Royalty split basis points (10,000 bps = 100%)
ROYALTY_REMIXER_BPS = 6000      # 60%
ROYALTY_ORIGINAL_BPS = 2500     # 25%
ROYALTY_ASSET_BPS = 1000        # 10%
ROYALTY_BURN_BPS = 500          # 5%

# Direct tip basis points
TIP_CREATOR_BPS = 9700          # 97%
TIP_BURN_BPS = 300              # 3%

BURN_ACCOUNT = "system:burn"
TREASURY_ACCOUNT = "system:treasury"
FOUNDER_ACCOUNT = "founder:creator"


def _canon_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    raw = prev_hash + "|" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class StardustEngine:
    """Manages off-chain Stardust credits, Fuel Tanks, Forge mod lineages, creator royalties, and founder milestones."""

    def __init__(self, db_path: Path, secret_seed: Optional[str] = None) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Ephemeral HMAC key for Fuel Tank session receipts (bound to local daemon runtime)
        self._receipt_key = (secret_seed or uuid.uuid4().hex).encode("utf-8")
        self._init_db()

    from contextlib import contextmanager

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS dust_accounts (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 0,
                    total_earned INTEGER NOT NULL DEFAULT 0,
                    total_spent INTEGER NOT NULL DEFAULT 0,
                    total_tipped_in INTEGER NOT NULL DEFAULT 0,
                    total_tipped_out INTEGER NOT NULL DEFAULT 0,
                    genesis_points INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dust_tanks (
                    tank_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    cap_dust INTEGER NOT NULL,
                    spent_dust INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL,
                    boardroom_decision_id TEXT,
                    receipt_sig TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dust_mods (
                    mod_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    game TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    parent_mod_id TEXT,
                    original_author_id TEXT,
                    asset_author_id TEXT,
                    mod_dir TEXT,
                    forks_count INTEGER NOT NULL DEFAULT 0,
                    tips_dust INTEGER NOT NULL DEFAULT 0,
                    royalties_dust INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dust_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    target_id TEXT,
                    amount_dust INTEGER NOT NULL,
                    burned_dust INTEGER NOT NULL DEFAULT 0,
                    meta_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dust_expenses (
                    expense_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    amount_usd_cents INTEGER NOT NULL,
                    reimbursed_usd_cents INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL
                );
                """
            )
            # Ensure system accounts and default demo accounts exist
            now = time.time()
            for acct_id, name, starter in (
                (BURN_ACCOUNT, "Protocol Burn Sink", 0),
                (TREASURY_ACCOUNT, "Compute & Ecosystem Treasury", 250_000_000),
                (FOUNDER_ACCOUNT, "Creator & Founder Reserve", 0),
                ("user:local", "Local OrionOS User", 250),
            ):
                c.execute(
                    """
                    INSERT OR IGNORE INTO dust_accounts (account_id, display_name, balance, genesis_points, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (acct_id, name, starter, 50 if acct_id == "user:local" else 0, now),
                )
            c.commit()

    # ==========================================================================
    # HASH-CHAINED LEDGER RECORDING
    # ==========================================================================
    def _append_event(
        self,
        conn: sqlite3.Connection,
        kind: str,
        actor_id: str,
        target_id: Optional[str],
        amount_dust: int,
        burned_dust: int = 0,
        meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        row = conn.execute("SELECT entry_hash FROM dust_events ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = row["entry_hash"] if row else "0" * 64
        event_id = f"dst-{uuid.uuid4().hex[:12]}"
        now = time.time()
        meta_dict = meta or {}
        payload = {
            "event_id": event_id,
            "kind": kind,
            "actor_id": actor_id,
            "target_id": target_id,
            "amount_dust": amount_dust,
            "burned_dust": burned_dust,
            "meta": meta_dict,
            "created_at": round(now, 4),
        }
        entry_hash = _canon_hash(prev_hash, payload)
        conn.execute(
            """
            INSERT INTO dust_events (event_id, kind, actor_id, target_id, amount_dust, burned_dust, meta_json, prev_hash, entry_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                kind,
                actor_id,
                target_id,
                amount_dust,
                burned_dust,
                json.dumps(meta_dict, ensure_ascii=False),
                prev_hash,
                entry_hash,
                now,
            ),
        )
        return {**payload, "prev_hash": prev_hash, "entry_hash": entry_hash}

    def _ensure_account(self, conn: sqlite3.Connection, account_id: str, display_name: Optional[str] = None) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO dust_accounts (account_id, display_name, balance, created_at)
            VALUES (?, ?, 0, ?)
            """,
            (account_id, display_name or account_id, time.time()),
        )

    def verify_chain(self) -> tuple[bool, int]:
        """Verifies the cryptographic integrity of every Stardust ledger event."""
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM dust_events ORDER BY seq ASC").fetchall()
            prev = "0" * 64
            for r in rows:
                payload = {
                    "event_id": r["event_id"],
                    "kind": r["kind"],
                    "actor_id": r["actor_id"],
                    "target_id": r["target_id"],
                    "amount_dust": r["amount_dust"],
                    "burned_dust": r["burned_dust"],
                    "meta": json.loads(r["meta_json"]),
                    "created_at": round(r["created_at"], 4),
                }
                expected = _canon_hash(prev, payload)
                if r["prev_hash"] != prev or r["entry_hash"] != expected:
                    return False, r["seq"]
                prev = r["entry_hash"]
            return True, len(rows)

    # ==========================================================================
    # ACCOUNTS & TOP-UPS
    # ==========================================================================
    def top_up(self, account_id: str, amount_dust: int, simulated: bool = True, note: str = "") -> dict[str, Any]:
        if amount_dust <= 0:
            raise ValueError("Top-up amount must be positive")
        with self._lock, self._conn() as c:
            self._ensure_account(c, account_id)
            c.execute(
                "UPDATE dust_accounts SET balance = balance + ?, genesis_points = genesis_points + ? WHERE account_id = ?",
                (amount_dust, max(1, amount_dust // 10), account_id),
            )
            ev = self._append_event(
                c,
                kind="top_up_simulated" if simulated else "top_up_paid",
                actor_id=account_id,
                target_id=account_id,
                amount_dust=amount_dust,
                meta={"simulated": simulated, "note": note},
            )
            c.commit()
            return {"account": self.get_account(account_id), "event": ev}

    def get_account(self, account_id: str = "user:local") -> dict[str, Any]:
        with self._conn() as c:
            self._ensure_account(c, account_id)
            row = c.execute("SELECT * FROM dust_accounts WHERE account_id = ?", (account_id,)).fetchone()
            return dict(row) if row else {}

    # ==========================================================================
    # FUEL TANKS (ONE BOARDROOM APPROVAL PER SESSION)
    # ==========================================================================
    def _sign_tank_receipt(self, tank_id: str, account_id: str, scope: str, cap_dust: int, expires_at: float) -> str:
        msg = f"ORION-TANK-v1|{tank_id}|{account_id}|{scope}|{cap_dust}|{int(expires_at)}".encode("utf-8")
        return hmac.new(self._receipt_key, msg, hashlib.sha256).hexdigest()

    def open_fuel_tank(
        self,
        account_id: str = "user:local",
        cap_dust: int = 100,
        minutes: int = 120,
        scope: str = "forge",
        boardroom_decision_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Opens a bounded session Fuel Tank so Forge/Assistant can spend without per-prompt popups."""
        if cap_dust <= 0 or minutes <= 0:
            raise ValueError("cap_dust and minutes must be positive")
        if scope not in ("assistant", "forge", "store", "all"):
            raise ValueError("scope must be assistant, forge, store, or all")

        with self._lock, self._conn() as c:
            self._ensure_account(c, account_id)
            acct = c.execute("SELECT balance FROM dust_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if not acct or acct["balance"] < cap_dust:
                raise ValueError(f"Insufficient Stardust balance ({acct['balance'] if acct else 0} DUST < {cap_dust} DUST)")

            tank_id = f"tnk-{uuid.uuid4().hex[:10]}"
            now = time.time()
            expires_at = now + (minutes * 60)
            sig = self._sign_tank_receipt(tank_id, account_id, scope, cap_dust, expires_at)

            c.execute(
                """
                INSERT INTO dust_tanks (tank_id, account_id, scope, cap_dust, spent_dust, expires_at, boardroom_decision_id, receipt_sig, status, created_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, 'open', ?)
                """,
                (tank_id, account_id, scope, cap_dust, expires_at, boardroom_decision_id, sig, now),
            )
            self._append_event(
                c,
                kind="tank_open",
                actor_id=account_id,
                target_id=tank_id,
                amount_dust=cap_dust,
                meta={"scope": scope, "minutes": minutes, "boardroom_decision_id": boardroom_decision_id},
            )
            c.commit()
            row = c.execute("SELECT * FROM dust_tanks WHERE tank_id = ?", (tank_id,)).fetchone()
            return dict(row)

    def meter_compute_spend(
        self,
        tank_id: str,
        cost_dust: int,
        model: str = "claude-3-5-sonnet",
        prompt_summary: str = "",
    ) -> dict[str, Any]:
        """Meters AI compute against an open Fuel Tank. Burns 20% of protocol fee."""
        if cost_dust <= 0:
            raise ValueError("cost_dust must be positive")
        with self._lock, self._conn() as c:
            tank = c.execute("SELECT * FROM dust_tanks WHERE tank_id = ?", (tank_id,)).fetchone()
            if not tank:
                raise ValueError(f"Fuel tank {tank_id} not found")
            if tank["status"] != "open":
                raise ValueError(f"Fuel tank {tank_id} is {tank['status']}")
            if time.time() > tank["expires_at"]:
                c.execute("UPDATE dust_tanks SET status = 'expired' WHERE tank_id = ?", (tank_id,))
                c.commit()
                raise ValueError(f"Fuel tank {tank_id} has expired — request a new Boardroom Fuel Tank")
            if tank["spent_dust"] + cost_dust > tank["cap_dust"]:
                raise ValueError(
                    f"Fuel tank cap exceeded ({tank['spent_dust'] + cost_dust} > {tank['cap_dust']} DUST)"
                )

            account_id = tank["account_id"]
            acct = c.execute("SELECT balance FROM dust_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if not acct or acct["balance"] < cost_dust:
                raise ValueError("Account balance too low for compute spend")

            burned = max(1, (cost_dust * 20) // 100)  # 20% of AI generation fee burned per STARDUST_PLAN
            treasury_cut = cost_dust - burned

            c.execute(
                "UPDATE dust_accounts SET balance = balance - ?, total_spent = total_spent + ?, genesis_points = genesis_points + ? WHERE account_id = ?",
                (cost_dust, cost_dust, max(1, cost_dust), account_id),
            )
            c.execute(
                "UPDATE dust_accounts SET balance = balance + ? WHERE account_id = ?",
                (burned, BURN_ACCOUNT),
            )
            c.execute(
                "UPDATE dust_accounts SET balance = balance + ? WHERE account_id = ?",
                (treasury_cut, TREASURY_ACCOUNT),
            )
            c.execute(
                "UPDATE dust_tanks SET spent_dust = spent_dust + ? WHERE tank_id = ?",
                (cost_dust, tank_id),
            )

            ev = self._append_event(
                c,
                kind="compute_spend",
                actor_id=account_id,
                target_id=tank_id,
                amount_dust=cost_dust,
                burned_dust=burned,
                meta={"model": model, "prompt_summary": prompt_summary[:140], "treasury_cut": treasury_cut},
            )
            c.commit()
            updated_tank = c.execute("SELECT * FROM dust_tanks WHERE tank_id = ?", (tank_id,)).fetchone()
            return {"tank": dict(updated_tank), "event": ev}

    # ==========================================================================
    # FORGE MOD LINEAGE, ROYALTY SPLITTER (60/25/10/5) & CREATOR TIPPING (97/3)
    # ==========================================================================
    def register_mod(
        self,
        title: str,
        game: str,
        author_id: str,
        parent_mod_id: Optional[str] = None,
        asset_author_id: Optional[str] = None,
        mod_dir: Optional[str] = None,
        mod_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Registers a Forge mod or remix and writes its `forge.json` lineage manifest."""
        with self._lock, self._conn() as c:
            self._ensure_account(c, author_id)
            original_author_id = author_id
            if parent_mod_id:
                parent = c.execute("SELECT * FROM dust_mods WHERE mod_id = ?", (parent_mod_id,)).fetchone()
                if parent:
                    original_author_id = parent["original_author_id"] or parent["author_id"]
                    if not asset_author_id:
                        asset_author_id = parent["asset_author_id"]
                    c.execute("UPDATE dust_mods SET forks_count = forks_count + 1 WHERE mod_id = ?", (parent_mod_id,))

            if asset_author_id:
                self._ensure_account(c, asset_author_id)
            self._ensure_account(c, original_author_id)

            mid = mod_id or f"mod-{uuid.uuid4().hex[:10]}"
            now = time.time()
            c.execute(
                """
                INSERT INTO dust_mods (mod_id, title, game, author_id, parent_mod_id, original_author_id, asset_author_id, mod_dir, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, title, game, author_id, parent_mod_id, original_author_id, asset_author_id, mod_dir, now),
            )
            # Award Genesis Airdrop Points for publishing a mod
            c.execute(
                "UPDATE dust_accounts SET genesis_points = genesis_points + 25 WHERE account_id = ?",
                (author_id,),
            )
            self._append_event(
                c,
                kind="mod_registered",
                actor_id=author_id,
                target_id=mid,
                amount_dust=0,
                meta={"title": title, "game": game, "parent_mod_id": parent_mod_id},
            )
            c.commit()
            row = c.execute("SELECT * FROM dust_mods WHERE mod_id = ?", (mid,)).fetchone()
            return dict(row)

    def spend_on_mod(self, buyer_id: str, mod_id: str, amount_dust: int, reason: str = "fork_or_purchase") -> dict[str, Any]:
        """Executes the 60% Remixer / 25% Original Author / 10% Asset Creator / 5% Burn royalty split."""
        if amount_dust <= 0:
            raise ValueError("amount_dust must be positive")
        with self._lock, self._conn() as c:
            self._ensure_account(c, buyer_id)
            buyer = c.execute("SELECT balance FROM dust_accounts WHERE account_id = ?", (buyer_id,)).fetchone()
            if not buyer or buyer["balance"] < amount_dust:
                raise ValueError(f"Insufficient balance ({buyer['balance'] if buyer else 0} < {amount_dust} DUST)")

            mod = c.execute("SELECT * FROM dust_mods WHERE mod_id = ?", (mod_id,)).fetchone()
            if not mod:
                raise ValueError(f"Mod {mod_id} not found")

            remixer_id = mod["author_id"]
            original_id = mod["original_author_id"] or remixer_id
            asset_id = mod["asset_author_id"] or original_id

            remixer_cut = (amount_dust * ROYALTY_REMIXER_BPS) // 10_000
            original_cut = (amount_dust * ROYALTY_ORIGINAL_BPS) // 10_000
            asset_cut = (amount_dust * ROYALTY_ASSET_BPS) // 10_000
            burn_cut = amount_dust - (remixer_cut + original_cut + asset_cut)

            # Debit buyer
            c.execute(
                "UPDATE dust_accounts SET balance = balance - ?, total_spent = total_spent + ?, genesis_points = genesis_points + 5 WHERE account_id = ?",
                (amount_dust, amount_dust, buyer_id),
            )
            # Credit creators
            for recipient_id, cut in ((remixer_id, remixer_cut), (original_id, original_cut), (asset_id, asset_cut)):
                self._ensure_account(c, recipient_id)
                c.execute(
                    "UPDATE dust_accounts SET balance = balance + ?, total_earned = total_earned + ?, genesis_points = genesis_points + 10 WHERE account_id = ?",
                    (cut, cut, recipient_id),
                )
            # Credit burn sink
            c.execute(
                "UPDATE dust_accounts SET balance = balance + ? WHERE account_id = ?",
                (burn_cut, BURN_ACCOUNT),
            )
            c.execute(
                "UPDATE dust_mods SET royalties_dust = royalties_dust + ? WHERE mod_id = ?",
                (amount_dust - burn_cut, mod_id),
            )

            splits = {
                "remixer": {"account_id": remixer_id, "dust": remixer_cut},
                "original_author": {"account_id": original_id, "dust": original_cut},
                "asset_author": {"account_id": asset_id, "dust": asset_cut},
                "burned": burn_cut,
            }
            ev = self._append_event(
                c,
                kind="mod_royalty_split",
                actor_id=buyer_id,
                target_id=mod_id,
                amount_dust=amount_dust,
                burned_dust=burn_cut,
                meta={"reason": reason, "splits": splits, "game": mod["game"]},
            )
            c.commit()
            return {"splits": splits, "event": ev}

    def tip_creator(
        self,
        sender_id: str,
        creator_id: str,
        amount_dust: int,
        game_project: str = "general",
        mod_id: Optional[str] = None,
        message: str = "",
    ) -> dict[str, Any]:
        """Direct cross-game creator tipping: 97% straight to the creator, 3% burned."""
        if amount_dust <= 0:
            raise ValueError("Tip amount must be positive")
        if sender_id == creator_id:
            raise ValueError("Cannot tip yourself")

        with self._lock, self._conn() as c:
            self._ensure_account(c, sender_id)
            self._ensure_account(c, creator_id)
            sender = c.execute("SELECT balance FROM dust_accounts WHERE account_id = ?", (sender_id,)).fetchone()
            if not sender or sender["balance"] < amount_dust:
                raise ValueError(f"Insufficient Stardust to tip ({sender['balance'] if sender else 0} < {amount_dust})")

            creator_cut = (amount_dust * TIP_CREATOR_BPS) // 10_000
            burn_cut = amount_dust - creator_cut

            c.execute(
                "UPDATE dust_accounts SET balance = balance - ?, total_spent = total_spent + ?, total_tipped_out = total_tipped_out + ?, genesis_points = genesis_points + 5 WHERE account_id = ?",
                (amount_dust, amount_dust, amount_dust, sender_id),
            )
            c.execute(
                "UPDATE dust_accounts SET balance = balance + ?, total_earned = total_earned + ?, total_tipped_in = total_tipped_in + ?, genesis_points = genesis_points + 15 WHERE account_id = ?",
                (creator_cut, creator_cut, creator_cut, creator_id),
            )
            c.execute(
                "UPDATE dust_accounts SET balance = balance + ? WHERE account_id = ?",
                (burn_cut, BURN_ACCOUNT),
            )
            if mod_id:
                c.execute("UPDATE dust_mods SET tips_dust = tips_dust + ? WHERE mod_id = ?", (creator_cut, mod_id))

            ev = self._append_event(
                c,
                kind="creator_tip",
                actor_id=sender_id,
                target_id=creator_id,
                amount_dust=amount_dust,
                burned_dust=burn_cut,
                meta={
                    "creator_received_dust": creator_cut,
                    "burned_dust": burn_cut,
                    "game_project": game_project,
                    "mod_id": mod_id,
                    "message": message[:200],
                },
            )
            c.commit()
            return {
                "sender": self.get_account(sender_id),
                "creator": self.get_account(creator_id),
                "creator_received_dust": creator_cut,
                "burned_dust": burn_cut,
                "event": ev,
            }

    # ==========================================================================
    # CLEAN-ROOM GAME INSPECTOR & GIT-BACKED FORGE SANDBOX
    # ==========================================================================
    def inspect_game_directory(self, game_dir: Path) -> dict[str, Any]:
        """Clean-room inspects a local game or mod directory to detect modding APIs without binary hacking."""
        if not game_dir.exists():
            return {"detected": False, "error": f"Path does not exist: {game_dir}"}

        files = list(game_dir.rglob("*"))[:500]
        names = [f.name.lower() for f in files if f.is_file()]
        exts = {f.suffix.lower() for f in files if f.is_file()}

        adapter = "generic_script"
        mod_subfolder = "mods"
        reload_hint = "Restart or reload the scene"

        if any(n.endswith(".toc") for n in names) or (game_dir / "Interface" / "AddOns").exists():
            adapter = "wow_lua_addon"
            mod_subfolder = "Interface/AddOns"
            reload_hint = "Type /reload in-game"
        elif "steamodded" in " ".join(names) or "balatro" in str(game_dir).lower():
            adapter = "balatro_steamodded"
            mod_subfolder = "Mods"
            reload_hint = "Press Ctrl+R in Balatro"
        elif "cargo.toml" in names:
            adapter = "rust_bevy_or_wenilla"
            mod_subfolder = "crates"
            reload_hint = "cargo check / hot-reload"
        elif ".glb" in exts or ".m2" in exts:
            adapter = "model_porter_3d"
            mod_subfolder = "assets/models"
            reload_hint = "Open in Model Porter preview"

        # Check for recent Lua / runtime error logs
        error_tail = ""
        for f in files:
            if f.is_file() and f.name.lower() in ("framexml.log", "errors.log", "latest.log", "crash.log"):
                try:
                    error_tail = f.read_text(encoding="utf-8", errors="ignore")[-1500:]
                except Exception:
                    pass

        return {
            "detected": True,
            "game_dir": str(game_dir),
            "adapter": adapter,
            "recommended_mod_folder": str(game_dir / mod_subfolder),
            "reload_hint": reload_hint,
            "extensions_found": sorted(e for e in exts if e),
            "error_log_tail": error_tail,
        }

    def commit_forge_mod(
        self,
        mod_root: Path,
        mod_name: str,
        filename: str,
        code_content: str,
        prompt: str,
        manifest: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Writes a self-contained mod file inside its own git repository and commits the prompt."""
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", mod_name).strip("_") or "CustomMod"
        target_dir = (mod_root / f"OrionForge_{safe_name}").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        # Ensure we only write inside target_dir (sandbox guard)
        target_file = (target_dir / Path(filename).name).resolve()
        if target_dir not in target_file.parents:
            raise ValueError("Sandbox violation: attempted to write outside mod directory")

        target_file.write_text(code_content, encoding="utf-8")
        if manifest:
            (target_dir / "forge.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Initialize git repo if needed and commit
        if not (target_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=str(target_dir), capture_output=True, check=False)
            subprocess.run(["git", "config", "user.name", "Orion Forge"], cwd=str(target_dir), capture_output=True, check=False)
            subprocess.run(["git", "config", "user.email", "forge@orionos.local"], cwd=str(target_dir), capture_output=True, check=False)

        subprocess.run(["git", "add", "."], cwd=str(target_dir), capture_output=True, check=False)
        res = subprocess.run(
            ["git", "commit", "-m", f"Forge: {prompt[:72]}"],
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

        return {
            "ok": True,
            "mod_dir": str(target_dir),
            "file": str(target_file),
            "commit": head or "initial",
            "git_output": (res.stdout or res.stderr).strip()[:300],
        }

    def revert_forge_mod(self, mod_dir: Path) -> dict[str, Any]:
        """Reverts the last Forge prompt via `git reset --hard HEAD~1`."""
        if not (mod_dir / ".git").exists():
            raise ValueError(f"Not a git-tracked Forge mod directory: {mod_dir}")
        res = subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=str(mod_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "ok": res.returncode == 0,
            "mod_dir": str(mod_dir),
            "output": (res.stdout or res.stderr).strip(),
        }

    # ==========================================================================
    # FOUNDER FINANCIAL ROADMAP, EXPENSES & AIRDROP CALCULATOR
    # ==========================================================================
    def log_founder_expense(self, description: str, amount_usd_cents: int) -> dict[str, Any]:
        """Logs an out-of-pocket creator expense (VPS, domain, API seed) for Tier-1 Cost Recoup."""
        if amount_usd_cents <= 0:
            raise ValueError("amount_usd_cents must be positive")
        exp_id = f"exp-{uuid.uuid4().hex[:8]}"
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO dust_expenses (expense_id, description, amount_usd_cents, created_at) VALUES (?, ?, ?, ?)",
                (exp_id, description, amount_usd_cents, now),
            )
            c.commit()
            return {"expense_id": exp_id, "description": description, "amount_usd_cents": amount_usd_cents}

    def calculate_airdrop_preview(self) -> list[dict[str, Any]]:
        """Computes Phase 3 Merkle airdrop share for each user based on Genesis Points."""
        airdrop_pool = TOKENOMICS_BUCKETS["community_airdrop"]["tokens"]
        with self._conn() as c:
            rows = c.execute(
                "SELECT account_id, display_name, genesis_points, total_earned, total_tipped_out FROM dust_accounts WHERE account_id NOT LIKE 'system:%' AND genesis_points > 0 ORDER BY genesis_points DESC"
            ).fetchall()
            total_pts = sum(r["genesis_points"] for r in rows) or 1
            allocations = []
            for r in rows:
                share = r["genesis_points"] / total_pts
                tokens = int(airdrop_pool * share)
                leaf_hash = hashlib.sha256(f"{r['account_id']}:{tokens}".encode("utf-8")).hexdigest()
                allocations.append({
                    "account_id": r["account_id"],
                    "display_name": r["display_name"],
                    "genesis_points": r["genesis_points"],
                    "share_pct": round(share * 100, 4),
                    "estimated_dust_tokens": tokens,
                    "tge_unlocked_50pct": tokens // 2,
                    "vested_6mo_50pct": tokens - (tokens // 2),
                    "merkle_leaf": f"0x{leaf_hash}",
                })
            return allocations

    def founder_roadmap_status(self) -> dict[str, Any]:
        """Returns the live status of the Creator's 4-Tier Financial & Exit Plan."""
        with self._conn() as c:
            exp_row = c.execute(
                "SELECT COALESCE(SUM(amount_usd_cents), 0) AS total_exp, COALESCE(SUM(reimbursed_usd_cents), 0) AS total_reimb FROM dust_expenses"
            ).fetchone()
            burn_row = c.execute("SELECT balance FROM dust_accounts WHERE account_id = ?", (BURN_ACCOUNT,)).fetchone()
            mods_count = c.execute("SELECT COUNT(*) AS n FROM dust_mods").fetchone()["n"]
            active_users = c.execute(
                "SELECT COUNT(*) AS n FROM dust_accounts WHERE account_id NOT LIKE 'system:%' AND genesis_points > 0"
            ).fetchone()["n"]

        founder_total = TOKENOMICS_BUCKETS["founder_creator_allocation"]["tokens"]
        return {
            "founder_total_allocation_dust": founder_total,
            "tiers": {
                "tier_1_cost_recoup": {
                    "description": "100% reimbursement of out-of-pocket server, domain & API seed expenses from compute margin",
                    "logged_expenses_usd": round((exp_row["total_exp"] or 0) / 100.0, 2),
                    "reimbursed_usd": round((exp_row["total_reimb"] or 0) / 100.0, 2),
                    "launch_recoup_tranche_dust": int(founder_total * 0.10),  # 15,000,000 DUST
                    "twap_sell_guard": "Max 0.5% of 24h DEX volume per day",
                },
                "tier_2_personal_safety_net": {
                    "description": "15% ongoing fiat compute margin + 30% of Founder tokens (12m cliff, 24m vest)",
                    "tokens_dust": int(founder_total * 0.30),  # 45,000,000 DUST
                    "milestone_trigger": "1,000 weekly active Forge modders",
                },
                "tier_3_moon_bag": {
                    "description": "Cold hardware wallet reserve; max 10% sold per 5x adoption milestone",
                    "tokens_dust": int(founder_total * 0.30),  # 45,000,000 DUST
                },
                "tier_4_let_it_ride": {
                    "description": "Permanent protocol alignment stake; never market-sold",
                    "tokens_dust": int(founder_total * 0.30),  # 45,000,000 DUST
                },
            },
            "gate_metrics": {
                "published_mods": mods_count,
                "gate_1_target_mods": 50,
                "active_genesis_accounts": active_users,
                "gate_3_target_wau": 500,
                "total_burned_dust": burn_row["balance"] if burn_row else 0,
            },
        }

    def snapshot(self, account_id: str = "user:local") -> dict[str, Any]:
        ok, event_count = self.verify_chain()
        with self._conn() as c:
            tanks = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM dust_tanks WHERE account_id = ? ORDER BY created_at DESC LIMIT 10", (account_id,)
                ).fetchall()
            ]
            mods = [
                dict(r)
                for r in c.execute("SELECT * FROM dust_mods ORDER BY created_at DESC LIMIT 25").fetchall()
            ]
            events = [
                dict(r)
                for r in c.execute("SELECT * FROM dust_events ORDER BY seq DESC LIMIT 25").fetchall()
            ]
        return {
            "account": self.get_account(account_id),
            "chain_verified": ok,
            "ledger_events_count": event_count,
            "tanks": tanks,
            "mods": mods,
            "recent_events": events,
            "tokenomics": {
                "total_supply_dust": TOTAL_SUPPLY_DUST,
                "usd_cents_per_dust": USD_CENTS_PER_DUST,
                "buckets": TOKENOMICS_BUCKETS,
            },
            "founder_roadmap": self.founder_roadmap_status(),
        }


def make_router(get_daemon):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse
    from config import settings

    router = APIRouter()
    _engine = StardustEngine(Path(settings.data_dir) / "stardust.db")

    @router.get("/stardust/state")
    async def stardust_state(account_id: str = "user:local") -> dict[str, Any]:
        return _engine.snapshot(account_id)

    @router.get("/stardust/airdrop")
    async def stardust_airdrop() -> dict[str, Any]:
        return {"allocations": _engine.calculate_airdrop_preview()}

    @router.post("/stardust/topup")
    async def stardust_topup(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine.top_up(
                account_id=str(body.get("account_id") or "user:local"),
                amount_dust=int(body.get("amount_dust") or 100),
                simulated=bool(body.get("simulated", True)),
                note=str(body.get("note") or ""),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/stardust/tank/open")
    async def stardust_tank_open(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine.open_fuel_tank(
                account_id=str(body.get("account_id") or "user:local"),
                cap_dust=int(body.get("cap_dust") or 100),
                minutes=int(body.get("minutes") or 120),
                scope=str(body.get("scope") or "forge"),
                boardroom_decision_id=body.get("boardroom_decision_id"),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/stardust/mod/register")
    async def stardust_mod_register(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine.register_mod(
                title=str(body["title"]),
                game=str(body.get("game") or "openwow-112"),
                author_id=str(body.get("author_id") or "user:local"),
                parent_mod_id=body.get("parent_mod_id"),
                asset_author_id=body.get("asset_author_id"),
                mod_dir=body.get("mod_dir"),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/stardust/mod/spend")
    async def stardust_mod_spend(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine.spend_on_mod(
                buyer_id=str(body.get("buyer_id") or "user:local"),
                mod_id=str(body["mod_id"]),
                amount_dust=int(body.get("amount_dust") or 10),
                reason=str(body.get("reason") or "fork_or_purchase"),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/stardust/tip")
    async def stardust_tip(req: Request) -> Any:
        body = await req.json()
        try:
            return _engine.tip_creator(
                sender_id=str(body.get("sender_id") or "user:local"),
                creator_id=str(body["creator_id"]),
                amount_dust=int(body.get("amount_dust") or 25),
                game_project=str(body.get("game_project") or "openwow-112"),
                mod_id=body.get("mod_id"),
                message=str(body.get("message") or ""),
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/stardust/forge/inspect")
    async def stardust_forge_inspect(req: Request) -> Any:
        body = await req.json()
        return _engine.inspect_game_directory(Path(str(body.get("game_dir") or ".")))

    return router
