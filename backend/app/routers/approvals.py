"""Approval requests — raise, review, decide.

The queue that "Escalate to management" always implied and never had. A
salesperson raises a request against a quote line they cannot authorize; a
manager or owner sees it, with the economics the salesperson never received,
and approves, rejects, or asks for a different price.

The request that matters most is not any endpoint here — it is
``quote_submission_block``, called from the endpoint that actually sends a
quote. This router is where a human resolves what that gate is waiting for.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status as http
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import approvals
from ..approvals import ApprovalError, NotAuthorized
from ..store import store
from ..authz import Principal, current_principal
from ..commercial.policy import load_for_org
from ..commercial.quote_service import QuoteLineInput, assess_quote
from ..db import get_session
from ..domain import models
from ..domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    QuoteOutcomeStatus,
)

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def _names(session: Session, org: str, rows) -> dict[str, str]:
    return approvals.user_names(
        session, org,
        [r.requested_by_user_id for r in rows] + [r.decided_by_user_id for r in rows])


@router.get("")
def list_approvals(
    status_filter: Optional[ApprovalStatus] = Query(default=None, alias="status"),
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    rows = approvals.inbox(session, principal, status=status_filter)
    names = _names(session, principal.organization_id, rows)
    return {
        "requests": [approvals.to_dict(r, principal.role, names) for r in rows],
        "pending_for_me": approvals.pending_count(session, principal),
    }


@router.get("/{request_id}")
def get_approval(
    request_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    row = session.get(models.ApprovalRequest, request_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Approval request not found")
    if (principal.role.value == "SALESPERSON"
            and row.requested_by_user_id != principal.user_id):
        raise HTTPException(http.HTTP_403_FORBIDDEN, "That request is not yours")
    names = _names(session, principal.organization_id, [row])
    return approvals.to_dict(row, principal.role, names)


class RaiseQuoteApproval(BaseModel):
    """Ask for sign-off on one quote line, at the price currently on it."""

    quote_id: str
    customer: str
    line_id: str
    product: str
    qty: Decimal = Decimal("1")
    proposed_price: Decimal
    family: Optional[str] = None
    reason_code: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=2000)


@router.post("/quote-line", status_code=http.HTTP_201_CREATED)
def request_quote_line_approval(
    body: RaiseQuoteApproval,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Raise an approval for a quote line.

    The economics are re-derived here rather than taken from the request body,
    for the same reason a quote snapshot is: an approval whose numbers were
    supplied by the requester is a request to approve whatever they typed.
    """
    th = load_for_org(session, principal.organization_id)
    result = assess_quote(
        session, principal.organization_id, customer_ref=body.customer.strip(),
        lines=[QuoteLineInput(line_id=body.line_id, product_ref=body.product,
                              qty=body.qty, proposed_price=body.proposed_price,
                              family=body.family,
                              item_master_cost=store.line_cost(body.quote_id,
                                                               body.line_id))],
        th=th)
    intel = result.lines[0]

    if not intel.requires_approval:
        raise HTTPException(
            http.HTTP_400_BAD_REQUEST,
            "This price does not need approval — it is within policy.")

    econ = intel.economics
    below_cost = bool(econ and econ.unit_cost is not None
                      and econ.quoted_unit_price is not None
                      and econ.quoted_unit_price <= econ.unit_cost)
    policy = approvals.get_policy(session, principal.organization_id)
    authority = approvals.authority_for(below_cost=below_cost, policy=policy)

    subject = {
        "quote_id": body.quote_id,
        "customer": body.customer.strip(),
        "customer_id": result.customer_id,
        "product_ref": body.product,
        "product_id": intel.product_id,
        "qty": float(intel.qty),
        "quantity_band": intel.band.label,
        "quoted_unit_price": (float(econ.quoted_unit_price)
                              if econ and econ.quoted_unit_price is not None else None),
        "unit_cost": (float(econ.unit_cost)
                      if econ and econ.unit_cost is not None else None),
        "margin": econ.margin if econ else None,
        "line_revenue": (float(econ.line_revenue)
                         if econ and econ.line_revenue is not None else None),
        "gross_profit": (float(econ.gross_profit)
                         if econ and econ.gross_profit is not None else None),
        "exceptions": [e.to_dict() for e in intel.exceptions],
        "references": [r.to_dict() for r in intel.references],
        "data_sufficiency": intel.data_sufficiency.value,
        "below_cost": below_cost,
    }

    blocking = [e for e in intel.exceptions if e.requires_approval]
    title = blocking[0].title if blocking else "Price approval"
    price = float(econ.quoted_unit_price) if econ and econ.quoted_unit_price else 0.0
    summary = (f"{body.product} · {float(intel.qty):g} units at ₹{price:,.0f} "
               f"for {body.customer.strip()}")

    row = approvals.raise_request(
        session, principal, kind=ApprovalKind.QUOTE_LINE_PRICE,
        subject_id=body.quote_id, subject_line_id=body.line_id, subject=subject,
        title=title, summary=summary, required_authority=authority,
        reason=body.reason, reason_code=body.reason_code,
        thresholds_version=th.version)
    return approvals.to_dict(row, principal.role,
                             {principal.user_id: principal.name})


