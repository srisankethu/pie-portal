"""CUSTOMER_DORMANCY detector (§9.2).

Fact: a customer is overdue relative to *their own* established ordering cadence
(days since last order exceeds their typical interval × a multiplier). Requires
enough orders to estimate cadence. No recommendation.
"""
from __future__ import annotations

from datetime import date

from ..domain.enums import EvidenceSufficiency, SignalType, SubjectEntityType
from . import aggregates as agg
from .base import (Coverage, Snapshot, SignalDraft, Sufficiency, Withholding,
                   clamp_severity, evidence_ref)
from .config import SignalThresholds


def detect(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> list[SignalDraft]:
    """Just the drafts — see ``examine`` for the denominator and the withholds."""
    return examine(snapshot, th, as_of).drafts


def examine(snapshot: Snapshot, th: SignalThresholds, as_of: date) -> Coverage:
    out = Coverage(detector="CUSTOMER_DORMANCY")
    drafts = out.drafts
    for cid in snapshot.customer_ids():
        out.considered += 1
        sales = snapshot.sales_for_customer(cid)
        # Eligibility, the median gap and the overdue test all live in
        # ``aggregates.cadence_of`` — shared with the buying-rhythm screen and
        # the quote context, so the three cannot disagree about who is late.
        cadence = agg.cadence_of(sales, as_of,
                                 min_orders=th.dormancy_min_orders,
                                 multiplier=th.dormancy_interval_multiplier)
        # Three outcomes, and only the last is a clear result. No orders and no
        # established rhythm are both "cannot look" — a customer with two orders
        # ever is not on time, they are unjudgeable, and folding them into the
        # quiet majority is what made a thin book look like a calm one.
        if not cadence.order_dates:
            out.withhold(cid, Withholding.NO_ORDERS_ON_RECORD)
            continue
        if cadence.expected_interval_days is None:
            out.withhold(cid, Withholding.CADENCE_NOT_ESTABLISHED)
            continue
        if not cadence.overdue:
            continue  # judged, and ordering on time

        dates = cadence.order_dates
        last_order = dates[-1]
        actual_gap = cadence.days_since_last or 0
        expected = cadence.expected_interval_days or 0.0
        metrics = {
            "typical_interval_days": cadence.typical_interval_days,
            "expected_interval_days": expected,
            "actual_gap_days": actual_gap,
            "order_count": len(dates),
            "last_order_date": last_order.isoformat(),
            "first_order_date": dates[0].isoformat(),
            "overdue_ratio": cadence.overdue_ratio,
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
    return out
