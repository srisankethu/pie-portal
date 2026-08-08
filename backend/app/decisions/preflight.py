"""What a decision run would send, and what it would cost — without sending it.

`ai/metrics.py` answers "what has the AI cost so far". This answers the question
that comes *before* that one, and the one an owner actually asks before pointing
a live model at a real book: **if I turn this on now, how many calls is that, how
big are they, and what is the bill?**

It assembles exactly the bundles ``DecisionService.generate`` would assemble,
over the same signals, applying the same cache and the same up-front suppression
— and then stops. No provider is constructed and nothing is sent. That is the
whole design constraint: an estimate that costs money to obtain is not an
estimate anyone runs.

**The token count is an estimate and is labelled as one.** ~4 characters per
token is the usual English-plus-JSON rule of thumb; the real count comes back
from the provider and is what `ai/telemetry` records per call. Output is priced
at ``AI_MAX_TOKENS`` — the ceiling, not a guess — so the figure here is an upper
bound on the run rather than a hopeful one.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..ai.prompt import build_system, build_user
from ..ai.provider import provider_status
from ..ai.telemetry import estimate_cost
from ..config import settings
from ..context.assembler import assemble_from_signal
from ..domain.enums import DecisionStatus
from ..repositories import DecisionRepository
from ..signals.config import SignalThresholds, load_thresholds

# Reused rather than reimplemented: which signals a run would act on, how a
# decision is keyed and which role receives it are the service's answers, and a
# preflight that disagreed with the run it is predicting would be worse than no
# preflight. Same reason `commercial/quote_service` borrows the Quote Builder's
# customer matcher instead of growing a second one.
from .service import _decision_key, _latest_signals, _recipient_role

#: Characters per token. A rule of thumb, stated as one — see the module note.
_CHARS_PER_TOKEN = 4


def estimate(session: Session, organization_id: str,
             thresholds: Optional[SignalThresholds] = None) -> dict:
    """The cost of the next ``DecisionService.generate`` for this organization."""
    th = thresholds or load_thresholds()
    repo = DecisionRepository(session, organization_id)

    would_call = would_skip = would_suppress = 0
    input_tokens = 0
    by_type: dict[str, int] = {}

    for signal in _latest_signals(session, organization_id):
        dtype = signal.signal_type
        role = _recipient_role(dtype)
        bundle = assemble_from_signal(session, signal, role, th)
        existing = repo.get_by_key(
            _decision_key(organization_id, dtype, signal.subject_entity_id, signal))

        if (existing is not None and existing.status == DecisionStatus.OPEN.value
                and (existing.ai or {}).get("context_hash") == bundle.context_hash()):
            would_skip += 1
            continue
        if bundle.evidence_sufficiency.get("level") == "INSUFFICIENT":
            would_suppress += 1
            continue

        would_call += 1
        by_type[dtype] = by_type.get(dtype, 0) + 1
        payload = build_system(bundle) + build_user(bundle)
        input_tokens += len(payload) // _CHARS_PER_TOKEN

    output_tokens = would_call * settings.AI_MAX_TOKENS
    total = estimate_cost(input_tokens, output_tokens) or 0.0

    return {
        "provider": provider_status(),
        "signals_considered": would_call + would_skip + would_suppress,
        "would_call_provider": would_call,
        "would_reuse_cached": would_skip,
        "would_suppress_up_front": would_suppress,
        "by_type": by_type,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "rates": {
            "currency": "USD",
            "per_mtok_input": settings.AI_COST_PER_MTOK_INPUT,
            "per_mtok_output": settings.AI_COST_PER_MTOK_OUTPUT,
            "max_output_tokens_per_call": settings.AI_MAX_TOKENS,
        },
        "estimated_cost_usd": round(total, 6),
        "estimated_cost_per_decision_usd": (
            round(total / would_call, 6) if would_call else 0.0),
        "note": (
            f"Upper bound. Input tokens estimated at ~{_CHARS_PER_TOKEN} characters "
            "per token from the prompts that would actually be sent; output priced "
            "at the configured ceiling. No provider was called to produce this."),
    }
