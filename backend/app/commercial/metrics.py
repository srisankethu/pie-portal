"""Customer × Item metrics: margin history, cost-vs-price, volume, gaps.

Pure functions over a relationship's costed lines. Analyses 1, 2, 4 and 5 of the
six live here; the same-item peer benchmark (3) is in ``benchmark.py`` because
it needs other customers' lines, and the customer portfolio (6) is in
``portfolio.py`` because it aggregates across relationships.

Two conventions hold everywhere:

- A **margin** is a ratio (0.261), never a percentage (26.1). It becomes a
  percentage only at the presentation edge.
- A margin *movement* is in **percentage points** (``_pp``). The percentage
  change of a percentage is never computed, because it is almost always misread.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..domain.enums import EvidenceSufficiency
from .config import CommercialThresholds
from .economics import LineEconomics, PeriodEconomics, aggregate, in_window

_ZERO = Decimal("0")
_DAYS_PER_MONTH = Decimal("30.44")

# How cost and price moved together — Analysis 2. Deterministic, from the two
# measured changes alone.
COST_DRIVEN = "COST_DRIVEN"      # cost rose, price did not follow
PRICE_DRIVEN = "PRICE_DRIVEN"    # cost stable, price fell
MIXED = "MIXED"                  # cost rose *and* price fell
NONE = "NONE"                    # neither moved materially


@dataclass
class RelationshipMetrics:
    """Everything computable about one customer's relationship with one item."""

    customer_id: str
    product_id: str

    # activity
    first_transaction_date: Optional[date] = None
    last_transaction_date: Optional[date] = None
    transaction_count: int = 0
    history_months: float = 0.0

    # periods (each is GP ÷ revenue over that window)
    recent: Optional[PeriodEconomics] = None
    previous: Optional[PeriodEconomics] = None
    current_margin: Optional[float] = None
    previous_margin: Optional[float] = None
    margin_3m: Optional[float] = None
    margin_6m: Optional[float] = None
    margin_12m: Optional[float] = None
    historical_margin: Optional[float] = None
    revenue_recent: Decimal = _ZERO
    revenue_12m: Decimal = _ZERO
    gross_profit_recent: Optional[Decimal] = None
    gross_profit_12m: Optional[Decimal] = None

    # latest observed position
    current_sell_price: Optional[Decimal] = None
    current_effective_cost: Optional[Decimal] = None

    # movement
    margin_change_pp: Optional[float] = None
    price_change_pct: Optional[float] = None
    cost_change_pct: Optional[float] = None
    erosion_kind: str = NONE

    # volume
    qty_recent: Decimal = _ZERO
    qty_previous: Decimal = _ZERO
    volume_change_pct: Optional[float] = None

    # impact (estimates — never "lost profit")
    historical_margin_gap: Optional[Decimal] = None
    annualized_historical_margin_gap: Optional[Decimal] = None

    # data quality
    cost_covered_txns: int = 0
    cost_missing_txns: int = 0
    data_sufficiency: EvidenceSufficiency = EvidenceSufficiency.INSUFFICIENT
    sufficiency_reasons: list[str] = field(default_factory=list)

    @property
    def has_margin(self) -> bool:
        return self.current_margin is not None


def _weighted_price(lines: list[LineEconomics]) -> Optional[Decimal]:
    """Quantity-weighted net selling price. Weighted, not a plain mean, so a
    one-unit sample cannot outvote a thousand-unit one."""
    qty = sum((ln.qty for ln in lines), _ZERO)
    if qty <= _ZERO:
        return None
    return sum((ln.revenue for ln in lines), _ZERO) / qty


def _weighted_cost(lines: list[LineEconomics]) -> Optional[Decimal]:
    """Quantity-weighted effective unit cost over the costed lines only."""
    costed = [ln for ln in lines if ln.has_cost]
    qty = sum((ln.qty for ln in costed), _ZERO)
    if qty <= _ZERO:
        return None
    return sum((ln.cogs for ln in costed), _ZERO) / qty


