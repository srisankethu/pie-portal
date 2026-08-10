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
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..authz import (Principal, can_view_customer, current_principal,
                     decision_queue_scope, require_manager_or_owner)
from ..repositories import DecisionRepository
from .. import approvals, clock
from ..commercial import floor, incentive, policy, portfolio, principals
from ..commercial import categories as cat
from ..commercial.insight import (absence, bonds, cadence, cashflow, cohorts,
                                  composition,
                                  daily as daily_view,
                                  dependency, flow, landscape, mix, msme, payments,
                                  periods, radar, schemes, simulate, stock, story,
                                  supply, terms as vendor_terms, weather,
                                  withholding)
from ..db import get_session
from ..domain import models
from ..domain.enums import (DecisionStatus, EnterpriseActivity, MsmeClassification,
                            MsmeEvidence, Role)
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


def _envelope(data: dict, *, th: Any, empty_reason: Optional[str] = None,
              **extra: Any) -> dict:
    """Every insight response, with the two facts about it that are not data.

    Takes the thresholds object rather than a currency string, and that is the
    whole point: of 26 manager-facing computed payloads, 8 carried a
    `thresholds_version` and 18 did not — including `weather`, which bands margin
    against `th.margin_floor` and could not say which version of that floor it
    used. `CLAUDE.md`'s rule that a computed *row* is never written without a
    version was satisfied; this was the adjacent gap, where a number on screen
    could not be traced to the policy that produced it without going back to the
    database.

    Passing the currency and remembering the version separately is what made 18
    omissions possible. One argument carries both, so the version cannot
    be left off a new endpoint without deliberately taking it off.
    """
    return {"currency": th.currency, "thresholds_version": th.version,
            "empty_reason": empty_reason, **extra, **data}


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


def _books_have_sales(session: Session, org: str) -> bool:
    """Whether any sale has ever been synced. One indexed count, no lines.

    Deliberately not `bool(snapshot.sales)`: the screens that need this read a
    labels-only snapshot, which carries no lines at all — so that test would be
    permanently False — and `test_bounded_loads` asserts structurally that none
    of those callers touches `snapshot.sales`. This answers the same question
    without loading the history the snapshot was trimmed to avoid.
    """
    return bool(session.scalar(
        select(func.count()).select_from(models.SalesTxn)
        .where(models.SalesTxn.organization_id == org).limit(1)))


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


