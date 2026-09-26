"""OpenAI-compatible chat completions provider.

Works with OpenAI itself, vLLM's OpenAI server, llama.cpp server, LiteLLM,
Together, Groq -- anything exposing `/v1/chat/completions`.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import LLMProvider, LLMResult, Message, extract_json

log = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def available(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
                return response.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    async def generate(
        self, messages: list[Message], schema: dict[str, Any] | None = None
    ) -> LLMResult:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2 if schema else 0.4,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": schema, "strict": False},
            }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                if response.status_code == 400 and schema is not None:
                    # Server does not understand json_schema -- retry with the
                    # older json_object mode.
                    payload["response_format"] = {"type": "json_object"}
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        json=payload,
                        headers=self._headers(),
                    )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM call failed: %s", exc)
            return LLMResult(provider=self.name, model=self.model, error=str(exc))

        text = body["choices"][0]["message"].get("content", "") or ""
        return LLMResult(
            text=text,
            data=extract_json(text) if schema is not None else None,
            provider=self.name,
            model=self.model,
            usage=body.get("usage", {}),
        )
