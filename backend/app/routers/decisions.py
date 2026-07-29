"""Decision API (scoped attention queue) — spec §12.

Phase 1 provides the read + human-action surface over persisted decisions.
Decisions are produced by detectors (a later phase); here the endpoints exist,
enforce scope server-side, and are exercised by tests against seeded rows.

Scope is enforced server-side on every read (never in the UI): a salesperson
only sees decisions assigned to them and never the RESTRICTED decision types.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..authz import Principal, can_view_decision, current_principal, decision_list_scope
from ..context.assembler import _flatten, _is_restricted
from ..db import get_session
from ..domain import models
from ..domain.enums import (ApprovalKind, DecisionType, HumanAction, Role,
                            SubjectEntityType)
from .. import approvals
from ..domain.schemas import ActionRequest, DecisionRead
from ..repositories import DecisionRepository

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


def _subject_label(session: Session, d: models.Decision) -> str:
    if d.subject_entity_type == SubjectEntityType.CUSTOMER.value:
        row = session.get(models.Customer, d.subject_entity_id)
        return row.name if row else d.subject_entity_id
    if d.subject_entity_type == SubjectEntityType.PRODUCT.value:
        row = session.get(models.Product, d.subject_entity_id)
        return row.name if row else d.subject_entity_id
    return d.subject_entity_id


def _detail(session: Session, d: models.Decision, principal: Principal) -> dict:
    """Full role-gated decision projection for the detail screen.

    Facts (deterministic, sourced) are kept strictly separate from the AI
    interpretation. RESTRICTED facts are absent for a salesperson.
    """
    is_sales = principal.role is Role.SALESPERSON
    signal = None
    if d.signal_ids:
        signal = session.get(models.Signal, d.signal_ids[0])

    facts: list[dict] = []
    if signal is not None:
        flat: list = []
        _flatten("", signal.metrics or {}, flat)
        sources = ", ".join(sorted({str(e.get("record_type") or "")
                                    for e in (signal.evidence_refs or []) if e.get("record_type")}))
        for label, value in flat:
            if value is None:
                continue
            restricted = _is_restricted(label)
            if is_sales and restricted:
                continue
            facts.append({"label": label, "value": value, "restricted": restricted,
                          "source": sources or "Zoho"})

    ai = d.ai or {}
    # AI economics never leak to a salesperson even in the interpretation text
    # (the interpreter already ran on a redacted bundle; this is defense in depth).
    return {
        "decision_id": d.decision_id,
        "decision_type": d.decision_type,
        "subject_entity_type": d.subject_entity_type,
        "subject_entity_id": d.subject_entity_id,
        "subject_label": _subject_label(session, d),
        "assigned_user_id": d.assigned_user_id,
        "assigned_role": d.assigned_role,
        "detected_at": d.detected_at.isoformat() if d.detected_at else None,
        "priority": {"band": d.priority_band, "score": d.priority_score,
                     "deterministic_base": d.priority_deterministic_base,
                     "ai_adjustment": d.priority_ai_adjustment},
        "status": d.status,
        "facts": facts,
        "evidence": signal.evidence_refs if signal else [],
        "signal": {"type": signal.signal_type, "severity_base": signal.severity_base,
                   "window": signal.window, "sufficiency": signal.sufficiency} if signal else None,
        "interpretation": {
            "status": ai.get("status", "PENDING"),
            "title": ai.get("title"),
            "recommendation": ai.get("recommendation"),
            "explanation": ai.get("explanation"),
            "caveat": ai.get("caveat"),
            "should_surface": ai.get("should_surface", True),
            "model": ai.get("model"),
        },
        "confidence": d.confidence or {},
        "human_action": d.human_action,
        "outcome": None,  # Outcome Tracker is a later phase
    }


def _to_read(d: models.Decision) -> DecisionRead:
    return DecisionRead(
        decision_id=d.decision_id, organization_id=d.organization_id,
        decision_type=d.decision_type, subject_entity_type=d.subject_entity_type,
        subject_entity_id=d.subject_entity_id, assigned_user_id=d.assigned_user_id,
        assigned_role=d.assigned_role, detected_at=d.detected_at,
        priority_band=d.priority_band, priority_score=d.priority_score,
        status=d.status, ai_status=(d.ai or {}).get("status", "PENDING"),
        human_action=d.human_action, created_at=d.created_at, updated_at=d.updated_at,
    )


@router.get("", response_model=list[DecisionRead])
def list_decisions(
    type: Optional[str] = None,
    status_filter: Optional[str] = None,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[DecisionRead]:
    repo = DecisionRepository(session, principal.organization_id)
    scope = decision_list_scope(principal)
    # QUOTE_CONTEXT is on-demand quote support, not a proactive attention item —
    # keep it out of the queue unless explicitly requested by type.
    if type != DecisionType.QUOTE_CONTEXT.value:
        existing = tuple(scope.get("exclude_types", ()))
        scope["exclude_types"] = existing + (DecisionType.QUOTE_CONTEXT.value,)
    rows = repo.list(decision_type=type, status=status_filter, **scope)
    return [_to_read(d) for d in rows]


@router.get("/{decision_id}", response_model=DecisionRead)
def get_decision(
    decision_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> DecisionRead:
    repo = DecisionRepository(session, principal.organization_id)
    d = repo.get(decision_id)
    if d is None or not can_view_decision(principal, d):
        # 404 (not 403) so scope is not probeable.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found")
    return _to_read(d)


@router.get("/{decision_id}/detail")
def get_decision_detail(
    decision_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    repo = DecisionRepository(session, principal.organization_id)
    d = repo.get(decision_id)
    if d is None or not can_view_decision(principal, d):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found")
    return _detail(session, d, principal)


@router.post("/{decision_id}/action", response_model=DecisionRead)
def act_on_decision(
    decision_id: str,
    body: ActionRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> DecisionRead:
    repo = DecisionRepository(session, principal.organization_id)
    d = repo.get(decision_id)
    if d is None or not can_view_decision(principal, d):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found")
    try:
        action = HumanAction(body.action)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown action {body.action!r}")
    repo.record_human_action(d, action, actor_user_id=principal.user_id,
                             note=body.note, reason=body.reason)

    # Escalation is the one action that has to leave this endpoint. It used to
    # mean "set OVERRIDDEN and write a note", so the decision closed and nobody
    # upstream was told; now it raises a request into the approval queue and the
    # decision waits there.
    if action is HumanAction.ESCALATE:
        policy = approvals.get_policy(session, principal.organization_id)
        if policy.escalation_creates_approval:
            approvals.raise_request(
                session, principal, kind=ApprovalKind.DECISION_ESCALATION,
                subject_id=d.decision_id,
                subject={
                    "decision_id": d.decision_id,
                    "decision_type": d.decision_type,
                    "subject_entity_type": d.subject_entity_type,
                    "subject_entity_id": d.subject_entity_id,
                    "priority_band": d.priority_band,
                    "priority_score": d.priority_score,
                    "ai": d.ai or {},
                    "confidence": d.confidence or {},
                },
                title=(d.ai or {}).get("title") or d.decision_type,
                summary=f"{d.decision_type} escalated for a decision above this role",
                reason=body.note or body.reason)
    return _to_read(d)
