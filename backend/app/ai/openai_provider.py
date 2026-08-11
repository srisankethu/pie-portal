"""Live OpenAI provider (Chat Completions API) via httpx.

Same contract and shape as ``AnthropicProvider``: compact request, bounded
output, temperature 0, typed provider errors so the Decision Layer degrades to
its deterministic fallback. Prompt content is never logged.

``max_completion_tokens`` rather than the deprecated ``max_tokens``: the newer
name is accepted across current chat models, the older one is rejected by the
reasoning families outright.
"""
from __future__ import annotations

from typing import Optional

from ..config import settings
from .provider import ProviderError, ProviderTimeout, ProviderUnavailable


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 api_base: Optional[str] = None) -> None:
        self.model = model or settings.OPENAI_MODEL
        self._api_key = api_key or settings.OPENAI_API_KEY
        self._api_base = api_base or settings.OPENAI_API_BASE
        self.last_usage: dict | None = None
        if not self._api_key:
            raise ProviderUnavailable("OPENAI_API_KEY is not set")

    def complete(self, system: str, user: str) -> str:
        import httpx  # local import: only needed for the live path

        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_completion_tokens": settings.AI_MAX_TOKENS,
            "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        self.last_usage = None
        try:
            resp = httpx.post(f"{self._api_base}/v1/chat/completions",
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
            self.last_usage = {"input_tokens": usage.get("prompt_tokens"),
                               "output_tokens": usage.get("completion_tokens")}
        choices = body.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else None
        return (text or "").strip()
