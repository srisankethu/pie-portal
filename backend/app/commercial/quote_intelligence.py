"""Quote intelligence for one line: Customer × Item × Quantity × Time.

This is the same deterministic engine the Customer × Item analysis screen uses,
pointed at the moment a salesperson is actually deciding a price. Nothing here
is a second implementation of that analysis: ``compute_relationship``,
``compute_benchmark``, ``line_economics`` and ``cost_basis_asof`` are the same
functions, called with the same thresholds, so the quote screen and the analysis
screen cannot disagree about a margin.

What this module adds is the three things a quote needs that an account review
does not:

- **quantity** — a price reference is meaningless without the size it applies to;
- **a proposed price** — the analysis screen reads history, a quote proposes a
  future, and the exception rules test that proposal;
- **a moment** — cost is resolved as of the quote date, not as of the last sale.

Pure. No database, no network, no model. The DB seam is ``quote_service.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ..domain.enums import EvidenceSufficiency
from ..signals.base import CostRow
from .benchmark import ItemBenchmark
from .config import CommercialThresholds
from .economics import LineEconomics, cost_basis_asof
from .metrics import RelationshipMetrics
from .quantity import QuantityBand, band_for
from .quote_exceptions import CRITICAL, QuoteException, evaluate
from .references import PriceReference, build_references

_ZERO = Decimal("0")


@dataclass(frozen=True)
class QuoteLineEconomics:
    """What this line earns at the proposed price. Entirely RESTRICTED."""

    unit_cost: Optional[Decimal]
    quoted_unit_price: Optional[Decimal]
    qty: Decimal
    line_revenue: Optional[Decimal]
    cogs: Optional[Decimal]
    gross_profit: Optional[Decimal]
    margin: Optional[float]
    cost_source_ref: Optional[dict] = None

    def to_dict(self) -> dict:
        def m(v):
            return float(round(v, 2)) if v is not None else None
        return {
            "unit_cost": m(self.unit_cost),
            "quoted_unit_price": m(self.quoted_unit_price),
            "qty": float(self.qty),
            "line_revenue": m(self.line_revenue),
            "cogs": m(self.cogs),
            "gross_profit": m(self.gross_profit),
            "margin": round(self.margin, 4) if self.margin is not None else None,
            "cost_source_ref": self.cost_source_ref,
        }


@dataclass
class QuoteLineIntelligence:
    """Everything deterministic that is known about one quote line."""

    line_id: str
    customer_id: Optional[str]
    product_id: Optional[str]
    qty: Decimal
    band: QuantityBand
    as_of: date
    references: list[PriceReference] = field(default_factory=list)
    economics: Optional[QuoteLineEconomics] = None
    exceptions: list[QuoteException] = field(default_factory=list)
    metrics: Optional[RelationshipMetrics] = None
    benchmark: Optional[ItemBenchmark] = None
    data_sufficiency: EvidenceSufficiency = EvidenceSufficiency.INSUFFICIENT
    sufficiency_reasons: list[str] = field(default_factory=list)
    transaction_count: int = 0
    thresholds_version: str = ""

    @property
    def requires_approval(self) -> bool:
        return any(e.requires_approval for e in self.exceptions)

    @property
    def worst_severity(self) -> Optional[str]:
        return self.exceptions[0].severity if self.exceptions else None

    @property
    def blocking(self) -> bool:
        return any(e.severity == CRITICAL for e in self.exceptions)


def assess_line(
    *,
    line_id: str,
    customer_id: Optional[str],
    product_id: Optional[str],
    qty: Decimal,
    proposed_price: Optional[Decimal],
    lines: list[LineEconomics],
    costs: list[CostRow],
    metrics: Optional[RelationshipMetrics],
    benchmark: Optional[ItemBenchmark],
    family: Optional[str],
    as_of: date,
    th: CommercialThresholds,
) -> QuoteLineIntelligence:
    """Assess one quote line. Pure, total, and deterministic.

    ``lines`` is this customer's costed history for this item (may be empty —
    a first-time item is a normal case, not an error). ``costs`` is the item's
    cost history, date-ascending; the applicable cost is resolved **as of the
    quote date**, which is the whole reason a quote cannot reuse the analysis
    screen's "current cost" without re-deriving it.
    """
    band = band_for(qty, th)

    basis = cost_basis_asof(costs, as_of)
    unit_cost = Decimal(basis.unit_cost) if basis is not None else None
    # A zero or negative cost is a placeholder, not a purchase price — the same
    # rule ``line_economics`` applies, for the same reason.
    if unit_cost is not None and unit_cost <= _ZERO:
        unit_cost, basis = None, None

    references = build_references(
        lines=lines, band=band, unit_cost=unit_cost, benchmark=benchmark,
        historical_margin=(metrics.historical_margin if metrics else None),
        family=family, as_of=as_of, th=th)

    economics = _economics(unit_cost, proposed_price, qty, basis)

    exceptions = evaluate(
        proposed_price=proposed_price, qty=qty, unit_cost=unit_cost,
        references=references, metrics=metrics, benchmark=benchmark, th=th)

    sufficiency = (metrics.data_sufficiency if metrics
                   else EvidenceSufficiency.INSUFFICIENT)
    reasons = list(metrics.sufficiency_reasons) if metrics else ["no transactions"]

    return QuoteLineIntelligence(
        line_id=line_id, customer_id=customer_id, product_id=product_id,
        qty=qty, band=band, as_of=as_of,
        references=references, economics=economics, exceptions=exceptions,
        metrics=metrics, benchmark=benchmark,
        data_sufficiency=sufficiency, sufficiency_reasons=reasons,
        transaction_count=(metrics.transaction_count if metrics else 0),
        thresholds_version=th.version)


def _economics(unit_cost: Optional[Decimal], price: Optional[Decimal],
               qty: Decimal, basis) -> QuoteLineEconomics:
    """Line economics at the proposed price. Missing cost stays missing."""
    revenue = (price * qty) if price is not None else None
    cogs = (unit_cost * qty) if unit_cost is not None else None
    gross_profit = (revenue - cogs) if (revenue is not None and cogs is not None) else None
    margin = (float(gross_profit / revenue)
              if gross_profit is not None and revenue is not None and revenue > _ZERO
              else None)
    return QuoteLineEconomics(
        unit_cost=unit_cost, quoted_unit_price=price, qty=qty,
        line_revenue=revenue, cogs=cogs, gross_profit=gross_profit, margin=margin,
        cost_source_ref=(basis.source_ref if basis is not None else None))
