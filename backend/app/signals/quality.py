"""Data-quality anomaly checks (§19).

Conservative, per-fact checks. Anomalies are *flagged* (surfaced as "verify"),
never auto-corrected and never used to fabricate a fact. A detector that depends
on a flagged fact withholds its signal rather than asserting on bad data.
"""
from __future__ import annotations

import statistics
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from .base import Anomaly
from .config import SignalThresholds

if TYPE_CHECKING:
    from .base import SaleRow


def cost_anomalies(unit_cost: Decimal, unit_price: Optional[Decimal],
                   th: SignalThresholds) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if unit_cost < 0:
        out.append({"code": Anomaly.NEGATIVE_VALUE, "detail": f"unit_cost {unit_cost} < 0"})
    if unit_cost <= Decimal(str(th.placeholder_cost_max)):
        out.append({"code": Anomaly.ZERO_OR_PLACEHOLDER_COST,
                    "detail": f"unit_cost {unit_cost} ≤ placeholder floor"})
    if unit_price is not None and unit_price > 0 and unit_cost > unit_price:
        out.append({"code": Anomaly.COST_GT_PRICE,
                    "detail": f"unit_cost {unit_cost} > unit_price {unit_price}"})
    if unit_price is not None and unit_price > 0 and unit_cost > 0 and unit_cost > unit_price:
        out.append({"code": Anomaly.NEGATIVE_MARGIN,
                    "detail": "margin < 0 (cost exceeds price)"})
    return out


def cost_is_reliable(anomalies: list[dict[str, str]]) -> bool:
    """Cost is usable for a margin/cost signal only if no blocking anomaly is present."""
    blocking = {Anomaly.ZERO_OR_PLACEHOLDER_COST, Anomaly.COST_GT_PRICE,
                Anomaly.NEGATIVE_VALUE}
    return not any(a["code"] in blocking for a in anomalies)


def sales_outliers(sales: "list[SaleRow]", th: SignalThresholds) -> list[dict[str, str]]:
    """Flag obvious outliers/negatives across a set of sales lines (surface, not
    suppress). Used to annotate a signal's sufficiency, not to fabricate/hide facts."""
    out: list[dict[str, str]] = []
    if not sales:
        return out
    for s in sales:
        if s.qty < 0 or s.unit_price < 0 or s.line_revenue < 0:
            out.append({"code": Anomaly.NEGATIVE_VALUE,
                        "detail": f"line {s.external_ref}: negative qty/price/revenue"})
    qtys = [float(s.qty) for s in sales if s.qty > 0]
    prices = [float(s.unit_price) for s in sales if s.unit_price > 0]
    if len(qtys) >= 3:
        mq = statistics.median(qtys)
        for s in sales:
            if mq > 0 and float(s.qty) > th.qty_spike_factor * mq:
                out.append({"code": Anomaly.QTY_SPIKE,
                            "detail": f"line {s.external_ref}: qty {s.qty} ≫ median {mq:g}"})
    if len(prices) >= 3:
        mp = statistics.median(prices)
        for s in sales:
            if mp > 0 and float(s.unit_price) > th.price_outlier_factor * mp:
                out.append({"code": Anomaly.PRICE_OUTLIER,
                            "detail": f"line {s.external_ref}: price {s.unit_price} ≫ median {mp:g}"})
    return out
