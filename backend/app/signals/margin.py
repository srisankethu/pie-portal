"""MARGIN_DETERIORATION detector (§9.3) — RESTRICTED (cost/margin).

Fact: a product's recent gross margin % is down more than the threshold vs a
baseline period, where margin = (unit_price − unit_cost) / unit_price computed
only from reliable cost. Withheld (no signal) when cost is missing/placeholder or
cost > price — the anomaly is recorded for verification instead of asserted.
No recommendation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from ..domain.enums import EvidenceSufficiency, SignalType, SubjectEntityType
from . import aggregates as agg
from .base import Snapshot, SignalDraft, Sufficiency, clamp_severity, evidence_ref
from .config import SignalThresholds
from .quality import cost_anomalies, cost_is_reliable


def _margin(price: Optional[Decimal], cost: Decimal) -> Optional[float]:
    if price is None or price <= 0:
        return None
    return float((price - cost) / price)


def detect(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> list[SignalDraft]:
    recent_w = agg.recent_window(as_of, th)
    prior_w = agg.prior_window(as_of, th)
    drafts: list[SignalDraft] = []

    for pid in snapshot.product_ids():
        sales = snapshot.sales_for_product(pid)
        costs = snapshot.costs_for_product(pid)
        if not sales or not costs:
            continue  # need both sales and cost to speak about margin

        recent_price = agg.avg_unit_price(sales, recent_w)
        prior_price = agg.avg_unit_price(sales, prior_w)
        if recent_price is None or prior_price is None:
            continue  # not enough priced activity in both periods

        recent_cost_row = agg.cost_basis_asof(costs, recent_w[1])
        prior_cost_row = agg.cost_basis_asof(costs, prior_w[1])
        if recent_cost_row is None or prior_cost_row is None:
            continue  # no applicable cost basis in a period

        # reliability of BOTH costs we compute the two margins from. A placeholder
        # or zero prior cost would inflate the baseline margin toward 100% and
        # manufacture a huge, false "deterioration"; withhold on either bad cost.
        anomalies = cost_anomalies(recent_cost_row.unit_cost, recent_price, th)
        prior_anomalies = cost_anomalies(prior_cost_row.unit_cost, prior_price, th)
        if not cost_is_reliable(anomalies) or not cost_is_reliable(prior_anomalies):
            # withhold: do not assert a margin on bad cost; flag for verification
            continue

        current_margin = _margin(recent_price, recent_cost_row.unit_cost)
        baseline_margin = _margin(prior_price, prior_cost_row.unit_cost)
        if current_margin is None or baseline_margin is None:
            continue
        drop = baseline_margin - current_margin
        if drop <= th.margin_drop_points:
            continue  # not a material deterioration

        price_change = float((recent_price - prior_price) / prior_price) if prior_price else None
        cost_change = (float((recent_cost_row.unit_cost - prior_cost_row.unit_cost)
                             / prior_cost_row.unit_cost) if prior_cost_row.unit_cost else None)

        metrics = {
            "baseline_margin_pct": round(baseline_margin, 4),
            "current_margin_pct": round(current_margin, 4),
            "margin_drop_points": round(drop, 4),
            "prior_unit_price": float(round(prior_price, 2)),
            "recent_unit_price": float(round(recent_price, 2)),
            "prior_unit_cost": float(round(prior_cost_row.unit_cost, 2)),
            "recent_unit_cost": float(round(recent_cost_row.unit_cost, 2)),
            "drivers": {"price_change_pct": round(price_change, 4) if price_change is not None else None,
                        "cost_change_pct": round(cost_change, 4) if cost_change is not None else None},
        }
        window_lines = [s for s in sales
                        if agg._in_window(s.date, recent_w) or agg._in_window(s.date, prior_w)]
        suff = Sufficiency(history_months=agg.history_span_months(sales, as_of),
                           txn_count=len(window_lines), anomalies=anomalies,
                           level=EvidenceSufficiency.SUFFICIENT)
        evidence = ([evidence_ref(s.source_ref) for s in window_lines]
                    + [evidence_ref(recent_cost_row.source_ref),
                       evidence_ref(prior_cost_row.source_ref)])
        drafts.append(SignalDraft(
            signal_type=SignalType.MARGIN_DETERIORATION.value,
            subject_entity_type=SubjectEntityType.PRODUCT.value,
            subject_entity_id=pid,
            window={"basis_period": {"start": recent_w[0].isoformat(), "end": recent_w[1].isoformat()},
                    "comparison_period": {"start": prior_w[0].isoformat(), "end": prior_w[1].isoformat()},
                    "granularity": "period"},
            metrics=metrics,
            severity_base=clamp_severity(drop * 200),
            evidence_refs=evidence,
            sufficiency=suff,
            detector_version="",
            threshold_config_version="",
        ))
    return drafts