def _no_data(th: Any, what: str, *, missing: str = "sales history",
             synced: bool = False) -> dict:
    """An empty screen, and the *actual* reason it is empty.

    Two parameters because one message was being used for two different states.
    Every screen with nothing to show said "No sales history has been synced yet
    … connect a Zoho company and run a sync", which is right for a screen that
    needs sales and has none, and wrong in both halves for `/payments`: 26 sale
    lines were synced and it is `payment_receipts` that is empty. It named the
    wrong missing thing and then gave advice that could not help — somebody sent
    to re-run a sync that had already worked.

    `missing` names what has to arrive. `synced` says the books are demonstrably
    connected, so the closing sentence stops suggesting otherwise.
    """
    if synced:
        return _envelope(
            {}, th=th,
            empty_reason=(f"The books are synced, but no {missing} has come with "
                          f"them, so {what} cannot be computed yet."))
    return _envelope(
        {}, th=th,
        empty_reason=(f"No {missing} has been synced yet, so {what} cannot be "
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
        return _no_data(th, "a briefing")

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
        dormant=dormant, concentration=concentration, currency=th.currency,
        restricted_ok=restricted_ok)
    built["as_of"] = as_of.isoformat()
    built["restricted_withheld"] = not restricted_ok
    return _envelope(built, th=th,
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
        return _no_data(th, "commercial health")

    comparison = periods.comparison(as_of, months=months)
    movement = flow.compute(snapshot.sales, snapshot.customer_names, comparison).to_dict()
    opportunities = radar.build(session, org, th,
                                customer_names=snapshot.customer_names,
                                product_names=snapshot.product_names)

    rows = session.scalars(
        select(models.CustomerItemMetric)
        .where(models.CustomerItemMetric.organization_id == org)).all()
    # Σ profit ÷ Σ *costed* revenue — never the mean of per-line margins, and
    # never over revenue that has no cost behind it. This route used to sum
    # `gross_profit_12m or 0` across every row while keeping all of their revenue
    # in the denominator, so relationships with no cost data contributed nothing
    # to the numerator and their full revenue to the divisor. On this book that
    # reported 7.8% and banded it POOR, below the 15% review floor, when the
    # figure over the relationships that actually have cost is 19.7% — above it.
    # A manager read "we are pricing below our own floor" off a coverage gap.
    #
    # The arithmetic lives in `commercial/` now, where §3 says it belongs, and
    # is the same function the customer portfolio and the landscape use.
    margin_now = portfolio.aggregate_margin(rows).margin
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
        th=th, as_of=as_of.isoformat(), period=movement["comparison"])


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
        return _no_data(th, "the revenue waterfall")

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
    return _envelope(result, th=th, as_of=as_of.isoformat(),
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
        return _no_data(th, "the customer journey")

    points = cohorts.journey(snapshot.sales, as_of, months=months,
                             names=snapshot.customer_names)
    return _envelope(
        {"series": [p.to_dict() for p in points],
         "dormant": cohorts.dormancy(snapshot.sales, snapshot.customer_names, as_of)},
        th=th, as_of=as_of.isoformat(),
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
        return _no_data(th, "band migration")

    comparison = periods.comparison(as_of, months=months)
    result = cohorts.migration(snapshot.sales, snapshot.customer_names, comparison)
    return _envelope(result, th=th, as_of=as_of.isoformat(),
                     empty_reason=(None if result["cells"] else
                                   "No customer traded in either period."))


def _require_visible_customer(session: Session, customer_id: str,
                              principal: Principal) -> models.Customer:
    """The account-list scope rule, applied to a per-customer endpoint.

    ``/api/v1/accounts`` narrows a salesperson to their own assigned accounts,
    and the decision endpoints do the same. A per-customer route that skips the
    check is a way around all of it: the id is the only thing standing between
    a salesperson and every relationship in the book, and ids travel.

    404 rather than 403, matching the decisions endpoints — a 403 confirms the
    customer exists, which is most of what an enumeration is after. The item
    picker in `routers/accounts.py` answers the same rule with an empty list
    instead, because a dropdown that errors is a field that breaks; both are
    indistinguishable from not-found, which is the property that matters.

    The rule itself is `authz.can_view_customer`, shared with that endpoint. It
    used to be written out here and again there, and a scope rule with two copies
    is one that eventually disagrees with itself about a reassigned account.
    """
    # `org` here is always `principal.organization_id` (see `_context`), and
    # `can_view_customer` checks the tenant itself — a second comparison would
    # only suggest the two can differ.
    customer = session.get(models.Customer, customer_id)
    if not can_view_customer(principal, customer):
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
    _require_visible_customer(session, customer_id, principal)
    as_of = _as_of(snapshot)
    rows = snapshot.sales_for_customer(customer_id)
    if as_of is None or not rows:
        return _no_data(th, "this customer's history")

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
            {"series": "margin", "kind": absence.WITHHELD,
             "reason": "Margin is management information."})
    return _envelope(result, th=th,
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
            f"{excluded['largest_excluded']:,.0f}. "
            # The materiality floor is part of the margin policy, which is
            # `require_owner` — the Settings field is rendered disabled for a
            # manager. Telling them to lower it addressed the action to the wrong
            # role; the Data screen already says "Ask an owner to add one" for the
            # same reason.
            + ("Lower the floor in Settings to see them, or leave it: "
               if principal.role is Role.OWNER else
               "Ask an owner to lower the floor if you want to see them, or "
               "leave it: ")
            + "below this, a gap is real and not worth an afternoon.")
    else:
        # The other way a radar empties, and until now the one it could not
        # describe: every row was examined and none could be given a cause. That
        # is a different message from "your floor is too high", and answering it
        # with the floor sentence sent an owner to a setting that would not have
        # helped.
        unnamed = excluded["unnamed_cause_count"]
        reason = (
            (f"{unnamed} of {excluded['relationships_examined']} relationships "
             f"have no nameable cause — no margin move, no peer gap and no "
             f"volume fall the rows can point at, so there is nothing to open a "
             f"conversation about. " if unnamed else "")
            + "No relationship shows a named gap. Either margins are holding, "
              "or there is not enough cost coverage yet to tell — check "
              "evidence quality on the weather view.")

    return _envelope(
        {"opportunities": [o.to_dict() for o in rows],
         "totals": radar.totals(rows), "excluded": excluded},
        th=th, empty_reason=reason)


@router.get("/lost-revenue")
def lost_revenue(months: int = Query(3, ge=MIN_MONTHS, le=MAX_MONTHS),
                 principal: Principal = Depends(require_manager_or_owner),
                 session: Session = Depends(get_session)) -> dict:
    """Revenue that stopped, grouped by what the rows say caused it."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th, "lost revenue")

    by_customer: dict[str, list] = {}
    for row in session.scalars(
            select(models.CustomerItemMetric)
            .where(models.CustomerItemMetric.organization_id == org)).all():
        by_customer.setdefault(row.customer_id, []).append(row)

    comparison = periods.comparison(as_of, months=months)
    result = cohorts.lost_revenue(snapshot.sales, snapshot.customer_names,
                                  comparison, by_customer)
    return _envelope(result, th=th, as_of=as_of.isoformat(),
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
        result, th=th,
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
        return _no_data(th, "the revenue mix")

    names = (snapshot.customer_names if dimension == composition.BY_CUSTOMER
             else snapshot.product_names)
    result = composition.build(snapshot.sales, names, as_of,
                               dimension=dimension, measure=measure, months=months)
    return _envelope(result, th=th, as_of=as_of.isoformat(),
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
        return _no_data(th, "buying rhythm")

    # Same thresholds the dormancy detector runs on, so this screen and the
    # decision queue never disagree about who is overdue.
    result = cadence.build(snapshot.sales, snapshot.customer_names, as_of,
                           thresholds=load_signal_thresholds())
    return _envelope(result, th=th,
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
            party_id=row.customer_id,
            document_ref=row.invoice_external_ref,
            document_number=row.invoice_number,
            document_date=row.invoice_date,
            due_date=row.invoice_due_date,
            paid_on=row.paid_on,
            amount=float(row.amount_applied or 0),
        )
        for row in session.scalars(stmt).all()
    ]


def _agreed_terms(session: Session, org: str) -> dict[str, vendor_terms.Term]:
    """Every supplier term somebody typed, keyed by vendor id.

    Absent means "no agreement on record", which is a different claim from "we
    agreed to whatever Zoho assumed" — see ``insight/terms.shifts``.
    """
    return {
        row.vendor_id: vendor_terms.Term(days=row.days, basis=row.basis)
        for row in session.scalars(
            select(models.VendorPaymentTerm).where(
                models.VendorPaymentTerm.organization_id == org)).all()
    }


def _open_bills(session: Session, org: str) -> list[vendor_terms.Bill]:
    """The bills a re-dating can move: attributable, and still owed.

    Scoped to bills with a balance for the same reason the fold is — a settled
    bill is not money that will move, and including it would let old documents
    drag a supplier's shift away from the one their open book actually needs.
    """
    rows = session.scalars(
        select(models.BillDoc).where(
            models.BillDoc.organization_id == org,
            models.BillDoc.vendor_id.is_not(None))).all()
    return [
        vendor_terms.Bill(vendor_id=b.vendor_id, document_date=b.date,
                          stated_due=b.due_date, amount=float(b.balance or 0))
        for b in rows if (b.balance or 0) > 0
    ]


def _bill_settlements(session: Session, org: str,
                      vendor_id: Optional[str] = None,
                      terms: Optional[dict[str, vendor_terms.Term]] = None,
                      ) -> tuple[list[payments.Settlement], int]:
    """Bill payment applications, and how many of them belong to nobody.

    The mirror of ``_settlements`` and deliberately the same grain, so both
    sides reach ``payments.build`` as the same kind of row. The count comes back
    beside the list because a bill payment can carry no vendor — money we
    genuinely sent, to a supplier the contact pull never returned — and dropping
    it silently would make the vendor list look shorter than the book is.

    **The due date is the agreed one where we have recorded an agreement.** This
    is what stops the correction being counted twice: if Zoho filed a bill under
    net-30 and the real term is net-45, then a payment made on day 40 is five
    days *early*, not ten days late. Measuring lateness against the ERP's date
    and then separately correcting that date in the projection would push the
    same fortnight into the schedule twice.
    """
    terms = terms if terms is not None else _agreed_terms(session, org)
    stmt = select(models.BillPaymentApplication).where(
        models.BillPaymentApplication.organization_id == org)
    if vendor_id:
        stmt = stmt.where(models.BillPaymentApplication.vendor_id == vendor_id)
    rows = session.scalars(stmt).all()

    def due(row: models.BillPaymentApplication) -> Optional[date]:
        term = terms.get(row.vendor_id or "")
        return term.due(row.bill_date) if term else row.bill_due_date

    return [
        payments.Settlement(
            party_id=row.vendor_id or "",
            document_ref=row.bill_external_ref,
            document_number=row.bill_number,
            document_date=row.bill_date,
            due_date=due(row),
            paid_on=row.paid_on,
            amount=float(row.amount_applied or 0),
        )
        for row in rows if row.vendor_id
    ], sum(1 for row in rows if not row.vendor_id)


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
        # Not "no sales history": the sales may be entirely there, and what is
        # missing is a payment against them.
        return _no_data(th, "payment behaviour",
                        missing="customer payment",
                        synced=_books_have_sales(session, org))

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
        result, th=th,
        empty_reason=(None if result["customers"] else
                      "Payments have synced, but none of them is applied to an "
                      "invoice yet — so there is no invoice date to measure "
                      "from. Advances are counted separately above."))


@router.get("/payables")
def payable_behaviour(principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """How long *we* take to pay, per supplier. Manager and above.

    Scoped like ``/supply`` rather than like ``/payments``, and the asymmetry is
    the rule this router already applies rather than a new one: what we owe a
    supplier is purchase cost by another name, and the platform does not put
    cost in front of a salesperson. Which customers pay us slowly is a call
    list; which suppliers we are stringing along is a commercial position.

    Computed by the same code as the receivable side — see ``insight/payments``
    for why there is one module and not two — with the vocabulary and the prose
    switched by ``payments.PAYABLE``.
    """
    org, _snapshot, th = _labels_only(session, principal)
    as_of = clock.today(th.timezone)

    vendors = {v.vendor_id: v for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    agreed = _agreed_terms(session, org)
    settled, unattributed = _bill_settlements(session, org, terms=agreed)
    made = session.scalars(
        select(models.VendorPaymentDoc).where(
            models.VendorPaymentDoc.organization_id == org)).all()
    if not made and not settled:
        return _no_data(th, "payment behaviour towards suppliers",
                        missing="supplier payment",
                        synced=_books_have_sales(session, org))

    result = payments.build(
        settled, {vid: v.name for vid, v in vendors.items()}, as_of,
        side=payments.PAYABLE,
        # The term to be judged against: ours where we recorded one, the ERP's
        # otherwise. This is the comparison the screen exists for, and it needs
        # both numbers — a measured median alone cannot be late.
        terms={vid: vendor_terms.effective_days(agreed.get(vid),
                                                v.payment_terms_days)
               for vid, v in vendors.items()},
        unattributed=unattributed)
    # Which of those terms is an agreement somebody typed and which is what
    # Zoho happened to hold. Without this the screen would present a number
    # from the nearest dropdown entry as though it had been negotiated.
    for row in result.get("vendors") or []:
        term = agreed.get(str(row.get("vendor_id")))
        row["terms_agreed"] = term is not None
        row["terms_basis"] = term.basis if term else vendor_terms.NET
        row["zoho_terms_days"] = (
            vendors[row["vendor_id"]].payment_terms_days
            if row["vendor_id"] in vendors else None)
    companies = Companies(session, org)
    companies.stamp(result.get("vendors") or [],
                    index_of(session, org, models.Vendor), by="vendor_id")
    result["sources_differ"] = companies.count > 1
    result["bases"] = vendor_terms.BASIS_LABELS
    return _envelope(
        result, th=th,
        empty_reason=(None if result["vendors"] else
                      "Payments out have synced, but none of them is applied to "
                      "a bill yet — so there is no bill date to measure from. "
                      "A sync run before bill breakdowns were read will fill "
                      "this in on its next pass."))


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

    The last two reads are the payment histories, which are what turn one line
    into a range: each party's own days-late distribution, measured from settled
    documents, so the same committed book can be placed on the timeline at the
    speed money has actually moved rather than the speed the terms claim. Both
    directions now — how customers pay us, and how we pay suppliers. Measured
    per party, and absent for a party with too little history; see
    ``payments.lag`` for why that is left absent rather than defaulted.
    """
    org, _snapshot, th = _labels_only(session, principal)
    on = latest_as_of(session, org, CASH_SCHEDULE)
    if on is None:
        # The projection is folded from receivables and payables state, not read
        # from sales lines, so a synced book with no open invoices lands here.
        return _no_data(th, "a cash projection",
                        missing="receivable or payable")
    agreed = _agreed_terms(session, org)
    settled_bills, _unattributed = _bill_settlements(session, org, terms=agreed)
    return _envelope(
        cashflow.project(
            state_engine.load(session, org, CASH_SCHEDULE, on),
            state_engine.load(session, org, COMMITMENTS, on),
            state_engine.load(session, org, RECEIVABLES, on),
            # The state's own build date, not today: a projection dated today
            # from a fold that last ran on Friday would silently age its own
            # first bucket into the overdue column over the weekend.
            as_of=on, weeks=weeks,
            lags=payments.lags(_settlements(session, org)),
            payable_lags=payments.lags(settled_bills),
            # Read from the bills rather than the fold, because the fold has
            # already aggregated away the individual due dates a re-dating needs
            # to measure itself against. The money still comes from the fold —
            # this only says how far each supplier's week moves.
            term_shifts=vendor_terms.shifts(_open_bills(session, org), agreed)),
        th=th)


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
            {}, th=th,
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
            "kind": absence.WITHHELD,
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
        result, th=th,
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
        return _no_data(th, "supplier orders",
                        missing="purchase order")

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
    # The agreed term where one was recorded, the ERP's otherwise. A supplier
    # screen quoting terms the ERP could only approximate would disagree with
    # the payables screen about the same relationship.
    agreed = _agreed_terms(session, org)
    effective = {
        vid: vendor_terms.effective_days(agreed.get(vid), v.payment_terms_days)
        for vid, v in vendors.items()
    }
    result = supply.build(
        orders, as_of,
        terms_by_vendor={vid: days for vid, days in effective.items()
                         if days is not None})
    # A supplier is per connected company too: the same vendor invoicing two of
    # the books is two rows, and concentration read across them without saying
    # so would look like one dependency where there are two relationships.
    companies = Companies(session, org)
    companies.stamp(result.get("suppliers") or [], vendors, by="vendor_id")
    companies.stamp(result.get("open_orders") or [], vendors, by="vendor_id")
    result["sources_differ"] = companies.count > 1
    return _envelope(result, th=th, empty_reason=None)


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


def _purchases(session: Session, org: str) -> list[principals.Purchase]:
    """Every cost line that names a supplier.

    The join that makes principal-level analysis possible at all: a sale is a
    customer buying an *item*, and only the purchase side knows who this book
    paid for it.
    """
    return [
        principals.Purchase(product_id=product_id, vendor_id=vendor_id,
                            amount=float((qty or 0) * (unit_cost or 0)))
        for product_id, vendor_id, qty, unit_cost in session.execute(
            select(models.CostRecord.product_id, models.CostRecord.vendor_id,
                   models.CostRecord.qty, models.CostRecord.unit_cost)
            .where(models.CostRecord.organization_id == org,
                   models.CostRecord.vendor_id.is_not(None))).all()
    ]


def _vendor_names(session: Session, org: str) -> dict[str, str]:
    return {v.vendor_id: v.name for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}


def _principal_of_product(session: Session, org: str,
                          ) -> dict[str, principals.Principal]:
    """Each item's principal for **sales** — the bill first, the maker second.

    Sales only, and the boundary is the point. Anything reconciling against a
    principal's own statement — spend, sole-source, target progress — reads
    ``models.CostRecord.vendor_id`` directly and never comes through here. See
    ``commercial/principals.py`` for why the two must not be chained.
    """
    makers = {
        product_id: manufacturer
        for product_id, manufacturer in session.execute(
            select(models.Product.product_id, models.Product.manufacturer)
            .where(models.Product.organization_id == org)).all()
    }
    return principals.resolve_all(makers, _purchases(session, org),
                                  _vendor_names(session, org))


def _principal_ids(resolved: dict[str, principals.Principal]) -> dict[str, str]:
    """product_id → principal_id, dropping the items nothing could attribute."""
    return {product_id: p.principal_id
            for product_id, p in resolved.items() if p.known}


def _customers_of_connection(session: Session, org: str,
                             connection_id: Optional[str]) -> Optional[list[str]]:
    """The customers belonging to one connected company, or None for all.

    ``SalesTxn`` carries no connection — a sale is keyed on the organization,
    the customer and the item — so the company a line belongs to is the
    company its *customer* was synced from. Scoping the snapshot therefore
    means scoping the customer set, which is exactly the bound
    ``load_snapshot`` already takes.

    None rather than a list when no company is chosen, because an empty list is
    a real bound meaning "no customers at all" and would silently empty the
    screen instead of showing everything.
    """
    if not connection_id:
        return None
    return [
        row for (row,) in session.execute(
            select(models.Customer.customer_id).where(
                models.Customer.organization_id == org,
                models.Customer.connection_id == connection_id)).all()
    ]


def _vendors_of_connection(session: Session, org: str,
                           connection_id: Optional[str]) -> Optional[list[str]]:
    """The suppliers belonging to one connected company, or None for all.

    The purchase-side mirror of ``_customers_of_connection``, and a separate
    function rather than a parameterised one because the two are not
    interchangeable: a bound built from customers would silently return nothing
    on the supply half, which reads as "this company buys from nobody" instead
    of as a mistake.
    """
    if not connection_id:
        return None
    return [
        row for (row,) in session.execute(
            select(models.Vendor.vendor_id).where(
                models.Vendor.organization_id == org,
                models.Vendor.connection_id == connection_id)).all()
    ]


def _companies(session: Session, org: str) -> list[dict]:
    """The companies a screen can scope itself to, with how many customers each
    has actually traded with.

    Read from the connections rather than from the rows on screen. A filter
    built from row provenance disappears exactly when it is most needed: a book
    synced before connections were stamped, or by a run that did not pass one,
    leaves every row's origin null and the control silently never renders —
    which is what happened here. The connection list is the truth about which
    companies exist; the counts then say which of them have anything to show.
    """
    counts = dict(session.execute(
        select(models.Customer.connection_id, func.count())
        .where(models.Customer.organization_id == org,
               models.Customer.connection_id.is_not(None))
        .group_by(models.Customer.connection_id)).all())
    return [
        {"connection_id": c.connection_id,
         "label": c.label or f"Zoho org {c.zoho_organization_id}",
         "customers": counts.get(c.connection_id, 0)}
        for c in session.scalars(
            select(models.ZohoConnection).where(
                models.ZohoConnection.organization_id == org)
            .order_by(models.ZohoConnection.label)).all()
    ]


def _revenue_by_product(snapshot) -> dict[str, float]:
    """What each item has sold, for weighting a coverage figure by money.

    A coverage report counted in items says "38% unattributed" whether those
    items sell nothing or carry a third of the book. Every report built on this
    leads with the revenue version for that reason.
    """
    revenue: dict[str, float] = {}
    for row in snapshot.sales:
        revenue[row.product_id] = (revenue.get(row.product_id, 0.0)
                                   + float(row.line_revenue))
    return revenue


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
                           else _principal_ids(_principal_of_product(session, org)))


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
        {"customers": customers, "vendors": vendors}, th=th,
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
                connection_id: Optional[str] = Query(None),
                principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)) -> dict:
    """Who takes which lines — or which principals — and where the gaps are.

    Two pivots, one grid. "Who has never bought coolant" and "who has never
    bought a single Sandvik item" are the same conversation with different
    people, and an authorised distributor needs both.

    ``connection_id`` scopes the whole grid to one connected company, **on the
    server**, and that is the difference between this and the row filter every
    other list uses. `CompanyFilter` hides rows and says so — it deliberately
    never restates a total, because the platform pools companies on purpose and
    a control that silently re-scoped an aggregate would be claiming something
    the server did not compute.

    On this screen the aggregates *are* the screen. "112 customers do not take
    cutting tools" and "75 of 171 buy from only one line" are the output; a
    filter that hid rows underneath them would leave both headline numbers
    describing a book the reader is no longer looking at. So the bound goes
    into the snapshot and every figure is recomputed for that company — which
    is also the only reading that is true, since one firm buying from two of
    the books is two relationships and a line SLS has never sold them is not a
    gap in 4U.
    """
    org, snapshot, th = _context(
        session, principal,
        sales_for_customers=_customers_of_connection(
            session, principal.organization_id, connection_id))
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th, "product mix")

    principal_of = _principal_of_product(session, org)
    vendor_of = _principal_ids(principal_of)
    lines_of = _category_of(session, org, th, vendor_of)

    if by == mix.BY_VENDOR:
        # Makers that matched no vendor row are principals too, and ``names_of``
        # is what can name them — a ``maker:`` key is deliberately absent from
        # the Vendor table, so a screen reading that table alone would render an
        # id where a principal's name belongs.
        names = principals.names_of(principal_of, _vendor_names(session, org))
        # Only principals whose product this book has actually *sold* become
        # columns. A supplier on the contact list nobody has traded is not a
        # line anybody has failed to sell, and a column of pure whitespace would
        # invent a hundred opportunities.
        traded = {vendor_of.get(row.product_id) for row in snapshot.sales}
        columns = [mix.Column(pid, name) for pid, name in
                   sorted(names.items(), key=lambda kv: kv[1])
                   if pid in traded]
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
        result, th=th,
        empty_reason=result.pop("empty_reason", None),
        sources_differ=companies.count > 1,
        catalogue=cat.coverage_report(lines_of),
        principals=principals.coverage_report(principal_of,
                                              _revenue_by_product(snapshot)),
        lines=cat.lines(),
        # The companies this grid can be scoped to, and which one it currently
        # is. Returned with the data rather than fetched separately so the
        # picker and the numbers under it can never describe different books.
        companies=_companies(session, org),
        scoped_to=connection_id,
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
            amount=float(row.amount or 0), target_id=row.target_id)
        for row in session.scalars(
            select(models.VendorTarget).where(
                models.VendorTarget.organization_id == org)).all()
    ]


