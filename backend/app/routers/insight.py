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
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal, require_manager_or_owner
from .. import clock
from ..commercial import floor, incentive, policy
from ..commercial import categories as cat
from ..commercial.insight import (bonds, cadence, cashflow, cohorts, composition,
                                  dependency, flow, landscape, mix, payments,
                                  periods, radar, simulate, stock, story, supply,
                                  weather)
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..domain.origin import Companies, index_of
from ..signals.aggregates import label_for, load_snapshot
from ..commercial.insight import series
from ..state.engine import latest_as_of, load as load_state
from ..state.reducers.trade import CUSTOMER_MONTH
from ..state import engine as state_engine
from ..state.reducers.cash import CASH_SCHEDULE
from ..state.reducers.commitments import COMMITMENTS
from ..state.reducers.inventory import INVENTORY
from ..state.reducers.receivables import RECEIVABLES
from ..signals.config import load_thresholds as load_signal_thresholds

log = logging.getLogger("pie_portal.insight")

router = APIRouter(prefix="/api/v1/insight", tags=["insight"])

#: A period shorter than this cannot support a comparison worth drawing.
MIN_MONTHS = 1
MAX_MONTHS = 12


def _envelope(data: dict, *, currency: str, empty_reason: Optional[str] = None,
              **extra: Any) -> dict:
    return {"currency": currency, "empty_reason": empty_reason, **extra, **data}


def _context(session: Session, principal: Principal, **bound: Any):
    """Snapshot, thresholds and currency — the three things every view needs.

    ``bound`` is passed straight to ``load_snapshot``. A screen that reads one
    customer's lines has no business loading four hundred customers' worth, and
    an unbounded call here is what made a page load four full scans of the
    organization's history. A caller that bounds says why at its own call site;
    the equality tests check the claim.
    """
    org = principal.organization_id
    snapshot = load_snapshot(session, org, **bound)
    th = policy.load_for_org(session, org)
    return org, snapshot, th


def _labels_only(session: Session, principal: Principal):
    """Names and the reference date, with none of the lines.

    Several screens read a snapshot solely to turn an id into a name — the
    supply screen never touched a line at all. Each of them was loading the
    organization's entire trading history to do it. The two name dictionaries
    and the reference date are three small indexed reads; the lines are the
    expensive part and these screens do not have one.

    ``test_bounded_loads`` asserts structurally that no caller of this touches
    ``snapshot.sales`` or ``snapshot.costs``, so the claim cannot rot as a
    screen grows.
    """
    return _context(session, principal, sales_for_customers=[],
                    costs_for_products=[])


def _flow_from_state(session: Session, principal: Principal):
    """Monthly totals, or ``None`` when this database has no fold yet.

    Names only from the snapshot — never a line. Kept as its own function
    rather than a branch inside ``_flow_rows`` so ``test_bounded_loads`` can
    still see the claim: a function that calls ``_labels_only`` must not read
    ``snapshot.sales``, and a structural check cannot tell that two branches
    are exclusive.
    """
    org = principal.organization_id
    on = latest_as_of(session, org, CUSTOMER_MONTH)
    if on is None:
        return None
    rows = series.month_rows(load_state(session, org, CUSTOMER_MONTH, on))
    if not rows:
        return None
    _org, snapshot, _th = _labels_only(session, principal)
    return rows, snapshot.customer_names, series.last_traded_on(rows)


def _flow_from_lines(session: Session, principal: Principal):
    """The original path: every sale line the organization has recorded."""
    _org, snapshot, _th = _context(session, principal)
    return snapshot.sales, snapshot.customer_names, snapshot.as_of()


def _flow_rows(session: Session, principal: Principal):
    """Rows for the period-comparison screens, and which path produced them.

    These screens decompose one period against the one before it, and every
    period here is a whole calendar month — so a month's total answers the same
    question the month's lines do, and reading the fold removes an unbounded
    scan of the organization's entire sales history. ``test_series_equality``
    runs both paths against one fixture and compares the whole decomposition,
    not just the totals.

    Falls back to the lines when there is no fold rather than showing an empty
    screen. A database that has not been re-synced since the monthly states
    were added has no ``CUSTOMER_MONTH`` rows, and "slower" is a much better
    failure than "your revenue is zero".
    """
    folded = _flow_from_state(session, principal)
    if folded is not None:
        return (*folded, True)
    return (*_flow_from_lines(session, principal), False)


def _as_of(snapshot) -> Optional[date]:
    """The last day the business actually traded, not today.

    Anchoring on today makes every screen show an empty current month for the
    first days of a month, and makes a demo or a stale sync look like a collapse.
    The data's own last date is what the periods should hang from.

    Delegates to the snapshot, which takes it from the loader's own whole-book
    query. Scanning the loaded rows for a maximum would give a *bounded* screen
    a reference date of whenever that one customer last bought — so a quiet
    account would make its own timeline look like the business had stopped.
    """
    return snapshot.as_of()


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
    """Movement between two periods, decomposed so the bars reconcile.

    Reads the monthly fold rather than the sale lines. This screen wants
    nothing from a line — not a product, not a quantity, not an invoice
    reference — only revenue per customer per period, and every period here is
    a whole calendar month. It was loading the organization's entire trading
    history to add up twelve numbers.
    """
    th = policy.load_for_org(session, principal.organization_id)
    rows, names, as_of, folded = _flow_rows(session, principal)
    if as_of is None:
        return _no_data(th.currency, "the revenue waterfall")

    comparison = periods.comparison(as_of, months=months)
    movement = flow.compute(rows, names, comparison)
    result = movement.to_dict()
    # Which path answered. Not decoration: if this ever reads "lines" on a
    # synced production database, the fold is missing and the page is quietly
    # doing the expensive thing it was moved off.
    result["source"] = "state" if folded else "lines"
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

    points = cohorts.journey(snapshot.sales, as_of, months=months,
                             names=snapshot.customer_names)
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


def _require_visible_customer(session: Session, org: str, customer_id: str,
                              principal: Principal) -> models.Customer:
    """The account-list scope rule, applied to a per-customer endpoint.

    ``/api/v1/accounts`` narrows a salesperson to their own assigned accounts,
    and the decision endpoints do the same. A per-customer route that skips the
    check is a way around all of it: the id is the only thing standing between
    a salesperson and every relationship in the book, and ids travel.

    404 rather than 403, matching the decisions endpoints — a 403 confirms the
    customer exists, which is most of what an enumeration is after.
    """
    customer = session.get(models.Customer, customer_id)
    if customer is None or customer.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    if principal.is_salesperson and customer.assigned_user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer


