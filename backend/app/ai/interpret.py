"""Interpretation orchestration: provider call → validation → fallback.

Never raises to the caller. The deterministic signal is the floor: any AI problem
degrades to a template so the decision still surfaces, actionable, with an honest
``ai.status``. Insufficient evidence withholds the recommendation up front without
calling the model (cost control + honesty).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import settings
from ..context.bundle import ContextBundle
from ..domain.enums import AiFailureReason, AiStatus
from . import fallback
from .contract import AIValidationError, validate_output
from .prompt import SYSTEM, build_user
from .provider import AIProvider, ProviderError
from .telemetry import CallTelemetry, failure_reason_for_provider_error, usage_of

log = logging.getLogger("pie_portal.ai")


@dataclass
class AIResult:
    status: AiStatus
    should_surface: bool
    concise_title: str
    explanation: str
    recommended_action: str = ""
    priority_adjustment: int = 0
    caveat: Optional[str] = None
    cited_fact_labels: list[str] = field(default_factory=list)
    cited_signal_ids: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    # WS3: exactly one telemetry record per interpretation, including the paths
    # that never call the provider. Purely additive — never affects the decision.
    telemetry: Optional[CallTelemetry] = None

    def to_ai_dict(self) -> dict[str, Any]:
        """The stored ``decision.ai`` sub-object — concise, user-facing rationale +
        audit metadata only. No chain-of-thought (the contract has none)."""
        return {
            "status": self.status.value,
            "recommendation": self.recommended_action,
            "title": self.concise_title,
            "explanation": self.explanation,
            "caveat": self.caveat,
            "should_surface": self.should_surface,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model,
            "prompt_version": settings.PROMPT_VERSION,
            "cited_fact_labels": self.cited_fact_labels,
            "cited_signal_ids": self.cited_signal_ids,
        }


def _fallback_result(status: AiStatus, bundle: ContextBundle, signal_type: str,
                     metrics: dict, subject_label: str, provider: AIProvider,
                     note: Optional[str] = None) -> AIResult:
    fb = fallback.template(signal_type, metrics, subject_label)
    expl = fb["explanation"] + (f" {note}" if note else "")
    return AIResult(
        status=status, should_surface=True, concise_title=fb["concise_title"],
        explanation=expl, recommended_action="", priority_adjustment=0,
        cited_fact_labels=[], cited_signal_ids=sorted(bundle.signal_ids()),
        provider=getattr(provider, "name", ""), model=getattr(provider, "model", ""))


def interpret(bundle: ContextBundle, provider: AIProvider, *, signal_type: str,
              metrics: dict, subject_label: str) -> AIResult:
    pname = getattr(provider, "name", "")
    pmodel = getattr(provider, "model", "")

    tel = CallTelemetry(
        decision_type=bundle.decision_type or signal_type, ai_status="",
        provider=pname, model=pmodel, prompt_version=settings.PROMPT_VERSION,
        context_hash=bundle.context_hash(), recipient_role=bundle.recipient_role,
        subject_entity_id=(bundle.subject_ref or {}).get("entity_id"),
    )

    def _done(result: AIResult, reason: Optional[AiFailureReason] = None) -> AIResult:
        tel.ai_status = result.status.value
        tel.failure_reason = reason.value if reason else None
        tel.input_tokens, tel.output_tokens = usage_of(provider)
        result.telemetry = tel.finalize_cost()
        return result

    # Insufficient evidence → withhold up front (no inference).
    if bundle.evidence_sufficiency.get("level") == "INSUFFICIENT":
        r = _fallback_result(AiStatus.SUPPRESSED, bundle, signal_type, metrics,
                             subject_label, provider,
                             note="Recommendation withheld: insufficient evidence.")
        return _done(r)

    system, user = SYSTEM, build_user(bundle)
    corrections: list[AiFailureReason] = []
    started = time.monotonic()
    for attempt in (1, 2):
        tel.attempts = attempt
        tel.provider_called = True
        try:
            raw = provider.complete(system, user)
        except ProviderError as e:
            tel.latency_ms = int((time.monotonic() - started) * 1000)
            log.warning("ai provider failed (%s) for %s", type(e).__name__, pname)
            return _done(
                _fallback_result(AiStatus.FAILED, bundle, signal_type, metrics,
                                 subject_label, provider,
                                 note="AI unavailable; showing the deterministic signal."),
                failure_reason_for_provider_error(e))
        try:
            out = validate_output(raw, bundle, corrections)
        except AIValidationError as e:
            if e.code == "malformed_output" and attempt == 1:
                continue  # retry once
            tel.latency_ms = int((time.monotonic() - started) * 1000)
            tel.corrections = [c.value for c in corrections]
            log.warning("ai output rejected (%s)", e.reason.value)
            return _done(
                _fallback_result(AiStatus.DEGRADED, bundle, signal_type, metrics,
                                 subject_label, provider,
                                 note="AI output failed validation; showing the signal."),
                e.reason)

        tel.latency_ms = int((time.monotonic() - started) * 1000)
        tel.corrections = [c.value for c in corrections]
        if out.cannot_recommend_reliably:
            return _done(AIResult(
                status=AiStatus.SUPPRESSED, should_surface=out.should_surface,
                concise_title=out.concise_title, explanation=out.explanation,
                recommended_action="", priority_adjustment=0,
                caveat=out.caveat or "Recommendation withheld: insufficient basis.",
                cited_fact_labels=out.cited_fact_labels, cited_signal_ids=out.cited_signal_ids,
                provider=pname, model=pmodel))
        return _done(AIResult(
            status=AiStatus.OK, should_surface=out.should_surface,
            concise_title=out.concise_title, explanation=out.explanation,
            recommended_action=out.recommended_action,
            priority_adjustment=out.priority_adjustment, caveat=out.caveat,
            cited_fact_labels=out.cited_fact_labels, cited_signal_ids=out.cited_signal_ids,
            provider=pname, model=pmodel))

    # both attempts malformed
    tel.latency_ms = int((time.monotonic() - started) * 1000)
    tel.corrections = [c.value for c in corrections]
    return _done(
        _fallback_result(AiStatus.DEGRADED, bundle, signal_type, metrics,
                         subject_label, provider,
                         note="AI output invalid; showing the deterministic signal."),
        AiFailureReason.SCHEMA_INVALID)