@router.get("/dependency")
def book_dependency(connection_id: Optional[str] = Query(None),
                    principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)) -> dict:
    """Who this book leans on, in both directions.

    ``connection_id`` scopes both halves to one connected company, on the
    server, for the reason the mix grid does: this screen's output is *shares
    of a total*. "Kennametal is 38% of spend" is the sentence somebody acts on,
    and a filter that narrowed the rows while leaving that headline org-wide
    would put one company's list under three companies' arithmetic.

    The two halves take different bounds because the two sides of a book are
    keyed differently. Sales scope by **customer** — a sale belongs to the
    company whose customer bought it. Purchases scope by **vendor** — neither
    ``CostRecord`` nor ``SalesTxn`` carries a connection, but ``Customer`` and
    ``Vendor`` both do, and those are the ends the money is attributed to.
    """
    scope = _customers_of_connection(session, principal.organization_id,
                                     connection_id)
    org, snapshot, th = _context(session, principal, sales_for_customers=scope)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th, "dependency")

    with_suppliers = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    principal_of = _principal_of_product(session, org)
    vendor_of = _principal_ids(principal_of)
    lines_of = _category_of(session, org, th, vendor_of)
    # A superset of the Vendor table: real suppliers plus the makers that
    # matched none. Purchase-side lookups are unaffected — a ``maker:`` key can
    # never appear in a cost row — so this only names principals the sales side
    # already found.
    vendors = principals.names_of(principal_of, _vendor_names(session, org))

    flows = [
        dependency.Flow(
            customer_id=row.customer_id, product_id=row.product_id,
            date=row.date, revenue=float(row.line_revenue),
            vendor_id=vendor_of.get(row.product_id),
            category=_line_of(lines_of, row.product_id))
        for row in snapshot.sales
    ]
    only_vendors = _vendors_of_connection(session, org, connection_id)
    spend_where = [models.CostRecord.organization_id == org,
                   models.CostRecord.vendor_id.is_not(None)]
    if only_vendors is not None:
        spend_where.append(models.CostRecord.vendor_id.in_(only_vendors))
    spends = [
        dependency.Spend(vendor_id=r.vendor_id, product_id=r.product_id,
                         date=r.date,
                         amount=float((r.qty or 0) * (r.unit_cost or 0)))
        for r in session.execute(
            select(models.CostRecord.vendor_id, models.CostRecord.product_id,
                   models.CostRecord.date, models.CostRecord.qty,
                   models.CostRecord.unit_cost).where(*spend_where)).all()
    ] if with_suppliers else []

    # Targets and sole-source counts narrow with the suppliers they describe.
    # A target left in for a vendor whose spend has been scoped out would show
    # as 0% achieved against a company that never buys from them.
    keep = set(only_vendors) if only_vendors is not None else None
    targets = [t for t in _targets(session, org)
               if keep is None or t.vendor_id in keep] if with_suppliers else []
    sole = {v: n for v, n in _sole_source_counts(session, org).items()
            if keep is None or v in keep} if with_suppliers else {}

    result = dependency.build(
        flows, spends, as_of, thresholds=th,
        vendor_names=vendors, customer_names=snapshot.customer_names,
        targets=targets, sole_source=sole, with_suppliers=with_suppliers)

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
        result, th=th,
        empty_reason=(None if result["customers"]["rows"] else
                      "Nothing has been traded yet, so there is no exposure to "
                      "measure."),
        sources_differ=companies.count > 1,
        supplier_side_visible=with_suppliers,
        catalogue=cat.coverage_report(lines_of),
        principals=principals.coverage_report(principal_of,
                                              _revenue_by_product(snapshot)),
        companies=_companies(session, org),
        scoped_to=connection_id)


