"""Pydantic schemas: the strict contract every LLM output must satisfy."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Intent = Literal["summary", "question", "coding", "automation", "observation"]
Tier = Literal["local", "cloud", "offline"]
ActionType = Literal[
    "hud_notify",
    "suggest_routine",
    "memory_note",
    "open_url",
    "code_suggestion",
    "screen_action",
    "screen_workflow",
    "telegram_alert",
    "save_skill",
    "self_evolve",
    "none",
]
InputSource = Literal["user", "voice", "ocr", "browser", "discord", "routine", "consolidation", "telegram"]

# Required payload keys per action type (validated below).
_PAYLOAD_CONTRACT: dict[str, dict[str, type]] = {
    "hud_notify": {"title": str, "body": str},
    "suggest_routine": {"items": list},
    "memory_note": {"content": str},
    "open_url": {"url": str},
    "code_suggestion": {"code": str},
    "screen_action": {"action": str},
    "screen_workflow": {"steps": list},
    "telegram_alert": {"message": str},
    "save_skill": {"name": str, "code": str, "triggers": list},
    "self_evolve": {"request": str},
    "none": {},
}


class HudAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_payload(self) -> "HudAction":
        for key, typ in _PAYLOAD_CONTRACT[self.type].items():
            if not isinstance(self.payload.get(key), typ):
                raise ValueError(f"action '{self.type}' requires payload.{key} of type {typ.__name__}")
        if self.type == "suggest_routine":
            items = [str(i).strip() for i in self.payload["items"] if str(i).strip()]
            if not items:
                raise ValueError("suggest_routine.items must contain at least one entry")
            self.payload["items"] = items[:10]
        if self.type == "open_url" and not str(self.payload["url"]).startswith(("http://", "https://")):
            raise ValueError("open_url.url must be http(s)")
        if self.type == "hud_notify":
            level = self.payload.get("level", "info")
            self.payload["level"] = level if level in {"info", "warn", "success"} else "info"
        if self.type == "screen_workflow":
            steps = self.payload.get("steps")
            if not isinstance(steps, list) or len(steps) == 0:
                raise ValueError("screen_workflow requires at least one step in payload.steps")
        return self



class LLMResult(BaseModel):
    """What the model itself must produce."""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    summary: str = Field(min_length=1, max_length=6000)
    actions: list[HudAction] = Field(default_factory=list, max_length=8)


class OrchestratorResponse(LLMResult):
    tier: Tier
    model: Optional[str] = None
    latency_ms: Optional[int] = None


class OrchestratorInput(BaseModel):
    source: InputSource
    content: str = Field(min_length=1)
    context: list[str] = Field(default_factory=list)
    instruction: Optional[str] = None
    force_tier: Optional[Tier] = None
    rules: list[str] = Field(default_factory=list)  # user's standing rules & preferences (Feature Bible D7)


class RouteDecision(BaseModel):
    intent: Intent
    complexity: Literal["simple", "complex"]


# Hand-written (ref-free) JSON Schemas: portable across Ollama `format`,
# Anthropic tool `input_schema`, and OpenAI JSON mode instructions.
LLM_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(Intent.__args__)},
        "summary": {"type": "string", "description": "Concise answer / observation shown on the HUD."},
        "actions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(ActionType.__args__)},
                    "payload": {"type": "object"},
                },
                "required": ["type", "payload"],
            },
        },
    },
    "required": ["intent", "summary", "actions"],
}

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(Intent.__args__)},
        "complexity": {"type": "string", "enum": ["simple", "complex"]},
    },
    "required": ["intent", "complexity"],
}
