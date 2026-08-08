"""Unit economics for one invoice line, and correct aggregation of many.

Pure and Decimal throughout — money is never computed in binary floating point.
Every downstream analysis reads these values and only these values, so there is
exactly one definition of "what did this line earn".

The two rules that matter most:

**Missing cost is never invented.** A line whose product has no applicable cost
record carries revenue and quantity but ``None`` for cost, COGS, gross profit
and margin. It is counted as uncovered rather than quietly costed at zero,
which would report a 100% margin on the platform's own ignorance.

**Aggregated margin is total gross profit ÷ total revenue** — never the
arithmetic mean of per-line margin percentages. A ₹4,00,000 line at 20% and a
₹1,000 line at 60% is a 20.1% relationship, not 40%.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from ..signals.base import CostRow, SaleRow

_ZERO = Decimal("0")


@dataclass(frozen=True)
class LineEconomics:
    """One invoice line, fully costed — or honestly marked as uncostable."""

    date: date
    customer_id: str
    product_id: str
    external_ref: str                    # invoice_id:line_id
    invoice_id: Optional[str]
    qty: Decimal
    rate: Optional[Decimal]              # pre-discount list price (audit)
    discount_percent: Optional[Decimal]  # audit
    net_unit_price: Decimal              # what the customer actually paid, per unit
    revenue: Decimal                     # pre-tax, post-discount
    effective_unit_cost: Optional[Decimal]
    cogs: Optional[Decimal]
    gross_profit: Optional[Decimal]
    gross_margin: Optional[float]        # a ratio (0.261), not a percentage (26.1)
    cost_source_ref: Optional[dict[str, Any]]
    # The sale line's own provenance, carried alongside the cost's. Both are
    # needed because a costed line cites two records from two documents, and
    # after a second connector they need not have come from the same system.
    # Defaulted so existing constructions stay valid; every real one sets it.
    source_ref: Optional[dict[str, Any]] = None

    @property
    def has_cost(self) -> bool:
        return self.effective_unit_cost is not None


def cost_basis_asof(costs: list[CostRow], as_of: date) -> Optional[CostRow]:
    """The applicable cost at a date: the latest cost record on or before it.

    Deliberately the same rule the Signal Engine already uses
    (``signals.aggregates.cost_basis_asof``) — there must be exactly one answer
    to "what did this item cost us then", or two screens will disagree.
    ``costs`` must be date-ascending.
    """
    applicable = [c for c in costs if c.date <= as_of]
    return applicable[-1] if applicable else None


def line_economics(sale: SaleRow, costs: list[CostRow]) -> LineEconomics:
    """Normalize one sale line into full unit economics.

    ``costs`` is that product's cost history, date-ascending. Cost is resolved
    as of the sale date, so a line is costed at what the item actually cost when
    it was sold, not at today's price.
    """
    qty = Decimal(sale.qty)
    revenue = Decimal(sale.line_revenue)
    net_unit_price = Decimal(sale.unit_price)

    basis = cost_basis_asof(costs, sale.date)
    unit_cost = Decimal(basis.unit_cost) if basis is not None else None

    # A zero or negative cost is a placeholder, not a real purchase price —
    # treating it as real would report a 100% margin. Withhold instead.
    if unit_cost is not None and unit_cost <= _ZERO:
        unit_cost = None
        basis = None

    cogs = (unit_cost * qty) if unit_cost is not None else None
    gross_profit = (revenue - cogs) if cogs is not None else None
    margin = (float(gross_profit / revenue)
              if gross_profit is not None and revenue > _ZERO else None)

    ref = sale.source_ref or {}
    return LineEconomics(
        date=sale.date,
        customer_id=sale.customer_id,
        product_id=sale.product_id,
        external_ref=sale.external_ref,
        invoice_id=ref.get("record_id"),
        qty=qty,
        rate=(Decimal(sale.rate) if sale.rate is not None else None),
        discount_percent=(Decimal(sale.discount_percent)
                          if sale.discount_percent is not None else None),
        net_unit_price=net_unit_price,
        revenue=revenue,
        effective_unit_cost=unit_cost,
        cogs=cogs,
        gross_profit=gross_profit,
        gross_margin=margin,
        cost_source_ref=(basis.source_ref if basis is not None else None),
        source_ref=ref,
    )


@dataclass(frozen=True)
class PeriodEconomics:
    """Many lines aggregated over a period. Margin is GP ÷ revenue."""

    revenue: Decimal
    cogs: Optional[Decimal]
    gross_profit: Optional[Decimal]
    margin: Optional[float]
    qty: Decimal
    txn_count: int
    costed_txn_count: int

    @property
    def is_empty(self) -> bool:
        return self.txn_count == 0


def aggregate(lines: Iterable[LineEconomics]) -> PeriodEconomics:
    """Roll lines up into one period.

    Revenue and quantity count every line. Margin is computed **only from the
    costed subset** — including an uncostable line's revenue with no COGS would
    silently inflate the margin toward 100%. The two counts are both reported so
    a caller can see how much of the period the margin actually speaks for.
    """
    rows = list(lines)
    revenue = sum((r.revenue for r in rows), _ZERO)
    qty = sum((r.qty for r in rows), _ZERO)

    costed = [r for r in rows if r.has_cost]
    if costed:
        costed_revenue = sum((r.revenue for r in costed), _ZERO)
        cogs = sum((r.cogs for r in costed), _ZERO)
        gross_profit = costed_revenue - cogs
        margin = float(gross_profit / costed_revenue) if costed_revenue > _ZERO else None
    else:
        cogs = gross_profit = margin = None

    return PeriodEconomics(
        revenue=revenue, cogs=cogs, gross_profit=gross_profit, margin=margin,
        qty=qty, txn_count=len(rows), costed_txn_count=len(costed),
    )


def in_window(lines: Iterable[LineEconomics], start: Optional[date],
              end: Optional[date]) -> list[LineEconomics]:
    """Lines with ``start < date <= end``.

    Half-open on the left, matching ``signals.aggregates._in_window``, so two
    adjacent windows partition a range without double-counting a boundary line.
    """
    return [
        ln for ln in lines
        if (start is None or ln.date > start) and (end is None or ln.date <= end)
    ]
