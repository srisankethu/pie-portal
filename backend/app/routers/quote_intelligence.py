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
recorded without it is a row that can be counted and never learned from. Both
ways of naming a quote are scoped like any other account read: an ERP-raised
quote by its own reference (``_may_record_erp_quote``), and a quote this
platform priced by its ``quote_id`` (``_holds_platform_quote``). Both keys
are checked, because either one on its own identifies the row that moves.

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
from .. import quote_workspace
from ..commercial.policy import load_for_org
from ..commercial.quote_service import (
    AmbiguousQuoteDocument,
    InvalidTransition,
    MissingLossReason,
    QuoteLineInput,
    QuoteOutcomeRepointed,
    assess_and_record,
    assess_quote,
    get_outcome,
    outcome_to_dict,
    project,
    resolve_customer,
    snapshot_to_dict,
    snapshots_for_quote,
    set_outcome,
    sole_erp_quote,
    summarize,
)
from ..db import get_session
from ..domain import models
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


#: The one answer a salesperson gets for every ERP reference that is not one of
#: theirs: a quote raised on another desk, a quote nobody attributed, a
#: reference that names nothing, and a reference that names two. Identical on
#: purpose — a refusal that told them apart would answer "does this reference
#: exist" for the whole book, which is the enumeration ``_visible_customer_ref``
#: degrades rather than confirms one field further up.
_NO_SUCH_ERP_QUOTE = (
    "No quote on your list answers to that reference. A quote raised for an "
    "account you do not hold is recorded by whoever holds it.")


def _may_record_erp_quote(session: Session, principal: Principal,
                          quote_document_ref: str) -> bool:
    """Whether this principal may write an outcome onto the ERP quote named.

    ``_visible_customer_ref`` scopes the customer *name the caller typed* and
    nothing else, which was the whole rule while ``quote_id`` was the only key.
    The reasoning offered for leaving that key unscoped — a quote this platform
    priced is reached through a store the caller already holds — turned out to
    be wrong twice over, and ``_holds_platform_quote`` below is the
    correction; read it beside this one, because the two keys are checked
    independently. ``quote_document_ref`` is still different in kind — it is the ERP's own id
    for a quote nobody here priced, it identifies the row by itself, and the
    worklist that hands those references out is scoped while the write was not.
    So a salesperson could name any estimate in the organization and put a
    terminal WON or LOST on it. Won and lost are terminal by design, so the
    account's rightful owner is then refused forever; and because the typed
    name is blanked rather than refused, the row carries no ``customer_id`` and
    slips past ``insight._scoped_outcomes``' narrowing into every manager's loss
    analysis. Reading the row back was the other half of it: ``outcome_to_dict``
    echoes ``customer_id``, the customer's real name and whatever free text a
    colleague wrote about the account, which is ``_visible_customer_ref``'s own
    enumeration running backwards — reference to identity — on the one field
    the caller supplied being correctly withheld.

    The rule is the one ``/insight/unrecorded-quotes`` narrows its worklist
    with, so that what a person may record is exactly what they were shown:
    ``commercial.insight.unrecorded._load`` keeps a quote for a narrowed reader
    only where its ``customer_id`` is one of theirs, and drops an unattributed
    one rather than showing a stranger's quote on somebody's list. Expressed
    through ``authz.can_view_customer`` rather than re-derived from
    ``assigned_user_id``, for the reason that function's docstring gives.

    **Four different failures answer False and the caller cannot tell them
    apart** — hence one message for all four, and hence the ambiguous reference
    being answered here rather than left to the 409 below: that message names
    both connected books a reference is torn between, and those are quotes this
    reader may not see. The manager who can see both books is the one who can
    do anything about it, and they still get the 409.

    Applied whenever a reference is supplied, including beside a ``quote_id``.
    Both keys together is the normal shape for a platform quote pushed to the
    ERP, and letting the reference through unchecked because a ``quote_id`` sat
    next to it would be this same defect in a second costume: ``set_outcome``
    writes the reference onto that row, and the rightful owner's next call —
    keyed on the reference — finds it and is refused by it. The cost is narrow
    and stated: a salesperson naming both before the sync has read the new
    estimate is refused and records against the ``quote_id`` alone, which
    identifies the row anyway, and the ERP link was already written by
    ``routers.quote`` at the moment it pushed.

    A manager or owner is not narrowed to any subset of the book, so nothing is
    resolved for them at all: they keep today's behaviour exactly, including
    recording against a reference the sync holds no document for, which
    ``_opening_status`` deliberately treats as a dangling pointer rather than
    as evidence of a send.
    """
    if not principal.is_salesperson:
        return True
    try:
        document = sole_erp_quote(session, principal.organization_id,
                                  quote_document_ref)
    except AmbiguousQuoteDocument:
        return False
    customer = (session.get(models.Customer, document.customer_id)
                if document is not None and document.customer_id else None)
    return can_view_customer(principal, customer, session)


