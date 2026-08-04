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
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from .base import CostRow, SaleRow, Snapshot
from .config import SignalThresholds


# ── DB loader ────────────────────────────────────────────────────────────────
def load_snapshot(session: Session, organization_id: str) -> Snapshot:
    sales = [
        SaleRow(customer_id=t.customer_id, product_id=t.product_id, date=t.date,
                qty=Decimal(t.qty), unit_price=Decimal(t.unit_price),
                line_revenue=Decimal(t.line_revenue), source_ref=t.source_ref or {},
                external_ref=t.external_ref,
                rate=(Decimal(t.rate) if t.rate is not None else None),
                discount_percent=(Decimal(t.discount_percent)
                                  if t.discount_percent is not None else None))
        for t in session.scalars(
            select(models.SalesTxn).where(models.SalesTxn.organization_id == organization_id))
    ]
    costs = [
        CostRow(product_id=c.product_id, date=c.date, qty=Decimal(c.qty),
                unit_cost=Decimal(c.unit_cost), source_ref=c.source_ref or {},
                external_ref=c.external_ref)
        for c in session.scalars(
            select(models.CostRecord).where(models.CostRecord.organization_id == organization_id))
    ]
    customer_names = {
        c.customer_id: c.name for c in session.scalars(
            select(models.Customer).where(models.Customer.organization_id == organization_id))
    }
    product_names = {
        p.product_id: p.name for p in session.scalars(
            select(models.Product).where(models.Product.organization_id == organization_id))
    }
    return Snapshot(organization_id=organization_id, sales=sales, costs=costs,
                    customer_names=customer_names, product_names=product_names)


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


def by_customer(sales: Iterable[SaleRow]) -> dict[str, list[SaleRow]]:
    """Group lines by customer, preserving order.

    Three insight modules each had their own identical copy of this. Grouping is
    not a decision any of them owns, and three copies is three places a change
    to what "a customer" means — a merged identity, say — would have to land.
    """
    out: dict[str, list[SaleRow]] = {}
    for row in sales:
        out.setdefault(row.customer_id, []).append(row)
    return out


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
