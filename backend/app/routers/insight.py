"""The visualization surface — one shape, so every screen behaves the same.

Every endpoint here returns the same envelope: the data, the period it covers,
the currency it is denominated in, and — when the answer is empty — *why* it is
empty. That last field is the one that matters. A screen that can distinguish
"no data yet" from "nothing qualified" from "this needs a sync" can say
something useful in its empty state instead of showing a blank panel, and that
is most of the difference between a product and a dashboard.

Role scoping is inherited, not reinvented: margin and cost are RESTRICTED, so a
salesperson gets the same screens with those fields absent from the response
rather than hidden in the browser. Two endpoints are manager-or-owner only
because they are *entirely* margin — there is nothing left of them once the
restricted fields are removed, and an empty screen is a worse answer than an
honest 403.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal, require_manager_or_owner
from ..commercial import policy
from ..commercial.insight import (cadence, cohorts, composition, flow, landscape,
                                  periods, radar, simulate, story, weather)
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..signals.aggregates import load_snapshot
from ..signals.config import load_thresholds as load_signal_thresholds

log = logging.getLogger("pie_portal.insight")

router = APIRouter(prefix="/api/v1/insight", tags=["insight"])

#: A period shorter than this cannot support a comparison worth drawing.
MIN_MONTHS = 1
MAX_MONTHS = 12


def _envelope(data: dict, *, currency: str, empty_reason: Optional[str] = None,
              **extra: Any) -> dict:
    return {"currency": currency, "empty_reason": empty_reason, **extra, **data}


def _context(session: Session, principal: Principal):
    """Snapshot, thresholds and currency — the three things every view needs."""
    org = principal.organization_id
    snapshot = load_snapshot(session, org)
    th = policy.load_for_org(session, org)
    return org, snapshot, th


def _as_of(snapshot) -> Optional[date]:
    """The last day the business actually traded, not today.

    Anchoring on today makes every screen show an empty current month for the
    first days of a month, and makes a demo or a stale sync look like a collapse.
    The data's own last date is what the periods should hang from.
    """
    dates = [s.date for s in snapshot.sales]
    return max(dates) if dates else None


def _no_data(currency: str, what: str) -> dict:
    return _envelope(
        {}, currency=currency,
        empty_reason=(f"No sales history has been synced yet, so {what} cannot be "
                      f"computed. Connect a Zoho company and run a sync."))


# ── the homepage ────────────────────────────────────────────────────────────
@router.get("/storyboard")
def storyboard(months: int = Query(3, ge=MIN_MONTHS, le=MAX_MONTHS),
               principal: Principal = Depends(current_principal),
               session: Session = Depends(get_session)) -> dict:
    """What changed, why, and what to do — the briefing the homepage renders."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "a briefing")

    comparison = periods.comparison(as_of, months=months)
    movement = flow.compute(snapshot.sales, snapshot.customer_names, comparison)

    metrics = session.scalars(
        select(models.CustomerItemMetric)
        .where(models.CustomerItemMetric.organization_id == org)).all()
    by_customer: dict[str, list] = {}
    for row in metrics:
        by_customer.setdefault(row.customer_id, []).append(row)

    opportunities = radar.build(session, org, th,
                                customer_names=snapshot.customer_names,
                                product_names=snapshot.product_names)
    totals = radar.totals(opportunities)
    lost = cohorts.lost_revenue(snapshot.sales, snapshot.customer_names,
                                comparison, by_customer)
    dormant = cohorts.dormancy(snapshot.sales, snapshot.customer_names, as_of)

    flow_dict = movement.to_dict()
    concentration = story.concentration_of(
        [m.to_dict() for m in movement.moves], snapshot.customer_names)

    # Salespeople see the movement and the customers; the margin beat and the
    # money-recoverable beat are RESTRICTED and simply absent for them.
    restricted_ok = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    built = story.build(
        flow=flow_dict, lost=lost,
        radar=[o.to_dict() for o in opportunities] if restricted_ok else [],
        radar_totals=totals if restricted_ok else {},
        dormant=dormant, concentration=concentration, currency=th.currency)
    built["as_of"] = as_of.isoformat()
    built["restricted_withheld"] = not restricted_ok
    return _envelope(built, currency=th.currency,
                     empty_reason=built.pop("empty_reason", None))