@router.get("/customers/{customer_id}/timeline")
def customer_timeline(customer_id: str,
                      months: int = Query(18, ge=6, le=36),
                      principal: Principal = Depends(current_principal),
                      session: Session = Depends(get_session)) -> dict:
    """One customer's revenue, cadence and margin over time."""
    # One customer's screen, one customer's lines. Everything below reads only
    # ``rows`` and the name dictionaries, and ``as_of`` comes from the loader's
    # own whole-book query — so the rest of the organization cannot change this
    # answer, and used to be loaded anyway.
    # One customer's lines, and no costs at all: the margin series on this
    # screen is built from ``CustomerItemMetric`` below, not from cost records.
    org, snapshot, th = _context(session, principal,
                                 sales_for_customers=[customer_id],
                                 costs_for_products=[])
    _require_visible_customer(session, org, customer_id, principal)
    as_of = _as_of(snapshot)
    rows = snapshot.sales_for_customer(customer_id)
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

    # Days-to-pay, where this customer has any settled invoices. Passing None
    # rather than an empty list matters: none synced is a gap the screen should
    # name, none settled is a row of blanks it can honestly draw.
    settled = _settlements(session, org, customer_id)
    payment_series = (payments.monthly_series(settled, periods.months_back(as_of, months))
                      if settled else None)
    result = cohorts.health_timeline(rows, costs, as_of, months=months,
                                     payment_series=payment_series)
    if not restricted_ok:
        # Absent, not masked: there is no margin field in a salesperson's copy.
        for point in result["series"]:
            point.pop("margin", None)
            point.pop("cost_coverage", None)
        result["unavailable"].append(
            {"series": "margin", "reason": "Margin is management information."})
    return _envelope(result, currency=th.currency,
                     customer_id=customer_id,
                     customer_label=label_for(snapshot.customer_names, customer_id,
                                              kind="customer"),
                     as_of=as_of.isoformat())


# ── opportunity radar and lost revenue ──────────────────────────────────────
@router.get("/opportunities")
def opportunities(limit: int = Query(100, ge=1, le=300),
                  principal: Principal = Depends(require_manager_or_owner),
                  session: Session = Depends(get_session)) -> dict:
    """Ranked by evidence, then by money. Manager+: every field is margin."""
    org, snapshot, th = _labels_only(session, principal)
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
    org, snapshot, th = _labels_only(session, principal)
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


# ── The book itself: the shelf, the suppliers and the cash ───────────────────────────
#
# Role scope follows the rule the rest of this router already uses, not a new
# one: no cost and no margin means every role, cost of any kind means manager
# and above. Payments are receivables — neither — so a salesperson sees them.
# Stock structure is visible to everyone and the purchase rate is dropped from
# their copy entirely, exactly as the health timeline drops margin.


def _settlements(session: Session, org: str,
                 customer_id: Optional[str] = None) -> list[payments.Settlement]:
    """Payment applications as the grain the payment view computes on."""
    stmt = select(models.PaymentApplication).where(
        models.PaymentApplication.organization_id == org)
    if customer_id:
        stmt = stmt.where(models.PaymentApplication.customer_id == customer_id)
    return [
        payments.Settlement(
            customer_id=row.customer_id,
            invoice_ref=row.invoice_external_ref,
            invoice_number=row.invoice_number,
            invoice_date=row.invoice_date,
            due_date=row.invoice_due_date,
            paid_on=row.paid_on,
            amount=float(row.amount_applied or 0),
        )
        for row in session.scalars(stmt).all()
    ]


@router.get("/payments")
def payment_behaviour(principal: Principal = Depends(current_principal),
                      session: Session = Depends(get_session)) -> dict:
    """How long customers take to pay. Receivables — no cost, so every role."""
    org, snapshot, th = _labels_only(session, principal)
    as_of = _as_of(snapshot) or clock.today(th.timezone)

    receipts = session.scalars(
        select(models.PaymentReceipt).where(
            models.PaymentReceipt.organization_id == org)).all()
    settled = _settlements(session, org)
    if not receipts and not settled:
        return _no_data(th.currency, "payment behaviour")

    result = payments.build(
        settled, snapshot.customer_names, as_of,
        advances=sum(1 for r in receipts if r.is_advance),
        unapplied_total=float(sum(r.unapplied_amount or 0 for r in receipts)))
    # Who pays slowly is a list of names to act on, and two accounts sharing a
    # name across two books are two different conversations with two different
    # people. Same projection as every other list.
    companies = Companies(session, org)
    companies.stamp(result.get("customers") or [],
                    index_of(session, org, models.Customer), by="customer_id")
    result["sources_differ"] = companies.count > 1
    return _envelope(
        result, currency=th.currency,
        empty_reason=(None if result["customers"] else
                      "Payments have synced, but none of them is applied to an "
                      "invoice yet — so there is no invoice date to measure "
                      "from. Advances are counted separately above."))


@router.get("/cashflow")
def cash_projection(weeks: int = Query(cashflow.WEEKS, ge=1, le=26),
                    principal: Principal = Depends(require_manager_or_owner),
                    session: Session = Depends(get_session)) -> dict:
    """What the committed book does to cash, week by week.

    Manager and above. The inflow half is receivables and would be fine for a
    salesperson, but the outflow half is what we owe suppliers — purchase cost
    by another name, in exactly the sense that scopes ``/supply``. A projection
    with one side removed would net to a number that is not the answer to any
    question, so the whole endpoint is scoped rather than half of it stripped.

    Every figure is an obligation already entered into. Reads three folded
    states and does no arithmetic here — the money lives in ``insight/cashflow``
    and the routing lives here.
    """
    org, _snapshot, th = _labels_only(session, principal)
    on = latest_as_of(session, org, CASH_SCHEDULE)
    if on is None:
        return _no_data(th.currency, "a cash projection")
    return _envelope(
        cashflow.project(
            state_engine.load(session, org, CASH_SCHEDULE, on),
            state_engine.load(session, org, COMMITMENTS, on),
            state_engine.load(session, org, RECEIVABLES, on),
            # The state's own build date, not today: a projection dated today
            # from a fold that last ran on Friday would silently age its own
            # first bucket into the overdue column over the weekend.
            as_of=on, weeks=weeks),
        currency=th.currency, thresholds_version=th.version)