class VendorTermIn(BaseModel):
    """The term actually agreed with one supplier."""

    vendor_id: str = Field(min_length=1)
    days: int = Field(ge=0, le=vendor_terms.MAX_TERM_DAYS)
    basis: str = Field(default=vendor_terms.NET)
    note: Optional[str] = Field(default=None, max_length=512)


@router.put("/vendor-terms", status_code=status.HTTP_200_OK)
def set_vendor_term(body: VendorTermIn,
                    principal: Principal = Depends(require_manager_or_owner),
                    session: Session = Depends(get_session)) -> dict:
    """Record what we actually agreed to pay a supplier in.

    Manager and above, and scoped that way for the same reason ``/payables`` is:
    a payment term is a negotiated commercial position, not a clerical field.

    An upsert on the supplier rather than an insert. A term gets renegotiated,
    and a second row for the same vendor would make "the term" a question about
    which row won — the schedule cannot be drawn from an ambiguous answer.

    Zoho's own value is left exactly as it is. Both are shown on the payables
    screen, because the gap between what the ERP could express and what was
    agreed is the thing this table exists to make visible.
    """
    org = principal.organization_id
    try:
        term = vendor_terms.validate(body.days, body.basis)
    except vendor_terms.InvalidTerm as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    vendor = session.get(models.Vendor, body.vendor_id)
    if vendor is None or vendor.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such supplier")

    row = session.scalar(
        select(models.VendorPaymentTerm).where(
            models.VendorPaymentTerm.organization_id == org,
            models.VendorPaymentTerm.vendor_id == body.vendor_id))
    if row is None:
        row = models.VendorPaymentTerm(organization_id=org,
                                       vendor_id=body.vendor_id)
        session.add(row)
    row.days = term.days
    row.basis = term.basis
    row.note = body.note
    row.set_by_user_id = principal.user_id
    session.flush()
    return {"vendor_id": row.vendor_id, "days": row.days, "basis": row.basis,
            "note": row.note, "zoho_terms_days": vendor.payment_terms_days}