# ── weather ─────────────────────────────────────────────────────────────────
@router.get("/weather")
def commercial_weather(months: int = Query(3, ge=MIN_MONTHS, le=MAX_MONTHS),
                       principal: Principal = Depends(require_manager_or_owner),
                       session: Session = Depends(get_session)) -> dict:
    """Executive health as independent fronts. Manager+: it is entirely margin."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "commercial health")

    comparison = periods.comparison(as_of, months=months)
    movement = flow.compute(snapshot.sales, snapshot.customer_names, comparison).to_dict()
    opportunities = radar.build(session, org, th,
                                customer_names=snapshot.customer_names,
                                product_names=snapshot.product_names)

    rows = session.scalars(
        select(models.CustomerItemMetric)
        .where(models.CustomerItemMetric.organization_id == org)).all()
    revenue = sum(float(r.revenue_12m or 0) for r in rows)
    profit = sum(float(r.gross_profit_12m or 0) for r in rows)
    # Aggregated as Σ profit ÷ Σ revenue — never the mean of per-line margins.
    margin_now = (profit / revenue) if revenue else None
    covered = sum(1 for r in rows if r.data_sufficiency in ("SUFFICIENT", "PARTIAL"))
    coverage = (covered / len(rows)) if rows else None

    dormant = cohorts.dormancy(snapshot.sales, snapshot.customer_names, as_of)
    # Customers who traded in the current period — not every customer ever, which
    # would make the quiet share shrink as history accumulated.
    active = len({s.customer_id for s in snapshot.sales
                  if comparison.current.contains(s.date)})

    return _envelope(
        weather.build(flow_summary=movement, radar_totals=radar.totals(opportunities),
                      margin_now=margin_now, margin_prev=None,
                      margin_floor=th.margin_floor, dormant_count=dormant["count"],
                      active_customers=active, coverage=coverage),
        currency=th.currency, as_of=as_of.isoformat(), period=movement["comparison"])


# ── revenue flow ────────────────────────────────────────────────────────────
@router.get("/revenue-flow")
def revenue_flow(months: int = Query(3, ge=MIN_MONTHS, le=MAX_MONTHS),
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)) -> dict:
    """Movement between two periods, decomposed so the bars reconcile."""
    _org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "the revenue waterfall")

    comparison = periods.comparison(as_of, months=months)
    movement = flow.compute(snapshot.sales, snapshot.customer_names, comparison)
    result = movement.to_dict()
    if not movement.reconciles():
        # Loud rather than quiet: a waterfall whose bars do not sum to the
        # movement is wrong, and rendering it anyway teaches people to distrust
        # every chart on the page.
        log.error("revenue flow does not reconcile for %s", principal.organization_id)
    return _envelope(result, currency=th.currency, as_of=as_of.isoformat(),
                     empty_reason=(None if movement.moves else
                                   "No customer traded in either period."))


# ── customer journey ────────────────────────────────────────────────────────
@router.get("/journey")
def customer_journey(months: int = Query(12, ge=3, le=24),
                     principal: Principal = Depends(current_principal),
                     session: Session = Depends(get_session)) -> dict:
    """Customer states month by month, plus who has gone quiet."""
    _org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "the customer journey")

    points = cohorts.journey(snapshot.sales, as_of, months=months)
    return _envelope(
        {"series": [p.to_dict() for p in points],
         "dormant": cohorts.dormancy(snapshot.sales, snapshot.customer_names, as_of)},
        currency=th.currency, as_of=as_of.isoformat(),
        empty_reason=(None if points else
                      "Less than two months of history — there is nothing to "
                      "compare a month against yet."))


@router.get("/migration")
def migration_matrix(months: int = Query(3, ge=MIN_MONTHS, le=MAX_MONTHS),
                     principal: Principal = Depends(current_principal),
                     session: Session = Depends(get_session)) -> dict:
    """Which revenue band each customer moved between."""
    _org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "band migration")

    comparison = periods.comparison(as_of, months=months)
    result = cohorts.migration(snapshot.sales, snapshot.customer_names, comparison)
    return _envelope(result, currency=th.currency, as_of=as_of.isoformat(),
                     empty_reason=(None if result["cells"] else
                                   "No customer traded in either period."))


@router.get("/customers/{customer_id}/timeline")
def customer_timeline(customer_id: str,
                      months: int = Query(18, ge=6, le=36),
                      principal: Principal = Depends(current_principal),
                      session: Session = Depends(get_session)) -> dict:
    """One customer's revenue, cadence and margin over time."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    rows = [s for s in snapshot.sales if s.customer_id == customer_id]
    if as_of is None or not rows:
        return _no_data(th.currency, "this customer's history")

    restricted_ok = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    costs: dict[str, Any] = {}
    if restricted_ok:
        for row in session.scalars(
                select(models.CustomerItemMetric).where(
                    models.CustomerItemMetric.organization_id == org,
                    models.CustomerItemMetric.customer_id == customer_id)).all():
            if row.current_effective_cost is not None:
                costs[row.product_id] = float(row.current_effective_cost)

    result = cohorts.health_timeline(rows, costs, as_of, months=months)
    if not restricted_ok:
        # Absent, not masked: there is no margin field in a salesperson's copy.
        for point in result["series"]:
            point.pop("margin", None)
            point.pop("cost_coverage", None)
        result["unavailable"].append(
            {"series": "margin", "reason": "Margin is management information."})
    return _envelope(result, currency=th.currency,
                     customer_id=customer_id,
                     customer_label=snapshot.customer_names.get(customer_id, customer_id),
                     as_of=as_of.isoformat())


