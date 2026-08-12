"""Live Anthropic provider (Claude Messages API) via httpx.

Compact request, bounded output, temperature 0 (stable, cacheable), strict-JSON
instruction. Raises typed provider errors so the Decision Layer degrades to a
deterministic fallback. Prompt content is never logged.

Constructor arguments override the environment so a per-organization key stored
from Settings (ai/byok.py) builds the same provider the env-configured path does
— one implementation, two ways of keying it.
"""
from __future__ import annotations

from typing import Optional

from ..config import settings
from .provider import ProviderError, ProviderTimeout, ProviderUnavailable


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 api_base: Optional[str] = None) -> None:
        self.model = model or settings.AI_MODEL
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._api_base = api_base or settings.ANTHROPIC_API_BASE
        # Token usage from the most recent call, read by the telemetry layer.
        # Optional by contract: ``complete`` still returns plain text.
        self.last_usage: dict | None = None
        if not self._api_key:
            raise ProviderUnavailable("ANTHROPIC_API_KEY is not set")

    def complete(self, system: str, user: str) -> str:
        import httpx  # local import: only needed for the live path

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": settings.AI_MAX_TOKENS,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        self.last_usage = None
        try:
            resp = httpx.post(f"{self._api_base}/v1/messages",
                              headers=headers, json=payload,
                              timeout=settings.AI_TIMEOUT_SECONDS)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(str(e))
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e))
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code}")
        body = resp.json()
        usage = body.get("usage") or {}
        if usage:
            self.last_usage = {"input_tokens": usage.get("input_tokens"),
                               "output_tokens": usage.get("output_tokens")}
        blocks = body.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text.strip()
