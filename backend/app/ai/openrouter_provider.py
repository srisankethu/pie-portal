"""Live OpenRouter provider — one key, many upstream models.

OpenRouter speaks the OpenAI Chat Completions shape, so this is
``OpenAIProvider`` with a different key, base and output-cap field rather than a
second copy of that request. What it is *not* is another lab: the model id names
the vendor behind it (``openai/gpt-4o``, ``anthropic/claude-sonnet-4``), so the
model matters more here than in any other provider and an organization is
expected to set it.

Two deliberate differences from the OpenAI path:

``max_tokens`` rather than ``max_completion_tokens``. OpenRouter normalises its
own documented parameter across every upstream vendor; the OpenAI-only spelling
is not that parameter, and an unrecognised field is forwarded to the upstream
provider, where a non-OpenAI model rejects it.

An error envelope returned with HTTP 200 is a failure, not an empty answer.
OpenRouter can answer 200 with ``{"error": {...}}`` and no choices when the
upstream vendor refuses — moderation, an exhausted credit balance, no provider
available for the requested model. Read as text that would be ``""``, which
reaches the contract layer as a model that said nothing rather than as a
provider that failed, and the telemetry would record the wrong reason. A
provider error is what actually happened, so that is what is raised.
"""
from __future__ import annotations

from .openai_provider import OpenAIProvider
from .provider import ProviderError


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    key_setting = "OPENROUTER_API_KEY"
    base_setting = "OPENROUTER_API_BASE"
    model_setting = "OPENROUTER_MODEL"
    max_tokens_field = "max_tokens"

    def _headers(self) -> dict:
        # X-Title is how a call is attributed in the account's own OpenRouter
        # dashboard. It is a constant, carries no business data, and is the one
        # thing that makes "which of my apps spent this" answerable there.
        return {**super()._headers(), "x-title": "pie-portal"}

    def _read(self, body: dict) -> str:
        error = body.get("error")
        if error and not body.get("choices"):
            detail = error.get("message") if isinstance(error, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            said = f"{code}: {detail or error}" if code else str(detail or error)
            raise ProviderError(f"OpenRouter returned an error — {said}"[:512])
        return super()._read(body)