#: How many names a dead-stock row can usefully carry. Beyond this the column
#: stops being a call list and starts being a wall of text.
_BUYERS_SHOWN = 6


def _recent_buyers(session: Session, org: str,
                   names: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """Who last bought each item, most recent first.

    One grouped query rather than a scan: the previous version loaded every
    sale line in the organization and folded them in Python to answer a
    question the database can answer with an index.
    """
    rows = session.execute(
        select(models.SalesTxn.product_id, models.SalesTxn.customer_id,
               func.max(models.SalesTxn.date))
        .where(models.SalesTxn.organization_id == org)
        .group_by(models.SalesTxn.product_id, models.SalesTxn.customer_id)).all()
    seen: dict[str, list[tuple[date, str]]] = {}
    for product_id, customer_id, last in rows:
        seen.setdefault(product_id, []).append((last, customer_id))
    return {
        pid: tuple(label_for(names, cid, kind="customer")
                   for _when, cid in sorted(pairs, reverse=True)[:_BUYERS_SHOWN])
        for pid, pairs in seen.items()
    }


@router.get("/stock")
def stock_position(principal: Principal = Depends(current_principal),
                   session: Session = Depends(get_session)) -> dict:
    """What is on the shelf, and which of it is a problem.

    Reads the folded INVENTORY state rather than the lines. Every number this
    screen shows — what is on hand, how much has moved, when it last sold, what
    it last cost — was computed once by the state fold at the end of the sync,
    with a thresholds version stamped on it. Before this, the screen loaded the
    organization's entire trading history on every request and re-derived all of
    it in Python: the "dashboard as calculation engine" the evolution exists to
    remove.

    Two dates, and they are different questions. ``as_of`` is the last day the
    business traded, which is what idle days are measured against and what the
    screen reports. ``state_on`` is the day the state was last built. A stale
    build shows stale stock and says when it was taken, which is the honest
    failure; guessing today would show an empty shelf.
    """
    org, snapshot, th = _labels_only(session, principal)
    as_of = _as_of(snapshot) or clock.today(th.timezone)
    with_cost = principal.role in (Role.SALES_MANAGER, Role.OWNER)

    state_on = state_engine.latest_as_of(session, org, INVENTORY)
    if state_on is None:
        return _envelope(
            {}, currency=th.currency,
            empty_reason=("Stock has not been folded into business state yet. "
                          "It is built at the end of every sync — run one, and "
                          "this screen fills in."))
    states = state_engine.load(session, org, INVENTORY, state_on)

    lines = stock.lines_from_state(
        states,
        labels=snapshot.product_names,
        # Who has bought this item, most recent buyer first. The answer to "who
        # do I call about this", which is the only thing that turns a dead-stock
        # row into a phone call — and it is operational, so every role gets it.
        #
        # Not in the state: it is a fact about a (product, customer) pair, and
        # INVENTORY is keyed by product. Grouped in the database instead of by
        # loading every line and grouping in Python.
        buyers=_recent_buyers(session, org, snapshot.customer_names),
        with_cost=with_cost)

    carrying = stock.Carrying(annual_pct=th.carrying_cost_annual_pct,
                              dead_days=th.dead_stock_days,
                              slow_days=th.slow_stock_days,
                              rate_is_published=th.carrying_rate_is_published)
    result = stock.build(lines, as_of, carrying, with_cost=with_cost)
    if not with_cost:
        result["unavailable"].append({
            "series": "inventory_value_and_carrying_rate",
            "reason": ("What the stock cost and what rate it is carried at are "
                       "management information. What it costs you to keep it "
                       "each month is not — that is the number this screen is "
                       "for, and it is on every row."),
        })
    # Which connected company each item belongs to. An item master is per
    # company — the same part number is a different row in each book — so a
    # shelf pooled across three companies needs to say which shelf.
    companies = Companies(session, org)
    items = index_of(session, org, models.Product)
    companies.stamp(result.get("items") or [], items, by="product_id")
    for group in result.get("groups") or []:
        companies.stamp(group.get("items") or [], items, by="product_id")
    result["sources_differ"] = companies.count > 1
    return _envelope(
        result, currency=th.currency,
        empty_reason=(None if lines else
                      "Nothing in the item master is stock-tracked, so there is "
                      "no shelf to report on."))


@router.get("/supply")
def supplier_position(principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Suppliers, open orders and measured lead times.

    Manager and above: supplier spend is purchase cost by another name, and
    the platform does not put cost in front of a salesperson.
    """
    org, _snapshot, th = _labels_only(session, principal)
    as_of = clock.today(th.timezone)

    vendors = {v.vendor_id: v for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    rows = session.scalars(
        select(models.PurchaseOrderDoc)
        .where(models.PurchaseOrderDoc.organization_id == org)).all()
    if not rows:
        return _no_data(th.currency, "supplier orders")

    orders = [
        supply.SupplierOrder(
            vendor_id=po.vendor_id,
            vendor_label=(vendors[po.vendor_id].name if po.vendor_id in vendors
                          # An order whose supplier the vendor pull did not
                          # return is still an order. Named honestly rather
                          # than dropped or attributed to somebody else.
                          else "Supplier not in the contact list"),
            number=po.number,
            ordered_on=po.date,
            expected_on=po.expected_date,
            received_on=po.received_on,
            pending_qty=float(po.pending_qty or 0),
            ordered_qty=float(po.ordered_qty or 0),
            total=(float(po.total) if po.total is not None else None),
            status=po.status or "",
        )
        for po in rows
    ]
    result = supply.build(
        orders, as_of,
        terms_by_vendor={vid: v.payment_terms_days for vid, v in vendors.items()
                         if v.payment_terms_days is not None})
    # A supplier is per connected company too: the same vendor invoicing two of
    # the books is two rows, and concentration read across them without saying
    # so would look like one dependency where there are two relationships.
    companies = Companies(session, org)
    companies.stamp(result.get("suppliers") or [], vendors, by="vendor_id")
    companies.stamp(result.get("open_orders") or [], vendors, by="vendor_id")
    result["sources_differ"] = companies.count > 1
    return _envelope(result, currency=th.currency, empty_reason=None)


# ── relationship bonds ──────────────────────────────────────────────────────
#
# The one screen that looks at both sides of the book at once, so the role split
# runs through the middle of it rather than around it.
#
# The customer half contains no cost and no margin — recency, regularity,
# catalogue spread, revenue share and payment behaviour — so every role sees it.
# The supplier half is denominated in *spend*, which is purchase cost by another
# name, so a salesperson's response has no supplier half at all. Omitted by the
# server, not hidden by the browser: there is nothing in the payload to read out
# of a network tab, which is the same rule ``/supply`` follows.

#: Wider than ``MAX_MONTHS`` on purpose. The comparison screens cap at a year
#: because a period compared against one more than a year old is comparing two
#: different businesses; a bond is the opposite question — whether a tie has
#: held — and two years is the shortest span that shows one forming or decaying.
BOND_MIN_MONTHS = 6
BOND_MAX_MONTHS = 36


def _vendor_of_product(session: Session, org: str) -> dict[str, str]:
    """Each item's dominant supplier, by what this book has spent with them.

    The join that makes principal-level analysis possible at all: a sale is a
    customer buying an *item*, and only the purchase side knows whose item it
    is. An item bought from two suppliers is attributed wholly to the larger —
    a real inference, and the reason every view built on this reports what share
    of revenue it could attribute rather than quietly showing a share of a
    fraction of the book.
    """
    spend: dict[str, dict[str, float]] = {}
    rows = session.execute(
        select(models.CostRecord.product_id, models.CostRecord.vendor_id,
               models.CostRecord.qty, models.CostRecord.unit_cost)
        .where(models.CostRecord.organization_id == org,
               models.CostRecord.vendor_id.is_not(None))).all()
    for product_id, vendor_id, qty, unit_cost in rows:
        bucket = spend.setdefault(product_id, {})
        bucket[vendor_id] = bucket.get(vendor_id, 0.0) + float((qty or 0) * (unit_cost or 0))
    return {product_id: max(by_vendor.items(), key=lambda kv: kv[1])[0]
            for product_id, by_vendor in spend.items() if by_vendor}


def _category_of(session: Session, org: str, th,
                 vendor_of: Optional[dict[str, str]] = None,
                 ) -> dict[str, cat.Resolution]:
    """Every product's line of the business, resolved once per request.

    One place, so the mix grid, the bond strip's lanes and the coverage facet
    cannot disagree about what an item is — three resolutions of one taxonomy is
    three answers waiting to happen.
    """
    products = session.scalars(
        select(models.Product).where(models.Product.organization_id == org)).all()
    overrides = {
        row.product_id: row.category
        for row in session.scalars(
            select(models.ItemCategoryOverride).where(
                models.ItemCategoryOverride.organization_id == org)).all()
    }
    return cat.resolve_all(products, th, overrides=overrides,
                           vendor_of=vendor_of if vendor_of is not None
                           else _vendor_of_product(session, org))


def _customer_bonds(session: Session, principal: Principal, snapshot,
                    th, as_of: date, months: int,
                    lines_of: dict[str, cat.Resolution]) -> dict:
    """Trade lines and settled invoices → one bond per customer."""
    lines = [
        bonds.TradeLine(
            counterparty_id=row.customer_id,
            date=row.date,
            amount=float(row.line_revenue),
            item_id=row.product_id,
            # One invoice is one document however many lines it has — the same
            # rule ``aggregates.order_dates`` applies, for the same reason.
            document_ref=str((row.source_ref or {}).get("record_id")
                             or row.external_ref),
            category=_line_of(lines_of, row.product_id),
        )
        for row in snapshot.sales
    ]
    settled = _settlements(session, principal.organization_id)
    return bonds.build(
        lines, snapshot.customer_names, as_of, side=bonds.CUSTOMER,
        thresholds=th, categories_sold=cat.ORDER,
        reliability=bonds.reliability_from_payments(settled),
        signal_thresholds=load_signal_thresholds(), months=months)


def _line_of(lines_of: dict[str, cat.Resolution], product_id: str) -> Optional[str]:
    found = lines_of.get(product_id)
    return found.category if found else None


def _vendor_bonds(session: Session, org: str,
                  lines_of: dict[str, cat.Resolution],
                  th, as_of: date, months: int) -> dict:
    """Bill lines and purchase orders → one bond per supplier.

    Bill lines rather than bill headers: breadth is "how much of the catalogue
    do they actually supply", which only exists at line grain. That is what
    ``cost_records.vendor_id`` was added for — before it, this question needed
    the bill id split back out of a composite ``external_ref``.
    """
    vendors = {v.vendor_id: v for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    cost_rows = session.execute(
        select(models.CostRecord.vendor_id, models.CostRecord.product_id,
               models.CostRecord.date, models.CostRecord.qty,
               models.CostRecord.unit_cost, models.CostRecord.source_ref,
               models.CostRecord.external_ref)
        .where(models.CostRecord.organization_id == org,
               models.CostRecord.vendor_id.is_not(None))).all()
    lines = [
        bonds.TradeLine(
            counterparty_id=r.vendor_id,
            date=r.date,
            amount=float((r.qty or 0) * (r.unit_cost or 0)),
            item_id=r.product_id,
            document_ref=str((r.source_ref or {}).get("record_id")
                             or r.external_ref),
            category=_line_of(lines_of, r.product_id),
        )
        for r in cost_rows
    ]

    # Reliability comes from orders, not from bills: a bill is what arrived, and
    # "did they leave an order hanging" is only answerable from the order.
    orders = [
        supply.SupplierOrder(
            vendor_id=po.vendor_id,
            vendor_label=(vendors[po.vendor_id].name if po.vendor_id in vendors
                          else "Supplier not in the contact list"),
            number=po.number, ordered_on=po.date, expected_on=po.expected_date,
            received_on=po.received_on,
            pending_qty=float(po.pending_qty or 0),
            ordered_qty=float(po.ordered_qty or 0),
            total=(float(po.total) if po.total is not None else None),
            status=po.status or "")
        for po in session.scalars(
            select(models.PurchaseOrderDoc)
            .where(models.PurchaseOrderDoc.organization_id == org)).all()
    ]
    built = bonds.build(
        lines, {vid: v.name for vid, v in vendors.items()}, as_of,
        side=bonds.VENDOR, thresholds=th, categories_sold=cat.ORDER,
        reliability=bonds.reliability_from_supply(
            orders, as_of, stale_after_days=supply.STALE_ORDER_DAYS),
        signal_thresholds=load_signal_thresholds(), months=months)
    # An empty supplier half has two quite different causes, and a screen that
    # cannot tell them apart shows a blank panel for both. "Nothing bought yet"
    # is a fact about the business; "bills exist but none names a supplier" is a
    # fact about the sync, and it has a fix somebody can act on.
    built["empty_reason"] = _why_no_vendor_bonds(session, org, lines)

    # What we still owe them, past due — the other half of a supplier bond, and
    # the half that cannot go in the score. ``bonds.unavailable`` says why: a
    # bill carries a balance and no payment date, so this is answerable for
    # today and not reconstructible for a past month.
    overdue = _overdue_to_vendors(session, org, as_of)
    for row in built["bonds"]:
        row["overdue_payable"] = overdue.get(row["counterparty_id"], 0.0)
    return built


def _why_no_vendor_bonds(session: Session, org: str, lines: list) -> Optional[str]:
    """Which kind of empty the supplier half is, in the words a fix needs."""
    if lines:
        return None
    total = session.scalar(
        select(func.count()).select_from(models.CostRecord)
        .where(models.CostRecord.organization_id == org)) or 0
    if total == 0:
        return ("No supplier bill has been synced yet, so there is no purchase "
                "relationship to measure.")
    return (f"{total} bill line(s) are on record, but none of them names a "
            "supplier. Cost lines only started carrying their vendor recently — "
            "re-run a full sync and this fills in.")


def _overdue_to_vendors(session: Session, org: str, as_of: date) -> dict[str, float]:
    """Balance still owed on bills whose due date has passed, per supplier."""
    out: dict[str, float] = {}
    for row in session.scalars(
            select(models.BillDoc).where(
                models.BillDoc.organization_id == org,
                models.BillDoc.vendor_id.is_not(None),
                models.BillDoc.due_date.is_not(None),
                models.BillDoc.due_date < as_of)).all():
        balance = float(row.balance or 0)
        if balance > 0:
            out[row.vendor_id] = out.get(row.vendor_id, 0.0) + balance
    return out


@router.get("/bonds")
def relationship_bonds(
        months: int = Query(bonds.DEFAULT_MONTHS,
                            ge=BOND_MIN_MONTHS, le=BOND_MAX_MONTHS),
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session)) -> dict:
    """How strong each tie is, and how it got that way."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot) or clock.today(th.timezone)
    with_suppliers = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    lines_of = _category_of(session, org, th)

    customers = _customer_bonds(session, principal, snapshot, th, as_of, months,
                                lines_of)
    vendors = (_vendor_bonds(session, org, lines_of, th, as_of, months)
               if with_suppliers else None)

    # A counterparty is per connected company: the same firm trading with two of
    # the books is two relationships with two different people, and one bond
    # drawn across both would claim a closeness neither half has. Same rule
    # ``/supply`` applies to suppliers, applied to both sides here.
    companies = Companies(session, org)
    companies.stamp(customers["bonds"], index_of(session, org, models.Customer),
                    by="counterparty_id")
    if vendors is not None:
        companies.stamp(vendors["bonds"],
                        index_of(session, org, models.Vendor),
                        by="counterparty_id")

    empty = None
    if not customers["bonds"] and not (vendors or {}).get("bonds"):
        empty = ("Nothing has been traded yet, so there is no relationship to "
                 "measure. Connect a Zoho company and run a sync.")
    return _envelope(
        {"customers": customers, "vendors": vendors}, currency=th.currency,
        empty_reason=empty, as_of=as_of.isoformat(),
        sources_differ=companies.count > 1,
        supplier_side_visible=with_suppliers,
        # How well the catalogue is placed. Rendered rather than hidden: a
        # coverage facet computed over a half-categorised catalogue understates
        # every customer's breadth, and the reader has to know that.
        catalogue=cat.coverage_report(lines_of),
        lines=cat.lines(),
        unavailable=(bonds.unavailable(
            bonds.CUSTOMER,
            has_reliability=any(b["facets"]["reliability"] is not None
                                for b in customers["bonds"]))
            + (bonds.unavailable(
                bonds.VENDOR,
                has_reliability=any(b["facets"]["reliability"] is not None
                                    for b in vendors["bonds"]))
               if vendors else [])))


# ── product mix: which lines each customer takes, and which they do not ─────
#
# Every role. The grid is revenue and dates — no cost, no margin — and the whole
# point of it is a conversation a salesperson has, so 403-ing them out of it
# would be removing the feature to protect a field it does not contain.
@router.get("/mix")
def product_mix(months: int = Query(12, ge=3, le=36),
                by: str = Query(mix.BY_CATEGORY,
                                pattern=f"^({mix.BY_CATEGORY}|{mix.BY_VENDOR})$"),
                principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)) -> dict:
    """Who takes which lines — or which principals — and where the gaps are.

    Two pivots, one grid. "Who has never bought coolant" and "who has never
    bought a single Sandvik item" are the same conversation with different
    people, and an authorised distributor needs both.
    """
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "product mix")

    vendor_of = _vendor_of_product(session, org)
    lines_of = _category_of(session, org, th, vendor_of)

    if by == mix.BY_VENDOR:
        vendors = {v.vendor_id: v.name for v in session.scalars(
            select(models.Vendor).where(
                models.Vendor.organization_id == org)).all()}
        # Only principals this book has actually bought from become columns. A
        # supplier on the contact list with no purchase history is not a line
        # anybody has failed to sell, and a column of pure whitespace would
        # invent a hundred opportunities.
        traded = {vendor_of.get(row.product_id) for row in snapshot.sales}
        columns = [mix.Column(vid, name) for vid, name in
                   sorted(vendors.items(), key=lambda kv: kv[1])
                   if vid in traded]
        key_of = vendor_of
    else:
        columns = [mix.Column(c, cat.LABELS[c]) for c in cat.ORDER]
        key_of = {pid: r.category for pid, r in lines_of.items()}

    result = mix.build(
        [
            mix.MixLine(customer_id=row.customer_id, date=row.date,
                        amount=float(row.line_revenue),
                        key=key_of.get(row.product_id) or "")
            for row in snapshot.sales
        ],
        snapshot.customer_names, as_of, thresholds=th,
        columns=columns, dimension=by, months=months)

    # Per connected company, like every other list: the same firm buying from
    # two of the books is two relationships, and a coverage gap read across
    # both would show a line as missing that one of them already sells them.
    companies = Companies(session, org)
    companies.stamp(result["customers"], index_of(session, org, models.Customer),
                    by="customer_id")
    return _envelope(
        result, currency=th.currency,
        empty_reason=result.pop("empty_reason", None),
        sources_differ=companies.count > 1,
        catalogue=cat.coverage_report(lines_of),
        lines=cat.lines(),
        unavailable=mix.unavailable())


