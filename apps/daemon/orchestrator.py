"""LLM Orchestrator & Intent Dispatcher.

Tier 1 (local SLM via Ollama): summaries, quick parsing, entity extraction.
Tier 2 (cloud frontier via Gemini / Anthropic / OpenAI): coding, multi-step automation.
Every output is validated against `schemas.LLMResult`; invalid output gets one
repair round-trip, then falls back to the other tier, then to an offline response.
Supports dynamic personality switching ('concise_companion' vs 'detailed_researcher').
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from compute_meter import ComputeMeter
from config import Settings, settings
from schemas import (
    LLM_RESPONSE_SCHEMA,
    ROUTE_SCHEMA,
    HudAction,
    Intent,
    LLMResult,
    OrchestratorInput,
    OrchestratorResponse,
    RouteDecision,
    Tier,
)
from skills_engine import skills_engine

log = logging.getLogger("orion.orchestrator")

BASE_SYSTEM_PROMPT = """You are Orion, a helpful assistant that lives on the user's desktop.
You see the user's context through screen captures (pHash + BitBlt OCR), open browser tabs via CDP, Discord, and spoken voice.
Speak in plain, normal words: calm, confident, technically sharp and friendly. No space, sci-fi or fantasy flavour.

{personality_guideline}

Directives:
1. Grounded & Fact-Bound: Always ground your answers in the user's actual context and memories. Never invent facts not present in observations.
2. High Signal, Low Noise: Eliminate boilerplate and conversational filler. Deliver clean, elegant, decisive solutions.
3. Action-Driven: When tasks arise, prioritize concrete payload actions (code_suggestion, screen_workflow, suggest_routine, memory_note, or save_skill).
4. Never wrap your output in markdown code blocks. Respond ONLY with a single JSON object matching this schema:
{schema}

Action payload contracts:
- hud_notify: {{"title": str, "body": str, "level": "info"|"warn"|"success"}}
- suggest_routine: {{"items": [str, ...]}}   (short actionable checklist entries)
- memory_note: {{"content": str}}            (a durable fact worth remembering about the user/projects)
- open_url: {{"url": "https://...", "label": str}}
- code_suggestion: {{"language": str, "code": str, "explanation": str}}
- screen_action: {{"action": "click"|"type"|"press"|"hotkey"|"scroll"|"drag", "x": int, "y": int, "text": str, "key": str, "keys": [str, ...]}}
- screen_workflow: {{"steps": [{{"action": str, ...}}, ...]}}
- telegram_alert: {{"message": str}}
- save_skill: {{"name": str, "code": str, "triggers": [str, ...]}}  (crystallize a successful workflow into a local script to bypass future LLM calls)
- self_evolve: {{"request": str}}  (ONLY when the user explicitly asks Orion to add/change one of its OWN features; generates a reviewable code patch)

Use an empty actions list when nothing is worth surfacing."""

ROUTER_PROMPT = """Classify the request. intent: summary|question|coding|automation|observation.
complexity: "complex" if it needs code generation, debugging, multi-step planning or deep reasoning; else "simple".
Respond with JSON only.

