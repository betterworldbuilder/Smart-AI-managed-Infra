"""Pluggable LLM providers."""

from __future__ import annotations

import logging

from ...config import Settings
from .base import (
    LLMProvider,
    LLMResult,
    Message,
    context_block,
    extract_json,
    parse_context,
)
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider

log = logging.getLogger(__name__)

__all__ = [
    "LLMProvider",
    "LLMResult",
    "Message",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "build_llm_provider",
    "context_block",
    "extract_json",
    "parse_context",
]


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Never fails: an unreachable provider degrades to the deterministic mock."""
    provider = settings.llm_provider
    if provider in ("openai", "openai-compatible"):
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        )
    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        )
    return MockProvider()


class FallbackProvider(LLMProvider):
    """Wraps a provider and silently falls back to the mock on error.

    A demo must never die because a model endpoint blinked.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or MockProvider()
        self.name = primary.name
        self.model = primary.model

    async def available(self) -> bool:
        return await self.primary.available()

    async def generate(self, messages, schema=None) -> LLMResult:  # type: ignore[override]
        result = await self.primary.generate(messages, schema)
        if result.error or (not result.text and schema is None):
            log.info("falling back to MockProvider (%s)", result.error or "empty response")
            fallback = await self.fallback.generate(messages, schema)
            fallback.error = result.error
            fallback.provider = f"{self.primary.name}->mock"
            return fallback
        return result
