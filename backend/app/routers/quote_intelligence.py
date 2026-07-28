"""Quote intelligence API — deterministic commercial context at quoting time.

``POST /api/v1/quote-intelligence/assess`` takes a whole quote (customer + N
lines with quantities and proposed prices) and returns, per line: the price
references, the exceptions that fired, and — for a manager or owner — the
economics behind them. One request per quote, not one per line.

``POST /api/v1/quote-intelligence/snapshot`` freezes those facts into the
immutable ``quote_decisions`` audit trail, optionally with the reason a
salesperson went ahead anyway.

``POST /api/v1/quote-intelligence/outcome`` moves the quote along
DRAFT → SENT → WON/LOST, so a price can later be joined to whether it won.

No endpoint here calls a model. Every number is computed by ``app.commercial``.
Role scoping is enforced server-side: a salesperson's response contains no cost,
no margin, and no figure derived from them — absent, not masked.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal
from ..commercial.config import load_commercial_thresholds
from ..commercial.quote_service import (
    InvalidTransition,
    QuoteLineInput,
    assess_quote,
    get_outcome,
    outcome_to_dict,
    project,
    record_snapshot,
    resolve_customer,
    snapshot_to_dict,
    snapshots_for_quote,
    set_outcome,
    summarize,
)
from ..db import get_session
from ..domain.enums import QuoteOutcomeStatus

router = APIRouter(prefix="/api/v1/quote-intelligence", tags=["quote-intelligence"])

_MAX_LINES = 200


class LineIn(BaseModel):
    line_id: str
    product: str = ""
    qty: Decimal = Decimal("1")
    proposed_price: Optional[Decimal] = None
    family: Optional[str] = None


class AssessRequest(BaseModel):
    customer: str
    lines: list[LineIn] = Field(default_factory=list)
    quote_id: Optional[str] = None
    as_of: Optional[date] = None


def _inputs(body: AssessRequest) -> list[QuoteLineInput]:
    return [
        QuoteLineInput(line_id=ln.line_id, product_ref=ln.product,
                       qty=ln.qty, proposed_price=ln.proposed_price,
                       family=ln.family)
        for ln in body.lines
    ]


@router.post("/assess")
def assess(
    body: AssessRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    if not body.customer.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A customer is required")
    if len(body.lines) > _MAX_LINES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"At most {_MAX_LINES} lines per request")

    org = principal.organization_id
    result = assess_quote(session, org, customer_ref=body.customer.strip(),
                          lines=_inputs(body), as_of=body.as_of)
    refs = {ln.line_id: ln.product for ln in body.lines}

    outcome = get_outcome(session, org, body.quote_id) if body.quote_id else None
    return {
        "customer": {
            "customer_id": result.customer_id,
            "label": result.customer_label,
            "resolved": result.customer_id is not None,
            "ref": body.customer.strip(),
        },
        "as_of": result.as_of.isoformat(),
        "lines": [
            project(intel, principal.role,
                    product_ref=refs.get(intel.line_id, ""),
                    unresolved=intel.line_id in result.unresolved)
            for intel in result.lines
        ],
        "summary": summarize(result, principal.role),
        "outcome": outcome_to_dict(outcome),
        "thresholds_version": result.thresholds_version,
    }


class SnapshotLine(LineIn):
    override_reason: Optional[str] = None
    override_reason_code: Optional[str] = None


class SnapshotRequest(BaseModel):
    quote_id: str
    customer: str
    lines: list[SnapshotLine] = Field(default_factory=list)
    as_of: Optional[date] = None


@router.post("/snapshot", status_code=status.HTTP_201_CREATED)
def snapshot(
    body: SnapshotRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Freeze the current assessment of these lines into the audit trail.

    The facts are re-derived here rather than taken from the client: a snapshot
    whose numbers were supplied by the browser records what the browser claimed,
    which is precisely the thing an audit trail must not do.
    """
    if not body.quote_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A quote id is required")
    if not body.lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No lines to record")
    if len(body.lines) > _MAX_LINES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"At most {_MAX_LINES} lines per request")

    org = principal.organization_id
    customer_ref = body.customer.strip()
    result = assess_quote(
        session, org, customer_ref=customer_ref,
        lines=[QuoteLineInput(line_id=ln.line_id, product_ref=ln.product, qty=ln.qty,
                              proposed_price=ln.proposed_price, family=ln.family)
               for ln in body.lines],
        as_of=body.as_of)

    overrides = {ln.line_id: (ln.override_reason, ln.override_reason_code)
                 for ln in body.lines}
    refs = {ln.line_id: ln.product for ln in body.lines}

    rows = []
    for intel in result.lines:
        reason, reason_code = overrides.get(intel.line_id, (None, None))
        rows.append(record_snapshot(
            session, org, quote_id=body.quote_id.strip(), intel=intel,
            customer_ref=customer_ref, product_ref=refs.get(intel.line_id, ""),
            user_id=principal.user_id,
            override_reason=reason, override_reason_code=reason_code))

    # A recorded quote is at least a draft, so the outcome path has a start.
    set_outcome(session, org, quote_id=body.quote_id.strip(),
                status=QuoteOutcomeStatus.DRAFT, customer_ref=customer_ref,
                customer_id=result.customer_id, user_id=principal.user_id)

    return {
        "quote_id": body.quote_id.strip(),
        "recorded": len(rows),
        "snapshots": [snapshot_to_dict(r, principal.role) for r in rows],
    }


@router.get("/quotes/{quote_id}")
def quote_audit(
    quote_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Every decision ever recorded against this quote, oldest first.

    Append-only, so a re-priced line appears more than once — that sequence is
    the negotiation, and collapsing it to the latest row would erase it.
    """
    org = principal.organization_id
    rows = snapshots_for_quote(session, org, quote_id)
    return {
        "quote_id": quote_id,
        "outcome": outcome_to_dict(get_outcome(session, org, quote_id)),
        "decisions": [snapshot_to_dict(r, principal.role) for r in rows],
    }


class OutcomeRequest(BaseModel):
    quote_id: str
    status: QuoteOutcomeStatus
    customer: str = ""
    note: Optional[str] = None


@router.post("/outcome")
def quote_outcome(
    body: OutcomeRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    org = principal.organization_id
    customer = (resolve_customer(session, org, body.customer.strip())
                if body.customer.strip() else None)
    try:
        row = set_outcome(
            session, org, quote_id=body.quote_id.strip(), status=body.status,
            customer_ref=body.customer.strip(),
            customer_id=customer.customer_id if customer else None,
            note=body.note, user_id=principal.user_id)
    except InvalidTransition as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return outcome_to_dict(row) or {}


@router.get("/thresholds")
def thresholds(principal: Principal = Depends(current_principal)) -> dict:
    """The pricing policy in force, so a flagged price can be argued with.

    A salesperson sees the quantity ladder and the tolerance — the shape of the
    rules. The margin numbers themselves are cost policy and stay with managers.
    """
    from ..domain.enums import Role
    th = load_commercial_thresholds()
    out: dict = {
        "version": th.version,
        "quantity_band_edges": list(th.quantity_band_edges),
        "quote_price_tolerance_pct": th.quote_price_tolerance_pct,
        "recent_days": th.recent_days,
    }
    if principal.role is not Role.SALESPERSON:
        out.update({
            "target_margin_default": th.target_margin_default,
            "target_margin_by_family": dict(th.target_margin_by_family),
            "min_margin": th.min_margin,
            "margin_floor": th.margin_floor,
            "sales_discretion_band": th.sales_discretion_band,
            "min_quote_exception_impact_rupees": th.min_quote_exception_impact_rupees,
        })
    return out