@router.delete("/vendor-terms/{vendor_id}", status_code=status.HTTP_200_OK)
def clear_vendor_term(vendor_id: str,
                      principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Drop the agreement and fall back to what Zoho holds.

    A real operation rather than "set it to Zoho's number": those are different
    states. An agreement that has been withdrawn means the schedule should use
    the ERP's date again, and typing the ERP's current value instead would
    freeze it against a term Zoho may later change.
    """
    org = principal.organization_id
    row = session.scalar(
        select(models.VendorPaymentTerm).where(
            models.VendorPaymentTerm.organization_id == org,
            models.VendorPaymentTerm.vendor_id == vendor_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No agreed term on record for that supplier")
    session.delete(row)
    session.flush()
    return {"vendor_id": vendor_id, "cleared": True}


@router.get("/vendor-terms")
def list_vendor_terms(principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Every supplier, what Zoho holds, and what we agreed.

    Every supplier rather than only those with an agreement: the screen this
    feeds is where somebody goes *to* record one, and a list of the rows already
    filled in is not the list somebody with work to do needs.
    """
    org = principal.organization_id
    th = policy.load_for_org(session, org)
    vendors = session.scalars(
        select(models.Vendor).where(
            models.Vendor.organization_id == org)).all()
    agreed = _agreed_terms(session, org)
    notes = {
        r.vendor_id: r
        for r in session.scalars(
            select(models.VendorPaymentTerm).where(
                models.VendorPaymentTerm.organization_id == org)).all()
    }
    open_bills = _open_bills(session, org)
    moved = vendor_terms.shifts(open_bills, agreed)
    owed: dict[str, float] = {}
    for b in open_bills:
        owed[b.vendor_id] = owed.get(b.vendor_id, 0.0) + b.amount

    rows = [
        {
            "vendor_id": v.vendor_id, "label": v.name,
            "zoho_terms_days": v.payment_terms_days,
            "agreed_days": agreed[v.vendor_id].days if v.vendor_id in agreed else None,
            "basis": (agreed[v.vendor_id].basis if v.vendor_id in agreed
                      else vendor_terms.NET),
            "note": notes[v.vendor_id].note if v.vendor_id in notes else None,
            # What recording this term actually does to the schedule, so the
            # consequence is visible at the point of editing rather than only
            # on the cash chart afterwards.
            "shift_days": moved[v.vendor_id].days if v.vendor_id in moved else None,
            "shift_exact": (moved[v.vendor_id].exact if v.vendor_id in moved
                            else None),
            "open_bills": moved[v.vendor_id].bills if v.vendor_id in moved else 0,
            "open_value": round(owed.get(v.vendor_id, 0.0), 2),
        }
        for v in vendors
    ]
    # Suppliers we owe most first: a term worth recording is one with money
    # behind it, and an alphabetical list buries those among the dormant.
    rows.sort(key=lambda r: (-r["open_value"], r["label"]))
    companies = Companies(session, org)
    companies.stamp(rows, index_of(session, org, models.Vendor), by="vendor_id")
    return _envelope(
        {"terms": rows, "bases": vendor_terms.BASIS_LABELS,
         "max_days": vendor_terms.MAX_TERM_DAYS},
        th=th,
        empty_reason=(None if rows else
                      "No suppliers have synced yet, so there is nothing to "
                      "record a term against."),
        sources_differ=companies.count > 1)


class SlabIn(BaseModel):
    """One rung of the scheme attached to a target: buy this much, earn this rate."""

    threshold: Decimal = Field(ge=0)
    #: A ratio — ``0.025`` is two and a half percent. Bounded here only against
    #: nonsense; ``schemes.validate`` owns what a *set* of them may mean.
    rate: Decimal = Field(gt=0, le=1)


class TargetIn(BaseModel):
    """One principal's number for one period, and what hitting it pays."""

    vendor_id: str = Field(min_length=1)
    period_start: date
    period_end: date
    amount: Decimal = Field(ge=0)
    basis: str = Field(default=dependency.ON_PURCHASE)
    note: Optional[str] = Field(default=None, max_length=512)
    #: The whole scheme, every time. A PUT states the target's full state, so an
    #: empty list clears the scheme rather than leaving whatever was there —
    #: a partial write here would make "the rebate" a question about which
    #: request last touched which rung.
    slabs: list[SlabIn] = Field(default_factory=list, max_length=schemes.MAX_SLABS)


@router.put("/targets", status_code=status.HTTP_200_OK)
def set_vendor_target(body: TargetIn,
                      principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Record what a principal expects, for one period, and what hitting it pays.

    An upsert on (vendor, period, basis) rather than an insert: a target gets
    revised, and a second row for the same quarter would make "the target" a
    question about which row won.

    The scheme travels with the target rather than having a write path of its
    own. A rebate with no number to hit is not a scheme, and two endpoints would
    let one be saved without the other — which is how a screen ends up showing a
    rate against a period nobody set. The slab set is **replaced** on every
    write, for the same reason the target row is: what arrives is the whole
    state, so a rung that was removed is gone rather than surviving because
    nothing mentioned it.
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
    # Validated before anything is written, so a scheme that cannot mean a
    # rebate does not leave a target behind it with the old slabs deleted.
    scheme: Optional[schemes.Scheme] = None
    if body.slabs:
        try:
            scheme = schemes.validate((s.threshold, s.rate) for s in body.slabs)
        except schemes.InvalidScheme as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                str(e)) from e

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

    _replace_slabs(session, org, row.target_id, scheme,
                   set_by_user_id=principal.user_id)
    session.flush()
    return {"target_id": row.target_id, "vendor_id": row.vendor_id,
            "amount": float(row.amount), "basis": row.basis,
            "period_start": row.period_start.isoformat(),
            "period_end": row.period_end.isoformat(),
            "slabs": [] if scheme is None else scheme.to_dict()["slabs"]}


def _replace_slabs(session: Session, org: str, target_id: str,
                   scheme: Optional[schemes.Scheme], *,
                   set_by_user_id: Optional[str]) -> None:
    """The scheme on one target, as the rows that say it. Replaced, not merged."""
    for old in session.scalars(
            select(models.VendorSchemeSlab).where(
                models.VendorSchemeSlab.target_id == target_id)).all():
        session.delete(old)
    # Flushed between the delete and the insert or the unique constraint on
    # (target, threshold) fires against rows this statement is about to remove.
    session.flush()
    for slab in (scheme.slabs if scheme else ()):
        session.add(models.VendorSchemeSlab(
            organization_id=org, target_id=target_id,
            threshold_amount=slab.threshold, rate=slab.rate,
            set_by_user_id=set_by_user_id))


def _schemes_by_target(session: Session, org: str) -> dict[str, schemes.Scheme]:
    """Every scheme on record, keyed by the target it hangs off.

    Read back through ``schemes.validate`` rather than assembled directly: the
    ordering and the rising-rate rule are properties of a scheme, not of the
    write path, and a row edited in the database by hand should surface as a
    refusal rather than as a quietly wrong rebate.
    """
    rungs: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for row in session.scalars(
            select(models.VendorSchemeSlab).where(
                models.VendorSchemeSlab.organization_id == org)).all():
        rungs.setdefault(row.target_id, []).append(
            (Decimal(row.threshold_amount), Decimal(row.rate)))
    out: dict[str, schemes.Scheme] = {}
    for target_id, pairs in rungs.items():
        try:
            out[target_id] = schemes.validate(pairs)
        except schemes.InvalidScheme:
            log.warning("scheme on target %s is not readable; omitted", target_id)
    return out


@router.get("/targets")
def list_vendor_targets(principal: Principal = Depends(require_manager_or_owner),
                        session: Session = Depends(get_session)) -> dict:
    """Every target on record with its scheme, newest period first."""
    org = principal.organization_id
    vendors = {v.vendor_id: v.name for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    rows = session.scalars(
        select(models.VendorTarget)
        .where(models.VendorTarget.organization_id == org)
        .order_by(models.VendorTarget.period_start.desc())).all()
    by_target = _schemes_by_target(session, org)
    return {
        "targets": [
            {"target_id": r.target_id, "vendor_id": r.vendor_id,
             "vendor_label": vendors.get(r.vendor_id, r.vendor_id),
             "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(),
             "basis": r.basis, "amount": float(r.amount or 0), "note": r.note,
             # The editor reads this back to fill its rows, so a revision starts
             # from what is stored rather than from an empty form that would
             # clear the scheme on save.
             "slabs": ([] if r.target_id not in by_target
                       else by_target[r.target_id].to_dict()["slabs"])}
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
    # The scheme goes with it. A rebate whose target no longer exists is not
    # merely untidy — it is unreachable, since every read of a scheme starts
    # from the target it hangs off.
    _replace_slabs(session, row.organization_id, target_id, None,
                   set_by_user_id=principal.user_id)
    session.delete(row)


# ── principal schemes: what hitting the number is worth ─────────────────────
#
# Manager and above, and for the same reason ``/supply`` and ``/payables`` are:
# purchase spend is cost by another name, and a rebate is a percentage of it.
# Scoped by the role rather than by field-stripping because there is nothing
# left of this screen once the money is removed.
#
# The target half is not recomputed here. ``dependency.progress_of`` owns
# actual-against-target, pace and the run rate that closes the gap; this
# endpoint calls it and adds the two things it deliberately does not do — what
# the scheme pays, and where the period lands if the book keeps buying at the
# rate it has.


def _purchase_lines(session: Session, org: str) -> list[tuple[str, date, Decimal, str]]:
    """Every cost line that names a supplier: (vendor, date, amount, bill).

    Money as ``Decimal`` all the way from the column, because a rebate is a rate
    times this sum and the number it produces has to agree with a principal's
    own statement to the paise.

    The bill reference is the ``bill_id`` half of ``CostRecord.external_ref``,
    which ``ingestion/normalize.normalize_bill`` writes as ``bill:line``. It is
    evidence rather than money: a fortnight of purchasing whose whole total came
    off one order is not a rate, and the projection floor needs to be able to
    see that.
    """
    return [
        (r.vendor_id, r.date,
         Decimal(r.qty or 0) * Decimal(r.unit_cost or 0),
         (r.external_ref or "").split(":")[0])
        for r in session.execute(
            select(models.CostRecord.vendor_id, models.CostRecord.date,
                   models.CostRecord.qty, models.CostRecord.unit_cost,
                   models.CostRecord.external_ref)
            .where(models.CostRecord.organization_id == org,
                   models.CostRecord.vendor_id.is_not(None))).all()
    ]


@router.get("/schemes")
def principal_schemes(principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Every live target with its rebate: secured, at stake, and where it lands.

    Only the periods ``as_of`` falls inside. A quarter that closed in March is
    settled — the principal has paid or has not — and a wall of finished periods
    would bury the one thing this screen exists for, which is the number still
    winnable this quarter.

    Which target is live is ``dependency.current_target``'s answer, not a second
    one: the shortest covering period wins, so a quarter set inside an annual
    number is what gets chased.
    """
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot)
    if as_of is None:
        return _no_data(th, "target progress")

    targets = _targets(session, org)
    live = {t.vendor_id: t for t in
            (dependency.current_target(vendor_id, targets, as_of)
             for vendor_id in {t.vendor_id for t in targets})
            if t is not None}
    if not live:
        return _envelope(
            {"rows": [], "at_stake_total": 0.0, "as_of": as_of.isoformat()},
            th=th,
            empty_reason=("No principal has a target covering today. Nothing in "
                          "Zoho holds one, so they are typed in — add this "
                          "quarter's numbers and their schemes and this fills "
                          "in."))

    purchases = _purchase_lines(session, org)
    # Sell-through targets are measured on sales of that maker's product, which
    # is the one figure here that runs through the manufacturer fallback. That
    # is correct and it is stated in ``commercial/principals.py``: what a
    # sell-through target measures *is* sales of their product. A purchase
    # target never touches this map.
    needs_sales = any(t.basis == dependency.ON_SALES for t in live.values())
    vendor_of = (_principal_ids(_principal_of_product(session, org))
                 if needs_sales else {})

    by_target = _schemes_by_target(session, org)
    names = _vendor_names(session, org)
    rows = []
    for vendor_id, target in live.items():
        on_purchase = target.basis == dependency.ON_PURCHASE
        if on_purchase:
            lines = [(amount, bill) for vid, day, amount, bill in purchases
                     if vid == vendor_id and target.covers(day)]
        else:
            lines = [(Decimal(s.line_revenue),
                      (s.source_ref or {}).get("record_id") or s.external_ref)
                     for s in snapshot.sales
                     if vendor_of.get(s.product_id) == vendor_id
                     and target.covers(s.date)]
        # Σ amount, never an average of anything: a target is a total and the
        # rebate is a rate on that total.
        actual = sum((amount for amount, _ in lines), Decimal("0"))
        documents = len({ref for _, ref in lines if ref})

        rows.append({
            "vendor_id": vendor_id,
            "label": names.get(vendor_id) or f"Unnamed supplier (id {vendor_id})",
            "target_id": target.target_id,
            # Actual, pace and the run rate that closes the gap — computed
            # where they already are, not restated here.
            "progress": dependency.progress_of(
                vendor_id, targets, as_of,
                purchased=float(actual) if on_purchase else 0.0,
                sold=0.0 if on_purchase else float(actual)),
            "rebate": schemes.outlook(
                target, by_target.get(target.target_id or ""),
                actual=actual, documents=documents, as_of=as_of),
        })

    # Behind pace first: the wall answers "where does this month go", and that is
    # only true if the principal furthest off their own pace leads it.
    rows.sort(key=lambda r: ((r["progress"] or {}).get("achieved") or 0.0)
              - ((r["progress"] or {}).get("period_elapsed") or 0.0))
    companies = Companies(session, org)
    companies.stamp(rows, index_of(session, org, models.Vendor), by="vendor_id")
    return _envelope(
        {"rows": rows,
         # What the quarter is worth if every principal's next rung is reached.
         # A sum of uplifts, so nothing already secured is counted into it.
         "at_stake_total": round(sum(r["rebate"]["at_stake"] or 0.0
                                     for r in rows), 2),
         "as_of": as_of.isoformat()},
        th=th,
        empty_reason=None,
        sources_differ=companies.count > 1,
        floors={"min_elapsed_days": schemes.MIN_ELAPSED_DAYS,
                "min_documents": schemes.MIN_DOCUMENTS})


# ── the catalogue: which line each item belongs to ──────────────────────────
#
# The last mile of category coverage. Three of the four sources resolve
# themselves — an override, the catalogue's own word, the tariff code, the
# supplier's dominant line — and whatever is left needs a person. This is where
# that person works.
#
# Manager and above: placing an item in a line is policy, and it moves every
# figure on the mix grid and the coverage facet on every bond.
#
# **Ordered by the money running through the item, not alphabetically.** A
# catalogue has thousands of items and almost nobody will place them all; the
# useful property is that placing the first ten closes most of the gap, and that
# is only true if the list leads with the ones revenue actually flows through.


@router.get("/catalogue")
def catalogue_lines(unplaced_only: bool = Query(True),
                    principal: Principal = Depends(require_manager_or_owner),
                    session: Session = Depends(get_session)) -> dict:
    """Every item's line, where it came from, and what it is worth placing."""
    org, snapshot, th = _context(session, principal)
    principal_of = _principal_of_product(session, org)
    vendor_of = _principal_ids(principal_of)
    lines_of = _category_of(session, org, th, vendor_of)

    overrides = {
        row.product_id: row for row in session.scalars(
            select(models.ItemCategoryOverride).where(
                models.ItemCategoryOverride.organization_id == org)).all()
    }
    revenue: dict[str, float] = {}
    for row in snapshot.sales:
        revenue[row.product_id] = revenue.get(row.product_id, 0.0) + float(row.line_revenue)

    products = session.scalars(
        select(models.Product).where(models.Product.organization_id == org)).all()

    rows = []
    for p in products:
        resolved = lines_of.get(p.product_id)
        if resolved is None:
            continue
        if unplaced_only and resolved.known:
            continue
        # Whose product this is, and on what evidence. Both, because "supplier:
        # Kennametal" reads identically whether a bill proves it or the item
        # master merely says so, and somebody correcting a catalogue needs to
        # know which of those they are looking at before they trust it.
        whose = principal_of.get(p.product_id)
        rows.append({
            "product_id": p.product_id,
            "name": p.name,
            "hsn": p.hsn,
            "heading": cat.heading_of(p.hsn),
            "zoho_category": p.category,
            "category": resolved.category,
            "label": cat.LABELS[resolved.category],
            "source": resolved.source,
            "source_label": cat.SOURCE_LABEL[resolved.source],
            "overridden": p.product_id in overrides,
            "note": overrides[p.product_id].note if p.product_id in overrides else None,
            "manufacturer": p.manufacturer,
            "supplier": whose.name if whose and whose.known else None,
            "supplier_source": whose.source if whose else principals.BY_NOTHING,
            "supplier_source_label": principals.SOURCE_LABEL[
                whose.source if whose else principals.BY_NOTHING],
            "revenue": round(revenue.get(p.product_id, 0.0), 2),
        })
    # The biggest first. Placing the item nothing sells is busywork; placing the
    # one a tenth of revenue runs through is the whole job.
    rows.sort(key=lambda r: -r["revenue"])

    unplaced_revenue = sum(
        revenue.get(pid, 0.0) for pid, r in lines_of.items() if not r.known)
    total_revenue = sum(revenue.values())
    return _envelope(
        {"items": rows,
         "lines": cat.lines(),
         "catalogue": cat.coverage_report(lines_of),
         "principals": principals.coverage_report(principal_of, revenue),
         # What placing the rest is worth. A coverage percentage counted in
         # *items* can look alarming while the unplaced ones sell nothing —
         # this is the number that says whether the work matters.
         "unplaced_revenue": round(unplaced_revenue, 2),
         "unplaced_revenue_share": (round(unplaced_revenue / total_revenue, 4)
                                    if total_revenue else None)},
        th=th,
        empty_reason=(None if rows else
                      ("Every item that has traded is placed in a line."
                       if unplaced_only else
                       "No items have been synced yet.")))


class ItemLineIn(BaseModel):
    category: str = Field(min_length=1)
    note: Optional[str] = Field(default=None, max_length=512)


@router.put("/catalogue/{product_id}")
def set_item_line(product_id: str, body: ItemLineIn,
                  principal: Principal = Depends(require_manager_or_owner),
                  session: Session = Depends(get_session)) -> dict:
    """Place an item in a line by hand. Beats every other source."""
    org = principal.organization_id
    if body.category not in cat.ORDER:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"category must be one of {', '.join(cat.ORDER)}")
    product = session.get(models.Product, product_id)
    if product is None or product.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item")

    row = session.scalar(
        select(models.ItemCategoryOverride).where(
            models.ItemCategoryOverride.organization_id == org,
            models.ItemCategoryOverride.product_id == product_id))
    if row is None:
        row = models.ItemCategoryOverride(organization_id=org,
                                          product_id=product_id)
        session.add(row)
    row.category = body.category
    row.note = body.note
    row.set_by_user_id = principal.user_id
    session.flush()
    return {"product_id": product_id, "category": row.category,
            "label": cat.LABELS[row.category], "source": cat.BY_OVERRIDE}


@router.delete("/catalogue/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def clear_item_line(product_id: str,
                    principal: Principal = Depends(require_manager_or_owner),
                    session: Session = Depends(get_session)) -> None:
    """Drop the override and let the automatic sources speak again."""
    row = session.scalar(
        select(models.ItemCategoryOverride).where(
            models.ItemCategoryOverride.organization_id == principal.organization_id,
            models.ItemCategoryOverride.product_id == product_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No override on that item")
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
    #: Agreed credit days on this deal — the term conceded, not the days this
    #: customer usually takes. The two are priced separately and deliberately:
    #: lateness by ``expected_days_late`` above, the term itself through the
    #: floor. Absent means none was stated and the published standard term
    #: applies, which is not the same as zero.
    credit_days: Optional[int] = Field(None, ge=0, le=365)
    #: Which floor table applies. Absent means it is resolved from the item.
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
    customer = _require_visible_customer(session, body.customer_id, principal)
    with_cost = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    as_of = clock.today(th.timezone)

    try:
        resolved = floor.resolve(session, org, body.product_id,
                                 family=body.family, as_of=as_of)
    except floor.UnknownFamily as e:
        # 400, and deliberately not the empty envelope below. "This item has no
        # floor" is a calm answer a screen renders; "there is no such family" is
        # a malformed request. Answering both the same way is what let the
        # family name be swept — every unknown name returned the default
        # multiplier's floor, so the table could be read off the floors.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except floor.FloorUnavailable as e:
        return _envelope({"negotiable": False}, th=th,
                         empty_reason=e.reason)

    deal = incentive.Deal(
        qty=Decimal(str(body.qty)),
        floor_price=resolved.floor_price,
        agreed_price=Decimal(str(body.agreed_price)),
        customer_discount=Decimal(str(body.customer_discount)),
        third_party_incentive=Decimal(str(body.third_party_incentive)),
        toolkit_spend=Decimal(str(body.toolkit_spend)),
        # The vendor ask is quoted per unit in the room and charged as a total.
        vendor_yield=Decimal(str(body.vendor_concession)) * Decimal(str(body.qty)),
        credit_days=body.credit_days)

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
            "kind": absence.WITHHELD,
            "reason": ("What the item costs is management information. You do "
                       "not need it: the floor already carries it, and "
                       "everything above is arithmetic you can check yourself "
                       "— price, less floor, times quantity."),
        }]

    return _envelope(payload, th=th,
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
    #: Restrict to one band of the shelf — DEAD, SLOW, HEALTHY or UNKNOWN.
    #: Absent means the whole shelf. UNKNOWN is accepted so a scenario can be
    #: run over "too new to judge" deliberately; it is never folded into DEAD,
    #: which is the whole point of the band existing.
    band: Optional[str] = Field(None, pattern="^(DEAD|SLOW|HEALTHY|UNKNOWN)$")
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
        th=th)


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
    agreed = _agreed_terms(session, org)
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
            # The agreed term where one exists. "What if we delayed suppliers
            # by N days" is only answerable against the terms we are actually
            # on, not the ones the ERP's dropdown could express.
            payment_terms_days=vendor_terms.effective_days(
                agreed.get(key),
                getattr(vendors.get(key), "payment_terms_days", None)))
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
                         th=th)

    lines = simulate.load_lines(session, org, customer_id=body.customer_id,
                                product_id=body.product_id,
                                customer_names=snapshot.customer_names,
                                product_names=snapshot.product_names)
    if not lines:
        return _no_data(th, "this scenario")

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

    return _envelope(result, th=th,)


# ── the morning read ────────────────────────────────────────────────────────
#
# An assembly, not a computation. Every figure below is produced by the builder
# that the screen owning it already uses, and this endpoint's whole job is to
# load those inputs once and hand the results to `daily.assemble`. The moment
# it starts working a number out for itself is the moment this page and the
# Cash screen can disagree about what "overdue" means.

def _last_two_syncs(session: Session, org: str) -> tuple[Optional[dict], Optional[dict]]:
    """The most recent completed sync and the one before it.

    Two, because the "what moved" band reports the window *between* them: the
    rows the latest run brought in that the run before it had not.
    """
    rows = session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == org)
        .order_by(models.SyncRun.started_at.desc())
        .limit(2)).all()

    def _d(run: Optional[models.SyncRun]) -> Optional[dict]:
        if run is None:
            return None
        return {
            "status": run.status,
            "started_at": clock.iso(run.started_at),
            "finished_at": clock.iso(run.finished_at),
        }

    return (_d(rows[0] if rows else None), _d(rows[1] if len(rows) > 1 else None))


