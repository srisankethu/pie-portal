"""Deterministic aggregates over a snapshot, and the DB → snapshot loader.

All period math is explicit and window-based. An "order" is a distinct source
invoice (``source_ref.record_id``), so multi-line invoices count once for cadence
and order-count floors.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain import models
from .base import CostRow, SaleRow, Snapshot, group_by
from .config import SignalThresholds


# ── DB loader ────────────────────────────────────────────────────────────────
#
# **Columns, not entities.** A snapshot reads nine columns and never writes, so
# it asks for nine columns rather than building an ORM instance per row.
#
# **Only the rows the caller can use.** Bounds default to unbounded. A caller
# that passes one asserts the excluded rows could not change its answer, which
# ``test_bounded_loads.py`` verifies against the unbounded result.
#
# ``last_sale_on`` is deliberately *not* bounded: it is the last day the
# business traded, and every screen hangs its periods off it. Deriving it from
# whatever was loaded would make a quote screen think the business stopped when
# its own customer did.
#
# **No date bound**, though it is the obvious one. Detectors gate on
# ``history_span_months`` and the first order date, and ``cost_pass_through``
# needs the previous cost however old — so cutting history would change which
# signals fire: a speed-up that behaves like a loosened evidence rule (§1 calls
# that a defect). Screens needing whole-book aggregates read the folded state
# rows instead.

_SALE_COLUMNS = (
    models.SalesTxn.customer_id, models.SalesTxn.product_id, models.SalesTxn.date,
    models.SalesTxn.qty, models.SalesTxn.unit_price, models.SalesTxn.line_revenue,
    models.SalesTxn.source_ref, models.SalesTxn.external_ref,
    models.SalesTxn.rate, models.SalesTxn.discount_percent,
)

_COST_COLUMNS = (
    models.CostRecord.product_id, models.CostRecord.date, models.CostRecord.qty,
    models.CostRecord.unit_cost, models.CostRecord.source_ref,
    models.CostRecord.external_ref,
)


def last_sale_date(session: Session, organization_id: str) -> Optional[date]:
    """The organization's most recent sale date, from an index rather than a scan."""
    return session.scalar(
        select(func.max(models.SalesTxn.date))
        .where(models.SalesTxn.organization_id == organization_id))


def load_snapshot(session: Session, organization_id: str, *,
                  sales_for_customers: Optional[Iterable[str]] = None,
                  costs_for_products: Optional[Iterable[str]] = None) -> Snapshot:
    """One organization's sale and cost lines, optionally bounded.

    The two bounds are deliberately separate and each names what it restricts.
    A single ``product_ids`` that narrowed *both* looked tidier and was wrong:
    the quote screen needs every line its customer ever bought — that is what
    their buying rhythm is measured from — while needing costs only for the
    items being quoted. Narrowing the sales by product moved that customer's
    last order date by a week, which is the kind of error a bound makes and a
    reader never sees. The equality test caught it; the naming is what stops it
    coming back.

    Both default to None, meaning everything — the behaviour every existing
    caller had. A bound is only correct when the excluded rows could not have
    changed the answer; callers that pass one say why at the call site, and
    ``test_bounded_loads.py`` runs the real consumer both ways.
    """
    sales_q = select(*_SALE_COLUMNS).where(
        models.SalesTxn.organization_id == organization_id)
    costs_q = select(*_COST_COLUMNS).where(
        models.CostRecord.organization_id == organization_id)
    if sales_for_customers is not None:
        sales_q = sales_q.where(
            models.SalesTxn.customer_id.in_(list(sales_for_customers)))
    if costs_for_products is not None:
        # An empty list is a real bound, not a mistake: it means this caller
        # reads no costs from the snapshot at all.
        costs_q = costs_q.where(
            models.CostRecord.product_id.in_(list(costs_for_products)))

    sales = [
        SaleRow(customer_id=r.customer_id, product_id=r.product_id, date=r.date,
                qty=Decimal(r.qty), unit_price=Decimal(r.unit_price),
                line_revenue=Decimal(r.line_revenue), source_ref=r.source_ref or {},
                external_ref=r.external_ref,
                rate=(Decimal(r.rate) if r.rate is not None else None),
                discount_percent=(Decimal(r.discount_percent)
                                  if r.discount_percent is not None else None))
        for r in session.execute(sales_q)
    ]
    costs = [
        CostRow(product_id=r.product_id, date=r.date, qty=Decimal(r.qty),
                unit_cost=Decimal(r.unit_cost), source_ref=r.source_ref or {},
                external_ref=r.external_ref)
        for r in session.execute(costs_q)
    ]
    customer_names = dict(session.execute(
        select(models.Customer.customer_id, models.Customer.name)
        .where(models.Customer.organization_id == organization_id)).all())
    product_names = dict(session.execute(
        select(models.Product.product_id, models.Product.name)
        .where(models.Product.organization_id == organization_id)).all())
    return Snapshot(organization_id=organization_id, sales=sales, costs=costs,
                    customer_names=customer_names, product_names=product_names,
                    last_sale_on=last_sale_date(session, organization_id))


