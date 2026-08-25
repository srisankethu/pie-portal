"""AI call telemetry (WS3) — one record per interpretation decision point.

Pure and DB-free by design: ``CallTelemetry`` is built inside the AI layer (which
stays a pure function of its inputs) and persisted by the caller, which is the
layer that knows the organization. Exactly one record is produced per call,
including the paths that never reach the provider — a cache hit and an
up-front suppression are as operationally interesting as a live call.

Cost is a deterministic estimate from token counts and configured rates. It is
backend arithmetic, never a figure the model supplies, and it is None when the
provider did not report usage rather than being guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import settings
from ..domain.enums import AiFailureReason


@dataclass
class CallTelemetry:
    """What one interpretation attempt cost and how it ended."""

    decision_type: str
    ai_status: str
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    context_hash: str = ""
    #: SHA-256 of the exact text sent to the provider, or None where nothing was
    #: sent (a cache hit, an up-front suppression).
    #:
    #: The hash, never the text. ``context_hash`` is the *input bundle*'s
    #: identity and two different prompt templates over one bundle share it, so
    #: it cannot answer "was this the prompt you say it was". This can. The
    #: plaintext has one home — ``model_payloads``, encrypted under the tenant
    #: key — and a second copy on an audit chain that survives erasure by design
    #: would quietly undo that.
    prompt_sha256: Optional[str] = None
    subject_entity_id: Optional[str] = None
    recipient_role: Optional[str] = None
    # path taken
    provider_called: bool = False
    cache_hit: bool = False
    attempts: int = 0
    latency_ms: Optional[int] = None
    # usage / cost
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    # outcome detail
    failure_reason: Optional[str] = None
    corrections: list[str] = field(default_factory=list)

    def finalize_cost(self) -> "CallTelemetry":
        """Compute the estimated cost from tokens + configured rates."""
        self.estimated_cost_usd = estimate_cost(self.input_tokens, self.output_tokens)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_type": self.decision_type, "ai_status": self.ai_status,
            "provider": self.provider, "model": self.model,
            "prompt_version": self.prompt_version, "context_hash": self.context_hash,
            "prompt_sha256": self.prompt_sha256,
            "subject_entity_id": self.subject_entity_id,
            "recipient_role": self.recipient_role,
            "provider_called": self.provider_called, "cache_hit": self.cache_hit,
            "attempts": self.attempts, "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "failure_reason": self.failure_reason, "corrections": self.corrections,
        }


def estimate_cost(input_tokens: Optional[int],
                  output_tokens: Optional[int]) -> Optional[float]:
    """Deterministic cost estimate in USD. None when usage is unknown —
    an unknown cost is reported as unknown, never as zero."""
    if input_tokens is None and output_tokens is None:
        return None
    tin = float(input_tokens or 0) / 1_000_000.0 * settings.AI_COST_PER_MTOK_INPUT
    tout = float(output_tokens or 0) / 1_000_000.0 * settings.AI_COST_PER_MTOK_OUTPUT
    return round(tin + tout, 8)


def usage_of(provider: Any) -> tuple[Optional[int], Optional[int]]:
    """Read the optional ``last_usage`` a provider may expose.

    Kept optional so the ``AIProvider`` protocol (``complete -> str``) is
    unchanged; a provider that reports nothing simply yields unknown usage.
    """
    usage = getattr(provider, "last_usage", None) or {}
    if not isinstance(usage, dict):
        return None, None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    return (int(inp) if isinstance(inp, (int, float)) else None,
            int(out) if isinstance(out, (int, float)) else None)


def failure_reason_for_provider_error(exc: BaseException) -> AiFailureReason:
    from .provider import ProviderTimeout, ProviderUnavailable

    if isinstance(exc, ProviderTimeout):
        return AiFailureReason.PROVIDER_TIMEOUT
    if isinstance(exc, ProviderUnavailable):
        return AiFailureReason.PROVIDER_UNAVAILABLE
    return AiFailureReason.PROVIDER_ERROR
