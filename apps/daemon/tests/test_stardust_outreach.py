"""Verification test suite for Stardust ($DUST) Engine and Social/Creator Outreach Engine."""
from __future__ import annotations

import tempfile
from pathlib import Path

from approval_queue import ApprovalQueue
from outreach_engine import OutreachEngine, humanize_copy, score_human_voice
from stardust import StardustEngine, TOTAL_SUPPLY_DUST


def test_stardust_ledger_and_royalties() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test_stardust.db"
        engine = StardustEngine(db, secret_seed="test-seed")

        # 1. Verify initial supply & account
        snap0 = engine.snapshot("user:local")
        assert snap0["tokenomics"]["total_supply_dust"] == TOTAL_SUPPLY_DUST == 1_000_000_000
        assert snap0["account"]["balance"] == 250

        # 2. Open a Fuel Tank and meter compute spend (20% burn)
        tank = engine.open_fuel_tank("user:local", cap_dust=100, minutes=60, scope="forge")
        spend_res = engine.meter_compute_spend(tank["tank_id"], cost_dust=20, model="claude-3-5-sonnet", prompt_summary="mana bar addon")
        assert spend_res["tank"]["spent_dust"] == 20
        assert spend_res["event"]["burned_dust"] == 4  # 20% of 20 DUST burned

        # 3. Register root mod + forked mod and verify 60/25/10/5 Royalty Split
        root_mod = engine.register_mod(
            title="Classic Threat Meter",
            game="openwow-112",
            author_id="creator:bob",
            asset_author_id="artist:carol",
        )
        fork_mod = engine.register_mod(
            title="Cosmic Threat Meter Remix",
            game="openwow-112",
            author_id="creator:alice",
            parent_mod_id=root_mod["mod_id"],
        )
        assert fork_mod["original_author_id"] == "creator:bob"
        assert fork_mod["asset_author_id"] == "artist:carol"

        royalty = engine.spend_on_mod("user:local", fork_mod["mod_id"], amount_dust=100)
        splits = royalty["splits"]
        assert splits["remixer"]["dust"] == 60          # 60% to Alice
        assert splits["original_author"]["dust"] == 25  # 25% to Bob
        assert splits["asset_author"]["dust"] == 10     # 10% to Carol
        assert splits["burned"] == 5                    # 5% burned

        # 4. Direct Creator Tipping across games (97% creator / 3% burn)
        tip = engine.tip_creator(
            sender_id="user:local",
            creator_id="creator:bob",
            amount_dust=100,
            game_project="balatro",
            message="thanks for the base threat library",
        )
        assert tip["creator_received_dust"] == 97
        assert tip["burned_dust"] == 3

        # 5. Clean-room game inspector + Git Forge mod commit & revert
        game_dir = Path(tmp) / "MockWoW"
        (game_dir / "Interface" / "AddOns").mkdir(parents=True)
        info = engine.inspect_game_directory(game_dir)
        assert info["detected"] is True
        assert info["adapter"] == "wow_lua_addon"

        c1 = engine.commit_forge_mod(
            mod_root=game_dir / "Interface" / "AddOns",
            mod_name="ManaTick",
            filename="ManaTick.lua",
            code_content="-- v1\nprint('ManaTick v1 loaded')\n",
            prompt="Initial mana tick frame",
        )
        assert c1["ok"] is True
        mod_dir = Path(c1["mod_dir"])
        mod_file = Path(c1["file"])

        engine.commit_forge_mod(
            mod_root=game_dir / "Interface" / "AddOns",
            mod_name="ManaTick",
            filename="ManaTick.lua",
            code_content="-- v2 broken\nprint('broken')\n",
            prompt="Second prompt",
        )
        assert "v2 broken" in mod_file.read_text(encoding="utf-8")

        rev = engine.revert_forge_mod(mod_dir)
        assert rev["ok"] is True
        assert "v1 loaded" in mod_file.read_text(encoding="utf-8")

        # 6. Verify hash-chain integrity, Founder Roadmap & Merkle Airdrop calculator
        chain_ok, count = engine.verify_chain()
        assert chain_ok is True and count >= 5

        engine.log_founder_expense("Initial VPS & Domain seed", 4500)
        roadmap = engine.founder_roadmap_status()
        assert roadmap["tiers"]["tier_1_cost_recoup"]["logged_expenses_usd"] == 45.0
        assert roadmap["founder_total_allocation_dust"] == 150_000_000

        airdrop = engine.calculate_airdrop_preview()
        assert len(airdrop) >= 3
        print("Stardust Engine tests PASSED.")


def test_outreach_humanizer_and_approval_queue() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        q = ApprovalQueue(Path(tmp) / "approvals.json")
        outreach = OutreachEngine(q, Path(tmp))

        # 1. Verify AI buzzwords and rocket emojis are stripped
        raw_ai = "Buckle up! Let's delve into this revolutionary game-changer 🚀🔥✨ that will elevate the landscape!"
        cleaned = humanize_copy(raw_ai, casual_lowercase=True)
        assert "delve" not in cleaned.lower()
        assert "game-changer" not in cleaned.lower()
        assert "🚀" not in cleaned

        # 2. Seed campaign queue and verify all items are pending (never auto-sent)
        seeded = outreach.seed_campaign_queue()
        assert seeded["queued_posts"] == 5
        assert seeded["queued_creator_outreach"] == 4

        pending = outreach.snapshot()["pending_outreach_approvals"]
        assert len(pending) == 9
        for item in pending:
            assert item["status"] == "pending"
            assert item["voice_score"] >= 85
            assert score_human_voice(item["draft_reply"])["passed"] is True

        # 3. Approve one item and verify human jitter scheduling
        first_id = pending[0]["id"]
        committed = outreach.commit_approved_item(first_id)
        assert committed["status"] == "approved_scheduled"
        assert 45 <= committed["jitter_seconds"] <= 180
        print("Outreach Engine & Anti-AI Voice tests PASSED.")


if __name__ == "__main__":
    test_stardust_ledger_and_royalties()
    test_outreach_humanizer_and_approval_queue()
    print("ALL STARDUST & OUTREACH TESTS PASSED.")