def label_for(names: dict[str, str], entity_id: str, *, kind: str = "record") -> str:
    """A name for an id, and a readable phrase when there is no name.

    Every caller used to fall back to the id itself — ``names.get(pid, pid)`` —
    which puts ``3452161000001252021`` in the column somebody scans to find
    their account. The id is still carried, because it is what makes the row
    findable when somebody goes to fix it, but it is labelled as an id rather
    than presented as a name.

    A missing name means the master has no such row, which is the same gap the
    sync reports as UNKNOWN_PRODUCT — so this phrasing and that worklist should
    stay recognisable as the same problem.
    """
    name = names.get(entity_id)
    if name:
        return name
    return f"Unnamed {kind} (id {entity_id})"


# ── windows ──────────────────────────────────────────────────────────────────
def recent_window(as_of: date, th: SignalThresholds) -> tuple[date, date]:
    return (as_of - timedelta(days=th.basis_period_days), as_of)


def prior_window(as_of: date, th: SignalThresholds) -> tuple[date, date]:
    end = as_of - timedelta(days=th.basis_period_days)
    return (end - timedelta(days=th.comparison_period_days), end)


def _in_window(d: date, window: tuple[date, date]) -> bool:
    start, end = window
    return start < d <= end


# ── sales aggregates ─────────────────────────────────────────────────────────
def revenue_in(sales: list[SaleRow], window: tuple[date, date]) -> Decimal:
    return sum((s.line_revenue for s in sales if _in_window(s.date, window)), Decimal("0"))


def orders_in(sales: list[SaleRow], window: tuple[date, date]) -> set[str]:
    """Distinct source invoices in the window."""
    return {str(s.source_ref.get("record_id") or s.external_ref)
            for s in sales if _in_window(s.date, window)}


def by_customer(sales: Iterable[Any]) -> dict[str, list[Any]]:
    """Group lines by customer, preserving order.

    Three insight modules each had their own identical copy of this. Grouping is
    not a decision any of them owns, and three copies is three places a change
    to what "a customer" means — a merged identity, say — would have to land.

    Delegates to ``base.group_by``, which is the same grouping ``Snapshot``
    indexes itself with — so a customer's lines are one list here and there.
    """
    return group_by(sales, lambda row: row.customer_id)


def order_dates(sales: list[SaleRow]) -> list[date]:
    """Sorted distinct order dates (one per source invoice)."""
    by_order: dict[str, date] = {}
    for s in sales:
        key = str(s.source_ref.get("record_id") or s.external_ref)
        by_order[key] = s.date
    return sorted(by_order.values())