def _moved_since(session: Session, org: str, since: datetime,
                 companies: Companies,
                 until: Optional[datetime] = None) -> dict[str, Any]:
    """What the platform first saw after ``since``, by kind and by company.

    Keyed on ``created_at`` — when PIE first wrote the row — not on the
    document's own date. A purchase order dated last week that arrived in this
    morning's sync belongs in this morning's briefing, because it is new to the
    reader. That makes the band "what the platform learned", which is a
    different claim from "what the business did", and the screen says so.

    Counted per company here rather than split out of a builder's list: those
    lists are capped, so a breakdown taken from one would not add up to its own
    headline.
    """
    spec: list[tuple[str, Any, Optional[str]]] = [
        ("invoices", models.InvoiceDoc, "total"),
        ("payments", models.PaymentApplication, "amount"),
        ("purchase_orders", models.PurchaseOrderDoc, "total"),
        ("customers", models.Customer, None),
        ("products", models.Product, None),
    ]
    out: dict[str, Any] = {}
    for key, model, amount_col in spec:
        if not hasattr(model, "created_at"):
            continue
        has_conn = hasattr(model, "connection_id")
        cols: list[Any] = [func.count()]
        if amount_col is not None and hasattr(model, amount_col):
            cols.append(func.sum(getattr(model, amount_col)))
        group = [model.connection_id] if has_conn else []
        bounds = [model.organization_id == org, model.created_at >= since]
        # Inclusive at the top: ``until`` already carries end-of-day when the
        # caller chose a date, so a strict `<` here would drop everything that
        # arrived on the last day of the range somebody asked for.
        if until is not None:
            bounds.append(model.created_at <= until)
        rows = session.execute(
            select(*group, *cols).where(*bounds).group_by(*group)).all()

        total = 0
        amount = 0.0
        by_company: list[dict[str, Any]] = []
        for row in rows:
            values = list(row)
            conn = values.pop(0) if has_conn else None
            count = int(values[0] or 0)
            total += count
            if len(values) > 1:
                amount += float(values[1] or 0)
            by_company.append({
                "company": companies.label_for(conn), "count": count})
        out[key] = {
            "count": total,
            "amount": round(amount, 2) if amount else None,
            "by_company": sorted(by_company, key=lambda r: r["count"], reverse=True),
        }
    return out