def _pct_change(new: Optional[Decimal], old: Optional[Decimal]) -> Optional[float]:
    if new is None or old is None or old <= _ZERO:
        return None
    return float((new - old) / old)


def classify_erosion(cost_change_pct: Optional[float], price_change_pct: Optional[float],
                     th: CommercialThresholds) -> str:
    """Analysis 2 — why margin moved, from the two measured changes alone.

    Deterministic and total: every pair of inputs maps to exactly one label.

    - Cost rose materially, price did not keep up  → COST_DRIVEN
    - Cost roughly stable, price fell materially   → PRICE_DRIVEN
    - Cost rose materially *and* price fell        → MIXED
    - Neither moved materially                     → NONE
    """
    if cost_change_pct is None or price_change_pct is None:
        return NONE

    cost_up = cost_change_pct >= th.meaningful_cost_increase_pct
    price_down = price_change_pct <= -th.meaningful_price_change_pct
    # "Kept up" means the price rose by at least as much as the cost did.
    price_kept_up = price_change_pct >= cost_change_pct

    if cost_up and price_down:
        return MIXED
    if cost_up and not price_kept_up:
        return COST_DRIVEN
    if not cost_up and price_down:
        return PRICE_DRIVEN
    return NONE


def _sufficiency(txn_count: int, history_months: float, cost_coverage: float,
                 th: CommercialThresholds) -> tuple[EvidenceSufficiency, list[str]]:
    """Reuses the platform's existing three-level evidence enum rather than
    inventing a parallel scale. Weak data must not produce confident output."""
    reasons: list[str] = []

    if txn_count < th.min_transactions:
        reasons.append(
            f"only {txn_count} transaction{'' if txn_count == 1 else 's'} "
            f"(need {th.min_transactions})")
    if history_months < th.min_history_months:
        # Rounded DOWN: 2.96 shown as "3.0 months (need 3)" reads as a
        # contradiction, and a reason a reader distrusts is worse than none.
        reasons.append(
            f"only {_floor_1dp(history_months)} months of history "
            f"(need {th.min_history_months:.0f})")
    if cost_coverage < th.min_cost_coverage:
        reasons.append(f"cost known for only {cost_coverage:.0%} of transactions")

    if reasons:
        return EvidenceSufficiency.INSUFFICIENT, reasons

    if (txn_count >= th.min_transactions_strong
            and history_months >= th.min_history_months_strong):
        return EvidenceSufficiency.SUFFICIENT, []

    if txn_count < th.min_transactions_strong:
        reasons.append(f"{txn_count} transactions — enough to show, not to be sure")
    if history_months < th.min_history_months_strong:
        reasons.append(f"{_floor_1dp(history_months)} months of history")
    return EvidenceSufficiency.PARTIAL, reasons


def _floor_1dp(value: float) -> str:
    """One decimal place, rounded down — a displayed figure must never appear
    to clear a threshold it is being reported as failing."""
    return f"{math.floor(value * 10) / 10:.1f}"


