"""Signal → Decision generation (spec §5, §13, §17).

For each latest signal per (type, subject): assemble permission-scoped context,
interpret via the AI layer (validated), finalize priority, and persist a Decision
— routed to the right role, deduped by ``decision_key``, and cost-guarded by a
context hash (unchanged context ⇒ no re-inference).
"""
from __future__ import annotations

import hashlib
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.interpret import interpret
from ..ai.provider import AIProvider, select_provider
from ..trust import disclosure, rehydrate
from ..config import settings
from ..context.assembler import assemble_from_signal
from ..domain import models
from ..domain.enums import (
    RESTRICTED_DECISION_TYPES,
    AiStatus,
    DecisionStatus,
    DecisionType,
    PriorityBand,
    Role,
    SubjectEntityType,
)
from ..ai.telemetry import CallTelemetry
from ..commercial import ownership
from ..repositories import AiTelemetryRepository, DecisionRepository
from ..signals.config import SignalThresholds, load_thresholds


def _band(score: int) -> str:
    if score >= settings.PRIORITY_HIGH_AT:
        return PriorityBand.HIGH.value
    if score >= settings.PRIORITY_MEDIUM_AT:
        return PriorityBand.MEDIUM.value
    return PriorityBand.LOW.value


def _recipient_role(decision_type: str) -> Role:
    if DecisionType(decision_type) in RESTRICTED_DECISION_TYPES:
        return Role.SALES_MANAGER
    return Role.SALESPERSON


def _decision_key(org: str, dtype: str, subject_id: str, signal: models.Signal) -> str:
    bucket = signal.detected_at.strftime("%Y-%W")  # open-window bucket (§17)
    blob = f"{org}|{dtype}|{subject_id}|{bucket}".encode()
    return "dk_" + hashlib.sha256(blob).hexdigest()[:16]


def reader_identity(provider_name: str, model: str) -> tuple[str, str, str]:
    """Who would write the narrative: provider, model, prompt version.

    Three facts rather than one because each moves independently and each
    changes the sentence a person reads — a different provider, the same
    provider on a different model, or the same model under a rewritten prompt.
    """
    return (provider_name or "", model or "", settings.PROMPT_VERSION)


def is_reusable(existing: Optional[models.Decision], context_hash: str,
                reader: tuple[str, str, str]) -> bool:
    """Whether an open decision can stand in place of a fresh inference.

    The cost guard: unchanged context ⇒ no re-inference. **Unchanged reader**
    belongs in it for the same reason unchanged context does, and leaving it out
    was a silent trap. An owner who stores a key, tests it, and switches the AI
    layer onto their own model sees nothing change anywhere — every open
    decision still carries the sentence the offline mock wrote, because the
    facts behind it did not move. The configuration screen says "Live", the
    cards say otherwise, and nothing in the product accounts for the difference.

    So a decision is reusable only if the same reader would produce it. Turning
    a provider on re-infers once, and `decisions/preflight.py` prices that run
    before it happens — which is the screen an owner already opens before
    pointing a live model at a real book.
    """
    if existing is None or existing.status != DecisionStatus.OPEN.value:
        return False
    ai = existing.ai or {}
    if ai.get("context_hash") != context_hash:
        return False
    return (ai.get("provider"), ai.get("model"),
            ai.get("prompt_version")) == reader


def _latest_signals(session: Session, org: str) -> list[models.Signal]:
    rows = session.scalars(
        select(models.Signal).where(models.Signal.organization_id == org)
        .order_by(models.Signal.created_at.desc())).all()
    latest: dict[tuple[str, str], models.Signal] = {}
    for s in rows:
        latest.setdefault((s.signal_type, s.subject_entity_id), s)
    return list(latest.values())


