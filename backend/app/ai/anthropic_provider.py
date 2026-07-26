"""Live Anthropic provider (Claude Messages API) via httpx.

Compact request, bounded output, temperature 0 (stable, cacheable), strict-JSON
instruction. Raises typed provider errors so the Decision Layer degrades to a
deterministic fallback. Prompt content is never logged.
"""
from __future__ import annotations

from ..config import settings
from .provider import ProviderError, ProviderTimeout, ProviderUnavailable


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        self.model = settings.AI_MODEL
        if not settings.ANTHROPIC_API_KEY:
            raise ProviderUnavailable("ANTHROPIC_API_KEY is not set")

    def complete(self, system: str, user: str) -> str:
        import httpx  # local import: only needed for the live path

        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
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
        try:
            resp = httpx.post(f"{settings.ANTHROPIC_API_BASE}/v1/messages",
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
        blocks = resp.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text.strip()