# ── opportunity radar and lost revenue ──────────────────────────────────────
@router.get("/opportunities")
def opportunities(limit: int = Query(100, ge=1, le=300),
                  principal: Principal = Depends(require_manager_or_owner),
                  session: Session = Depends(get_session)) -> dict:
    """Ranked by evidence, then by money. Manager+: every field is margin."""
    org, snapshot, th = _context(session, principal)
    rows = radar.build(session, org, th, customer_names=snapshot.customer_names,
                       product_names=snapshot.product_names, limit=limit)
    excluded = radar.below_floor(session, org, th)

    # An empty radar should answer the question, not shrug. "Nothing qualified"
    # is useless; "seven relationships have gaps, the largest is 3,352, your
    # floor is 10,000" tells the owner both that the platform looked and what
    # the dial would have to move to for anything to appear.
    if rows:
        reason = None
    elif excluded["excluded_count"]:
        reason = (
            f"{excluded['excluded_count']} of {excluded['relationships_examined']} "
            f"relationships have a real gap, but every one is below your "
            f"{excluded['floor']:,.0f} materiality floor — the largest is "
            f"{excluded['largest_excluded']:,.0f}. Lower the floor in Settings to "
            f"see them, or leave it: below this, a gap is real and not worth an "
            f"afternoon.")
    else:
        reason = ("No relationship shows a named gap. Either margins are holding, "
                  "or there is not enough cost coverage yet to tell — check "
                  "evidence quality on the weather view.")

    return _envelope(
        {"opportunities": [o.to_dict() for o in rows],
         "totals": radar.totals(rows), "excluded": excluded},
        currency=th.currency, thresholds_version=th.version, empty_reason=reason)


