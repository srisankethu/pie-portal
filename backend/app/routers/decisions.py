"""Decision API (scoped attention queue) — spec §12.

Phase 1 provides the read + human-action surface over persisted decisions.
Decisions are produced by detectors (a later phase); here the endpoints exist,
enforce scope server-side, and are exercised by tests against seeded rows.

Scope is enforced server-side on every read (never in the UI): a salesperson
only sees decisions assigned to them and never the RESTRICTED decision types.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, can_view_decision, current_principal, decision_list_scope
from ..context.assembler import _flatten, _is_restricted
from ..db import get_session
from ..domain import models
from ..domain.origin import Companies, index_of
from ..domain.enums import (ApprovalKind, DecisionType, HumanAction, Role,
                            SubjectEntityType)
from .. import clock, approvals
from ..domain.schemas import ActionRequest, DecisionRead
from ..repositories import DecisionRepository
from ..state.engine import why as state_why
from ..state.opportunities import ACTIONS as _ACTIONS

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


#: Which master holds the name for each kind of subject. A mapping rather than
#: a chain of ``if``s: a new subject kind is a row here, and a kind with no row
#: falls through to its id rather than silently borrowing another's name.
_SUBJECT_MASTERS = {
    SubjectEntityType.CUSTOMER.value: (models.Customer, "name"),
    SubjectEntityType.PRODUCT.value: (models.Product, "name"),
    SubjectEntityType.VENDOR.value: (models.Vendor, "name"),
}


def _subject_label(session: Session, d: models.Decision) -> str:
    entry = _SUBJECT_MASTERS.get(d.subject_entity_type)
    if entry is None:
        return d.subject_entity_id
    model, attr = entry
    row = session.get(model, d.subject_entity_id)
    return getattr(row, attr, None) or d.subject_entity_id


def _subject_origin(session: Session, d: models.Decision) -> dict:
    """Which connected company the card is *about*.

    Named `subject_origin`, not `origin`: a decision already has an origin, and
    it means something else entirely — whether the card was folded from state or
    raised from a signal. Reusing the word silently replaced that field, and the
    test that asserts a signal card still says SIGNAL is what caught it. Two
    meanings for one key on one object is a defect however carefully it is
    documented.

    The queue carried this and the card did not, which is the wrong way round:
    the queue is scanned and the card is where somebody decides. Two accounts
    called "Pitti Engineering" produce two cards, and opening one of them
    without knowing which book it belongs to is opening the wrong one half the
    time.
    """
    entry = _SUBJECT_MASTERS.get(d.subject_entity_type)
    row = session.get(entry[0], d.subject_entity_id) if entry else None
    companies = Companies(session, d.organization_id)
    return {
        "subject_origin": companies.of(row).to_dict() if row is not None else None,
        "sources_differ": companies.count > 1,
    }


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
    confidence = dict(d.confidence or {})
    # AI economics never leak to a salesperson even in the interpretation text
    # (the interpreter already ran on a redacted bundle; this is defense in depth).
    return {
        # Which producer made this. The card renders two quite different
        # things — an interpretation, or quantified impact with its own
        # arithmetic — and asking the row rather than sniffing its fields is
        # what stops a signal decision with an empty impact rendering as a
        # state one worth nothing.
        "origin": d.origin,
        # ── the state-derived half ───────────────────────────────────────────
        #
        # Empty for a signal decision, which measures the shape of evidence
        # rather than money. Present and complete for a state one: what it is
        # worth, why it exists, what can be done, and the working behind its
        # position in the queue.
        "impact": d.impact or {},
        "rationale": d.rationale,
        "actions": [{"key": a, "label": _ACTIONS.get(a, a)} for a in (d.actions or [])],
        "ranking": confidence.get("ranking") or {},
        "state_evidence": confidence.get("evidence") or {},
        "state": {"keys": list(d.state_keys or []),
                  "as_of": d.state_as_of.isoformat() if d.state_as_of else None,
                  "thresholds_version": confidence.get("thresholds_version")},
        "decision_id": d.decision_id,
        "decision_type": d.decision_type,
        "subject_entity_type": d.subject_entity_type,
        "subject_entity_id": d.subject_entity_id,
        "subject_label": _subject_label(session, d),
        **_subject_origin(session, d),
        "assigned_user_id": d.assigned_user_id,
        "assigned_role": d.assigned_role,
        "detected_at": clock.iso(d.detected_at),
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


def _visible(session: Session, principal: Principal,
             decision_id: str) -> models.Decision:
    """This principal's own decision, or 404.

    One implementation because it is the authorization check, and four copies
    of an authorization check is three chances for the fifth endpoint to forget
    it. 404 rather than 403 throughout, so scope is not probeable: a manager's
    decision must be indistinguishable from one that does not exist.
    """
    d = DecisionRepository(session, principal.organization_id).get(decision_id)
    if d is None or not can_view_decision(principal, d):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found")
    return d


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
    # Which company each decision is about. A queue pooled across three
    # connected books lists "ABC Industries" three times otherwise, and the
    # three are different customers with different problems.
    companies = Companies(session, principal.organization_id)
    masters = {
        SubjectEntityType.CUSTOMER.value: models.Customer,
        SubjectEntityType.PRODUCT.value: models.Product,
        SubjectEntityType.VENDOR.value: models.Vendor,
    }
    # One index per entity kind that actually appears, loaded once rather than
    # per row — and never for a kind this page does not show.
    indexes = {
        kind: index_of(session, principal.organization_id, model)
        for kind, model in masters.items()
        if any(d.subject_entity_type == kind for d in rows)
    }
    out = []
    for d in rows:
        read = _to_read(d)
        record = indexes.get(d.subject_entity_type, {}).get(d.subject_entity_id)
        read.subject_origin = (companies.of(record).to_dict()
                               if record is not None else None)
        read.sources_differ = companies.count > 1
        out.append(read)
    return out


@router.get("/{decision_id}", response_model=DecisionRead)
def get_decision(
    decision_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> DecisionRead:
    return _to_read(_visible(session, principal, decision_id))


@router.get("/{decision_id}/detail")
def get_decision_detail(
    decision_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    return _detail(session, _visible(session, principal, decision_id), principal)


#: How much of a state key's working one response carries by default. A busy
#: item can have hundreds of transitions and a card is read before it is
#: audited, so the first page is short. The *total* is always reported, and the
#: rest is one page away rather than out of reach — a chain that stops at forty
#: with no way forward is a chain that cannot settle an argument about the
#: forty-first.
_TRACE_PAGE = 40
#: The most one request will return. Somebody reconciling a full year is a real
#: reader; a request for ten thousand rows is not.
_TRACE_MAX = 500


def _erp_ref(event: models.BusinessEvent) -> dict:
    """The ERP record an event was read from. The bottom of the chain."""
    return {
        "system": event.connector or "unknown",
        "record_type": event.source_doc_type,
        "record_id": event.source_doc_id,
        "line_id": event.source_line_id,
        "modified_at": event.source_modified_at or None,
    }


@router.get("/{decision_id}/trace")
def trace_decision(
    decision_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(_TRACE_PAGE, ge=1, le=_TRACE_MAX),
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Why this decision exists, all the way down.

    The chain the platform has been building towards:

        decision → impact → business state → state transition
                 → business event → ERP record

    Assembled rather than stored: every hop is a lookup along a key that
    already exists, so nothing here can disagree with the decision it explains.

    A signal-derived decision has no state keys and says so, rather than
    returning an empty chain that reads like a gap in the data.

    Paged newest-first by offset rather than by cursor: the transitions of one
    state key are a bounded set re-derived by each fold, not a growing feed, so
    there is no stream for a cursor to keep its place in — and an offset is a
    page number the reader can reason about.
    """
    d = _visible(session, principal, decision_id)
    org = principal.organization_id
    evidence = (d.confidence or {}).get("evidence") or {}
    state_name = evidence.get("state")
    levels = []
    for key in (d.state_keys or []):
        if not state_name or d.state_as_of is None:
            continue
        row = session.scalar(
            select(models.BusinessState).where(
                models.BusinessState.organization_id == org,
                models.BusinessState.state == state_name,
                models.BusinessState.key == key,
                models.BusinessState.as_of == d.state_as_of))
        steps = state_why(session, org, state_name, key, d.state_as_of)
        # Newest first: the reader is asking "what moved this", and the most
        # recent movements are the ones they can still act on.
        steps = list(reversed(steps))
        total = len(steps)
        page = steps[offset:offset + limit]
        events = {
            e.seq: e for e in session.scalars(
                select(models.BusinessEvent).where(
                    models.BusinessEvent.seq.in_(
                        [s["event_seq"] for s in page] or [-1])))}
        levels.append({
            "state": state_name,
            "key": key,
            "label": _subject_label(session, d),
            "as_of": d.state_as_of.isoformat(),
            "value": (row.value if row else {}),
            "event_count": (row.event_count if row else 0),
            "thresholds_version": (row.thresholds_version if row else None),
            "transitions_total": total,
            "transitions_offset": offset,
            "has_more": offset + len(page) < total,
            "transitions": [
                {
                    "event_seq": step["event_seq"],
                    "event_type": step["event_type"],
                    "occurred_on": step["occurred_on"],
                    "changes": step["changes"],
                    "erp": (_erp_ref(events[step["event_seq"]])
                            if step["event_seq"] in events else None),
                }
                for step in page
            ],
        })

    return {
        "decision_id": d.decision_id,
        "decision_type": d.decision_type,
        "origin": d.origin,
        "subject_label": _subject_label(session, d),
        **_subject_origin(session, d),
        "impact": d.impact or {},
        "rationale": d.rationale,
        "ranking": (d.confidence or {}).get("ranking") or {},
        "states": levels,
        "unavailable": (None if levels else
                        "This decision was raised from a signal over sales and "
                        "cost lines rather than from Business State, so it has "
                        "no state to drill into. Its source records are listed "
                        "as evidence on the card."),
    }


@router.post("/{decision_id}/action", response_model=DecisionRead)
def act_on_decision(
    decision_id: str,
    body: ActionRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> DecisionRead:
    repo = DecisionRepository(session, principal.organization_id)
    d = _visible(session, principal, decision_id)
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