# ── dependency: what this book leans on, at both ends ───────────────────────
#
# The customer half is revenue and counts — no cost — so every role reads it.
# The supplier half is denominated in purchase spend and carries each
# principal's target, so it is manager-and-above and is omitted from a
# salesperson's response rather than hidden in it.


def _sole_source_counts(session: Session, org: str) -> dict[str, int]:
    """Items only ever supplied by one vendor, counted per vendor.

    Derived from the same cost lines the rest of this reads rather than from the
    ``SUPPLIER`` state, because that fold is keyed per (vendor, item) and this
    needs the inverse — how many *items* have a single supplier. The rule is the
    one ``state/opportunities/supplier.py`` states: one supplier on record is
    not evidence that no other exists, which is why the count travels with the
    caveat rather than as a recommendation.
    """
    suppliers: dict[str, set[str]] = {}
    for product_id, vendor_id in session.execute(
            select(models.CostRecord.product_id, models.CostRecord.vendor_id)
            .where(models.CostRecord.organization_id == org,
                   models.CostRecord.vendor_id.is_not(None))).all():
        suppliers.setdefault(product_id, set()).add(vendor_id)
    out: dict[str, int] = {}
    for vendors in suppliers.values():
        if len(vendors) == 1:
            only = next(iter(vendors))
            out[only] = out.get(only, 0) + 1
    return out