def compute_relationship(customer_id: str, product_id: str,
                         lines: list[LineEconomics], as_of: date,
                         th: CommercialThresholds) -> RelationshipMetrics:
    """Analyses 1, 2, 4 and 5 for one Customer × Item relationship.

    ``lines`` is every costed line for this pair, any order. ``as_of`` anchors
    every window so two relationships computed in the same run are comparable.
    """
    m = RelationshipMetrics(customer_id=customer_id, product_id=product_id)
    if not lines:
        m.sufficiency_reasons = ["no transactions"]
        return m

    ordered = sorted(lines, key=lambda ln: ln.date)
    m.first_transaction_date = ordered[0].date
    m.last_transaction_date = ordered[-1].date
    m.transaction_count = len(ordered)
    m.history_months = float(
        Decimal((m.last_transaction_date - m.first_transaction_date).days) / _DAYS_PER_MONTH)
    m.cost_covered_txns = sum(1 for ln in ordered if ln.has_cost)
    m.cost_missing_txns = m.transaction_count - m.cost_covered_txns

    # ── windows ─────────────────────────────────────────────────────────────
    recent_start = as_of - timedelta(days=th.recent_days)
    previous_start = recent_start - timedelta(days=th.previous_days)
    historical_start = as_of - timedelta(days=th.historical_lookback_days)

    recent_lines = in_window(ordered, recent_start, as_of)
    previous_lines = in_window(ordered, previous_start, recent_start)

    m.recent = aggregate(recent_lines)
    m.previous = aggregate(previous_lines)
    m.current_margin = m.recent.margin
    m.previous_margin = m.previous.margin
    m.revenue_recent = m.recent.revenue
    m.gross_profit_recent = m.recent.gross_profit
    m.qty_recent = m.recent.qty
    m.qty_previous = m.previous.qty

    # ── Analysis 1: margin over standard periods ────────────────────────────
    for months, attr in ((3, "margin_3m"), (6, "margin_6m"), (12, "margin_12m")):
        window = aggregate(in_window(ordered, as_of - timedelta(days=months * 30), as_of))
        setattr(m, attr, window.margin)
        if months == 12:
            m.revenue_12m = window.revenue
            m.gross_profit_12m = window.gross_profit

    # The relationship's own baseline: everything in the lookback *before* the
    # recent window. Comparing recent against a baseline that contains recent
    # would dilute exactly the move being measured.
    historical = aggregate(in_window(ordered, historical_start, recent_start))
    m.historical_margin = historical.margin

    if m.current_margin is not None and m.historical_margin is not None:
        m.margin_change_pp = m.current_margin - m.historical_margin

    # ── Analysis 2: cost vs price ───────────────────────────────────────────
    m.current_sell_price = _weighted_price(recent_lines) if recent_lines else None
    m.current_effective_cost = _weighted_cost(recent_lines) if recent_lines else None
    if m.current_sell_price is None and ordered:
        # No recent activity: fall back to the last observed line so the UI can
        # still show a last-known position rather than an empty cell.
        m.current_sell_price = ordered[-1].net_unit_price
        m.current_effective_cost = ordered[-1].effective_unit_cost

    baseline_lines = in_window(ordered, historical_start, recent_start)
    m.price_change_pct = _pct_change(m.current_sell_price, _weighted_price(baseline_lines))
    m.cost_change_pct = _pct_change(m.current_effective_cost, _weighted_cost(baseline_lines))
    m.erosion_kind = classify_erosion(m.cost_change_pct, m.price_change_pct, th)

    # ── Analysis 5: volume ──────────────────────────────────────────────────
    if m.qty_previous > _ZERO:
        m.volume_change_pct = float((m.qty_recent - m.qty_previous) / m.qty_previous)

    # ── Analysis 4: historical margin gap ───────────────────────────────────
    # What this relationship would have earned on *today's* revenue at the
    # margin it used to hold. An estimate framing a review, not lost profit.
    if (m.margin_change_pp is not None and m.margin_change_pp < 0
            and m.revenue_recent > _ZERO):
        m.historical_margin_gap = m.revenue_recent * Decimal(str(-m.margin_change_pp))
        m.annualized_historical_margin_gap = _annualize(
            m.historical_margin_gap, m, th)

    coverage = (m.cost_covered_txns / m.transaction_count) if m.transaction_count else 0.0
    m.data_sufficiency, m.sufficiency_reasons = _sufficiency(
        m.transaction_count, m.history_months, coverage, th)
    return m


def _annualize(window_gap: Decimal, m: RelationshipMetrics,
               th: CommercialThresholds) -> Optional[Decimal]:
    """Scale a recent-window gap to a year — only where that is honest.

    A yearly figure extrapolated from six weeks of trading is a guess wearing a
    suit, so both span and transaction count must clear their floors first.
    """
    if (m.history_months < th.annualize_min_history_months
            or m.transaction_count < th.annualize_min_transactions):
        return None
    periods_per_year = Decimal("365") / Decimal(str(th.recent_days))
    return window_gap * periods_per_year
