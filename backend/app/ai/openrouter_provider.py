"""Live OpenRouter provider — one key, many upstream models.

OpenRouter speaks the OpenAI Chat Completions shape, so this is
``OpenAIProvider`` with a different key, base and output-cap field rather than a
second copy of that request. What it is *not* is another lab: the model id names
the vendor behind it (``openai/gpt-4o``, ``anthropic/claude-sonnet-4``), so the
model matters more here than in any other provider.

The shipped default is ``openrouter/free``, the free router. Interpretation is a
small bounded task and every figure on every screen is computed deterministically
either way, so the cheapest thing that can read a fact set is the right default.

Three deliberate differences from the OpenAI path:

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

And output truncated to nothing is a failure by the same argument. Reasoning
tokens are spent out of ``max_tokens``, so a reasoning model under this
platform's small cap can return ``finish_reason: "length"`` with the whole
budget gone and ``content`` empty — nothing was withheld, the answer simply
never fit. The free router makes that likely rather than hypothetical: what it
picks is not named in advance and may well reason. Absence of evidence is not a
pass (CLAUDE.md §1), so an empty answer that ran out of room says so, and names
the cap that caused it.
"""
from __future__ import annotations

from ..config import settings
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
        text = super()._read(body)
        if not text and self._ran_out_of_room(body):
            # ProviderError, not ProviderUnavailable: nothing is unavailable and
            # an identical retry truncates identically. Telemetry reads the type
            # (telemetry.failure_reason_for_provider_error), so the distinction
            # is what a person later sees as the reason this decision degraded.
            raise ProviderError(
                f"the model spent all {settings.AI_MAX_TOKENS} output tokens "
                f"without producing an answer (finish_reason=length) — raise "
                f"AI_MAX_TOKENS, or name a model that does not reason")
        return text

    @staticmethod
    def _ran_out_of_room(body: dict) -> bool:
        """Whether the empty answer is a truncation rather than a silent model.

        ``native_finish_reason`` is the upstream vendor's own word, kept
        alongside the normalised one; either saying "length" is the same fact.
        """
        choice = (body.get("choices") or [{}])[0]
        return "length" in {choice.get("finish_reason"),
                            choice.get("native_finish_reason")}
