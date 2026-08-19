"""Live OpenAI provider (Chat Completions API) via httpx.

Same contract and shape as ``AnthropicProvider``: compact request, bounded
output, temperature 0, typed provider errors so the Decision Layer degrades to
its deterministic fallback. Prompt content is never logged.

``max_completion_tokens`` rather than the deprecated ``max_tokens``: the newer
name is accepted across current chat models, the older one is rejected by the
reasoning families outright.

The class attributes below exist because Chat Completions is a *shape* several
services speak, not one endpoint — ``openrouter_provider.py`` is the same
request with a different key, base and token field. They are the seam that made
a second gateway a subclass rather than a second copy of this file; nothing
about the OpenAI path changed when they were introduced.
"""
from __future__ import annotations

from typing import Optional

from ..config import settings
from .provider import ProviderError, ProviderTimeout, ProviderUnavailable


class OpenAIProvider:
    name = "openai"

    #: Which ``settings`` fields supply the defaults, and what this service
    #: calls its output cap. Subclasses override; nothing else varies.
    key_setting = "OPENAI_API_KEY"
    base_setting = "OPENAI_API_BASE"
    model_setting = "OPENAI_MODEL"
    max_tokens_field = "max_completion_tokens"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 api_base: Optional[str] = None) -> None:
        self.model = model or getattr(settings, self.model_setting)
        self._api_key = api_key or getattr(settings, self.key_setting)
        self._api_base = api_base or getattr(settings, self.base_setting)
        self.last_usage: dict | None = None
        if not self._api_key:
            raise ProviderUnavailable(f"{self.key_setting} is not set")

    def _headers(self) -> dict:
        return {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

    def _read(self, body: dict) -> str:
        """Turn one decoded response body into text, recording usage."""
        usage = body.get("usage") or {}
        if usage:
            self.last_usage = {"input_tokens": usage.get("prompt_tokens"),
                               "output_tokens": usage.get("completion_tokens")}
        choices = body.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else None
        return (text or "").strip()

    def complete(self, system: str, user: str) -> str:
        import httpx  # local import: only needed for the live path

        payload = {
            "model": self.model,
            self.max_tokens_field: settings.AI_MAX_TOKENS,
            "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        self.last_usage = None
        try:
            resp = httpx.post(f"{self._api_base}/v1/chat/completions",
                              headers=self._headers(), json=payload,
                              timeout=settings.AI_TIMEOUT_SECONDS)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(str(e))
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e))
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code}")
        return self._read(resp.json())
