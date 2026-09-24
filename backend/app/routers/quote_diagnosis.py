"""Serving a quote diagnosis to whoever is asking.

Thin, per §3: it maps HTTP onto ``commercial.quote_diagnosis`` and decides which
of the two views a principal receives. It computes nothing.

**The role decision is a choice between two types, not a filter over one.** A
salesperson's response is built from ``rules.OperationsDiagnosis``, which
declares no cost, margin, opportunity or peer field; a manager's is built from
``OwnerDiagnosis``. Nothing in this module reaches into the owner object and
removes things, because that is the shape both of this repository's boundary
leaks had — a guard that was right, with one line below it that was not.

The endpoints:

``GET  /api/v1/quote-diagnosis/quote/{quote_id}``  what is on record for a quote
``POST /api/v1/quote-diagnosis/assess``            diagnose now, and record it
``POST /api/v1/quote-diagnosis/erp-quote/{ref}``   diagnose a quote the ERP
                                                   issued, as of the day it did
``POST /api/v1/quote-diagnosis/{id}/dismiss``      somebody says a card is wrong

The first two take what to diagnose from the caller; the third takes only a
reference and reads the rest from the document. That is not a convenience —
``assess`` bounds how far back a caller-supplied ``as_of`` may reach, because a
date a caller can move is a date a caller can walk an item's price history with,
and the ERP quote page needed documents far older than that bound. A reference
names one date, fixed by a table no endpoint writes.

**A quote is also answered as a whole**, on the responses that already carried
its lines rather than from a parallel endpoint: ``coverage`` — what was checked
and what could not be, to both roles — and ``rollup`` — the totals and the lines
those totals do not show, on the owner branch and absent from the other. The
split is the one ``commercial.quote_diagnosis.rollup`` already declares: the
first is built from a type with no money field on it at all, the second from one
that is RESTRICTED in its entirety, so nothing here filters anything.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal
from ..clock import aware
from ..commercial.policy import load_for_org
from ..commercial.quote_service import _resolve_products, resolve_customer
from ..commercial.insight import quote_book
from ..domain.origin import Companies
from ..commercial.quote_diagnosis import (considerations, render, replay,
                                          rollup, rules, service)
from ..db import get_session
# The one answer to "which accounts is this principal narrowed to". Imported
# rather than re-derived: that helper's own docstring is about two disagreeing
# answers to "whose book is this" in one file, and a second copy here would be
# a third — on the path that decides whether somebody sees a quote at all.
from .insight import _assigned_customer_ids
from ..domain import models

router = APIRouter(prefix="/api/v1/quote-diagnosis", tags=["quote-diagnosis"])

#: One request may not diagnose more lines than a real quote has. The same
#: reasoning as ``quote_intelligence``'s ceiling: the limit is not what stops a
#: determined caller, it is what stops a sweep being one cheap request.
_MAX_LINES = 200

#: How far back a diagnosis may be asked for. A quote back-dated a few weeks is
#: ordinary; asking for one as of an arbitrary past date walks an item's price
#: and cost history one day at a time. ``rediagnose`` is the supported way to
#: look at an old line, and it replays a *stored* moment rather than choosing one.
_AS_OF_WINDOW_DAYS = 90


class LineIn(BaseModel):
    line_id: str
    #: The platform's product id **or** the code the desk typed. Resolved
    #: through ``quote_service._resolve_products``, which is the same function
    #: ``quote-intelligence/assess`` uses and already accepts either.
    #:
    #: This field used to demand an id, and that is why nothing ever called
    #: this endpoint: the Quote Builder is its only caller and the builder
    #: speaks codes — the two screens that assess the same line wanted two
    #: different vocabularies, so the one that was written second was never
    #: wired up. One vocabulary, one resolver.
    product_id: str
    #: The platform's customer id or the name on the quote, resolved through
    #: the Quote Builder's own tolerant matcher for the same reason.
    customer_id: Optional[str] = None
    qty: Decimal = Decimal("1")
    quoted_unit_price: Optional[Decimal] = None


class AssessRequest(BaseModel):
    quote_id: str
    lines: list[LineIn] = Field(default_factory=list)
    as_of: Optional[date] = None
    #: Write the result. Default true — a diagnosis nobody stored cannot be
    #: replayed, measured against an outcome, or dismissed, and those three are
    #: most of why it exists.
    record: bool = True

    @field_validator("as_of")
    @classmethod
    def _within_window(cls, value: Optional[date]) -> Optional[date]:
        if value is not None and abs((value - date.today()).days) > _AS_OF_WINDOW_DAYS:
            raise ValueError(
                f"A diagnosis date must be within {_AS_OF_WINDOW_DAYS} days of "
                f"today. Diagnosing as of a date well outside that window reads "
                f"an item's price history rather than judging a quote.")
        return value

    @field_validator("lines")
    @classmethod
    def _bounded(cls, value: list[LineIn]) -> list[LineIn]:
        if len(value) > _MAX_LINES:
            raise ValueError(f"at most {_MAX_LINES} lines per request")
        return value


class DismissRequest(BaseModel):
    reason_code: str
    note: Optional[str] = None


@router.post("/assess")
def assess(body: AssessRequest,
           principal: Principal = Depends(current_principal),
           session: Session = Depends(get_session)) -> dict[str, Any]:
    """Diagnose each line and return the view this principal may have.

    ``knowable_by`` is derived here rather than taken from the caller. It is the
    instant the evidence is cut off at, and a caller who could choose it could
    ask what the engine would have said before an inconvenient bill landed.
    """
    as_of = body.as_of or date.today()
    return _diagnose(session, principal, quote_id=body.quote_id,
                     lines=body.lines, as_of=as_of, record=body.record)


@router.post("/erp-quote/{quote_ref:path}")
def assess_erp_quote(quote_ref: str,
                     connection: str = Query("", description=(
                         "The connected company whose book raised this quote. "
                         "An ERP reference is unique only inside one book; "
                         "omitted, the reference is read as before, which is "
                         "correct while it names one quote.")),
                     principal: Principal = Depends(current_principal),
                     session: Session = Depends(get_session)) -> dict[str, Any]:
    """Diagnose a quote the ERP already issued, as of the day it went out.

    **Why this exists rather than the caller posting to ``/assess``.** It can,
    and for a recent quote it did — but ``AssessRequest.as_of`` may not be more
    than ``_AS_OF_WINDOW_DAYS`` back, so every quote older than that came back
    refused. The window is not a nuisance to route around: a caller who can
    choose the date can move it a day at a time and read an item's price history
    out of the answers, which is the whole reason it is there.

    **This endpoint does not take a date, so there is nothing to walk.** The
    caller names a quote; the server reads that quote's own ``raised_on`` and
    diagnoses against it. One document, one date, fixed by the ERP — and fixed
    is the operative word: ``erp_quotes`` is written by the sync and by nothing
    else, so a caller cannot mint a quote to obtain a date they wanted. What a
    caller can enumerate is the quotes they are already allowed to read, at the
    dates those quotes were actually raised.

    So the window is **not relaxed**. It still applies, unchanged, to every
    caller-supplied ``as_of`` on ``/assess``. This is a different question with
    a different input, and the validator that guards the other one is not
    something this path needs an exemption from.

    **Scoped through the book itself**, exactly as ``insight.quote_book_lines``
    is and for its stated reason: a salesperson who may not see the quote may
    not see its diagnosis, and deriving that twice is how the two answers drift.
    404 rather than 403 — whether a quote exists in a book you cannot read is
    itself something you should not learn.

    **Records nothing.** Reading an issued document must not append a row per
    visit, and the page this serves says in as many words that nothing on it is
    written. A diagnosis worth storing is stored when a quote is *sent*.
    """
    org = principal.organization_id
    # Keyed on the reference *and* the book it was raised in, for the reason
    # ``insight.quote_book_lines`` states: an ERP reference is unique only
    # inside one company, and a reader who may see one company's quote may not
    # see another's under the same number. Two books answering to one bare
    # reference is answered by the same 404 as a reference naming nothing —
    # picking either would diagnose a document the reader is not holding.
    visible = [
        (q, (q.origin or {}).get("connection_id"))
        for q in quote_book.build(session, org, customer_names={},
                                  customer_ids=_assigned_customer_ids(
                                      session, principal),
                                  companies=Companies(session, org))
    ]
    candidates = [q for q, conn in visible
                  if q.quote_document_ref == quote_ref
                  and (not connection or conn == connection)]
    quote = candidates[0] if len(candidates) == 1 else None
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such quote")

    rows = quote_book.lines_for(session, org, quote_ref=quote_ref,
                                connection_id=connection or None)
    # The same filter the draft side applies, for the same reason: a line naming
    # no product cannot be compared against anything, and a line the ERP never
    # priced is not a claim about what this customer should pay.
    lines = [
        LineIn(line_id=str(row.line_number), product_id=row.item_code,
               customer_id=quote.customer_id or quote.customer_label or None,
               qty=row.qty if row.qty is not None else Decimal("1"),
               quoted_unit_price=_net_unit_price(row))
        for row in rows
        if row.item_code and _net_unit_price(row) is not None
    ]
    return _diagnose(session, principal, quote_id=quote_ref, lines=lines,
                     as_of=quote.raised_on, record=False,
                     connection_id=connection or None)


def _net_unit_price(row: Any) -> Optional[Decimal]:
    """What the customer was actually asked to pay per unit on this line.

    ``amount / qty``, not ``rate``. The distinction is the whole correctness of
    a diagnosis on a discounting book: ``rate`` is the list price before the
    line's discount and ``amount`` is what the line came to after it, and the
    history this gets compared against is net — ``_effective_unit_amount``
    resolves an invoice line's discount before storing ``unit_price``.

    On the book this was written for almost every line carries 50% or 55% off,
    so comparing list against net would have reported every line on every quote
    as far above what the customer has paid. A diagnosis engine that flags
    everything is one nobody reads, and it would have been flagging an
    arithmetic mistake rather than a price.

    Falls back to ``rate`` where the ERP gave no amount or no quantity: a line
    with a rate and nothing else is still a price somebody quoted. ``None``
    where there is no price at all, which is not a claim about anything.
    """
    if row.amount is not None and row.qty:
        return Decimal(str(row.amount)) / Decimal(str(row.qty))
    return Decimal(str(row.rate)) if row.rate is not None else None


def _diagnose(session: Session, principal: Principal, *, quote_id: str,
              lines: list[LineIn], as_of: date, record: bool,
              connection_id: Optional[str] = None) -> dict[str, Any]:
    """The engine loop, shared by the two endpoints that run it.

    Extracted when the ERP quote page needed the same pass over the same engine
    with the lines and the date read from the database instead of the body. A
    second copy would be two places deciding what ``knowable_by`` means, and
    that value is the one this whole engine's reproducibility rests on.
    """
    th = load_for_org(session, principal.organization_id)
    knowable_by = _knowable_by(as_of)
    cutover = service.backfill_cutover(session, principal.organization_id)

    # One pass over the catalogue for the whole request, not one per line:
    # `_resolve_products` says in as many words that the per-call form is the
    # N+1 it exists to avoid on a forty-line quote.
    products = _resolve_products(session, principal.organization_id,
                                 {ln.product_id for ln in lines})

    out: list[dict[str, Any]] = []
    # What the roll-up reads, gathered in the same pass that diagnoses. The
    # live producer: `from_diagnosis` reads the engine's own figures, including
    # the attribution a stored row has no column for.
    facts: list[rollup.LineFacts] = []
    for line in lines:
        product = products.get((line.product_id or "").strip())
        result = service.diagnose_line(
            session, principal.organization_id, quote_id=quote_id,
            line_id=line.line_id,
            customer_id=_customer_id(session, principal.organization_id,
                                     line.customer_id),
            # The resolved id where the ref named something, and the ref itself
            # where it did not. An unresolved product is an ordinary case — the
            # desk quotes things the master has never held — and the engine
            # already answers INSUFFICIENT_EVIDENCE for an id it cannot find,
            # which is the right answer rather than an error.
            product_id=(product.product_id if product is not None
                        else line.product_id),
            qty=line.qty,
            quoted_unit_price=line.quoted_unit_price, as_of=as_of,
            knowable_by=knowable_by, th=th,
            segment=service.segment_roster(session, principal.organization_id,
                                           line.customer_id),
            backfill_before=cutover,
            # The book the caller opened, where it named one. Without it the
            # engine's source read falls back to picking a winner among the
            # connected companies holding this reference — the guess the
            # qualifier on this endpoint exists to remove, and one that the
            # 404 guard above has already resolved correctly for the lines.
            connection_id=connection_id)
        stored = (service.record(session, principal.organization_id,
                                 quote_id=quote_id, result=result)
                  if record else None)
        facts.append(rollup.from_diagnosis(result.owner))
        out.append(_project(result.owner, result.opportunity, principal, th,
                            diagnosis_id=(stored.quote_diagnosis_id
                                          if stored is not None else None)))
    if record:
        session.commit()
    return {"quote_id": quote_id, "as_of": as_of.isoformat(),
            "cutover_known": cutover is not None,
            **_quote_level(facts, principal, th, quote_id=quote_id),
            "lines": out}


@router.get("/quote/{quote_id}")
def for_quote(quote_id: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)) -> dict[str, Any]:
    """What is on record for a quote — the diagnosis in force for each line.

    Reads the stored rows rather than re-running the engine. A card somebody was
    shown last Tuesday is a fact about last Tuesday, and recomputing it on every
    page load would quietly re-judge it against today's thresholds.

    The roll-up over those rows is real arithmetic over stored figures — the
    price, the quantity and the cost baseline are all columns — and names no
    dominant factor, because the attribution is not. ``_stored_facts`` says so
    by passing ``render.NOT_STORED``, and ``render_rollup`` writes the refusal
    out rather than leaving the line blank.
    """
    rows = service.for_quote(session, principal.organization_id,
                             quote_id=quote_id)
    th = load_for_org(session, principal.organization_id)
    return {"quote_id": quote_id,
            **_quote_level([_stored_facts(row) for row in rows], principal, th,
                           quote_id=quote_id),
            "lines": [_project_stored(row, principal, th) for row in rows]}


@router.post("/{quote_diagnosis_id}/dismiss")
def dismiss(quote_diagnosis_id: str, body: DismissRequest,
            principal: Principal = Depends(current_principal),
            session: Session = Depends(get_session)) -> dict[str, Any]:
    """Record that somebody read a card and judged it wrong.

    Open to a salesperson on purpose — they are the person the card interrupted,
    and a dismissal only they can make but only a manager can record is a
    dismissal nobody records. It writes a new row; the diagnosis is untouched.
    """
    row = session.get(models.QuoteDiagnosis, quote_diagnosis_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status_code=404, detail="no such diagnosis")
    try:
        service.dismiss(session, principal.organization_id,
                        quote_diagnosis_id=quote_diagnosis_id,
                        reason_code=body.reason_code, note=body.note,
                        user_id=principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return {"dismissed": True, "quote_diagnosis_id": quote_diagnosis_id}


@router.get("/reasons")
def reasons() -> dict[str, Any]:
    """The dismissal vocabulary the front end offers.

    Served rather than duplicated in TypeScript: a front end offering a reason
    the service refuses is a dead button somebody discovers in front of a
    customer.
    """
    return {"reasons": [{"code": code, "label": label}
                        for code, label in render.dismissal_reasons()]}


@router.get("/evaluation")
def evaluation(since: Optional[date] = None,
               principal: Principal = Depends(current_principal),
               session: Session = Depends(get_session)) -> dict[str, Any]:
    """How each rule has done against recorded outcomes. Management only.

    Not because the numbers are sensitive — they are counts — but because it is
    a tuning instrument, and a scoreboard of which warnings a salesperson
    dismissed, read by that salesperson, changes what gets dismissed.
    """
    _require_management(principal)
    return replay.evaluate(session, principal.organization_id,
                           since=since).to_dict()


def _customer_id(session: Session, org: str, ref: Optional[str]) -> Optional[str]:
    """A customer id from an id or a name.

    Delegates to the Quote Builder's own tolerant matcher through
    ``quote_service.resolve_customer`` — the pattern CLAUDE.md §2 holds up as
    already-done-right, and for its stated reason: a customer resolvable on the
    quote screen but not in the diagnosis would be a bug nobody can reproduce.

    ``None`` stays ``None``, and a ref that resolves to nothing stays itself:
    quoting somebody who has never bought before is ordinary, and the engine
    answers INSUFFICIENT_EVIDENCE for a customer with no history, which is the
    correct answer rather than a refusal.
    """
    if not ref:
        return None
    customer = resolve_customer(session, org, ref)
    return customer.customer_id if customer is not None else ref


def _knowable_by(as_of: date) -> datetime:
    """The cut-off instant for a diagnosis dated ``as_of``.

    End of that day in UTC. A quote written on the 1st may cite anything the
    source recorded on the 1st, and the alternative — the current wall clock —
    would make the same stored quote reproduce differently on every replay.
    """
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59,
                    tzinfo=timezone.utc)


def _require_management(principal: Principal) -> None:
    if not principal.is_manager_or_owner:
        raise HTTPException(status_code=403,
                            detail="this view is for managers and owners")


def _project(owner: rules.OwnerDiagnosis, opportunity, principal: Principal,
             th, *, diagnosis_id: Optional[str]) -> dict[str, Any]:
    """One line, as the principal may see it.

    **The salesperson branch never reaches into ``owner`` and removes
    anything.** It passes the owner diagnosis to two functions that *construct*
    the desk's types from it — ``rules.operations_view``, which declares no
    cost, margin, opportunity or peer field, and
    ``considerations.for_operations``, which keeps an allowlist — and renders
    from what comes back. There is no field on this branch to forget to take
    out, which is the property that matters; "does not touch the owner object"
    was the shorter way of saying it and stopped being literally true when the
    options arrived, because an option is derived from the finding under it and
    there is nowhere else to derive it from.
    """
    # Computed once and narrowed for whoever asked, which is the shape
    # `operations_view` already has: `for_operations` builds the desk's list
    # from an allowlist rather than removing anything from the owner's, so
    # there is no field here to forget to take out.
    proposed = considerations.propose(owner, th=th)
    if principal.is_salesperson:
        ops = rules.operations_view(owner)
        card = render.render_operations(ops, th=th)
        return {"quote_diagnosis_id": diagnosis_id, "view": "OPERATIONS",
                # Allowlist-filtered by `operations_view`, so this is the
                # desk's context and not the owner's narrowed afterwards.
                "context": list(ops.context),
                # The desk's own options, built from `OPERATIONS_CONSIDERATIONS`
                # — the two that rest on the purchase ledger and on the cash
                # cycle are not present rather than removed.
                "considerations": _considerations(render.render_considerations(
                    considerations.for_operations(proposed),
                    surfaces=owner.surfaces)),
                **_card(card), **_shared(card.renders, owner.strength)}
    report = render.render_owner(owner, opportunity, th=th)
    return {"quote_diagnosis_id": diagnosis_id, "view": "OWNER",
            # Same field, same meaning, on both views. It was on the operations
            # card alone at first, and a manager's screen then reported every
            # line of a real quote as incomparable — the reader cannot see which
            # projection they were served, so a field that answers a question
            # for one role and is absent for the other is a wrong answer for
            # that role rather than a missing one.
            "comparable": rules.had_enough_to_compare(owner),
            "line_id": owner.line_id, "headline": report.headline,
            "lines": list(report.lines), "opportunity": report.opportunity,
            "evidence": report.evidence, "codes": list(report.codes),
            "context": list(report.context), "strength": owner.strength,
            **_shared(owner.surfaces, owner.strength),
            # RESTRICTED, and on this branch only. The desk's projection is
            # built from a type with no field for it, so there is nothing here
            # to forget to remove.
            "attribution": _attribution(report.attribution),
            # RESTRICTED, and on this branch only, for the same reason. This is
            # the most direct of the three: the capital is the purchase cost.
            "working_capital": _working_capital(report.working_capital),
            # **Not** restricted, and the only block published under one key to
            # both roles. Every sentence in it is a fact about a field on the
            # source document; the one sentence that is a margin claim reached
            # this view from ``OwnerDiagnosis.intent.exposure``, which the
            # salesperson's projection has no object to read it from.
            "intent": _intent(report.intent),
            # Published to both roles under one key, and the two lists are
            # different objects rather than one filtered: two of these rest on
            # the purchase ledger and on the cash cycle and are outside
            # `OPERATIONS_CONSIDERATIONS`, so the desk's payload does not carry
            # them at all.
            "considerations": _considerations(
                render.render_considerations(proposed,
                                             surfaces=owner.surfaces)),
            "price_band": owner.price.to_dict(),
            "cost_baseline": owner.cost.to_dict(),
            "peer_band": owner.peer.to_dict(),
            "opportunity_detail": opportunity.to_dict()}


def _project_stored(row: models.QuoteDiagnosis, principal: Principal,
                    th) -> dict[str, Any]:
    """A stored diagnosis, as the principal may see it.

    Built from the row's own columns rather than by re-running the engine, so
    the salesperson branch has to do its own withholding — and it does it by
    naming the four fields it emits rather than by removing the ones it must
    not. A positive list cannot forget a field that is added later.
    """
    band = row.price_band.get("band", {}) if row.price_band else {}
    codes = tuple(row.codes or [])
    context = tuple(row.context or [])
    common = {"quote_diagnosis_id": row.quote_diagnosis_id,
              "line_id": row.quote_line_id,
              "as_of": row.as_of.isoformat(),
              "strength": row.strength}
    if principal.is_salesperson:
        ops = rules.OperationsDiagnosis(
            line_id=row.quote_line_id,
            quoted_unit_price=(Decimal(row.quoted_unit_price)
                               if row.quoted_unit_price is not None else None),
            historical_low=_money(band.get("low")),
            historical_high=_money(band.get("high")),
            comparable_count=int(band.get("sample_count") or 0),
            recent_comparable_count=int(band.get("recent_sample_count") or 0),
            strength=row.strength,
            codes=tuple(c for c in codes if c in rules.OPERATIONS_CODES),
            context=tuple(c for c in context if c in rules.OPERATIONS_CODES),
            surfaces=row.surfaces,
            # A stored row has no column for what the record said — see the
            # decision in ``rules.ENGINE_VERSION`` — so the refusal is stated
            # rather than a reading being re-run against today's declarations,
            # which is the one thing a stored diagnosis must not be re-judged by.
            intent=render.INTENT_NOT_STORED)
        return {**common, "view": "OPERATIONS",
                "context": list(ops.context),
                # And likewise: every option rests on something this row has no
                # column for, so the refusal is published rather than an empty
                # list, which a reader would take for "there is nothing you
                # could do about this line".
                "considerations": _considerations(render.render_considerations(
                    render.considerations_not_stored(row.quote_line_id,
                                                     surfaces=row.surfaces),
                    surfaces=row.surfaces)),
                **_card(render.render_operations(ops, th=th)),
                **_shared(row.surfaces, row.strength)}
    return {**common, "view": "OWNER", "codes": list(codes),
            **_shared(row.surfaces, row.strength),
            # A stored row has no attribution column — see the decision in
            # ``rules.ENGINE_VERSION``. The block is published anyway, carrying
            # the refusal that says so: a manager reading a diagnosis back who
            # simply found no attribution key would read the absence as "the
            # price and the cost both behaved", which is the one thing it does
            # not mean.
            "attribution": _attribution(render.render_attribution(
                render.NOT_STORED, surfaces=row.surfaces, th=th)),
            # Likewise: no column, so the block is published carrying the
            # refusal that says so. A manager who found no key would read the
            # absence as "this line ties up no cash", which is never true.
            "working_capital": _working_capital(render.render_working_capital(
                render.WC_NOT_STORED, surfaces=row.surfaces, th=th)),
            # And likewise: no column, so the block is published carrying the
            # refusal. A manager who found no key would read the absence as "no
            # pricing reason was recorded", which is a statement about the quote
            # rather than about the row it was read back from.
            "intent": _intent(render.render_intent(
                render.INTENT_NOT_STORED, surfaces=row.surfaces)),
            # The fourth instance of one pattern. Every option rests on the cost
            # side, on the cash cycle or on the record reading, and none of the
            # three is a column here — so the block carries the refusal rather
            # than an empty list a reader would take for "nothing to weigh".
            "considerations": _considerations(render.render_considerations(
                render.considerations_not_stored(row.quote_line_id,
                                                 surfaces=row.surfaces),
                surfaces=row.surfaces)),
            "context": list(context), "price_band": row.price_band,
            "cost_baseline": row.cost_baseline, "peer_band": row.peer_band,
            "opportunity_detail": row.opportunity,
            "evidence_summary": row.evidence_summary,
            "evidence_hash": row.evidence_hash,
            "evidence_ids": row.evidence_ids, "excluded": row.excluded,
            "thresholds_version": row.thresholds_version,
            "engine_version": row.engine_version,
            "created_at": (aware(row.created_at).isoformat()
                           if row.created_at else None)}


def _quote_level(facts: list[rollup.LineFacts], principal: Principal, th, *,
                 quote_id: str) -> dict[str, Any]:
    """What this quote says about itself as a whole, for whoever is asking.

    **One roll-up, two keys, and the split is structural.** ``coverage`` is
    ``QuoteCoverage`` — a type with no cost, margin or value field on it, not one
    field withheld — and goes to both roles under one key. ``rollup`` is
    ``QuoteRollup``, RESTRICTED in its entirety, and is simply **absent** from a
    salesperson's payload: there is no key here for a future author to forget to
    remove, which is the shape both of this repository's boundary leaks did not
    have.

    The coverage published here is the roll-up's own, so the two readers cannot
    come to be told different counts about one quote — which is why
    ``QuoteRollup`` holds a ``QuoteCoverage`` rather than restating one. It is
    published once, beside the roll-up rather than inside it, because the same
    dict serialised twice into one payload is two copies of one answer.

    **Both endpoints call this**, for the reason ``_diagnose`` exists at all: two
    places totalling one quote would be two answers to what a quote comes to, on
    the screen whose whole job is to be trusted about prices.
    """
    quote = rollup.roll_up(facts, quote_id=quote_id)
    out: dict[str, Any] = {
        "coverage": _coverage(render.render_coverage(quote.coverage))}
    if not principal.is_salesperson:
        out["rollup"] = _rollup(render.render_rollup(quote, th=th))
    return out


def _stored_facts(row: models.QuoteDiagnosis) -> rollup.LineFacts:
    """The second producer: one diagnosis as it was written, not as it is re-run.

    ``rollup.from_diagnosis`` is the live half and cannot be used here — there is
    no ``OwnerDiagnosis`` to read, only columns. That is exactly why the roll-up
    takes a record rather than the diagnosis itself: a shape only one of the two
    callers can build is a shape the other one copies badly.

    **The attribution is ``render.NOT_STORED``**, the same refusal the per-line
    block on this branch publishes, because it is the same fact about the same
    row: ``quote_diagnoses`` has no attribution column. A roll-up over stored
    rows therefore totals money perfectly well and names no dominant factor, and
    ``render_rollup`` writes that out rather than leaving the line blank.

    Every other field is a column read straight off. ``expected_cost`` is the
    level the quote should have been priced against — the one figure on
    ``cost_baseline`` the roll-up wants — and ``None`` stays ``None``: a nought
    cost would report a 100% margin on the platform's own ignorance.
    """
    baseline = row.cost_baseline or {}
    return rollup.LineFacts(
        line_id=row.quote_line_id, product_id=row.product_id or "",
        qty=Decimal(str(row.quantity or 0)),
        quoted_unit_price=_money(row.quoted_unit_price),
        unit_cost=_money(baseline.get("expected_cost")),
        codes=tuple(row.codes or []), strength=row.strength,
        surfaces=row.surfaces, attribution=render.NOT_STORED,
        thresholds_version=row.thresholds_version or None)


def _shared(surfaces: bool, strength: str) -> dict[str, Any]:
    """The fields a card needs whichever projection it was built from.

    Both views now draw a card, so both have to answer "is this worth
    interrupting somebody for", "how strong is the evidence" and "what can the
    reader do about it" — and they have to answer under the SAME NAMES. The
    owner projection used to publish the first of those as ``surfaces`` while
    the operations one called it ``renders``, and the front end read only
    ``renders``: a manager was served two flagged lines, matched none of them,
    and was shown a summary saying the quote was clean. Two names for one
    answer is a bug with a grace period.

    None of it is economics. ``renders`` is a threshold decision, ``strength``
    grades the band, and the actions are the same two either reader gets, so
    this helper stays callable from the salesperson path if it ever needs it.
    The cost, margin and peer figures remain where they were — reachable only
    from ``render_owner``, on a projection a salesperson is never built.
    """
    return {"renders": surfaces,
            "strength_word": render.strength_word(strength),
            "qualification": render.QUALIFICATION,
            "actions": [render.REVIEW_PRICE, render.DISMISS]}


def _card(card: render.OperationsCard) -> dict[str, Any]:
    """The desk's own half. The four fields both roles share come from
    ``_shared``, so neither branch can spell one of them its own way."""
    return {"line_id": card.line_id,
            "comparable": card.comparable,
            "headline": card.headline, "quoted": card.quoted,
            "historical": card.historical, "evidence": card.evidence,
            "evidence_detail": card.evidence_detail, "why": card.why,
            "note": card.note,
            # The desk's half of the recorded-reason reading, under the same key
            # and the same wording the owner gets. There is nothing to withhold:
            # ``OperationsDiagnosis.intent`` is an ``intent.Reading``, whose
            # every sentence is a fact about a field on the source document.
            "intent": _intent(card.intent)}


def _attribution(view: render.AttributionView) -> dict[str, Any]:
    """The split as JSON. RESTRICTED — reached only from the owner branches.

    Every figure is already a string: the front end may not format money or
    compute a number (CLAUDE.md §3), and a percentage point rounded in two
    places is two answers to one question.
    """
    return {"renders": view.renders,
            # The machine-readable half of ``note``. Published beside the
            # sentence rather than inside it, so a caller reads a code and a
            # reader reads English — neither recovered from the other.
            "reason": view.reason,
            "headline": view.headline,
            "drivers": [{"code": d.code, "severity": d.severity,
                         "strength_word": d.strength_word,
                         "effect": d.effect, "basis": d.basis}
                        for d in view.drivers],
            "note": view.note}


def _working_capital(view: render.WorkingCapitalView) -> dict[str, Any]:
    """What the line's cash costs, as JSON. RESTRICTED — reached only from the
    owner branches.

    Every figure is already a string, for the reason ``_attribution``'s are: the
    front end may not format money or compute a number (CLAUDE.md §3). The
    figures travel as label/value pairs rather than as named numeric keys
    precisely because there is nothing here for a browser to do but print them.
    """
    return {"assessed": view.assessed,
            "reason": view.reason,
            "interrupts": view.interrupts,
            "renders": view.renders,
            "headline": view.headline,
            "figures": [{"label": label, "value": value}
                        for label, value in view.figures],
            "severity": view.severity,
            "strength_word": view.strength_word,
            "note": view.note}


def _intent(view: render.IntentView) -> dict[str, Any]:
    """What the record says, as JSON. Published to both roles, under one key.

    Not restricted, and this is the one block on the owner branch that is not.
    The sentences are the engine's own and travel verbatim — the front end may
    not word a rule (CLAUDE.md §3), and a claim worded one way on the server and
    another in the browser is two claims, of which the one people read is the one
    nobody reviewed.

    The owner's ``lines`` may lead with the potential-leakage sentence. That
    difference was made by ``render_owner``, which had an ``OwnerDiagnosis`` to
    read it from; nothing here decides it, and the salesperson branch has no
    object it could have come from.
    """
    return {"read": view.read,
            # ``IntentView.reason`` is ``intent.Reading``'s and can be nothing
            # else, which is why this key is safe on the branch that serves a
            # salesperson: ``POSSIBLE_MARGIN_LEAKAGE`` is a code on
            # ``PricingIntent`` and never a reason on the reading.
            "reason": view.reason,
            "renders": view.renders,
            "headline": view.headline,
            "lines": list(view.lines),
            "codes": list(view.codes),
            "note": view.note}


def _considerations(view: render.ConsiderationsView) -> dict[str, Any]:
    """The options on a line, as JSON. Published to both roles, under one key.

    Not restricted as a block. The narrowing happened at the source —
    ``considerations.for_operations`` builds the desk's list from an allowlist —
    so this maps whichever list it was given and chooses nothing.

    **No dismissal vocabulary travels here, and that is the point.** A
    consideration hangs off the line whose diagnosis produced it, ``line_id``
    says which, and that line's stored diagnosis is what ``POST
    /{quote_diagnosis_id}/dismiss`` already points at with a reason from
    ``GET /reasons``. Rejecting the finding rejects the option resting on it,
    because no option here survives its finding being wrong — and a second
    dismissal path would split the one labelled dataset this engine has into two
    nobody can join.
    """
    return {"line_id": view.line_id,
            "renders": view.renders,
            "reason": view.reason,
            "items": [{"code": c.code, "label": c.label, "detail": c.detail,
                       "line_id": c.line_id, "rests_on": list(c.rests_on),
                       "strength_word": c.strength_word,
                       # ``None`` travels as ``null``. It is not
                       # ``NEGLIGIBLE``: "this finding is not a movement" and
                       # "this movement is small" are different answers.
                       "severity": c.severity,
                       "surfaces": c.surfaces}
                      for c in view.items],
            "note": view.note}


def _coverage(view: render.CoverageView) -> dict[str, Any]:
    """What was checked on this quote, as JSON. Published to both roles.

    Built from ``QuoteCoverage``, which declares no cost, margin or value field,
    so there is nothing here to withhold and no branch that could forget to. It
    is served on every response including the quiet one — an absent coverage key
    would read as "all clear", which is the failure CLAUDE.md §1 names.
    """
    return {"quote_id": view.quote_id,
            "renders": view.renders,
            "headline": view.headline,
            "figures": [{"label": label, "value": value}
                        for label, value in view.figures]}


def _rollup(view: render.RollupView) -> dict[str, Any]:
    """What the quote comes to, as JSON. RESTRICTED — the owner branch only.

    Every figure is already a string, for the reason ``_attribution``'s and
    ``_working_capital``'s are: the front end may not format money or compute a
    number (CLAUDE.md §3).

    ``loss_lines`` is emitted unconditionally, exactly as ``QuoteRollup.to_dict``
    emits it. An empty list means checked and none found; a missing key would be
    read as the same thing and means something else entirely.
    """
    return {"quote_id": view.quote_id,
            "renders": view.renders,
            "headline": view.headline,
            "total_hides_a_loss": view.total_hides_a_loss,
            "loss_lines": [{"line_id": ln.line_id,
                            "product_id": ln.product_id,
                            "sentence": ln.sentence}
                           for ln in view.loss_lines],
            "figures": [{"label": label, "value": value}
                        for label, value in view.figures],
            "dominant": view.dominant,
            "note": view.note}


def _money(value: Any) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None
