"""CUSTOMER_DECLINE detector (§9.1).

Fact: a customer's recent-period revenue is down more than the threshold vs a
comparable prior period, subject to an activity floor and minimum history. No
recommendation — just the measured drop.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..domain.enums import EvidenceSufficiency, SignalType, SubjectEntityType
from . import aggregates as agg
from .base import Snapshot, SignalDraft, Sufficiency, clamp_severity, evidence_ref
from .config import SignalThresholds
from .quality import sales_outliers


def detect(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> list[SignalDraft]:
    recent_w = agg.recent_window(as_of, th)
    prior_w = agg.prior_window(as_of, th)
    drafts: list[SignalDraft] = []

    for cid in snapshot.customer_ids():
        sales = snapshot.sales_for_customer(cid)
        history_months = agg.history_span_months(sales, as_of)
        prior_orders = len(agg.orders_in(sales, prior_w))
        recent_orders = len(agg.orders_in(sales, recent_w))
        window_lines = [s for s in sales
                        if agg._in_window(s.date, recent_w) or agg._in_window(s.date, prior_w)]

        # eligibility / minimum evidence — withhold (no signal) if insufficient
        if history_months < th.decline_min_history_months:
            continue
        if prior_orders < th.decline_min_prior_orders:
            continue
        baseline = agg.revenue_in(sales, prior_w)
        recent = agg.revenue_in(sales, recent_w)
        if baseline <= 0:
            continue  # cannot express a decline against a zero/absent baseline

        change_pct = float((recent - baseline) / baseline)
        if change_pct > -th.decline_drop_pct:
            continue  # not a material decline

        metrics = {
            "baseline_revenue": float(round(baseline, 2)),
            "recent_revenue": float(round(recent, 2)),
            "pct_change": round(change_pct, 4),
            "abs_change": float(round(recent - baseline, 2)),
            "prior_orders": prior_orders,
            "recent_orders": recent_orders,
            "top_declining_products": agg.top_products_by_revenue_change(
                sales, recent_w, prior_w, snapshot.product_names),
        }
        suff = Sufficiency(history_months=history_months, txn_count=len(window_lines),
                           anomalies=sales_outliers(window_lines, th),
                           level=EvidenceSufficiency.SUFFICIENT)
        drafts.append(SignalDraft(
            signal_type=SignalType.CUSTOMER_DECLINE.value,
            subject_entity_type=SubjectEntityType.CUSTOMER.value,
            subject_entity_id=cid,
            window={"basis_period": {"start": recent_w[0].isoformat(), "end": recent_w[1].isoformat()},
                    "comparison_period": {"start": prior_w[0].isoformat(), "end": prior_w[1].isoformat()},
                    "granularity": "period"},
            metrics=metrics,
            severity_base=clamp_severity(abs(change_pct) * 100),
            evidence_refs=[evidence_ref(s.source_ref) for s in window_lines],
            sufficiency=suff,
            detector_version="",  # stamped by the engine
            threshold_config_version="",
        ))
    return drafts