#: The one answer a salesperson gets for every ``quote_id`` that is not one of
#: theirs: a quote priced on another desk, a quote nobody attributed, and an id
#: that names nothing at all. Identical for the same reason
#: ``_NO_SUCH_ERP_QUOTE`` is — and here the ids are *generated*, not typed.
#: They were once ``q{run}-{counter}``, so a refusal that told a live quote
#: from an empty id would have enumerated the run with two nested loops; they
#: are UUIDs now, and the refusal stays identical because a rule that depends
#: on the id being hard to guess is a rule waiting for the next id scheme.
_NO_SUCH_PLATFORM_QUOTE = (
    "No quote on your list answers to that id. A quote priced for an account "
    "you do not hold is recorded by whoever holds it.")


def _holds_platform_quote(session: Session, principal: Principal,
                          quote_id: str, *, when_unattributed: bool) -> bool:
    """Whether this principal holds the platform quote named by ``quote_id``.

    ``_may_record_erp_quote`` scoped the other key on the reasoning that
    ``quote_id`` needed none — "a quote this platform priced is reached through
    a store the caller already holds". That is the sentence this function
    exists to correct. Nothing on this path ever consulted the store:
    ``set_outcome`` keys straight off ``quote_id``, and ``store.py``'s own
    comment on ``Quote.organizationId`` says why typing one is enough — the
    store is one process-wide dict and the ids are enumerable,
    ``q{run}-{counter}``. So a salesperson could name any quote in their own
    organization and move it, including to a terminal WON or LOST, which is
    terminal by design and therefore refuses the rightful owner from then on.
    The response handed back the row as well: ``customer_id``, the account's
    real name and a colleague's note — ``_visible_customer_ref``'s enumeration
    running backwards, exactly as on the ERP path.

    **It answers one question for every path on this router, reads included**,
    which is why it is named for holding the quote rather than for recording
    one. The first version guarded ``POST /outcome`` alone, and the review that
    followed walked straight around it: ``GET /quotes/{quote_id}`` handed a
    salesperson the whole outcome row and the entire snapshot trail for any id
    in the organization, and ``POST /assess`` echoed ``outcome_to_dict`` for
    whatever ``quote_id`` the body carried. Both answered exactly what the 404
    declines to — live or dead, and then the account id, the account's real
    name, the colleague's note and the quoted prices — so the refusal was an
    oracle undone by the endpoint next door. A read rule and a write rule that
    are the same rule must be the same function, or the next endpoint added
    here inherits only the one somebody remembered.

    Cross-*tenant* was never the hole here and is not fixed here: ``set_outcome``
    filters on the ``organization_id`` taken from the principal and never from
    the body, and ``store.line_cost`` refuses a foreign ``quote_id`` on the one
    seam that reads a cost. This is the *desk* axis inside one book.

    **What scopes a platform quote, and in what order.** There is no single
    column to read, so three facts are consulted and the first that attributes
    the quote decides. The two that are about *this outcome row* come before
    the one that is about the quote generally:

    1. ``QuoteOutcome.customer_id`` on the row being moved. It is the row this
       call would rewrite, it is what ``insight._scoped_outcomes`` narrows the
       *read* of this same table by, and a quote recorded through ``/snapshot``
       carries it from the moment it becomes a draft.
    2. ``QuoteOutcome.updated_by_user_id`` — the person who recorded it —
       where that row carries no customer. That is the ordinary walk-in (a name
       typed slightly differently, or a customer quoting for the first time),
       and it is also **every quote this platform pushed to the ERP**:
       ``routers.quote`` calls ``set_outcome`` with ``customer_ref`` and the
       pusher's user id and no ``customer_id``, so the row it opens has that
       column NULL however ordinary the customer was.
    3. ``QuoteDecision.customer_id`` on the append-only snapshot trail, where
       no outcome row exists at all. Written by ``assess_and_record``, which
       both ``/snapshot`` and the estimate push run — so the trail exists
       before anybody records an outcome, and it survives as evidence when the
       mutable row does not.

    Clauses 2 and 3 were the other way round for one round, and it refused a
    salesperson the quote they had priced and sent themselves. The builder
    always starts from the customer picker, so the trail written at line 399 of
    ``routers.quote`` carries a resolved ``customer_id`` while the outcome row
    written at line 479 carries none — clause 1 therefore always missed and the
    trail always decided, which made attribution rest on who holds the account
    *now* rather than on who sent the quote. ``ingestion.sync._sync_assignments``
    rewrites ``Customer.assigned_user_id`` from the salesperson on the account's
    latest invoice on every pull, so an account routinely moves between the send
    and the record, and the sender met a 404 on the quote their own screen was
    still offering to close.

    In this order the write scope is ``_scoped_outcomes`` exactly — ``customer_id``
    in mine, or ``customer_id`` NULL and the recorder is me — which is the
    property ``_may_record_erp_quote``'s docstring states the ERP half was built
    to preserve: what a person may record is exactly what they were shown. The
    cost is stated rather than hidden: where a colleague pushed a quote for an
    account somebody else holds, the account's holder is refused it — and that
    same row is absent from their worklist for the same reason, so nothing they
    can see is refused to them.

    Two candidates were considered and are not used. ``QuoteDraft.salesperson_id``
    is the obvious one, and it now records who *started* a draft — but the
    workspace is shared by design (``quote_workspace``): a colleague may open,
    price and send a draft somebody else started, and the person who sent it is
    the one this scope should follow, which is what clause 2 already reads.
    ``store.Quote.customerId`` is live and persisted, and is still not read
    here: it is ``Optional`` — a quote starts with no customer and may be sent
    against a typed name — so a rule on it would be empty for exactly the
    walk-in case clause 2 exists for.

    **When none of them attributes the quote, the caller says what that means**
    — and every caller but one says False. The benign default is to let the
    write through and have ``set_outcome`` open a fresh row, which is how a
    terminal status gets parked on an id nobody has minted yet: the counter is
    visible in any id the caller has legitimately seen, and a WON sitting on
    ``q{run}-550`` meets the colleague whose builder mints it as an
    ``InvalidTransition`` they can do nothing about.

    ``/snapshot`` is the one caller that passes True, because there creating the
    quote *is* the operation: a quote being priced for the first time has no
    outcome row and no trail by definition, and failing closed would refuse the
    Quote Builder its own first save. The residual that buys is real and is the
    reason it is a parameter rather than a default: on that endpoint 201 and 404
    do tell "free id" from "already another desk's", which the three paths
    that pass False deliberately do not. It costs a probe a written, attributed,
    auditable row of their own each time and discloses no name, and the
    alternative — letting a snapshot be appended to a stranger's trail — hands
    ``insight._latest_lines`` a price the customer never saw as the price they
    answered.

    ``POST /outcome`` passed the ERP half's answer here for one round: a
    reference the caller was found to hold let an *unattributed* ``quote_id``
    through beside it. That is removed. It made the 404 mean "this id names a
    live quote and it is not yours" while a fresh id answered 200, so any
    salesperson holding a single worklist reference could sweep the id space one
    request at a time — the enumeration ``_NO_SUCH_PLATFORM_QUOTE``'s own
    comment rules out — and each probe left a stray outcome row pointing at that
    reference. Nothing legitimate is lost: ``intelligence.ts`` sends the two keys
    from disjoint entry points and never both, the estimate push calls
    ``set_outcome`` directly with no guard in front of it, and the row that push
    opens is attributed by clause 2 from the moment it exists.

    A manager or owner is not narrowed to any subset of the book, so nothing is
    resolved for them at all and they keep today's behaviour exactly —
    including opening a row for a quote id nothing has recorded yet.
    """
    if not principal.is_salesperson:
        return True
    org = principal.organization_id
    row = get_outcome(session, org, quote_id)
    if row is not None:
        if row.customer_id:
            return _holds_account(session, principal, row.customer_id)
        if row.updated_by_user_id:
            return row.updated_by_user_id == principal.user_id
    for snapshot in snapshots_for_quote(session, org, quote_id):
        if snapshot.customer_id:
            return _holds_account(session, principal, snapshot.customer_id)
    return when_unattributed


