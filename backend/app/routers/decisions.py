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
from ..db import get_session
from ..domain import models
from ..domain.enums import HumanAction
from ..domain.schemas import ActionRequest, DecisionRead
from ..repositories import DecisionRepository

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


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
    return _to_read(d)
