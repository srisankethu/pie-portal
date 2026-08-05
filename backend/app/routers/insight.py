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
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal, require_manager_or_owner
from .. import clock
from ..commercial import floor, incentive, policy
from ..commercial.insight import (cadence, cohorts, composition, flow, landscape,
                                  payments, periods, radar, simulate, stock, story,
                                  supply, weather)
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..signals.aggregates import label_for, load_snapshot
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
    org, snapshot, th = _context(session, principal)
    _require_visible_customer(session, org, customer_id, principal)
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


# ── Tier 3: the shelf, the suppliers and the cash ───────────────────────────
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
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot) or date.today()

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
    return _envelope(
        result, currency=th.currency,
        empty_reason=(None if result["customers"] else
                      "Payments have synced, but none of them is applied to an "
                      "invoice yet — so there is no invoice date to measure "
                      "from. Advances are counted separately above."))


@router.get("/stock")
def stock_position(principal: Principal = Depends(current_principal),
                   session: Session = Depends(get_session)) -> dict:
    """What is on the shelf, and which of it is a problem."""
    org, snapshot, th = _context(session, principal)
    as_of = _as_of(snapshot) or date.today()
    with_cost = principal.role in (Role.SALES_MANAGER, Role.OWNER)

    # The most recent snapshot per item. Zoho reports stock as a current
    # number, so "latest" is the only meaningful reading; the older rows are
    # history for a trend, not alternatives to choose between.
    latest: dict[str, models.StockSnapshot] = {}
    for row in session.scalars(
            select(models.StockSnapshot)
            .where(models.StockSnapshot.organization_id == org)
            .order_by(models.StockSnapshot.as_of)).all():
        latest[row.product_id] = row
    if not latest:
        return _no_data(th.currency, "stock")

    sold_qty: dict[str, float] = {}
    last_sold: dict[str, date] = {}
    for sale in snapshot.sales:
        sold_qty[sale.product_id] = sold_qty.get(sale.product_id, 0.0) + float(sale.qty)
        if sale.date > last_sold.get(sale.product_id, date.min):
            last_sold[sale.product_id] = sale.date

    lines = [
        stock.StockLine(
            product_id=pid,
            label=label_for(snapshot.product_names, pid, kind="item"),
            on_hand=float(row.on_hand or 0),
            available=(float(row.available) if row.available is not None else None),
            actual_available=(float(row.actual_available)
                              if row.actual_available is not None else None),
            reorder_level=(float(row.reorder_level)
                           if row.reorder_level is not None else None),
            last_sold=last_sold.get(pid),
            sold_qty_window=sold_qty.get(pid, 0.0),
            purchase_rate=(float(row.purchase_rate)
                           if row.purchase_rate is not None else None),
        )
        for pid, row in latest.items()
        # A service has no shelf; counting it as zero on hand would put the
        # whole service catalogue in the out-of-stock list forever.
        if row.tracked
    ]
    result = stock.build(lines, as_of, with_cost=with_cost)
    if not with_cost:
        result["unavailable"].append(
            {"series": "stock_value", "reason": "Stock value is management information."})
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
    org, _snapshot, th = _context(session, principal)
    as_of = date.today()

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
    return _envelope(result, currency=th.currency, empty_reason=None)


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
    org, snapshot, th = _context(session, principal)
    customer = _require_visible_customer(session, org, body.customer_id, principal)
    with_cost = principal.role in (Role.SALES_MANAGER, Role.OWNER)
    as_of = clock.now().date()

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
