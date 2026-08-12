"""Live Google Gemini provider (Generative Language API) via httpx.

Same contract and shape as ``AnthropicProvider``: compact request, bounded
output, temperature 0, typed provider errors so the Decision Layer degrades to
its deterministic fallback. Prompt content is never logged. The key travels in
the ``x-goog-api-key`` header, never in the URL, so it cannot end up in an
access log.
"""
from __future__ import annotations

from typing import Optional

from ..config import settings
from .provider import ProviderError, ProviderTimeout, ProviderUnavailable


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 api_base: Optional[str] = None) -> None:
        self.model = model or settings.GEMINI_MODEL
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._api_base = api_base or settings.GEMINI_API_BASE
        self.last_usage: dict | None = None
        if not self._api_key:
            raise ProviderUnavailable("GEMINI_API_KEY is not set")

    def complete(self, system: str, user: str) -> str:
        import httpx  # local import: only needed for the live path

        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": settings.AI_MAX_TOKENS,
            },
        }
        self.last_usage = None
        try:
            resp = httpx.post(
                f"{self._api_base}/v1beta/models/{self.model}:generateContent",
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
        usage = body.get("usageMetadata") or {}
        if usage:
            self.last_usage = {"input_tokens": usage.get("promptTokenCount"),
                               "output_tokens": usage.get("candidatesTokenCount")}
        candidates = body.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        text = "".join(p.get("text", "") for p in parts)
        return text.strip()