def _holds_account(session: Session, principal: Principal,
                   customer_id: str) -> bool:
    """Whether this principal holds the account named, by id.

    ``can_view_customer`` takes ``Optional`` precisely so a failed ``session.get``
    goes straight in — a customer that does not exist and one this principal
    cannot see must give the same answer, or the difference between them is the
    oracle. The shared rule rather than a comparison against
    ``assigned_user_id``: that column is Zoho's, rewritten by the sync from the
    salesperson on the last invoice, and reading it here would hide a reassigned
    account from the person it was given to.
    """
    return can_view_customer(
        principal, session.get(models.Customer, customer_id), session)


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


def _inputs(session: Session, body: "AssessRequest | SnapshotRequest", org: str, *,
            holds_quote: bool) -> list[QuoteLineInput]:
    """The assessment's line inputs, including the server-held cost per line.

    ``holds_quote`` decides whether the named quote's costs are read at all, and
    it is a required keyword because getting it wrong is silent. ``line_cost``
    is scoped to the *organization* and says so — it refuses another tenant's
    ``quote_id`` — but the workspace is shared across the organization
    (``quote_workspace``), so inside one book a salesperson
    could name any desk's quote and borrow the cost sitting on its line.

    That is not the accepted residual §1 licenses. The accepted one is that a
    salesperson who is entitled to a line's negotiation floor can do algebra on
    it. This was different in kind: ``quote_intelligence`` takes
    ``item_master_cost`` as the *fallback* used when the books hold no cost for
    the product, so borrowing another desk's line **manufactured a boundary
    where none existed**. Measured before the fix, on a product with no
    ``cost_records`` row and a borrowed cost of 500: sweeping ``proposed_price``
    moved the verdict at 568.18 and again at 588.24 — ``cost/(1 - min_margin)``
    and ``cost/(1 - margin_floor)`` exactly — while the same sweep with no
    ``quote_id`` answered ``NO_COST_BASIS`` at every price. Two boundaries for a
    number the platform otherwise refuses to hold, on a line the caller cannot
    act on. §1's budget is one boundary per action the recipient can take, and
    they can take none here.

    Refused by *degrading*, not by raising: no id and an id the caller does not
    hold produce the identical ``NO_COST_BASIS`` answer, which is the same
    choice ``_visible_customer_ref`` makes one line down and for the same
    reason — a refusal that stood out would confirm the quote exists.
    """
    quote_id = (body.quote_id or "").strip()
    out = []
    for ln in body.lines:
        # Only when the caller named a quote they hold. Without one there is no
        # server-held line to read a cost from and the assessment falls back to
        # bills alone, which is exactly what a caller who does not hold it now
        # gets. The hand-entered cost is withheld on the same condition and for
        # the same reason: it is a second cost sitting on somebody's line, and
        # borrowing it manufactures the same boundary measured above.
        #
        # One read per line, not one per cost: `line_cost_basis` walks the whole
        # quote, and asking it twice would double that on a forty-line request.
        basis = (quote_workspace.line_cost_basis(session, org, quote_id, ln.line_id)
                 if holds_quote and quote_id else quote_workspace.LineCostBasis())
        out.append(QuoteLineInput(
            line_id=ln.line_id, product_ref=ln.product, qty=ln.qty,
            proposed_price=ln.proposed_price, family=ln.family,
            item_master_cost=basis.system, custom_cost=basis.custom))
    return out


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
    # Resolved once, and BEFORE the costs are read. It used to be checked after,
    # guarding only the outcome echoed at the bottom of this handler — so the
    # cost on another desk's line had already been read into the assessment by
    # the time anybody asked whether the caller held the quote. See ``_inputs``.
    holds_quote = bool(body.quote_id) and _holds_platform_quote(
        session, principal, body.quote_id, when_unattributed=False)
    result = assess_quote(
        session, org,
        customer_ref=_visible_customer_ref(session, principal,
                                           body.customer.strip()),
        lines=_inputs(session, body, org, holds_quote=holds_quote), as_of=body.as_of)
    refs = {ln.line_id: ln.product for ln in body.lines}

    # Withheld unless this reader holds the quote. ``_visible_customer_ref``
    # correctly blanks the customer name the caller typed, and then this line
    # handed back that account's real name, its platform id and a colleague's
    # note anyway — keyed on a ``quote_id`` the caller chose, which is the same
    # reference-to-identity enumeration running backwards. Resolved to None
    # rather than refused, so a quote on another desk and an id that names
    # nothing are one answer here too.
    outcome = get_outcome(session, org, body.quote_id) if holds_quote else None
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

    # The same key the outcome path scopes, checked here because this endpoint
    # writes the very row that path reads. It had no scope check at all, and
    # that made ``_holds_platform_quote`` decorative: a salesperson refused on
    # another desk's ``quote_id`` could snapshot it with their own customer
    # name, and ``set_outcome`` below would rewrite ``customer_id``,
    # ``customer_ref`` and ``updated_by_user_id`` on the colleague's row —
    # DRAFT over DRAFT, so the lifecycle never objected — and then walk in the
    # front door and file the loss into their own numbers. The trail is the
    # other half: ``insight._latest_lines`` reads the newest snapshot per quote
    # as the price the customer answered, so an appended line is a price nobody
    # quoted counted as one that was.
    #
    # ``when_unattributed=True`` because this is where a quote *becomes*
    # attributed — a first save has no outcome row and no trail, and failing
    # closed here would refuse the Quote Builder every new quote.
    if not _holds_platform_quote(session, principal, body.quote_id.strip(),
                                 when_unattributed=True):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_SUCH_PLATFORM_QUOTE)

    org = principal.organization_id
    customer_ref = _visible_customer_ref(session, principal, body.customer.strip())
    quote = quote_workspace.load(session, org, body.quote_id.strip())
    result, rows = assess_and_record(
        session, org, quote_id=body.quote_id.strip(), customer_ref=customer_ref,
        # The company whose catalogue resolved these lines, read off the quote
        # this assessment is about. None where the quote is not in the
        # workspace — an honest "not recorded" rather than a guess.
        connection_id=quote.connectionId if quote is not None else None,
        # The same builder the assessment path uses — one answer to "where does
        # a quote line's cost come from", which is the question that had two.
        lines=_inputs(session, body, org, holds_quote=True),
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

    Scoped by the same rule that scopes the write, and refusing in the same
    sentence. Org-scoped alone, this route answered for any id in the book —
    the outcome row with the account's id, its real name and whatever a
    colleague wrote in the note, plus every snapshot with its quantities and
    quoted prices — while a missing id answered ``{"outcome": null,
    "decisions": []}``. Those two are trivially distinguishable, so it supplied
    both halves of what ``POST /outcome``'s 404 exists to withhold: the live
    id list to aim at, and the identity behind each one.
    """
    if not _holds_platform_quote(session, principal, quote_id,
                                 when_unattributed=False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_SUCH_PLATFORM_QUOTE)
    org = principal.organization_id
    rows = snapshots_for_quote(session, org, quote_id)
    return {
        "quote_id": quote_id,
        "outcome": outcome_to_dict(get_outcome(session, org, quote_id)),
        "decisions": [snapshot_to_dict(r, principal.role) for r in rows],
    }


class OutcomeRequest(BaseModel):
    #: A quote this platform priced. Optional since ``quote_document_ref``
    #: exists: it was a required ``str``, which made the ERP-raised quote —
    #: most of the book — impossible to name over HTTP at all.
    quote_id: Optional[str] = None
    #: The id the source ERP gave a quote it raised itself, as carried in
    #: ``quote_documents.external_ref``.
    quote_document_ref: Optional[str] = None
    status: QuoteOutcomeStatus
    customer: str = ""
    note: Optional[str] = None
    #: Required when ``status`` is LOST. Not enforced here as a Pydantic
    #: constraint on purpose — ``quote_service.set_outcome`` owns the rule, so
    #: the CLI, a future importer and this endpoint cannot drift about what
    #: counts as a recordable loss. "At least one document to be about" is
    #: likewise that function's rule and not a Pydantic one, for the same
    #: reason and with the same consequence if it were restated here: both
    #: keys together is the *normal* shape for a platform quote pushed to the
    #: ERP, and a stricter model here would refuse what the service documents
    #: as ordinary.
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

    document_ref = (body.quote_document_ref or "").strip() or None
    if document_ref is not None and not _may_record_erp_quote(
            session, principal, document_ref):
        # 404 rather than 403, and the same 404 the reference naming nothing
        # gets: see ``_NO_SUCH_ERP_QUOTE``. Before the call and not inside it,
        # so a refused request writes nothing at all.
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_SUCH_ERP_QUOTE)

    # And the other key, on its own evidence, and on nothing the first one
    # answered. Both are checked because either one on its own identifies the
    # row ``set_outcome`` will move: a reference this person holds does not
    # make a stranger's ``quote_id`` theirs, and a quote id they hold does not
    # make a stranger's reference theirs — that second direction is what
    # ``_may_record_erp_quote``'s last paragraph is about. The first direction
    # is why the ERP answer is no longer passed down: an unattributed id
    # riding in on a held reference made this 404 tell a live quote from an
    # empty one, one request per id. ``_holds_platform_quote``'s last
    # paragraphs have the walk.
    quote_key = (body.quote_id or "").strip() or None
    if quote_key is not None and not _holds_platform_quote(
            session, principal, quote_key, when_unattributed=False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_SUCH_PLATFORM_QUOTE)
    try:
        row = set_outcome(
            session, org,
            quote_id=quote_key,
            quote_document_ref=document_ref,
            status=body.status,
            customer_ref=customer_ref,
            customer_id=customer.customer_id if customer else None,
            note=body.note, loss_reason=body.loss_reason,
            lost_to=body.lost_to, user_id=principal.user_id)
    except MissingLossReason as e:
        # 422 rather than 409: the request is well-formed and the transition is
        # legal, one required field is absent, and the message names the
        # choices. A 409 would send the caller looking at the lifecycle.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except AmbiguousQuoteDocument as e:
        # 409 rather than the 422 above, and the test that separates them is
        # "is there a body this caller could send that fixes it?" — here, no,
        # by design and permanently: ``OutcomeRequest`` carries no qualifier
        # naming which connected book the reference came from, and deliberately
        # does not. The 422 clause's own three-part test also fails at its
        # second part: ``sole_erp_quote`` refuses before the ``QuoteOutcome``
        # lookup and before ``_opening_status``, so there is no current status
        # the transition could have been legal against, and answering 422 would
        # assert a premise nothing established.
        #
        # ``detail`` is a plain string on purpose. It already names the count
        # and both books, which is the only thing that explains this to the
        # person who hit it, and the client reads ``parsed.detail`` straight
        # into a message — a list there renders as "[object Object]".
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except QuoteOutcomeRepointed as e:
        # A conflict with a row that already exists, not a malformed request.
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except InvalidTransition as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        # Last, and only for the "names neither document" guard: all four
        # clauses above are ``ValueError`` subclasses, so any of them ordered
        # after this one would be unreachable. That message already reads as a
        # missing-field message and names both fields that would satisfy it.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
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
