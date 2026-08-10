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
        self.provider = provider or select_provider()
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
        by_type: dict[str, int] = {}

        for signal in _latest_signals(self.s, self.org):
            dtype = signal.signal_type
            role = _recipient_role(dtype)
            bundle = assemble_from_signal(self.s, signal, role, self.th)
            key = _decision_key(self.org, dtype, signal.subject_entity_id, signal)
            existing = self.repo.get_by_key(key)

            # cost control: unchanged context on an open decision ⇒ no re-inference
            if (existing is not None and existing.status == DecisionStatus.OPEN.value
                    and (existing.ai or {}).get("context_hash") == bundle.context_hash()):
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
                "skipped": skipped, "by_type": by_type,
                "provider": getattr(self.provider, "name", ""),
                "model": getattr(self.provider, "model", "")}