def _targets(session: Session, org: str) -> list[dependency.Target]:
    return [
        dependency.Target(
            vendor_id=row.vendor_id, period_start=row.period_start,
            period_end=row.period_end, basis=row.basis,
            amount=float(row.amount or 0))
        for row in session.scalars(
            select(models.VendorTarget).where(
                models.VendorTarget.organization_id == org)).all()
    ]


@router.get("/dependency")
def book_dependency(principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)) -> dict:
    """Who this book leans on, in both directions."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th.currency, "dependency")

    with_suppliers = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    vendor_of = _vendor_of_product(session, org)
    lines_of = _category_of(session, org, th, vendor_of)
    vendors = {v.vendor_id: v.name for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}

    flows = [
        dependency.Flow(
            customer_id=row.customer_id, product_id=row.product_id,
            date=row.date, revenue=float(row.line_revenue),
            vendor_id=vendor_of.get(row.product_id),
            category=_line_of(lines_of, row.product_id))
        for row in snapshot.sales
    ]
    spends = [
        dependency.Spend(vendor_id=r.vendor_id, product_id=r.product_id,
                         date=r.date,
                         amount=float((r.qty or 0) * (r.unit_cost or 0)))
        for r in session.execute(
            select(models.CostRecord.vendor_id, models.CostRecord.product_id,
                   models.CostRecord.date, models.CostRecord.qty,
                   models.CostRecord.unit_cost)
            .where(models.CostRecord.organization_id == org,
                   models.CostRecord.vendor_id.is_not(None))).all()
    ] if with_suppliers else []

    result = dependency.build(
        flows, spends, as_of, thresholds=th,
        vendor_names=vendors, customer_names=snapshot.customer_names,
        targets=_targets(session, org) if with_suppliers else [],
        sole_source=_sole_source_counts(session, org) if with_suppliers else {},
        with_suppliers=with_suppliers)

    # The whole book as one picture, on the same rows the lists were built
    # from — so a band and a row can never disagree about a number.
    result["flow"] = dependency.sankey(
        flows, vendor_names=vendors,
        customer_names=snapshot.customer_names) if with_suppliers else None

    companies = Companies(session, org)
    companies.stamp(result["customers"]["rows"],
                    index_of(session, org, models.Customer), by="entity_id")
    if result["vendors"] is not None:
        companies.stamp(result["vendors"]["rows"],
                        index_of(session, org, models.Vendor), by="entity_id")
    return _envelope(
        result, currency=th.currency,
        empty_reason=(None if result["customers"]["rows"] else
                      "Nothing has been traded yet, so there is no exposure to "
                      "measure."),
        sources_differ=companies.count > 1,
        supplier_side_visible=with_suppliers,
        catalogue=cat.coverage_report(lines_of))


class TargetIn(BaseModel):
    """One principal's number for one period."""

    vendor_id: str = Field(min_length=1)
    period_start: date
    period_end: date
    amount: Decimal = Field(ge=0)
    basis: str = Field(default=dependency.ON_PURCHASE)
    note: Optional[str] = Field(default=None, max_length=512)


