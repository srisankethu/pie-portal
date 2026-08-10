"""Quote intelligence API — deterministic commercial context at quoting time.

``POST /api/v1/quote-intelligence/assess`` takes a whole quote (customer + N
lines with quantities and proposed prices) and returns, per line: the price
references, the exceptions that fired, and — for a manager or owner — the
economics behind them. One request per quote, not one per line.

``POST /api/v1/quote-intelligence/snapshot`` freezes those facts into the
immutable ``quote_decisions`` audit trail, optionally with the reason a
salesperson went ahead anyway.

``POST /api/v1/quote-intelligence/outcome`` moves the quote along
DRAFT → SENT → WON/LOST, so a price can later be joined to whether it won. A
loss carries a reason from ``QuoteLossReason`` and is refused without one —
``/api/v1/insight/quote-outcomes`` reads that column directly, and a loss
recorded without it is a row that can be counted and never learned from.

No endpoint here calls a model. Every number is computed by ``app.commercial``.
Role scoping is enforced server-side: a salesperson's response contains no cost,
no margin, and no figure derived from them — absent, not masked.

**Repeated querying is part of the threat model here, and it is the reason for
three otherwise-odd restrictions below.** Every guard in this file used to be
correct for one request and useless across two: ``proposed_price`` is supplied
by the caller and the response says which rules fired, so walking the price
locates the boundary each rule fires at, and a boundary computed from cost
discloses cost. That is the ``filterCounts.MFLOOR`` defect (CLAUDE.md §1) in a
second costume, and it survived here because the tests assert what one response
contains rather than what a sequence of them reveals.

What the rules themselves disclose is handled in ``quote_service.project`` via
``QuoteException.boundary_refs``. This file handles the three things that made
the walk *cheap*: an unbounded ``as_of`` (the same probe per date recovers the
item's whole cost history), an unscoped customer, and a 200-line batch that
turns 200 probes into one request.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .. import approvals
from ..authz import Principal, can_view_customer, current_principal
from ..store import store
from ..commercial.policy import load_for_org
from ..commercial.quote_service import (
    InvalidTransition,
    MissingLossReason,
    QuoteLineInput,
    assess_and_record,
    assess_quote,
    get_outcome,
    outcome_to_dict,
    project,
    resolve_customer,
    snapshot_to_dict,
    snapshots_for_quote,
    set_outcome,
    summarize,
)
from ..db import get_session
from ..domain.enums import QuoteLossReason, QuoteOutcomeStatus

router = APIRouter(prefix="/api/v1/quote-intelligence", tags=["quote-intelligence"])

_MAX_LINES = 200

#: How far from today a quote may be assessed. Back-dating a quote by a few
#: weeks is ordinary; assessing one as of an arbitrary past date is how the
#: cost *history* of an item is read out one day at a time, which is worth more
#: to a competitor than today's cost is. Wide enough that no real quote hits it.
_AS_OF_WINDOW_DAYS = 90

#: How many distinct prices one product may carry within a single request.
#: Quoting an item at three quantity-break prices on one RFQ is real work; two
#: hundred is a bisection. This does not stop the walk — it removes the batch
#: that made it two requests instead of fifty, which is what makes it visible.
_MAX_PRICES_PER_PRODUCT = 4


class _AsOfBounded(BaseModel):
    """A request body carrying an assessment date, bounded to near today.

    Inherited rather than repeated, because the two bodies below are assessed by
    the same engine and a bound on only one of them is not a bound.
    """

    as_of: Optional[date] = None

    @field_validator("as_of")
    @classmethod
    def _within_window(cls, value: Optional[date]) -> Optional[date]:
        """``date.today()`` rather than ``clock.today(th.timezone)``
        deliberately: this decides whether a request body is well-formed, and it
        must not need a database session to do it. ``assess_quote`` defaults to
        the same clock, so the two cannot disagree about what "today" is."""
        if value is not None and abs((value - date.today()).days) > _AS_OF_WINDOW_DAYS:
            raise ValueError(
                f"An assessment date must be within {_AS_OF_WINDOW_DAYS} days "
                f"of today. Assessing as of a date well outside that window "
                f"reads an item's cost history rather than pricing a quote.")
        return value


class LineIn(BaseModel):
    line_id: str
    product: str = ""
    qty: Decimal = Decimal("1")
    proposed_price: Optional[Decimal] = None
    family: Optional[str] = None


class AssessRequest(_AsOfBounded):
    customer: str
    lines: list[LineIn] = Field(default_factory=list)
    quote_id: Optional[str] = None


def _visible_customer_ref(session: Session, principal: Principal, ref: str) -> str:
    """``ref`` if this principal may see the customer it names, else ``""``.

    A salesperson is scoped to their own accounts on ``/api/v1/accounts``, on the
    insight timeline and on ``/insight/negotiate``; this endpoint skipped the
    rule entirely, so any customer name in the book returned that relationship's
    price history to anyone who typed it. The rule is `authz.can_view_customer`,
    shared rather than rewritten, for the reason that function's own docstring
    gives.

    Answering with an empty ref rather than 404 is the deliberate part. An
    unresolved customer is an ordinary case here — quoting somebody who has
    never bought before is the point of the screen, and a test pins that
    behaviour — so degrading to "we do not know this customer" makes an
    out-of-scope account **indistinguishable from a new one**. A 403 would
    confirm the account exists, which is most of what an enumeration is after.
    """
    customer = resolve_customer(session, principal.organization_id, ref)
    if customer is not None and not can_view_customer(principal, customer, session):
        return ""
    return ref


def _reject_price_sweep(lines: list[LineIn]) -> None:
    """Refuse a request that prices one product many ways at once.

    A quote carries one price per line. Two hundred lines naming the same
    product at two hundred prices is not a quote — it is a parallel search for
    the price at which the exception rules change their answer, and the answer
    they change at is computed from cost. Refused rather than truncated: a
    silently shortened assessment is a screen quietly telling somebody their
    line is fine.
    """
    prices: dict[str, set[Decimal]] = {}
    for ln in lines:
        if ln.proposed_price is None:
            continue
        key = (ln.product or "").strip().casefold()
        prices.setdefault(key, set()).add(ln.proposed_price)
    for key, distinct in prices.items():
        if len(distinct) > _MAX_PRICES_PER_PRODUCT:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"One product may carry at most {_MAX_PRICES_PER_PRODUCT} "
                f"different prices in a single assessment; this request prices "
                f"{key or 'a line'} {len(distinct)} ways. Assess the quote you "
                f"are sending.")


def _validate(body: AssessRequest | SnapshotRequest, *, what: str) -> None:
    """The shape checks both endpoints share. One implementation, so a limit
    added to the assessment path cannot be missing from the recording one."""
    if len(body.lines) > _MAX_LINES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"At most {_MAX_LINES} lines per {what}")
    _reject_price_sweep(list(body.lines))


def _inputs(body: AssessRequest) -> list[QuoteLineInput]:
    return [
        QuoteLineInput(line_id=ln.line_id, product_ref=ln.product,
                       qty=ln.qty, proposed_price=ln.proposed_price,
                       family=ln.family,
                       # Only when the caller named the quote. Without an id
                       # there is no server-held line to read a cost from, and
                       # the assessment falls back to bills alone as before.
                       item_master_cost=(store.line_cost(body.quote_id, ln.line_id)
                                         if body.quote_id else None))
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
    _validate(body, what="request")

    org = principal.organization_id
    result = assess_quote(
        session, org,
        customer_ref=_visible_customer_ref(session, principal,
                                           body.customer.strip()),
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


class SnapshotRequest(_AsOfBounded):
    quote_id: str
    customer: str
    lines: list[SnapshotLine] = Field(default_factory=list)


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
    _validate(body, what="request")

    org = principal.organization_id
    customer_ref = _visible_customer_ref(session, principal, body.customer.strip())
    result, rows = assess_and_record(
        session, org, quote_id=body.quote_id.strip(), customer_ref=customer_ref,
        lines=[QuoteLineInput(line_id=ln.line_id, product_ref=ln.product, qty=ln.qty,
                              proposed_price=ln.proposed_price, family=ln.family,
                              item_master_cost=store.line_cost(body.quote_id,
                                                               ln.line_id))
               for ln in body.lines],
        user_id=principal.user_id,
        overrides={ln.line_id: (ln.override_reason, ln.override_reason_code)
                   for ln in body.lines},
        as_of=body.as_of)

    for intel in result.lines:
        # A line re-priced back within policy should not leave an unanswerable
        # request sitting in an approver's queue.
        approvals.release_if_no_longer_needed(
            session, principal, quote_id=body.quote_id.strip(),
            line_id=intel.line_id,
            still_requires_approval=intel.requires_approval)

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
    #: Required when ``status`` is LOST. Not enforced here as a Pydantic
    #: constraint on purpose — ``quote_service.set_outcome`` owns the rule, so
    #: the CLI, a future importer and this endpoint cannot drift about what
    #: counts as a recordable loss.
    loss_reason: Optional[QuoteLossReason] = None
    lost_to: Optional[str] = None


@router.post("/outcome")
def quote_outcome(
    body: OutcomeRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    org = principal.organization_id
    # Through the same scope rule as the other two: this endpoint hands back a
    # resolved `customer_id`, so without it a name typed here is a lookup from
    # any customer's name to their platform id.
    customer_ref = _visible_customer_ref(session, principal, body.customer.strip())
    customer = resolve_customer(session, org, customer_ref) if customer_ref else None
    try:
        row = set_outcome(
            session, org, quote_id=body.quote_id.strip(), status=body.status,
            customer_ref=customer_ref,
            customer_id=customer.customer_id if customer else None,
            note=body.note, loss_reason=body.loss_reason,
            lost_to=body.lost_to, user_id=principal.user_id)
    except MissingLossReason as e:
        # 422 rather than 409: the request is well-formed and the transition is
        # legal, one required field is absent, and the message names the
        # choices. A 409 would send the caller looking at the lifecycle.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except InvalidTransition as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return outcome_to_dict(row) or {}


@router.get("/thresholds")
def thresholds(principal: Principal = Depends(current_principal),
               session: Session = Depends(get_session)) -> dict:
    """The pricing policy in force, so a flagged price can be argued with.

    A salesperson sees the quantity ladder and the tolerance — the shape of the
    rules. The margin numbers themselves are cost policy and stay with managers.
    """
    from ..domain.enums import Role
    th = load_for_org(session, principal.organization_id)
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
            "min_quote_exception_impact": th.min_quote_exception_impact,
        })
    return out