@router.get("/daily")
def daily(moved_from: Optional[date] = Query(None),
          moved_to: Optional[date] = Query(None),
          committed_weeks: int = Query(1, ge=1, le=13),
          principal: Principal = Depends(require_manager_or_owner),
          session: Session = Depends(get_session)) -> dict:
    """The morning read.

    ``moved_from``/``moved_to`` set the window the **What moved** band reports
    over — a single day when only ``moved_from`` is given, an inclusive range
    when both are. They govern that band and no other, which is a deliberate
    limit rather than an unfinished one: the remaining bands are not periods.
    An approval is waiting *now*, an invoice is overdue *now*, a commitment
    lands in the seven days *from now*. "What was overdue last Tuesday" would
    mean reconstructing a past state, which this platform does not do — so a
    date control spanning the whole page would return three numbers that either
    ignored it or lied about it.

    ``committed_weeks`` is the **Committed** band's own horizon, and it is a
    separate control because it is a separate axis: forward, on the dates
    documents already carry, rather than backward over when the platform
    learned of a row. It counts weeks because the cash fold is bucketed by ISO
    week — an arbitrary range would have to be answered approximately, which is
    precision the data does not have. Capped at a quarter, past which the
    committed book is mostly empty and the tile stops saying anything.

    Manager and above, for the same reason `/supply` and `/cashflow` are: two
    of its five bands are cash and supplier exposure, which is purchase cost by
    another name. A salesperson's version is the same assembly with those bands
    not built — worth doing, and not done here, because it wants its own pass
    over what a salesperson's morning actually asks.
    """
    org, snapshot, th = _labels_only(session, principal)
    as_of = _as_of(snapshot) or clock.today(th.timezone)
    companies = Companies(session, org)
    last_sync, previous_sync = _last_two_syncs(session, org)

    # ── each band's source, loaded the way its own screen loads it ──────────
    cash: dict[str, Any] = {}
    cash_on = latest_as_of(session, org, CASH_SCHEDULE)
    if cash_on is not None:
        cash = cashflow.project(
            state_engine.load(session, org, CASH_SCHEDULE, cash_on),
            state_engine.load(session, org, COMMITMENTS, cash_on),
            state_engine.load(session, org, RECEIVABLES, cash_on),
            as_of=cash_on, weeks=committed_weeks)

    stock_result: dict[str, Any] = {}
    stock_on = state_engine.latest_as_of(session, org, INVENTORY)
    if stock_on is not None:
        stock_result = stock.build(
            stock.lines_from_state(
                state_engine.load(session, org, INVENTORY, stock_on),
                labels=snapshot.product_names, buyers={}, with_cost=True),
            _as_of(snapshot) or clock.today(th.timezone),
            stock.Carrying(annual_pct=th.carrying_cost_annual_pct,
                           dead_days=th.dead_stock_days,
                           slow_days=th.slow_stock_days,
                           rate_is_published=th.carrying_rate_is_published),
            with_cost=True)

    vendors = {v.vendor_id: v for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}
    po_rows = session.scalars(
        select(models.PurchaseOrderDoc)
        .where(models.PurchaseOrderDoc.organization_id == org)).all()
    supply_result = supply.build(
        [supply.SupplierOrder(
            vendor_id=po.vendor_id,
            vendor_label=(vendors[po.vendor_id].name if po.vendor_id in vendors
                          else "Supplier not in the contact list"),
            number=po.number, ordered_on=po.date, expected_on=po.expected_date,
            received_on=po.received_on,
            pending_qty=float(po.pending_qty or 0),
            ordered_qty=float(po.ordered_qty or 0),
            total=(float(po.total) if po.total is not None else None),
            status=po.status or "") for po in po_rows],
        as_of) if po_rows else {}

    # Cadence needs the lines, and is the one genuinely expensive read here.
    _org, full, _th = _context(session, principal)
    cadence_result = (
        cadence.build(full.sales, full.customer_names, _as_of(full),
                      thresholds=load_signal_thresholds())
        if _as_of(full) else {})

    # Role-scoped, and the same function the queue and the nav badge use. This
    # was an unscoped `count(*)` over every PENDING request in the organization,
    # so one role's landing page said 3 while the badge said 2 and exactly 1 was
    # decidable: the tile counted an OWNER-authority below-cost request that
    # `approvals.inbox` deliberately keeps out of a manager's queue, and its
    # "Work through these →" therefore landed on a screen where the third item
    # did not exist and could not be made to appear. One number, from one place.
    approvals_pending = approvals.pending_count(session, principal)

    # The same scope the queue itself applies, for the same reason the approvals
    # count above was fixed: this was an org-wide `count(*)` over every OPEN row,
    # and the screen its "Work through these →" opens is not org-wide.
    #
    # The gap today is QUOTE_CONTEXT, which the queue excludes as on-demand quote
    # support rather than an attention item, and which the count included. This
    # endpoint is manager-or-owner only, so the RESTRICTED types are *not* part
    # of the discrepancy — a salesperson never loads this page. That makes the
    # bug smaller than the approvals one it mirrors, and the fix the same shape:
    # a count and the list it promises to count come from one place.
    #
    # It also stops the gap widening on its own. `decision_list_scope` is where
    # role scoping is decided, so a future role that can open this page inherits
    # its scope here rather than needing somebody to remember this line.
    #
    # Counted from the repository's own list rather than by a parallel aggregate
    # query, so the tile cannot drift from the queue: one scope, one reader. The
    # queue is a worklist a person is expected to finish, so its length is
    # bounded by what it is for.
    decisions_by_band: dict[str, int] = {}
    for row in DecisionRepository(session, org).list(
            status=DecisionStatus.OPEN.value, **decision_queue_scope(principal)):
        decisions_by_band[row.priority_band] = decisions_by_band.get(row.priority_band, 0) + 1

    since, until = daily_view.window_since(last_sync, previous_sync,
                                           now=clock.now(),
                                           frm=moved_from, to=moved_to)
    moved = (_moved_since(session, org, since, companies, until=until)
             if since else {})

    return _envelope(
        daily_view.assemble(
            now=clock.now(), as_of=as_of, state_on=cash_on or stock_on,
            last_sync=last_sync, approvals_pending=int(approvals_pending),
            decisions_by_band=decisions_by_band, stock=stock_result,
            supply=supply_result, cadence=cadence_result, cash=cash,
            moved=moved, moved_window=(moved_from, moved_to),
            committed_weeks=committed_weeks),
        th=th)


# ── statutory payment timing ────────────────────────────────────────────────
#
# Two screens over the same book, both manager-or-owner and both scoped that
# way for the reason ``/payables`` is: what we owe a supplier is purchase cost
# by another name, and this platform does not put cost in front of a
# salesperson. The MSME view additionally carries a cost estimate derived from
# the organization's tax rate, which is RESTRICTED in its own right.
#
# Neither endpoint interprets anything. They assemble rows, hand them to a pure
# module in ``commercial/insight`` and map the result — there is no arithmetic
# here, and the figures that come back are dates and amounts rather than a
# position anybody should file a return on.


def _msme_statuses(session: Session, org: str) -> dict[str, msme.Status]:
    """Every MSME status somebody recorded, keyed by vendor id.

    Absent means UNKNOWN, which ``msme.NO_STATUS`` supplies at the point of
    use. Deliberately not defaulted here: a dict that answered for every vendor
    would hide the difference between a supplier nobody has assessed and one
    that is not in the book at all.
    """
    return {
        row.vendor_id: msme.Status(
            classification=row.classification,
            activity=row.enterprise_activity,
            written_agreement=row.written_agreement,
            agreed_days=row.agreed_days,
            evidence=row.evidence,
            captured_at=(row.captured_at.date() if row.captured_at else None),
        )
        for row in session.scalars(
            select(models.VendorMsmeStatus).where(
                models.VendorMsmeStatus.organization_id == org)).all()
    }