class DecideRequest(BaseModel):
    status: ApprovalStatus
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/{request_id}/decide")
def decide_approval(
    request_id: str,
    body: DecideRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    row = session.get(models.ApprovalRequest, request_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Approval request not found")
    try:
        if body.status is ApprovalStatus.WITHDRAWN:
            approvals.withdraw(session, principal, row, body.note)
        else:
            approvals.decide(session, principal, row, body.status, body.note)
    except NotAuthorized as e:
        raise HTTPException(http.HTTP_403_FORBIDDEN, str(e)) from e
    except ApprovalError as e:
        raise HTTPException(http.HTTP_409_CONFLICT, str(e)) from e
    names = _names(session, principal.organization_id, [row])
    return approvals.to_dict(row, principal.role, names)


def _below_floor_lines(quote_id: str) -> dict[str, str]:
    """The open quote's below-floor lines, or nothing if it is not this process's.

    The same argument the send endpoint passes, so this window shows what that
    gate will actually decide. Without it the two disagreed in the way that is
    worst to be on the receiving end of: the button was enabled, said "Create
    Zoho estimate", and returned a 403 when pressed.
    """
    quote = store.get(quote_id)
    if quote is None:
        return {}
    return {ln.id: ln.reqCode for ln in quote.lines if ln.economics().below_floor}


@router.get("/quotes/{quote_id}/gate")
def quote_gate(
    quote_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Whether this quote may be sent, and what is holding it.

    The same function the send endpoint calls, exposed so the UI can show the
    state before someone presses a button that will fail. It is not the gate —
    it is a window onto it.
    """
    org = principal.organization_id
    blocked = approvals.quote_submission_block(
        session, org, quote_id, also_requiring=_below_floor_lines(quote_id))
    rows = list(session.scalars(
        select(models.ApprovalRequest)
        .where(models.ApprovalRequest.organization_id == org,
               models.ApprovalRequest.subject_id == quote_id)
        .order_by(models.ApprovalRequest.requested_at)))
    names = _names(session, org, rows)
    outcome = session.scalar(
        select(models.QuoteOutcome)
        .where(models.QuoteOutcome.organization_id == org,
               models.QuoteOutcome.quote_id == quote_id))
    return {
        "quote_id": quote_id,
        "can_submit": blocked is None,
        "blocked_reason": blocked,
        "outcome": outcome.status if outcome else QuoteOutcomeStatus.DRAFT.value,
        "requests": [approvals.to_dict(r, principal.role, names) for r in rows],
        "policy": {
            "require_approval_for_quotes":
                approvals.get_policy(session, org).require_approval_for_quotes,
        },
    }
