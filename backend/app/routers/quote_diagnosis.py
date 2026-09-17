"""Serving a quote diagnosis to whoever is asking.

Thin, per §3: it maps HTTP onto ``commercial.quote_diagnosis`` and decides which
of the two views a principal receives. It computes nothing.

**The role decision is a choice between two types, not a filter over one.** A
salesperson's response is built from ``rules.OperationsDiagnosis``, which
declares no cost, margin, opportunity or peer field; a manager's is built from
``OwnerDiagnosis``. Nothing in this module reaches into the owner object and
removes things, because that is the shape both of this repository's boundary
leaks had — a guard that was right, with one line below it that was not.

Three endpoints and no more:

``GET  /api/v1/quote-diagnosis/quote/{quote_id}``  what is on record for a quote
``POST /api/v1/quote-diagnosis/assess``            diagnose now, and record it
``POST /api/v1/quote-diagnosis/{id}/dismiss``      somebody says a card is wrong
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal
from ..clock import aware
from ..commercial.policy import load_for_org
from ..commercial.quote_diagnosis import render, replay, rules, service
from ..db import get_session
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
    product_id: str
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
    th = load_for_org(session, principal.organization_id)
    as_of = body.as_of or date.today()
    knowable_by = _knowable_by(as_of)
    cutover = service.backfill_cutover(session, principal.organization_id)

    out: list[dict[str, Any]] = []
    for line in body.lines:
        result = service.diagnose_line(
            session, principal.organization_id, quote_id=body.quote_id,
            line_id=line.line_id, customer_id=line.customer_id,
            product_id=line.product_id, qty=line.qty,
            quoted_unit_price=line.quoted_unit_price, as_of=as_of,
            knowable_by=knowable_by, th=th,
            segment=service.segment_roster(session, principal.organization_id,
                                           line.customer_id),
            backfill_before=cutover)
        stored = (service.record(session, principal.organization_id,
                                 quote_id=body.quote_id, result=result)
                  if body.record else None)
        out.append(_project(result.owner, result.opportunity, principal, th,
                            diagnosis_id=(stored.quote_diagnosis_id
                                          if stored is not None else None)))
    if body.record:
        session.commit()
    return {"quote_id": body.quote_id, "as_of": as_of.isoformat(),
            "cutover_known": cutover is not None, "lines": out}


@router.get("/quote/{quote_id}")
def for_quote(quote_id: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)) -> dict[str, Any]:
    """What is on record for a quote — the diagnosis in force for each line.

    Reads the stored rows rather than re-running the engine. A card somebody was
    shown last Tuesday is a fact about last Tuesday, and recomputing it on every
    page load would quietly re-judge it against today's thresholds.
    """
    rows = service.for_quote(session, principal.organization_id,
                             quote_id=quote_id)
    th = load_for_org(session, principal.organization_id)
    return {"quote_id": quote_id,
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

    The salesperson branch never touches ``owner``. It builds the operations
    type and renders from that, so there is no field to forget to remove.
    """
    if principal.is_salesperson:
        card = render.render_operations(rules.operations_view(owner), th=th)
        return {"quote_diagnosis_id": diagnosis_id, "view": "OPERATIONS",
                **_card(card)}
    report = render.render_owner(owner, opportunity, th=th)
    return {"quote_diagnosis_id": diagnosis_id, "view": "OWNER",
            "line_id": owner.line_id, "headline": report.headline,
            "lines": list(report.lines), "opportunity": report.opportunity,
            "evidence": report.evidence, "codes": list(report.codes),
            "context": list(report.context), "strength": owner.strength,
            "surfaces": owner.surfaces,
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
              "strength": row.strength, "surfaces": row.surfaces}
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
            surfaces=row.surfaces)
        return {**common, "view": "OPERATIONS",
                **_card(render.render_operations(ops, th=th))}
    return {**common, "view": "OWNER", "codes": list(codes),
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


def _card(card: render.OperationsCard) -> dict[str, Any]:
    return {"line_id": card.line_id, "renders": card.renders,
            "headline": card.headline, "quoted": card.quoted,
            "historical": card.historical, "evidence": card.evidence,
            "evidence_detail": card.evidence_detail, "why": card.why,
            "note": card.note, "qualification": card.qualification,
            "actions": list(card.actions)}


def _money(value: Any) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None