@dataclass(frozen=True)
class Cadence:
    """How often a customer orders, and whether they are late by their own clock.

    One implementation, because there were three. ``dormancy`` decided whether to
    raise a signal, ``quote_context`` published the same numbers as facts for the
    model, and the buying-rhythm screen described the whole population — each with
    its own copy of "median of the gaps, overdue past a multiple of it". Three
    copies of one rule is three answers waiting to happen, and the quote copy had
    already drifted: it accepted two orders as a rhythm and divided by a zero
    interval, so a customer with two same-day orders was reported overdue on every
    quote forever.

    ``min_orders`` stays a parameter rather than being unified away, because the
    two eligibility bars are a real judgement and not an accident — a quote can
    usefully say "we have seen two orders, roughly N days apart" where a *signal*
    raised off one gap would be noise in somebody's queue.
    """
    order_dates: list[date]
    typical_interval_days: Optional[float]
    expected_interval_days: Optional[float]
    days_since_last: Optional[int]
    #: Gap ÷ expected. Above 1.0 is overdue; None when there is no rhythm to be
    #: late against. Never invent one — an unestimable customer is not "fine".
    overdue_ratio: Optional[float]

    @property
    def estimable(self) -> bool:
        return self.typical_interval_days is not None

    @property
    def overdue(self) -> bool:
        return self.overdue_ratio is not None and self.overdue_ratio > 1.0


def cadence_of(sales: list[SaleRow], as_of: date, *, min_orders: int,
               multiplier: float) -> Cadence:
    """Median gap between a customer's orders, and their position in it."""
    dates = order_dates(sales)
    if not dates:
        return Cadence([], None, None, None, None)

    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    since = (as_of - dates[-1]).days
    # A degenerate cadence — same-day orders only — is not a cadence. Guarding
    # here rather than at each call site is the point of having one function.
    typical = statistics.median(gaps) if gaps else 0.0
    if len(dates) < min_orders or not gaps or typical <= 0:
        return Cadence(dates, None, None, since, None)

    expected = typical * multiplier
    return Cadence(
        order_dates=dates,
        typical_interval_days=round(typical, 1),
        expected_interval_days=round(expected, 1),
        days_since_last=since,
        overdue_ratio=round(since / expected, 2) if expected else None,
    )


def history_span_months(sales: list[SaleRow], as_of: date) -> float:
    if not sales:
        return 0.0
    first = min(s.date for s in sales)
    return max(0.0, (as_of - first).days / 30.44)


def top_products_by_revenue_change(
    sales: list[SaleRow], recent: tuple[date, date], prior: tuple[date, date],
    names: dict[str, str], limit: int = 3,
) -> list[dict]:
    """Products with the largest revenue drop recent-vs-prior (labels + magnitude)."""
    prods = {s.product_id for s in sales}
    rows = []
    for pid in prods:
        ps = [s for s in sales if s.product_id == pid]
        r = revenue_in(ps, recent)
        p = revenue_in(ps, prior)
        rows.append({"product_id": pid, "label": names.get(pid, pid),
                     "recent_revenue": float(round(r, 2)), "baseline_revenue": float(round(p, 2)),
                     "change": float(round(r - p, 2))})
    rows.sort(key=lambda x: x["change"])  # most negative first
    return rows[:limit]


# ── cost aggregates ──────────────────────────────────────────────────────────
def prior_cost_basis(costs: list[CostRow]) -> Optional[CostRow]:
    """The most recent cost strictly before the latest cost record."""
    return costs[-2] if len(costs) >= 2 else None


def avg_unit_price(sales: list[SaleRow], window: tuple[date, date]) -> Optional[Decimal]:
    """Quantity-weighted average unit price in the window (revenue / qty)."""
    return avg_unit_price_range(sales, window[0], window[1])


def avg_unit_price_range(sales: list[SaleRow], start: Optional[date],
                         end: Optional[date]) -> Optional[Decimal]:
    """Quantity-weighted average unit price for start < date ≤ end (open bounds allowed)."""
    rev = Decimal("0")
    qty = Decimal("0")
    for s in sales:
        if (start is None or s.date > start) and (end is None or s.date <= end):
            rev += s.line_revenue
            qty += s.qty
    if qty <= 0:
        return None
    return rev / qty


def cost_basis_asof(costs: list[CostRow], as_of: date) -> Optional[CostRow]:
    """The applicable cost basis at a date: latest cost record with date ≤ as_of."""
    applicable = [c for c in costs if c.date <= as_of]
    return applicable[-1] if applicable else None