class DecisionService:
    def __init__(self, session: Session, organization_id: str,
                 provider: Optional[AIProvider] = None,
                 thresholds: Optional[SignalThresholds] = None) -> None:
        self.s = session
        self.org = organization_id
        self.provider = provider or select_provider(session, organization_id)
        self.reader = reader_identity(getattr(self.provider, "name", ""),
                                      getattr(self.provider, "model", ""))
        self.th = thresholds or load_thresholds()
        self.repo = DecisionRepository(session, organization_id)
        self.telemetry = AiTelemetryRepository(session, organization_id)

    def _assigned_user(self, signal: models.Signal, role: Role) -> Optional[str]:
        if role is Role.SALESPERSON and signal.subject_entity_type == SubjectEntityType.CUSTOMER.value:
            cust = self.s.get(models.Customer, signal.subject_entity_id)
            if cust is None:
                return None
            # The effective owner, so a decision lands with whoever the account
            # was actually given to rather than with Zoho's last salesperson.
            owner = ownership.owner_of(self.s, cust)
            return owner.user_id if owner else None
        return None  # restricted/team decisions are not owned by a salesperson

    def generate(self) -> dict:
        created = refreshed = skipped = 0
        # Counted separately from created/refreshed: a decision whose narrative
        # call failed still lands (with its deterministic signal), so the run
        # "succeeds" — but a summary that only says so reads as all-clear while
        # every provider call is bouncing off a bad key. The caller gets both.
        ai_failed = 0
        by_type: dict[str, int] = {}

        for signal in _latest_signals(self.s, self.org):
            dtype = signal.signal_type
            role = _recipient_role(dtype)
            bundle = assemble_from_signal(self.s, signal, role, self.th)
            key = _decision_key(self.org, dtype, signal.subject_entity_id, signal)
            existing = self.repo.get_by_key(key)

            # cost control: unchanged context *and* unchanged reader on an open
            # decision ⇒ no re-inference. See is_reusable for why the reader is
            # half of that test.
            if is_reusable(existing, bundle.context_hash(), self.reader):
                skipped += 1
                # a cache hit is still an AI-layer event worth counting (WS3)
                self.telemetry.record(CallTelemetry(
                    decision_type=dtype,
                    ai_status=(existing.ai or {}).get("status", AiStatus.PENDING.value),
                    provider=getattr(self.provider, "name", ""),
                    model=getattr(self.provider, "model", ""),
                    prompt_version=settings.PROMPT_VERSION,
                    context_hash=bundle.context_hash(),
                    subject_entity_id=signal.subject_entity_id,
                    recipient_role=role.value,
                ), cache_hit=True)
                continue

            result = interpret(bundle, self.provider, signal_type=dtype,
                               metrics=signal.metrics or {},
                               subject_label=bundle.subject_ref.get("label", ""))
            # The label above is a pseudonym. Names re-enter here, at the seam,
            # after interpretation and before anything is persisted or shown.
            rehydrate.result(result, bundle.display_names)
            disclosure.log_result(self.s,
                                  organization_id=signal.organization_id,
                                  decision_type=dtype, result=result)
            self.telemetry.record(result.telemetry)
            if result.status is AiStatus.FAILED:
                ai_failed += 1
            base = max(0, min(100, int(signal.severity_base)))
            adj = result.priority_adjustment if result.status is AiStatus.OK else 0
            final = max(0, min(100, base + adj))

            ai_obj = result.to_ai_dict()
            ai_obj["context_hash"] = bundle.context_hash()
            ai_obj["redactions_applied"] = bundle.redactions_applied
            confidence = {"evidence_sufficiency": bundle.evidence_sufficiency.get("level"),
                          "reasons": bundle.evidence_sufficiency.get("reasons", [])}

            if existing is not None:
                d = existing
                refreshed += 1
            else:
                d = models.Decision(organization_id=self.org, decision_key=key)
                self.repo.add(d)
                created += 1
            d.decision_type = dtype
            d.subject_entity_type = signal.subject_entity_type
            d.subject_entity_id = signal.subject_entity_id
            d.assigned_user_id = self._assigned_user(signal, role)
            d.assigned_role = role.value
            d.detected_at = signal.detected_at
            d.signal_ids = [signal.signal_id]
            d.evidence_refs = signal.evidence_refs or []
            d.ai = ai_obj
            d.priority_deterministic_base = base
            d.priority_ai_adjustment = adj
            d.priority_score = final
            d.priority_band = _band(final)
            d.confidence = confidence
            if existing is None:
                d.status = DecisionStatus.OPEN.value
            self.s.flush()
            by_type[dtype] = by_type.get(dtype, 0) + 1

        return {"organization_id": self.org, "created": created, "refreshed": refreshed,
                "skipped": skipped, "ai_failed": ai_failed, "by_type": by_type,
                "provider": getattr(self.provider, "name", ""),
                "model": getattr(self.provider, "model", "")}

