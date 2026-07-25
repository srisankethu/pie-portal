"""CUSTOMER_DORMANCY detector (§9.2).

Fact: a customer is overdue relative to *their own* established ordering cadence
(days since last order exceeds their typical interval × a multiplier). Requires
enough orders to estimate cadence. No recommendation.
"""
from __future__ import annotations

import statistics
from datetime import date

from ..domain.enums import EvidenceSufficiency, SignalType, SubjectEntityType
from . import aggregates as agg
from .base import Snapshot, SignalDraft, Sufficiency, clamp_severity, evidence_ref
from .config import SignalThresholds


def detect(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> list[SignalDraft]:
    drafts: list[SignalDraft] = []
    for cid in snapshot.customer_ids():
        sales = snapshot.sales_for_customer(cid)
        dates = agg.order_dates(sales)
        # eligibility: need enough orders to establish a cadence (K orders → K-1 gaps)
        if len(dates) < th.dormancy_min_orders:
            continue
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        if not gaps:
            continue
        typical = statistics.median(gaps)
        if typical <= 0:
            continue  # degenerate cadence (same-day orders); not estimable
        expected = typical * th.dormancy_interval_multiplier
        last_order = dates[-1]
        actual_gap = (as_of - last_order).days
        if actual_gap <= expected:
            continue  # within their normal pattern — not overdue

        metrics = {
            "typical_interval_days": round(typical, 1),
            "expected_interval_days": round(expected, 1),
            "actual_gap_days": actual_gap,
            "order_count": len(dates),
            "last_order_date": last_order.isoformat(),
            "first_order_date": dates[0].isoformat(),
            "overdue_ratio": round(actual_gap / expected, 2) if expected else None,
        }
        suff = Sufficiency(history_months=agg.history_span_months(sales, as_of),
                           txn_count=len(sales), level=EvidenceSufficiency.SUFFICIENT)
        drafts.append(SignalDraft(
            signal_type=SignalType.CUSTOMER_DORMANCY.value,
            subject_entity_type=SubjectEntityType.CUSTOMER.value,
            subject_entity_id=cid,
            window={"basis_period": {"as_of": as_of.isoformat(),
                                     "last_order": last_order.isoformat()},
                    "granularity": "cadence"},
            metrics=metrics,
            severity_base=clamp_severity((actual_gap / expected - 1) * 100),
            evidence_refs=[evidence_ref(s.source_ref) for s in sales],
            sufficiency=suff,
            detector_version="",
            threshold_config_version="",
        ))
    return drafts
