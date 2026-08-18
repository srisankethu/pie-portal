"""COST_PASS_THROUGH detector (§9.4) — RESTRICTED (cost/margin).

Fact: a product's latest purchase cost rose more than the threshold vs its prior
cost basis, while the selling price has not moved correspondingly (margin
compressed). Withheld when cost history is absent or single-point (an increase
cannot be established). No recommendation — it states the exposure, it does not
say to reprice.
"""
from __future__ import annotations

from datetime import date

from ..domain.enums import EvidenceSufficiency, SignalType, SubjectEntityType
from . import aggregates as agg
from .base import (Coverage, Snapshot, SignalDraft, Sufficiency, Withholding,
                   clamp_severity, evidence_ref)
from .config import SignalThresholds
from .quality import cost_anomalies, cost_is_reliable


def detect(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> list[SignalDraft]:
    """Just the drafts — see ``examine`` for the denominator and the withholds."""
    return examine(snapshot, th, as_of).drafts


def examine(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> Coverage:
    out = Coverage(detector="COST_PASS_THROUGH")
    drafts = out.drafts
    for pid in snapshot.product_ids():
        out.considered += 1
        costs = snapshot.costs_for_product(pid)
        # need ≥ 2 distinct cost points to establish an increase
        if len(costs) < 2:
            out.withhold(pid, Withholding.TOO_FEW_COST_POINTS)
            continue
        latest = costs[-1]
        prior = costs[-2]
        if prior.unit_cost <= 0:
            out.withhold(pid, Withholding.PRIOR_COST_NOT_USABLE)
            continue

        # cost reliability (latest cost must be trustworthy)
        anomalies = cost_anomalies(latest.unit_cost, None, th)
        if not cost_is_reliable(anomalies):
            out.withhold(pid, Withholding.COST_NOT_RELIABLE)
            continue

        cost_delta = float((latest.unit_cost - prior.unit_cost) / prior.unit_cost)
        if cost_delta <= th.cost_increase_pct:
            continue  # judged, and no material cost increase — a clear result

        sales = snapshot.sales_for_product(pid)
        price_before = agg.avg_unit_price_range(sales, None, latest.date)
        price_after = agg.avg_unit_price_range(sales, latest.date, as_of)
        if price_before is None or price_before <= 0 or price_after is None:
            out.withhold(pid, Withholding.PRICE_NOT_COMPARABLE)
            continue  # cannot assess whether price moved with cost

        price_change = float((price_after - price_before) / price_before)
        # compressed = price rose materially less than cost
        if price_change >= cost_delta - th.cost_price_lag_points:
            continue  # judged: price kept pace with cost — a clear result

        resulting_margin = (float((price_after - latest.unit_cost) / price_after)
                            if price_after > 0 else None)
        after_sales = [s for s in sales if s.date > latest.date]
        affected = sorted({s.customer_id for s in after_sales})

        metrics = {
            "prior_unit_cost": float(round(prior.unit_cost, 2)),
            "latest_unit_cost": float(round(latest.unit_cost, 2)),
            "cost_delta_pct": round(cost_delta, 4),
            "price_before": float(round(price_before, 2)),
            "price_after": float(round(price_after, 2)),
            "price_change_pct": round(price_change, 4),
            "resulting_margin_pct": round(resulting_margin, 4) if resulting_margin is not None else None,
            "affected_customer_ids": affected,
            "affected_customers": [snapshot.customer_names.get(c, c) for c in affected],
            "cost_increase_date": latest.date.isoformat(),
        }
        suff = Sufficiency(history_months=agg.history_span_months(sales, as_of),
                           txn_count=len(after_sales), anomalies=anomalies,
                           level=EvidenceSufficiency.SUFFICIENT)
        evidence = ([evidence_ref(latest.source_ref), evidence_ref(prior.source_ref)]
                    + [evidence_ref(s.source_ref) for s in after_sales])
        drafts.append(SignalDraft(
            signal_type=SignalType.COST_PASS_THROUGH.value,
            subject_entity_type=SubjectEntityType.PRODUCT.value,
            subject_entity_id=pid,
            window={"basis_period": {"cost_increase_date": latest.date.isoformat(),
                                     "as_of": as_of.isoformat()},
                    "granularity": "cost_event"},
            metrics=metrics,
            severity_base=clamp_severity((cost_delta - max(price_change, 0.0)) * 300),
            evidence_refs=evidence,
            sufficiency=suff,
            detector_version="",
            threshold_config_version="",
        ))
    return out