@router.put("/targets", status_code=status.HTTP_200_OK)
def set_vendor_target(body: TargetIn,
                      principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Record what a principal expects, for one period.

    An upsert on (vendor, period, basis) rather than an insert: a target gets
    revised, and a second row for the same quarter would make "the target" a
    question about which row won.
    """
    org = principal.organization_id
    if body.basis not in (dependency.ON_PURCHASE, dependency.ON_SALES):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "basis must be PURCHASE or SALES")
    if body.period_end < body.period_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "the period ends before it starts")
    vendor = session.get(models.Vendor, body.vendor_id)
    if vendor is None or vendor.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such supplier")

    row = session.scalar(
        select(models.VendorTarget).where(
            models.VendorTarget.organization_id == org,
            models.VendorTarget.vendor_id == body.vendor_id,
            models.VendorTarget.period_start == body.period_start,
            models.VendorTarget.period_end == body.period_end,
            models.VendorTarget.basis == body.basis))
    if row is None:
        row = models.VendorTarget(
            organization_id=org, vendor_id=body.vendor_id,
            period_start=body.period_start, period_end=body.period_end,
            basis=body.basis)
        session.add(row)
    row.amount = body.amount
    row.note = body.note
    row.set_by_user_id = principal.user_id
    session.flush()
    return {"target_id": row.target_id, "vendor_id": row.vendor_id,
            "amount": float(row.amount), "basis": row.basis,
            "period_start": row.period_start.isoformat(),
            "period_end": row.period_end.isoformat()}


@router.get("/targets")
def list_vendor_targets(principal: Principal = Depends(require_manager_or_owner),
                        session: Session = Depends(get_session)) -> dict:
    """Every target on record, newest period first."""
    org = principal.organization_id
    vendors = {v.vendor_id: v.name for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    rows = session.scalars(
        select(models.VendorTarget)
        .where(models.VendorTarget.organization_id == org)
        .order_by(models.VendorTarget.period_start.desc())).all()
    return {
        "targets": [
            {"target_id": r.target_id, "vendor_id": r.vendor_id,
             "vendor_label": vendors.get(r.vendor_id, r.vendor_id),
             "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(),
             "basis": r.basis, "amount": float(r.amount or 0), "note": r.note}
            for r in rows
        ],
        "vendors": [{"vendor_id": vid, "label": name}
                    for vid, name in sorted(vendors.items(), key=lambda kv: kv[1])],
        "bases": [{"basis": b, "label": label}
                  for b, label in dependency.BASIS_LABEL.items()],
    }


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor_target(target_id: str,
                         principal: Principal = Depends(require_manager_or_owner),
                         session: Session = Depends(get_session)) -> None:
    row = session.get(models.VendorTarget, target_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such target")
    session.delete(row)


# ── the negotiation desk ────────────────────────────────────────────────────
#
# The one screen in this product a salesperson uses to *decide* rather than to
# read, so the role projection here is doing more work than anywhere else.
#
# A salesperson gets the floor price, and everything that follows from it: what
# the line contributes, what a discount costs, what the vendor ask is worth,
# what price holds a target. They do NOT get cost, margin or ``m_floor`` — and
# not because this function strips them, but because ``Assessment`` and
# ``ResolvedFloor`` have no such fields. A manager or owner additionally gets
# the reconciliation: the cost behind the floor and the margin it implies.
#
# The floor is what makes that split possible. See ``commercial/floor.py``.


class NegotiationRequest(BaseModel):
    customer_id: str
    product_id: str
    qty: float = Field(gt=0)
    agreed_price: float = Field(ge=0)
    #: Per unit, given back to the customer.
    customer_discount: float = Field(0, ge=0)
    #: Per unit, asked of the vendor. A request until a document exists.
    vendor_concession: float = Field(0, ge=0)
    #: K — a declared payment to somebody at the customer. A total, not per
    #: unit. Blocked outright on a restricted or unclassified account.
    third_party_incentive: float = Field(0, ge=0)
    #: The compliant alternative to K: tooling, training, trials. A total.
    toolkit_spend: float = Field(0, ge=0)
    #: How late the money is expected. CAF is banked on invoice and earned on
    #: receipt, so this is a lever the salesperson holds, not a KPI.
    expected_days_late: int = Field(0, ge=-365, le=730)
    #: Which floor table applies. Absent means the default multiplier.
    family: Optional[str] = None
    #: "What price leaves this line contributing X?" — solved, not searched.
    target_caf: Optional[float] = None


def _last_price_paid(session: Session, org: str, customer_id: str,
                     product_id: str) -> Optional[Decimal]:
    """What this customer last paid for this item. Context, not the currency.

    Shown next to the floor because it is the number the customer will quote
    back in the room, and OPERATIONAL by construction — it is a price they
    themselves agreed. Nothing is computed from it: the contribution is
    measured against the floor, which is what the month is paid on.
    """
    row = session.scalars(
        select(models.SalesTxn)
        .where(models.SalesTxn.organization_id == org,
               models.SalesTxn.customer_id == customer_id,
               models.SalesTxn.product_id == product_id)
        .order_by(models.SalesTxn.date.desc())
        .limit(1)).first()
    return Decimal(str(row.unit_price)) if row else None


@router.post("/negotiate")
def negotiate(body: NegotiationRequest,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)) -> dict:
    """Price the line in the currency it will be paid in, and say what blocks."""
    org, snapshot, th = _labels_only(session, principal)
    customer = _require_visible_customer(session, org, body.customer_id, principal)
    with_cost = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    as_of = clock.today(th.timezone)

    try:
        resolved = floor.resolve(session, org, body.product_id,
                                 family=body.family, as_of=as_of)
    except floor.FloorUnavailable as e:
        return _envelope({"negotiable": False}, currency=th.currency,
                         empty_reason=e.reason)

    deal = incentive.Deal(
        qty=Decimal(str(body.qty)),
        floor_price=resolved.floor_price,
        agreed_price=Decimal(str(body.agreed_price)),
        customer_discount=Decimal(str(body.customer_discount)),
        third_party_incentive=Decimal(str(body.third_party_incentive)),
        toolkit_spend=Decimal(str(body.toolkit_spend)),
        # The vendor ask is quoted per unit in the room and charged as a total.
        vendor_yield=Decimal(str(body.vendor_concession)) * Decimal(str(body.qty)))

    # I2. Checked before anything is computed, because the answer to "what
    # would it be worth?" on a government account is not a number.
    if deal.third_party_incentive > 0:
        try:
            incentive.check_third_party(
                deal.third_party_incentive, customer_id=body.customer_id,
                eligibility=customer.incentive_eligibility, as_of=as_of,
                entity_id=org)
        except incentive.IncentiveBlocked as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                str(e)) from e

    result = incentive.assess(
        deal, as_of=as_of, customer_id=body.customer_id,
        product_id=body.product_id, family=body.family, entity_id=org,
        salesperson_id=principal.user_id,
        expected_days_late=body.expected_days_late)
    free_to_give = incentive.discount_to_floor(deal, as_of=as_of)
    hold_price = (incentive.price_for_target(deal, Decimal(str(body.target_caf)),
                                             as_of=as_of)
                  if body.target_caf is not None else None)

    payload = {
        "negotiable": True,
        "floor_price": float(resolved.floor_price),
        "floor_basis": resolved.basis,
        "last_price_paid": _as_float(_last_price_paid(session, org,
                                                      body.customer_id,
                                                      body.product_id)),
        "product_label": snapshot.product_names.get(body.product_id,
                                                    body.product_id),
        "customer_label": snapshot.customer_names.get(body.customer_id,
                                                      body.customer_id),
        "third_party_allowed": incentive.may_pay_third_party(
            customer.incentive_eligibility),
        **result.to_dict(),
        "discount_to_floor_per_unit": _as_float(free_to_give),
        "price_to_hold_target": _as_float(hold_price),
        "unavailable": [],
    }

    if with_cost:
        # The reconciliation, and the only place cost appears. A separate call
        # rather than a wider return type: the salesperson path never
        # constructs the object that carries it.
        rec = floor.reconcile(session, org, body.product_id,
                              family=body.family, as_of=as_of)
        payload["unit_cost"] = float(rec.unit_cost)
        payload["gross_profit"] = float(
            (deal.net_price - rec.unit_cost) * deal.qty)
        payload["margin_at_floor"] = round(rec.gross_margin_at_floor, 4)
    else:
        payload["unavailable"] = [{
            "series": "cost_and_margin",
            "reason": ("What the item costs is management information. You do "
                       "not need it: the floor already carries it, and "
                       "everything above is arithmetic you can check yourself "
                       "— price, less floor, times quantity."),
        }]

    return _envelope(payload, currency=th.currency,
                     thresholds_version=th.version,
                     incentive_config_version=resolved.config_version,
                     empty_reason=None)


def _as_float(v: Optional[Decimal]) -> Optional[float]:
    return float(v) if v is not None else None


# ── the simulator ───────────────────────────────────────────────────────────
class SimulationRequest(BaseModel):
    scenario: str = Field(
        ..., description=("PRICE_CHANGE | MARGIN_FLOOR | CUSTOMER_RECOVERY | "
                          "INVENTORY_CHANGE | SUPPLIER_DELAY"))
    # ── inventory change ────────────────────────────────────────────────────
    #: How much of each line moves, and what it takes to move it. Both are the
    #: user's assumptions; neither is guessed at.
    share_moved: float = Field(1.0, ge=0.0, le=1.0)
    discount: float = Field(0.0, ge=0.0, le=1.0)
    #: Restrict to one band of the shelf — DEAD, SLOW or HEALTHY. Absent means
    #: the whole shelf.
    band: Optional[str] = Field(None, pattern="^(DEAD|SLOW|HEALTHY)$")
    # ── supplier delay ──────────────────────────────────────────────────────
    delay_days: int = Field(30, ge=1, le=365)
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
    _org, _snapshot, th = _labels_only(session, principal)
    return _envelope(
        {"available": [
            {"scenario": simulate.PRICE_CHANGE, "label": "Price change",
             "inputs": ["price_change_pct", "assumed_volume_change"]},
            {"scenario": simulate.INVENTORY_CHANGE, "label": "Clear stock",
             "inputs": ["band", "share_moved", "discount"]},
            {"scenario": simulate.SUPPLIER_DELAY, "label": "Supplier delay",
             "inputs": ["delay_days"]},
            {"scenario": simulate.MARGIN_FLOOR, "label": "Lift to a margin floor",
             "inputs": ["floor"]},
            {"scenario": simulate.CUSTOMER_RECOVERY, "label": "Win back customers",
             "inputs": ["customer_ids", "recovery_share"]},
         ],
         "unavailable": [dict(u) for u in simulate.UNAVAILABLE]},
        currency=th.currency)


def _state_scenario(session: Session, org: str, th, body: "SimulationRequest",
                    snapshot) -> dict:
    """The two scenarios that read Business State rather than metric rows.

    Loaded here and passed in as plain data, so ``simulate`` stays a set of
    functions of its inputs — the same arrangement the stock screen has with
    ``stock.lines_from_state``, and the reason ``commercial/`` still imports
    neither ``state/`` nor a session.
    """
    on = state_engine.latest_as_of(session, org, INVENTORY)
    if on is None:
        return {"scenario": body.scenario, "empty_reason": (
            "Business state has not been folded yet. It is built at the end of "
            "every sync — run one, and this scenario can be computed.")}
    as_of = _as_of(snapshot) or clock.today(th.timezone)
    carrying = stock.Carrying(annual_pct=th.carrying_cost_annual_pct,
                              dead_days=th.dead_stock_days,
                              slow_days=th.slow_stock_days,
                              rate_is_published=th.carrying_rate_is_published)

    if body.scenario == simulate.INVENTORY_CHANGE:
        states = state_engine.load(session, org, INVENTORY, on)
        shelf = stock.lines_from_state(
            states, labels=snapshot.product_names, buyers={}, with_cost=True)
        # Band the same way the Stock screen does, so a scenario run against
        # "dead stock" covers exactly the rows that screen calls dead.
        if body.band:
            shelf = [ln for ln in shelf if ln.health(as_of, carrying) == body.band]
        return simulate.inventory_change(
            [simulate.ShelfLine(product_id=ln.product_id, label=ln.label,
                                on_hand=ln.on_hand,
                                purchase_rate=ln.purchase_rate,
                                idle_days=ln.idle_days(as_of))
             for ln in shelf],
            monthly_carrying_pct=carrying.monthly_pct,
            share=body.share_moved, discount=body.discount)

    commitments = state_engine.load(session, org, COMMITMENTS, on)
    vendors = {v.vendor_id: v for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    return simulate.supplier_delay(
        [simulate.OpenCommitment(
            vendor_id=key,
            label=(vendors[key].name if key in vendors
                   # An order whose supplier the vendor pull did not return is
                   # still an order. Named honestly rather than dropped.
                   else "Supplier not in the contact list"),
            open_orders=int(value.get("open_purchase_orders") or 0),
            open_value=float(value.get("open_purchase_value") or 0),
            oldest_open_on=value.get("oldest_open_purchase_on"),
            payment_terms_days=getattr(vendors.get(key), "payment_terms_days", None))
         for key, value in commitments.items()
         if value.get("direction") == "supplier"],
        days=body.delay_days, as_of=as_of)


@router.post("/simulate")
def run_simulation(body: SimulationRequest,
                   principal: Principal = Depends(require_manager_or_owner),
                   session: Session = Depends(get_session)) -> dict:
    """Deterministic scenario arithmetic. Same inputs, same answer, every time."""
    org, snapshot, th = _labels_only(session, principal)

    # The two state-backed scenarios come first: they read folded state rather
    # than CustomerItemMetric, so loading the metric lines for them would be a
    # scan for nothing.
    if body.scenario in (simulate.INVENTORY_CHANGE, simulate.SUPPLIER_DELAY):
        return _envelope(_state_scenario(session, org, th, body, snapshot),
                         currency=th.currency, thresholds_version=th.version)

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
            "Supported: PRICE_CHANGE, MARGIN_FLOOR, CUSTOMER_RECOVERY, "
            "INVENTORY_CHANGE, SUPPLIER_DELAY.")

    return _envelope(result, currency=th.currency,
                     thresholds_version=th.version)
