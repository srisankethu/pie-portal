"""Interpretation orchestration: provider call → validation → fallback.

Never raises to the caller. The deterministic signal is the floor: any AI problem
degrades to a template so the decision still surfaces, actionable, with an honest
``ai.status``. Insufficient evidence withholds the recommendation up front without
calling the model (cost control + honesty).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import settings
from ..context.bundle import ContextBundle
from ..domain.enums import AiStatus
from . import fallback
from .contract import AIValidationError, validate_output
from .prompt import SYSTEM, build_user
from .provider import AIProvider, ProviderError

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

    # Insufficient evidence → withhold up front (no inference).
    if bundle.evidence_sufficiency.get("level") == "INSUFFICIENT":
        r = _fallback_result(AiStatus.SUPPRESSED, bundle, signal_type, metrics,
                             subject_label, provider,
                             note="Recommendation withheld: insufficient evidence.")
        return r

    system, user = SYSTEM, build_user(bundle)
    for attempt in (1, 2):
        try:
            raw = provider.complete(system, user)
        except ProviderError as e:
            log.warning("ai provider failed (%s) for %s", type(e).__name__, pname)
            return _fallback_result(AiStatus.FAILED, bundle, signal_type, metrics,
                                    subject_label, provider,
                                    note="AI unavailable; showing the deterministic signal.")
        try:
            out = validate_output(raw, bundle)
        except AIValidationError as e:
            if e.code == "malformed_output" and attempt == 1:
                continue  # retry once
            log.warning("ai output rejected (%s)", e.code)
            return _fallback_result(AiStatus.DEGRADED, bundle, signal_type, metrics,
                                    subject_label, provider,
                                    note="AI output failed validation; showing the signal.")

        if out.cannot_recommend_reliably:
            return AIResult(
                status=AiStatus.SUPPRESSED, should_surface=out.should_surface,
                concise_title=out.concise_title, explanation=out.explanation,
                recommended_action="", priority_adjustment=0,
                caveat=out.caveat or "Recommendation withheld: insufficient basis.",
                cited_fact_labels=out.cited_fact_labels, cited_signal_ids=out.cited_signal_ids,
                provider=pname, model=pmodel)
        return AIResult(
            status=AiStatus.OK, should_surface=out.should_surface,
            concise_title=out.concise_title, explanation=out.explanation,
            recommended_action=out.recommended_action,
            priority_adjustment=out.priority_adjustment, caveat=out.caveat,
            cited_fact_labels=out.cited_fact_labels, cited_signal_ids=out.cited_signal_ids,
            provider=pname, model=pmodel)

    # both attempts malformed
    return _fallback_result(AiStatus.DEGRADED, bundle, signal_type, metrics,
                            subject_label, provider,
                            note="AI output invalid; showing the deterministic signal.")
