"""Ollama provider -- a local model with no external dependency.

Enabled with `./enable-ollama.sh` (or `LLM_PROVIDER=ollama`). The POC keeps
working if Ollama is absent: the factory falls back to `MockProvider`.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import LLMProvider, LLMResult, Message, extract_json

log = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def available(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def generate(
        self, messages: list[Message], schema: dict[str, Any] | None = None
    ) -> LLMResult:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2 if schema else 0.4},
        }
        if schema is not None:
            # Ollama accepts a JSON schema directly in `format` (0.5+), and
            # "json" on older builds.
            payload["format"] = schema

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if response.status_code == 400 and schema is not None:
                    payload["format"] = "json"
                    response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("Ollama call failed: %s", exc)
            return LLMResult(provider=self.name, model=self.model, error=str(exc))

        text = (body.get("message") or {}).get("content", "")
        return LLMResult(
            text=text,
            data=extract_json(text) if schema is not None else None,
            provider=self.name,
            model=self.model,
            usage={
                "eval_count": body.get("eval_count"),
                "total_duration": body.get("total_duration"),
            },
        )
