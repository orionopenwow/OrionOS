"""Ollama Model & Local SLM Assistant.
Detects Ollama health, pulls required models (llama3.2 / phi3 / nomic-embed-text),
and handles setup automation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import settings

log = logging.getLogger("orion.ollama")


async def check_ollama_status() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            res = await client.get(f"{settings.ollama_url}/api/tags")
            if res.status_code == 200:
                models = [m.get("name") for m in res.json().get("models", [])]
                return {"online": True, "models": models, "url": settings.ollama_url}
        except Exception:
            pass
    return {"online": False, "models": [], "url": settings.ollama_url}


async def pull_model_stream(model_name: str) -> bool:
    """Trigger Ollama model pull for zero-shot SLM inference."""
    log.info("Pulling Ollama model: %s...", model_name)
    async with httpx.AsyncClient(timeout=600.0) as client:
        try:
            res = await client.post(
                f"{settings.ollama_url}/api/pull",
                json={"name": model_name, "stream": False},
            )
            return res.status_code == 200
        except Exception as exc:
            log.warning("Failed to pull model %s: %s", model_name, exc)
            return False
