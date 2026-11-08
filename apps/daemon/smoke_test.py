"""Smoke test & verification suite for Orion daemon, Kokoro audio engine,
and zero-API skill crystallization router.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

import httpx
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smoke_test")


async def run_verification():
    log.info("--- STARTING ORION BACKEND VERIFICATION SUITE ---")

    # 1. Test AudioEngine directly
    log.info("1. Testing AudioEngine local Kokoro synthesis & voice blend...")
    from audio_engine import audio_engine
    test_wav = audio_engine.speak("Orion audio check.", voice="af_nicole", speed=0.92)
    assert len(test_wav) > 1000, f"WAV too small: {len(test_wav)}"
    assert test_wav.startswith(b"RIFF"), "Invalid WAV header"
    log.info("   ✓ AudioEngine produced valid WAV (%d bytes)", len(test_wav))

    # 2. Test SkillEngine crystallization & zero-API matching
    log.info("2. Testing SkillEngine crystallization and cosine similarity matching...")
    from skills_engine import skills_engine
    sample_code = """import sys, os
input_val = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('ORION_SKILL_INPUT', '')
print(f'CRYSTALLIZED_SUCCESS: processed input: {input_val}')
"""
    saved = await skills_engine.save_skill(
        name="verify_system_clock",
        code=sample_code,
        triggers=[
            "verify system clock status",
            "check local system clock",
            "crystallized system clock check",
        ],
    )
    assert saved["success"], "Skill save failed"
    log.info("   ✓ Skill saved to %s", saved["script_path"])

    # Test trigger matching with confidence > 0.85
    match = await skills_engine.match_skill("check local system clock", threshold=0.85)
    assert match is not None, "Failed to match exact trigger"
    assert match["confidence"] >= 0.85, f"Confidence {match['confidence']} below 0.85"
    log.info("   ✓ Zero-API match successful (skill=%s, conf=%.4f)", match["skill_name"], match["confidence"])

    # Test direct subprocess execution bypassing LLM
    exec_res = await skills_engine.execute_skill(match, "smoke_test_query")
    assert exec_res["success"], f"Execution failed: {exec_res}"
    assert "CRYSTALLIZED_SUCCESS" in exec_res["output"]
    log.info("   ✓ Subprocess executed successfully in %d ms: %s", exec_res["latency_ms"], exec_res["output"])

    # 3. Test IntentOrchestrator Zero-API Router bypass
    log.info("3. Testing IntentOrchestrator Zero-API bypass...")
    from orchestrator import IntentOrchestrator
    from schemas import OrchestratorInput
    orch = IntentOrchestrator()
    resp = await orch.handle(OrchestratorInput(
        source="user",
        content="check local system clock",
    ))
    await orch.aclose()
    assert "zero-api" in (resp.model or ""), f"Expected zero-api model, got {resp.model}"
    assert "CRYSTALLIZED_SUCCESS" in resp.summary
    log.info("   ✓ IntentOrchestrator bypassed frontier LLM and returned Zero-API response: %s", resp.summary)

    log.info("--- ALL DIRECT VERIFICATION TESTS PASSED SUCCESSFULLY! ---")


if __name__ == "__main__":
    asyncio.run(run_verification())