@router.get("/lost-revenue")
def lost_revenue(months: int = Query(3, ge=MIN_MONTHS, le=MAX_MONTHS),
                 principal: Principal = Depends(require_manager_or_owner),
                 session: Session = Depends(get_session)) -> dict:
    """Revenue that stopped, grouped by what the rows say caused it."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "lost revenue")

    by_customer: dict[str, list] = {}
    for row in session.scalars(
            select(models.CustomerItemMetric)
            .where(models.CustomerItemMetric.organization_id == org)).all():
        by_customer.setdefault(row.customer_id, []).append(row)

    comparison = periods.comparison(as_of, months=months)
    result = cohorts.lost_revenue(snapshot.sales, snapshot.customer_names,
                                  comparison, by_customer)
    return _envelope(result, currency=th.currency, as_of=as_of.isoformat(),
                     empty_reason=(None if result["causes"] else
                                   "No customer spent less this period than last."))


# ── landscape: two measures per subject, positioned ─────────────────────────
@router.get("/landscape")
def commercial_landscape(
        subject: str = Query("relationship", pattern="^(relationship|product)$"),
        measure: str = Query("margin", pattern="^(margin|momentum)$"),
        principal: Principal = Depends(require_manager_or_owner),
        session: Session = Depends(get_session)) -> dict:
    """Margin-vs-revenue and product momentum — one chart, two parameters.

    Manager+ when the vertical axis is margin. Momentum is volume and carries no
    cost, but the endpoint stays manager-scoped rather than switching its own
    permission on a query parameter: a route whose authorisation depends on an
    argument is one that will eventually be called with the other argument.
    """
    org, snapshot, th = _context(session, principal)
    result = landscape.build(session, org, th, subject=subject, measure=measure,
                             customer_names=snapshot.customer_names,
                             product_names=snapshot.product_names)
    return _envelope(
        result, currency=th.currency,
        empty_reason=(None if result["points"] else
                      "No relationship has trailing revenue yet. Run a sync, "
                      "then recompute metrics."))


# ── composition: the mix, over time ─────────────────────────────────────────
@router.get("/composition")
def revenue_composition(
        dimension: str = Query("customer", pattern="^(customer|product)$"),
        measure: str = Query("revenue", pattern="^(revenue|orders)$"),
        months: int = Query(12, ge=3, le=24),
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session)) -> dict:
    """Revenue composition and order flow — one chart, two parameters.

    Neither measure is margin, so this is visible to every role.
    """
    _org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "the revenue mix")

    names = (snapshot.customer_names if dimension == composition.BY_CUSTOMER
             else snapshot.product_names)
    result = composition.build(snapshot.sales, names, as_of,
                               dimension=dimension, measure=measure, months=months)
    return _envelope(result, currency=th.currency, as_of=as_of.isoformat(),
                     empty_reason=(None if result["series"] else
                                   "Nothing traded in this window."))


# ── cadence: the buying rhythm ──────────────────────────────────────────────
@router.get("/cadence")
def buying_cadence(principal: Principal = Depends(current_principal),
                   session: Session = Depends(get_session)) -> dict:
    """When customers order, and who is off their own rhythm."""
    _org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "buying rhythm")

    # Same thresholds the dormancy detector runs on, so this screen and the
    # decision queue never disagree about who is overdue.
    result = cadence.build(snapshot.sales, snapshot.customer_names, as_of,
                           thresholds=load_signal_thresholds())
    return _envelope(result, currency=th.currency,
                     empty_reason=(None if result["customers"] else
                                   "No customer has ordered yet."))


# ── the simulator ───────────────────────────────────────────────────────────
class SimulationRequest(BaseModel):
    scenario: str = Field(..., description="PRICE_CHANGE | MARGIN_FLOOR | CUSTOMER_RECOVERY")
    price_change_pct: Optional[float] = Field(None, ge=-0.9, le=2.0)
    assumed_volume_change: float = Field(0.0, ge=-1.0, le=2.0)
    floor: Optional[float] = Field(None, ge=0.0, lt=1.0)
    customer_ids: list[str] = Field(default_factory=list)
    recovery_share: float = Field(1.0, ge=0.0, le=1.0)
    # Scope: leaving both unset simulates the whole book.
    customer_id: Optional[str] = None
    product_id: Optional[str] = None


@router.get("/simulate/scenarios")
def scenarios(principal: Principal = Depends(require_manager_or_owner),
              session: Session = Depends(get_session)) -> dict:
    """What can be simulated, and what cannot — with the reason."""
    _org, _snapshot, th = _context(session, principal)
    return _envelope(
        {"available": [
            {"scenario": simulate.PRICE_CHANGE, "label": "Price change",
             "inputs": ["price_change_pct", "assumed_volume_change"]},
            {"scenario": simulate.MARGIN_FLOOR, "label": "Lift to a margin floor",
             "inputs": ["floor"]},
            {"scenario": simulate.CUSTOMER_RECOVERY, "label": "Win back customers",
             "inputs": ["customer_ids", "recovery_share"]},
         ],
         "unavailable": [dict(u) for u in simulate.UNAVAILABLE]},
        currency=th.currency)


@router.post("/simulate")
def run_simulation(body: SimulationRequest,
                   principal: Principal = Depends(require_manager_or_owner),
                   session: Session = Depends(get_session)) -> dict:
    """Deterministic scenario arithmetic. Same inputs, same answer, every time."""
    org, snapshot, th = _context(session, principal)
    lines = simulate.load_lines(session, org, customer_id=body.customer_id,
                                product_id=body.product_id,
                                customer_names=snapshot.customer_names,
                                product_names=snapshot.product_names)
    if not lines:
        return _no_data(th.currency, "this scenario")

    if body.scenario == simulate.PRICE_CHANGE:
        if body.price_change_pct is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "price_change_pct is required for PRICE_CHANGE.")
        result = simulate.price_change(
            lines, pct=body.price_change_pct,
            assumed_volume_change=body.assumed_volume_change)
    elif body.scenario == simulate.MARGIN_FLOOR:
        result = simulate.margin_floor(
            lines, floor=body.floor if body.floor is not None else th.margin_floor)
    elif body.scenario == simulate.CUSTOMER_RECOVERY:
        if not body.customer_ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "customer_ids is required for CUSTOMER_RECOVERY.")
        result = simulate.customer_recovery(
            lines, customer_ids=body.customer_ids,
            recovery_share=body.recovery_share)
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{body.scenario!r} is not a scenario this platform can compute. "
            f"Supported: PRICE_CHANGE, MARGIN_FLOOR, CUSTOMER_RECOVERY.")

    return _envelope(result, currency=th.currency,
                     thresholds_version=th.version)
