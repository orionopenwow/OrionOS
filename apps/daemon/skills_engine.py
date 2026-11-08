"""Event-Driven Skill Crystallization & Zero-API Router.

1. Exposes `save_skill(name, code, triggers)` which writes standalone deterministic
   Python scripts into `~/.orion/skills/`.
2. Matches incoming user actions/prompts against stored skill trigger embeddings.
   If cosine confidence > 0.85, executes the local script directly via subprocess,
   completely bypassing frontier LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from config import settings
from memory_core import Embedder

log = logging.getLogger("orion.skills")

DEFAULT_SKILLS_DIR = settings.data_dir / "skills"


class SkillEngine:
    def __init__(self, skills_dir: Optional[Path] = None, embedder: Optional[Embedder] = None) -> None:
        self.skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.skills_dir / "registry.json"
        self.embedder = embedder or Embedder()
        self.skills: dict[str, dict[str, Any]] = {}
        self._trigger_vectors: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._load_registry()

    def _load_registry(self) -> None:
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    self.skills = json.load(f)
                log.info("Loaded %d crystallized skills from %s", len(self.skills), self.skills_dir)
            except Exception as exc:
                log.warning("Could not read skills registry: %s", exc)
                self.skills = {}
        else:
            self.skills = {}

    def _save_registry(self) -> None:
        try:
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump(self.skills, f, indent=2)
        except Exception as exc:
            log.error("Failed to save skills registry: %s", exc)

    async def _ensure_trigger_vectors(self) -> None:
        """Precomputes/caches embedding vectors for all skill triggers."""
        if self._trigger_vectors and len(self._trigger_vectors) == sum(len(s.get("triggers", [])) for s in self.skills.values()):
            return

        vectors: list[dict[str, Any]] = []
        for name, data in self.skills.items():
            script_path = data.get("script_path")
            triggers = data.get("triggers", [])
            for trig in triggers:
                clean_trig = trig.strip()
                if not clean_trig:
                    continue
                vec = await self.embedder.embed(clean_trig)
                vectors.append({
                    "skill_name": name,
                    "trigger": clean_trig,
                    "script_path": script_path,
                    "vector": np.asarray(vec, dtype=np.float32),
                })
        self._trigger_vectors = vectors

    async def save_skill(self, name: str, code: str, triggers: list[str]) -> dict[str, Any]:
        """Crystallize a successful user workflow into a standalone deterministic Python script."""
        async with self._lock:
            # 1. Sanitize script name
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip().lower())
            if not safe_name:
                safe_name = f"skill_{int(time.time())}"

            script_filename = f"{safe_name}.py"
            script_path = self.skills_dir / script_filename

            # 2. Write standalone executable python script
            header = (
                f'"""Standalone Crystallized Orion Skill: {safe_name}\n'
                f'Auto-crystallized: {time.strftime("%Y-%m-%d %H:%M:%S")}\n'
                f'Triggers: {json.dumps(triggers)}\n'
                f'"""\n'
            )
            full_code = header + code.strip() + "\n"
            await asyncio.to_thread(script_path.write_text, full_code, "utf-8")

            # 3. Clean and store triggers
            clean_triggers = [t.strip() for t in triggers if t.strip()]
            self.skills[safe_name] = {
                "name": safe_name,
                "script_path": str(script_path),
                "script_filename": script_filename,
                "triggers": clean_triggers,
                "created_at": time.time(),
            }
            self._save_registry()

            # 4. Invalidate trigger vectors cache so it rebuilds on next match
            self._trigger_vectors = []
            await self._ensure_trigger_vectors()

            log.info("Crystallized skill '%s' saved to %s with %d triggers", safe_name, script_path, len(clean_triggers))
            return {
                "success": True,
                "name": safe_name,
                "script_path": str(script_path),
                "triggers": clean_triggers,
            }

    async def match_skill(self, query: str, threshold: float = 0.85) -> Optional[dict[str, Any]]:
        """Compare query embedding against stored triggers.

        Returns match info if confidence > threshold (0.85).
        """
        clean_query = query.strip()
        if not clean_query or not self.skills:
            return None

        await self._ensure_trigger_vectors()
        if not self._trigger_vectors:
            return None

        query_vec = np.asarray(await self.embedder.embed(clean_query), dtype=np.float32)

        best_score = -1.0
        best_match: Optional[dict[str, Any]] = None

        for item in self._trigger_vectors:
            trig_vec = item["vector"]
            # Both vectors are unit-normalized; dot product is exact cosine similarity
            cos_sim = float(np.dot(query_vec, trig_vec))
            if cos_sim > best_score:
                best_score = cos_sim
                best_match = item

        if best_match and best_score >= threshold:
            log.info(
                "Zero-API Match found: skill '%s' triggered by '%s' with cosine confidence %.4f (threshold %.2f)",
                best_match["skill_name"],
                best_match["trigger"],
                best_score,
                threshold,
            )
            return {
                "skill_name": best_match["skill_name"],
                "trigger": best_match["trigger"],
                "script_path": best_match["script_path"],
                "confidence": round(best_score, 4),
            }

        return None

    async def execute_skill(self, skill_info: dict[str, Any], query_text: str = "") -> dict[str, Any]:
        """Execute the crystallized local script directly via subprocess and return output."""
        script_path = skill_info.get("script_path")
        if not script_path or not Path(script_path).exists():
            return {
                "success": False,
                "output": f"Script not found at {script_path}",
                "exit_code": -1,
            }

        start_t = time.perf_counter()
        try:
            env = os.environ.copy()
            env["ORION_SKILL_INPUT"] = query_text
            env["PYTHONIOENCODING"] = "utf-8"

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script_path),
                query_text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)

            out_str = stdout.decode("utf-8", errors="replace").strip()
            err_str = stderr.decode("utf-8", errors="replace").strip()

            combined_output = out_str or err_str or "Script executed successfully with no output."
            return {
                "success": proc.returncode == 0,
                "output": combined_output,
                "exit_code": proc.returncode,
                "latency_ms": elapsed_ms,
            }
        except asyncio.TimeoutError:
            return {
                "success": False,
                "output": "Script execution timed out after 30 seconds",
                "exit_code": -2,
                "latency_ms": int((time.perf_counter() - start_t) * 1000),
            }
        except Exception as exc:
            return {
                "success": False,
                "output": f"Subprocess execution error: {exc}",
                "exit_code": -3,
                "latency_ms": int((time.perf_counter() - start_t) * 1000),
            }


skills_engine = SkillEngine()
