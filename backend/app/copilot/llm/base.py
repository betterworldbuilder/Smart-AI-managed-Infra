"""LLM provider abstraction.

The application must never depend on one model vendor, so everything the
Copilot needs from a model goes through this one interface:

    class LLMProvider:
        async def generate(self, messages, schema=None): ...

`schema` (a JSON Schema dict) asks for structured output; without it the
provider returns prose. Providers are interchangeable, and `MockProvider` is
always available so the whole POC runs with no model at all.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, TypedDict

from pydantic import BaseModel, Field

CONTEXT_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


class Message(TypedDict):
    role: str  # system | user | assistant
    content: str


class LLMResult(BaseModel):
    text: str = ""
    data: dict[str, Any] | None = None
    provider: str = "mock"
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class LLMProvider(ABC):
    name: str = "provider"
    model: str | None = None

    @abstractmethod
    async def generate(
        self, messages: list[Message], schema: dict[str, Any] | None = None
    ) -> LLMResult:
        """Return prose, or JSON matching `schema` when one is supplied."""

    async def available(self) -> bool:  # pragma: no cover - overridden where useful
        return True


def context_block(payload: dict[str, Any]) -> str:
    """Render the sanitised context the Copilot shares with the model.

    Wrapped in a fenced JSON block: a real model reads it as context, and
    `MockProvider` parses it to drive deterministic templates.
    """
    body = json.dumps(payload, indent=2, default=str)
    return "CONTEXT (sanitised -- capacity facts only):\n```json\n" + body + "\n```"


def parse_context(messages: list[Message]) -> dict[str, Any]:
    for message in reversed(messages):
        match = CONTEXT_FENCE.search(message.get("content", ""))
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:  # pragma: no cover - defensive
                return {}
    return {}


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response."""
    text = text.strip()
    if text.startswith("```"):
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fenced:
            text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
