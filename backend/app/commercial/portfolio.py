"""Analysis 6 — roll Customer × Item intelligence up to the customer.

Answers exactly one question: *which specific items are causing this customer's
commercial performance to improve or deteriorate?*

Reads the persisted ``CustomerItemMetric`` rows rather than recomputing from
invoice lines, which is the whole reason that table exists — a customer screen
must not scan the organization's entire sales history on every page load.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import EvidenceSufficiency, SignalType
from .config import CommercialThresholds, load_commercial_thresholds

_ZERO = Decimal("0")


@dataclass
class CustomerPortfolio:
    """One customer's commercial position, decomposed by item."""

    customer_id: str
    revenue_12m: Decimal = _ZERO
    gross_profit_12m: Optional[Decimal] = None
    gross_margin_12m: Optional[float] = None
    active_items: int = 0
    items_with_margin_erosion: int = 0
    items_below_peer_benchmark: int = 0
    items_cost_not_passed: int = 0
    items_margin_down_volume_up: int = 0
    material_gap_items: int = 0
    historical_margin_gap: Decimal = _ZERO
    peer_benchmark_gap: Decimal = _ZERO
    items_without_cost: int = 0
    rows: list[models.CustomerItemMetric] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "revenue_12m": _money(self.revenue_12m),
            "gross_profit_12m": _money(self.gross_profit_12m),
            "gross_margin_12m": _pct(self.gross_margin_12m),
            "active_items": self.active_items,
            "items_with_margin_erosion": self.items_with_margin_erosion,
            "items_below_peer_benchmark": self.items_below_peer_benchmark,
            "items_cost_not_passed": self.items_cost_not_passed,
            "items_margin_down_volume_up": self.items_margin_down_volume_up,
            "material_gap_items": self.material_gap_items,
            "historical_margin_gap": _money(self.historical_margin_gap),
            "peer_benchmark_gap": _money(self.peer_benchmark_gap),
            "items_without_cost": self.items_without_cost,
        }


def _money(v) -> Optional[float]:
    return float(round(Decimal(str(v)), 2)) if v is not None else None


def _pct(v: Optional[float]) -> Optional[float]:
    return round(v, 4) if v is not None else None


def load_portfolio(session: Session, org: str, customer_id: str,
                   th: Optional[CommercialThresholds] = None) -> CustomerPortfolio:
    """Aggregate this customer's item metrics into a portfolio view.

    12-month gross margin is total gross profit ÷ total revenue across items —
    the same rule as everywhere else, never a mean of per-item percentages.
    Only revenue whose margin is actually known contributes to the denominator,
    so an item with no cost cannot dilute the figure toward zero.
    """
    th = th or load_commercial_thresholds()
    rows = list(session.scalars(
        select(models.CustomerItemMetric).where(
            models.CustomerItemMetric.organization_id == org,
            models.CustomerItemMetric.customer_id == customer_id,
        )))

    p = CustomerPortfolio(customer_id=customer_id, rows=rows)
    costed_revenue = _ZERO

    for r in rows:
        p.active_items += 1
        revenue = Decimal(r.revenue_12m or 0)
        p.revenue_12m += revenue

        if r.gross_profit_12m is not None:
            p.gross_profit_12m = (p.gross_profit_12m or _ZERO) + Decimal(r.gross_profit_12m)
            costed_revenue += revenue
        if not r.cost_covered_txns:
            p.items_without_cost += 1

        signals = set(r.signals or [])
        if SignalType.CI_MARGIN_EROSION.value in signals:
            p.items_with_margin_erosion += 1
        if SignalType.CI_LOW_PEER_PRICING.value in signals:
            p.items_below_peer_benchmark += 1
        if SignalType.CI_COST_NOT_PASSED.value in signals:
            p.items_cost_not_passed += 1
        if SignalType.CI_MARGIN_DECLINE_WITH_VOLUME.value in signals:
            p.items_margin_down_volume_up += 1
        if SignalType.CI_MATERIAL_MARGIN_GAP.value in signals:
            p.material_gap_items += 1

        if r.historical_margin_gap:
            p.historical_margin_gap += Decimal(r.historical_margin_gap)
        if r.peer_margin_gap:
            p.peer_benchmark_gap += Decimal(r.peer_margin_gap)

    if p.gross_profit_12m is not None and costed_revenue > _ZERO:
        p.gross_margin_12m = float(p.gross_profit_12m / costed_revenue)
    return p


def attention_rank(row: models.CustomerItemMetric) -> tuple:
    """Sort key for "items requiring attention" — economic materiality first.

    A 3 pp slip on ₹40 lakh must outrank a 10 pp collapse on ₹20,000: the first
    is worth someone's afternoon, the second is a rounding error. Percentage
    deterioration only breaks ties between comparable rupee impacts.

    Returned for ascending sort, so every component is negated.
    """
    gap = float(row.historical_margin_gap or 0)
    peer_gap = float(row.peer_margin_gap or 0)
    deterioration = -(row.margin_change_pp or 0.0)
    revenue = float(row.revenue_12m or 0)
    # A relationship the platform cannot stand behind sorts last regardless.
    confident = row.data_sufficiency != EvidenceSufficiency.INSUFFICIENT.value
    return (not confident, -max(gap, peer_gap), -deterioration, -revenue)


def items_requiring_attention(portfolio: CustomerPortfolio,
                              th: Optional[CommercialThresholds] = None
                              ) -> list[models.CustomerItemMetric]:
    """The rows carrying at least one signal, worst rupee impact first."""
    flagged = [r for r in portfolio.rows if r.signals]
    return sorted(flagged, key=attention_rank)