Request ({source}):
{content}"""

_COMPLEX = re.compile(
    r"\b(code|function|class|method|bug|stack\s?trace|traceback|exception|error|refactor|implement|compile|"
    r"regex|sql|script|automat\w*|workflow|macro|deploy|pipeline|multi-?step|architecture|debug|"
    r"typescript|javascript|python|rust|golang|java|c\+\+|api|click|type|mouse|keyboard)\b",
    re.I,
)
_QUESTION = re.compile(r"^\s*(what|why|how|when|where|who|which|can|could|should|is|are|do|does)\b|\?\s*$", re.I)


def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


class IntentOrchestrator:
    def __init__(self, cfg: Settings = settings) -> None:
        self.cfg = cfg
        self.personality_mode = cfg.personality_mode
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))
        self.usage = {"local_calls": 0, "cloud_calls": 0, "tokens_in": 0, "tokens_out": 0, "failures": 0}
        # Per-model tokens and estimated dollars (compute meter); daily totals persist in data_dir.
        self.meter = ComputeMeter(cfg.data_dir / "compute_meter.json")
        self._ollama_ok: Optional[bool] = None
        self._ollama_checked_at = 0.0

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------- V2 self_evolve tool
    def attach_self_evolve(self, engine: Any) -> None:
        self._self_evolve = engine

    async def self_evolve(self, request: str, trigger: str = "voice") -> dict[str, Any]:
        """Talk-to-Develop tool: generate a reviewable patch to Orion's own source."""
        engine = getattr(self, "_self_evolve", None)
        if engine is None:
            raise RuntimeError("self_evolve engine not attached")
        return await engine.propose(request, trigger=trigger)

    def set_personality(self, mode: str) -> None:
        valid_modes = (
            "concise_companion",
            "detailed_researcher",
            "cosmic_oracle",
            "stellar_scholar",
            "cosmic_vanguard",
        )
        if mode in valid_modes:
            self.personality_mode = mode
            log.info("Orion orchestrator personality set to: %s", mode)

    # ------------------------------------------------------------ backends
    @property
    def cloud_provider(self) -> Optional[str]:
        p = self.cfg.cloud_provider
        if p in ("gemini", "auto") and self.cfg.gemini_api_key:
            return "gemini"
        if p in ("anthropic", "auto") and self.cfg.anthropic_api_key:
            return "anthropic"
        if p in ("openai", "auto") and self.cfg.openai_api_key:
            return "openai"
        if self.cfg.gemini_api_key:
            return "gemini"
        return None

    async def ollama_available(self) -> bool:
        if self._ollama_ok is not None and time.monotonic() - self._ollama_checked_at < 30:
            return self._ollama_ok
        try:
            r = await self.http.get(f"{self.cfg.ollama_url}/api/tags", timeout=2.0)
            self._ollama_ok = r.status_code == 200
        except Exception:
            self._ollama_ok = False
        self._ollama_checked_at = time.monotonic()
        return self._ollama_ok

    # ------------------------------------------------------------- routing
    @staticmethod
    def heuristic_route(inp: OrchestratorInput) -> tuple[Intent, Tier]:
        text = inp.content
        complex_hit = bool(_COMPLEX.search(text))
        if inp.source in ("ocr", "browser", "discord"):
            intent: Intent = "coding" if complex_hit and inp.source == "ocr" else "observation"
        elif re.search(r"\b(automat\w*|macro|workflow|schedule|routine|click|type)\b", text, re.I):
            intent = "automation"
        elif complex_hit:
            intent = "coding"
        elif re.search(r"\b(summari[sz]e|tl;?dr|recap|digest)\b", text, re.I) or inp.source == "consolidation":
            intent = "summary"
        elif _QUESTION.search(text):
            intent = "question"
        else:
            intent = "observation"
        tier: Tier = "cloud" if intent in ("coding", "automation") or len(text) > 6000 else "local"
        return intent, tier

    async def classify(self, inp: OrchestratorInput) -> tuple[Intent, Tier]:
        intent, tier = self.heuristic_route(inp)
        if inp.force_tier:
            return intent, inp.force_tier
        if self.cfg.slm_router and inp.source in ("user", "voice") and await self.ollama_available():
            try:
                raw, _ = await self._ollama(
                    ROUTER_PROMPT.format(source=inp.source, content=inp.content[:2000]),
                    ROUTE_SCHEMA, system=None, timeout=15.0,
                )
                decision = RouteDecision.model_validate(raw)
                intent = decision.intent
                tier = "cloud" if decision.complexity == "complex" else "local"
            except Exception as exc:
                log.debug("SLM router failed, using heuristics: %s", exc)
        return intent, tier

    # ------------------------------------------------------------ dispatch
    async def handle(self, inp: OrchestratorInput) -> OrchestratorResponse:
        started = time.perf_counter()

        # Zero-API Router: Match query against crystallized skills with >0.85 cosine confidence
        try:
            skill_match = await skills_engine.match_skill(inp.content, threshold=0.85)
            if skill_match:
                log.info(
                    "Zero-API Router activated: matched skill '%s' (confidence: %.4f). Bypassing LLM calls.",
                    skill_match["skill_name"],
                    skill_match["confidence"],
                )
                exec_res = await skills_engine.execute_skill(skill_match, inp.content)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return OrchestratorResponse(
                    intent="automation",
                    tier="local",
                    model=f"zero-api:{skill_match['skill_name']}",
                    summary=f"⚡ [Zero-API Skill '{skill_match['skill_name']}' Executed]\n{exec_res['output']}",
                    actions=[],
                    latency_ms=elapsed_ms,
                )
        except Exception as skill_err:
            log.warning("Skill matching check encountered error: %s", skill_err)

        intent, tier = await self.classify(inp)
        prompt = self._build_prompt(inp, intent)
        order: list[Tier] = [tier, "local" if tier == "cloud" else "cloud"]
        errors: list[str] = []
        for t in order:
            if t == "cloud" and not self.cloud_provider:
                errors.append("cloud: no API key")
                continue
            if t == "local" and not await self.ollama_available():
                errors.append(f"local: Ollama unreachable at {self.cfg.ollama_url}")
                continue
            try:
                result, model = await self._run_validated(t, prompt)
                return OrchestratorResponse(
                    **result.model_dump(), tier=t, model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                self.usage["failures"] += 1
                errors.append(f"{t}: {exc}")
                log.warning("Tier %s failed: %s", t, exc)
        return self._offline(intent, inp, errors, started)

    async def summarize(self, text: str) -> str:
        resp = await self.handle(OrchestratorInput(
            source="consolidation", content=text, force_tier="local",
            instruction="Summarise this activity log into a durable memory: projects, tasks, people, "
                        "tools and open loops. 5-10 bullet points in `summary`.",
        ))
        return "" if resp.tier == "offline" else resp.summary

    async def gemini_search(self, query: str) -> str:
        """Direct search / diagnosis with Gemini or fallback to active model."""
        if self.cfg.gemini_api_key:
            try:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self.cfg.gemini_model}:generateContent?key={self.cfg.gemini_api_key}"
                )
                body = {
                    "contents": [{"parts": [{"text": f"You are Gemini Search & Code Diagnostic Engine. Search and analyze this game/code issue and provide actionable root causes, fixes, and code blocks:\n\n{query}"}]}]
                }
                r = await self.http.post(url, json=body, timeout=25.0)
                if r.status_code == 200:
                    data = r.json()
                    self._count_gemini(data, self.cfg.gemini_model)
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "")
            except Exception as exc:
                log.warning("Direct Gemini search failed: %s", exc)

        # Fallback to standard orchestrator handle
        resp = await self.handle(OrchestratorInput(
            source="user", content=query,
            instruction="Analyze this game issue / question. Provide root causes, quick workarounds, and ready-to-paste code fixes.",
        ))
        return resp.summary

    # ------------------------------------------------- V2 generic model access
    async def ollama_json(self, prompt: str, schema: dict, *, system: Optional[str] = None,
                          model: Optional[str] = None, timeout: float = 30.0) -> tuple[Any, str]:
        """Tier-1 zero-cost structured call against the local SLM."""
        if not await self.ollama_available():
            raise RuntimeError(f"Ollama unreachable at {self.cfg.ollama_url}")
        return await self._ollama(prompt, schema, system=system, timeout=timeout, model=model)

    async def frontier_text(self, system: str, user: str, *, json_mode: bool = False,
                            model: Optional[str] = None, max_tokens: int = 1500,
                            temperature: float = 0.4, timeout: float = 120.0) -> tuple[str, str]:
        """Free-form completion from the best available frontier model.
        Preference order: OpenAI (Tier-2 drafting default) -> Anthropic -> Gemini -> local Ollama."""
        errors: list[str] = []
        if self.cfg.openai_api_key:
            try:
                body: dict[str, Any] = {
                    "model": model or self.cfg.draft_model or self.cfg.openai_model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                }
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                r = await self.http.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.cfg.openai_api_key}"}, json=body, timeout=timeout,
                )
                r.raise_for_status()
                data = r.json()
                self._count_cloud(data.get("usage", {}), "prompt_tokens", "completion_tokens",
                                  model=body["model"], provider="openai")
                return data["choices"][0]["message"]["content"], body["model"]
            except Exception as exc:
                errors.append(f"openai: {exc}")
        if self.cfg.anthropic_api_key:
            try:
                sys_prompt = system + ("\n\nRespond with a single JSON object only." if json_mode else "")
                r = await self.http.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": self.cfg.anthropic_api_key, "anthropic-version": "2023-06-01"},
                    json={"model": self.cfg.anthropic_model, "max_tokens": max_tokens, "system": sys_prompt,
                          "temperature": temperature, "messages": [{"role": "user", "content": user}]},
                    timeout=timeout,
                )
                r.raise_for_status()
                data = r.json()
                self._count_cloud(data.get("usage", {}), "input_tokens", "output_tokens",
                                  model=self.cfg.anthropic_model, provider="anthropic")
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                return text, self.cfg.anthropic_model
            except Exception as exc:
                errors.append(f"anthropic: {exc}")
        if self.cfg.gemini_api_key:
            try:
                url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                       f"{self.cfg.gemini_model}:generateContent?key={self.cfg.gemini_api_key}")
                gen: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
                if json_mode:
                    gen["response_mime_type"] = "application/json"
                r = await self.http.post(url, json={
                    "system_instruction": {"parts": [{"text": system}]},
                    "contents": [{"parts": [{"text": user}]}],
                    "generationConfig": gen,
                }, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                self._count_gemini(data, self.cfg.gemini_model)
                return data["candidates"][0]["content"]["parts"][0]["text"], self.cfg.gemini_model
            except Exception as exc:
                errors.append(f"gemini: {exc}")
        if await self.ollama_available():
            body = {"model": self.cfg.ollama_model, "system": system, "prompt": user, "stream": False,
                    "options": {"temperature": temperature}}
            if json_mode:
                body["format"] = "json"
            r = await self.http.post(f"{self.cfg.ollama_url}/api/generate", json=body, timeout=max(timeout, 180.0))
            r.raise_for_status()
            data = r.json()
            self._count_local(data, self.cfg.ollama_model)
            return data.get("response", ""), self.cfg.ollama_model
        raise RuntimeError("No model backend reachable: " + " | ".join(errors or ["no API keys / Ollama offline"]))

    def _account(self, provider: str, model: str, tokens_in: int, tokens_out: int) -> None:
        """Count one model call in the totals and in the per-model compute meter."""
        self.usage["local_calls" if provider == "ollama" else "cloud_calls"] += 1
        self.usage["tokens_in"] += tokens_in
        self.usage["tokens_out"] += tokens_out
        try:
            self.meter.record(model, provider, tokens_in, tokens_out)
        except Exception as exc:  # the meter must never break a reply
            log.debug("compute meter record failed: %s", exc)

    def _count_cloud(self, usage: dict[str, Any], k_in: str, k_out: str, *, model: str = "unknown",
                     provider: str = "cloud") -> None:
        self._account(provider, model, int(usage.get(k_in, 0) or 0), int(usage.get(k_out, 0) or 0))

    def _count_gemini(self, data: dict[str, Any], model: str) -> None:
        meta = data.get("usageMetadata", {}) if isinstance(data, dict) else {}
        self._account("gemini", model, int(meta.get("promptTokenCount", 0) or 0),
                      int(meta.get("candidatesTokenCount", 0) or 0))

    def _count_local(self, data: dict[str, Any], model: str) -> None:
        self._account("ollama", model, int(data.get("prompt_eval_count", 0) or 0), int(data.get("eval_count", 0) or 0))

    # ------------------------------------------------------------- helpers
    def _build_prompt(self, inp: OrchestratorInput, intent: Intent) -> str:
        parts = [f"Source: {inp.source}", f"Preliminary intent: {intent}"]
        if inp.rules:
            parts.append("The user's standing rules & preferences (always follow):\n"
                         + "\n".join(f"- {r}" for r in inp.rules[:20]))
        if inp.instruction:
            parts.append(f"Task: {inp.instruction}")
        if inp.context:
            parts.append("Relevant memory:\n" + "\n".join(f"- {c}" for c in inp.context[:8]))
        parts.append(f"Input:\n{inp.content[:12000]}")
        return "\n\n".join(parts)

    async def _run_validated(self, tier: Tier, prompt: str) -> tuple[LLMResult, str]:
        last_error: Exception | None = None
        for _ in range(2):
            if tier == "local":
                raw, model = await self._ollama(prompt, LLM_RESPONSE_SCHEMA, system=self._system())
            elif self.cloud_provider == "gemini":
                raw, model = await self._gemini(prompt)
            elif self.cloud_provider == "anthropic":
                raw, model = await self._anthropic(prompt)
            else:
                raw, model = await self._openai(prompt)
            try:
                return LLMResult.model_validate(raw), model
            except ValidationError as exc:
                last_error = exc
                prompt += (f"\n\nYour previous output failed schema validation: "
                           f"{json.dumps(exc.errors(include_url=False)[:5], default=str)}\n"
                           f"Previous output: {json.dumps(raw)[:2000]}\nReturn corrected JSON only.")
        raise ValueError(f"schema validation failed twice: {last_error}")

    def _system(self) -> str:
        if self.personality_mode in ("detailed_researcher", "stellar_scholar"):
            guideline = (
                "Persona Mode: Deep Researcher.\n"
                "You are a careful researcher and systems architect. Provide thorough technical depth, "
                "rigorous systems breakdown, edge-case evaluations, and complete, production-grade code.\n"
                "Your tone is clear, thoughtful and precise."
            )
        elif self.personality_mode == "cosmic_oracle":
            guideline = (
                "Persona Mode: Big-Picture Advisor.\n"
                "You look for the overall patterns and hidden connections across codebases, workflows, and ideas.\n"
                "Speak thoughtfully and in plain words, while keeping technical "
                "recommendations sharp, inventive, and practical."
            )
        else:
            guideline = (
                "Persona Mode: Concise Companion.\n"
                "You are a sharp, loyal, friendly desktop companion. Speak with warm confidence.\n"
                "Keep spoken summaries punchy, clear, and under 2-3 sentences. Prioritize immediate action, direct clarity, "
                "and zero conversational fluff."
            )
        return BASE_SYSTEM_PROMPT.format(
            personality_guideline=guideline,
            schema=json.dumps(LLM_RESPONSE_SCHEMA),
        )

    async def _gemini(self, prompt: str) -> tuple[Any, str]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.cfg.gemini_model}:generateContent?key={self.cfg.gemini_api_key}"
        )
        body = {
            "system_instruction": {"parts": [{"text": self._system()}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        }
        r = await self.http.post(url, json=body, timeout=60.0)
        r.raise_for_status()
        data = r.json()
        self._count_gemini(data, self.cfg.gemini_model)
        text_resp = data["candidates"][0]["content"]["parts"][0]["text"]
        return _extract_json(text_resp), self.cfg.gemini_model

    async def _ollama(self, prompt: str, schema: dict, system: Optional[str], timeout: float = 120.0,
                      model: Optional[str] = None) -> tuple[Any, str]:
        model = model or self.cfg.ollama_model
        body: dict[str, Any] = {
            "model": model, "prompt": prompt, "format": schema,
            "stream": False, "options": {"temperature": 0.2},
        }
        if system:
            body["system"] = system
        r = await self.http.post(f"{self.cfg.ollama_url}/api/generate", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        self._count_local(data, model)
        return _extract_json(data.get("response", "")), model

    async def _anthropic(self, prompt: str) -> tuple[Any, str]:
        r = await self.http.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.cfg.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": self.cfg.anthropic_model,
                "max_tokens": 4096,
                "system": self._system(),
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{
                    "name": "emit_orion_response",
                    "description": "Emit the structured Orion HUD response.",
                    "input_schema": LLM_RESPONSE_SCHEMA,
                }],
                "tool_choice": {"type": "tool", "name": "emit_orion_response"},
            },
        )
        r.raise_for_status()
        data = r.json()
        self._count_cloud(data.get("usage", {}), "input_tokens", "output_tokens",
                          model=self.cfg.anthropic_model, provider="anthropic")
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"], self.cfg.anthropic_model
        raise ValueError("Anthropic response contained no tool_use block")

    async def _openai(self, prompt: str) -> tuple[Any, str]:
        r = await self.http.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.openai_api_key}"},
            json={
                "model": self.cfg.openai_model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": self._system()},
                             {"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        data = r.json()
        self._count_cloud(data.get("usage", {}), "prompt_tokens", "completion_tokens",
                          model=self.cfg.openai_model, provider="openai")
        return _extract_json(data["choices"][0]["message"]["content"]), self.cfg.openai_model

    def _offline(self, intent: Intent, inp: OrchestratorInput, errors: list[str], started: float) -> OrchestratorResponse:
        snippet = inp.content.strip().replace("\n", " ")[:240]
        return OrchestratorResponse(
            intent=intent,
            tier="offline",
            summary=f"No LLM backend reachable. Captured input: “{snippet}”",
            actions=[HudAction(type="hud_notify", payload={
                "title": "LLM backends offline",
                "body": "Start Ollama (`ollama serve`) or configure GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY. "
                        + " | ".join(errors),
                "level": "warn",
            })],
            model=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