def _unpaid_bills(session: Session, org: str) -> list[msme.OpenBill]:
    """Bills with something still owed on them, whether or not they resolved.

    ``vendor_id`` is not required, unlike ``_open_bills``. A bill from a
    supplier the contact pull never returned is still money owed on a date, and
    dropping it here would take exactly the least visible bills off a list
    whose whole job is to be complete about a deadline.
    """
    rows = session.scalars(
        select(models.BillDoc).where(models.BillDoc.organization_id == org)).all()
    return [
        msme.OpenBill(vendor_id=b.vendor_id, external_ref=b.external_ref,
                      number=b.number, bill_date=b.date, due_date=b.due_date,
                      balance=float(b.balance or 0))
        for b in rows if (b.balance or 0) > 0
    ]


def _vendor_names(session: Session, org: str) -> dict[str, str]:
    return {v.vendor_id: v.name for v in session.scalars(
        select(models.Vendor).where(models.Vendor.organization_id == org)).all()}


@router.get("/msme-watchlist")
def msme_watchlist(principal: Principal = Depends(require_manager_or_owner),
                   session: Session = Depends(get_session)) -> dict:
    """Open bills approaching or past the MSME 45-day deadline.

    Section 43B(h) disallows the deduction for anything still owed to a
    registered micro or small supplier past the section 15 limit, for that
    year. Every input but one was already here; the missing one is the
    supplier's status, which is captured through ``PUT /msme-status`` and is
    never inferred.

    A supplier with no status on record produces a row in the ``gaps`` band
    rather than being quietly dropped, and the amount beside it is what *would*
    be at risk — reported separately and never added to the confirmed total.
    """
    org, _snapshot, th = _labels_only(session, principal)
    as_of = clock.today(th.timezone)

    bills = _unpaid_bills(session, org)
    if not bills:
        return _no_data(th, "supplier payment deadlines", missing="supplier bill",
                        synced=_books_have_sales(session, org))

    # No ``po_receipts``, and deliberately not a stub that returns nothing.
    # Section 15 counts from acceptance, for which a recorded goods receipt is
    # the better proxy — but ``BillDoc`` carries no link to a purchase order,
    # so there is nothing to join on. ``msme.deadline_for`` accepts receipts
    # for when there is, and until then every row reports its
    # ``deadline_start_basis`` as BILL_DATE, which is the true statement about
    # what the date rests on. An always-empty resolver here would look like the
    # feature exists.
    built = msme.watchlist(bills, _msme_statuses(session, org),
                           _vendor_names(session, org), as_of=as_of, th=th)
    Companies(session, org).stamp(built["rows"],
                                  index_of(session, org, models.Vendor),
                                  by="vendor_id")
    return _envelope(
        built, th=th,
        empty_reason=(None if built["rows"] else
                      "No open bill is inside the watch horizon, and none has "
                      "passed its deadline. Suppliers confirmed as outside the "
                      "rule are not listed at all."))


class MsmeStatusIn(BaseModel):
    """What was established about one supplier, and on what evidence."""

    vendor_id: str = Field(min_length=1)
    classification: str = Field(default=MsmeClassification.UNKNOWN.value)
    enterprise_activity: str = Field(default=EnterpriseActivity.UNKNOWN.value)
    # Tri-state on the wire as well as in the column. A client that omits this
    # is saying "not established", which is not the same as sending false.
    written_agreement: Optional[bool] = Field(default=None)
    agreed_days: Optional[int] = Field(default=None, ge=0, le=365)
    evidence: str = Field(default=MsmeEvidence.NONE.value)
    udyam_number: Optional[str] = Field(default=None, max_length=32)
    effective_from: Optional[date] = Field(default=None)
    note: Optional[str] = Field(default=None, max_length=512)


@router.put("/msme-status", status_code=status.HTTP_200_OK)
def set_msme_status(body: MsmeStatusIn,
                    principal: Principal = Depends(require_manager_or_owner),
                    session: Session = Depends(get_session)) -> dict:
    """Record what was established about a supplier's MSME position.

    An upsert on the supplier: a status gets corrected and re-confirmed, and a
    second row would make "their classification" a question about which row
    won.

    Zoho's ``payment_terms`` is untouched and unconsulted. Whether a *written*
    agreement exists is a separate fact from what the ERP's dropdown holds, and
    conflating them is what would push a supplier with no contract from the
    fifteen-day limit to the forty-five-day one.
    """
    org = principal.organization_id
    vendor = session.get(models.Vendor, body.vendor_id)
    if vendor is None or vendor.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such supplier")
    try:
        checked = msme.validate_status(
            classification=body.classification, activity=body.enterprise_activity,
            written_agreement=body.written_agreement, agreed_days=body.agreed_days,
            evidence=body.evidence)
    except msme.InvalidStatus as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

    row = session.scalar(
        select(models.VendorMsmeStatus).where(
            models.VendorMsmeStatus.organization_id == org,
            models.VendorMsmeStatus.vendor_id == body.vendor_id))
    if row is None:
        row = models.VendorMsmeStatus(organization_id=org, vendor_id=body.vendor_id)
        session.add(row)
    row.classification = checked.classification
    row.enterprise_activity = checked.activity
    row.written_agreement = checked.written_agreement
    row.agreed_days = checked.agreed_days
    row.evidence = checked.evidence
    row.udyam_number = body.udyam_number
    row.effective_from = body.effective_from
    row.note = body.note
    row.captured_at = clock.now()
    row.set_by_user_id = principal.user_id
    session.flush()

    return {"vendor_id": row.vendor_id, "classification": row.classification,
            "enterprise_activity": row.enterprise_activity,
            "written_agreement": row.written_agreement,
            "agreed_days": row.agreed_days, "evidence": row.evidence,
            "udyam_number": row.udyam_number, "scope": checked.scope,
            "scope_label": msme.SCOPE_LABELS[checked.scope],
            "zoho_terms_days": vendor.payment_terms_days}


@router.get("/msme-capture-backlog")
def msme_capture_backlog(principal: Principal = Depends(require_manager_or_owner),
                         session: Session = Depends(get_session)) -> dict:
    """Which suppliers are worth establishing an MSME status for, in order.

    The watchlist is only as good as its coverage, and coverage is collected by
    a person one supplier at a time. "Go and check four hundred vendors" is
    advice nobody takes, so this ranks the question by what knowing the answer
    is worth: spend, weighted by how often that supplier is already paid past
    the limit.
    """
    org, _snapshot, th = _labels_only(session, principal)
    as_of = clock.today(th.timezone)
    limit_days = int(th.msme_default_days)

    spend: dict[str, list[float]] = {}
    for bill in session.scalars(
            select(models.BillDoc).where(
                models.BillDoc.organization_id == org,
                models.BillDoc.vendor_id.is_not(None))).all():
        bucket = spend.setdefault(bill.vendor_id, [0.0, 0.0])
        bucket[0] += float(bill.total or 0)
        bucket[1] += 1

    settled: dict[str, list[int]] = {}
    for row in session.scalars(
            select(models.BillPaymentApplication).where(
                models.BillPaymentApplication.organization_id == org,
                models.BillPaymentApplication.vendor_id.is_not(None))).all():
        counts = settled.setdefault(row.vendor_id, [0, 0])
        counts[1] += 1
        if (row.paid_on - row.bill_date).days > limit_days:
            counts[0] += 1

    spends = [
        msme.VendorSpend(vendor_id=vendor_id, spend=totals[0], bills=int(totals[1]),
                         settled_past_limit=settled.get(vendor_id, [0, 0])[0],
                         settled_total=settled.get(vendor_id, [0, 0])[1])
        for vendor_id, totals in spend.items()
    ]
    built = msme.capture_backlog(spends, _msme_statuses(session, org),
                                 _vendor_names(session, org))
    Companies(session, org).stamp(built["suppliers"],
                                  index_of(session, org, models.Vendor),
                                  by="vendor_id")
    return _envelope(
        {"as_of": as_of.isoformat(), "limit_days": limit_days, **built},
        th=th,
        empty_reason=(None if built["suppliers"] else
                      "Every supplier with purchase history already has an MSME "
                      "status on record."))


@router.get("/withholding-crossings")
def withholding_crossings(principal: Principal = Depends(require_manager_or_owner),
                          session: Session = Depends(get_session)) -> dict:
    """Suppliers crossing the section 194Q threshold this financial year.

    Silent until the organization's own turnover gate is confirmed in Settings
    — that figure spans three legal entities and is not derivable from a
    two-year window of synced documents, and an alert built on an unverified
    gate asserts a duty nobody established applies.
    """
    org, _snapshot, th = _labels_only(session, principal)
    as_of = clock.today(th.timezone)

    purchases = [
        withholding.Purchase(vendor_id=b.vendor_id, date=b.date,
                             amount=float(b.total or 0))
        for b in session.scalars(
            select(models.BillDoc).where(
                models.BillDoc.organization_id == org,
                models.BillDoc.vendor_id.is_not(None))).all()
    ]
    built = withholding.crossings(purchases, _vendor_names(session, org),
                                  as_of=as_of, th=th)
    return _envelope(
        built, th=th,
        empty_reason=(None if built["crossings"] else
                      ("Confirm this entity's prior-year turnover in Settings to "
                       "enable this check."
                       if not built["gate_confirmed"] else
                       "No supplier is near the threshold this financial year.")))
