"""When customers order, and who is off their own rhythm.

The specification calls this a "Buying Cycle Wheel". A radial form is genuinely
the right shape for one part of this and the wrong shape for the other, so both
are returned and the screen picks per question:

**The wheel is for the cycle.** Day-of-month is *cyclical* — the 31st is
adjacent to the 1st, and a bar chart cuts that join, which is exactly the
information a distributor cares about because orders cluster at month ends. A
radial layout is the honest encoding here, not decoration.

**The list is for the customers.** "Who is overdue" is a rank, not a position,
and putting it on a wheel would make a reader estimate angles to compare two
numbers. So it is a list.

Cadence is measured against each customer's *own* history — median gap between
their orders — because a distributor's customers order on wildly different
rhythms and a single "overdue" threshold would flag the quarterly buyers every
month. The median rather than the mean: one emergency order six years ago should
not move someone's normal.

This mirrors ``signals/dormancy.py`` deliberately. That detector decides whether
to *raise* something; this describes the whole population so a person can see
the distribution the detector is drawing from. Neither owns the arithmetic:
both call ``aggregates.cadence_of``, and both take the eligibility bar and the
overdue multiplier from the same ``SignalThresholds``. A screen calling somebody
overdue while the queue beside it stays silent is the kind of disagreement
nobody can debug from the outside, and copying the rule across would have agreed
only until the first org tuned ``SIG_DORMANCY_MULT``.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ...signals import aggregates as agg
from ...signals.base import SaleRow
from ...signals.config import SignalThresholds, load_thresholds


@dataclass
class CustomerCadence:
    customer_id: str
    label: str
    order_count: int
    typical_interval_days: Optional[float]
    days_since_last: int
    last_order: str
    overdue_ratio: Optional[float]
    lifetime_revenue: float
    #: Carried, not recomputed. `agg.Cadence` owns the rule that turns a ratio
    #: into "late"; a second `> 1.0` here is a second rule waiting to drift.
    overdue: bool

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id, "label": self.label,
            "order_count": self.order_count,
            "typical_interval_days": self.typical_interval_days,
            "days_since_last": self.days_since_last,
            "last_order": self.last_order,
            "overdue_ratio": self.overdue_ratio,
            "overdue": self.overdue,
            "lifetime_revenue": round(self.lifetime_revenue, 2),
        }


def build(sales: Iterable[SaleRow], names: dict[str, str], as_of: date, *,
          thresholds: Optional[SignalThresholds] = None) -> dict:
    """The population's rhythm, and every customer's position in their own."""
    th = thresholds or load_thresholds()
    min_orders = th.dormancy_min_orders
    multiplier = th.dormancy_interval_multiplier
    grouped = agg.by_customer(sales)

    # ── the wheel: which day of the month orders land on ─────────────────────
    # 31 slots. Months are unequal, so the count for day 31 is over fewer
    # opportunities than day 15; the response carries the opportunity count so a
    # screen can normalise rather than showing a dip that is an artefact of the
    # calendar.
    day_counts = [0] * 31
    day_revenue = [0.0] * 31
    counted: set[str] = set()
    for row in sales:
        key = str((row.source_ref or {}).get("record_id") or row.external_ref)
        idx = row.date.day - 1
        day_revenue[idx] += float(row.line_revenue)
        if key not in counted:
            counted.add(key)
            day_counts[idx] += 1

    rows: list[CustomerCadence] = []
    for customer_id, customer_rows in grouped.items():
        # The eligibility bar, the median and the overdue test come from the one
        # function the dormancy detector uses. This screen exists to show the
        # distribution that detector draws from, so a second implementation here
        # would be a screen quietly disagreeing with the queue beside it.
        c = agg.cadence_of(customer_rows, as_of, min_orders=min_orders,
                           multiplier=multiplier)
        if not c.order_dates:
            continue
        rows.append(CustomerCadence(
            customer_id=customer_id,
            label=names.get(customer_id, customer_id),
            order_count=len(c.order_dates),
            typical_interval_days=c.typical_interval_days,
            days_since_last=c.days_since_last or 0,
            last_order=c.order_dates[-1].isoformat(),
            overdue_ratio=c.overdue_ratio,
            lifetime_revenue=float(sum(r.line_revenue for r in customer_rows)),
            overdue=c.overdue,
        ))

    # Overdue first and by how much, then everyone else by size. The question
    # this screen answers is "who should I call", and that is a rank.
    rows.sort(key=lambda c: ((c.overdue_ratio or 0) if c.overdue else 0,
                             c.lifetime_revenue), reverse=True)

    estimable = [c for c in rows if c.typical_interval_days is not None]
    intervals = [c.typical_interval_days for c in estimable
                 if c.typical_interval_days is not None]

    return {
        "as_of": as_of.isoformat(),
        "wheel": [
            {"day": i + 1, "orders": day_counts[i],
             "revenue": round(day_revenue[i], 2),
             # Days 29–31 do not exist in every month; without this a screen
             # would draw a cliff that is the calendar, not the customers.
             "occurs_in_months": 12 if i < 28 else (11 if i < 30 else 7)}
            for i in range(31)
        ],
        "customers": [c.to_dict() for c in rows],
        "overdue_count": sum(1 for c in rows if c.overdue),
        "estimable_count": len(estimable),
        "unestimable_count": len(rows) - len(estimable),
        "median_interval_days": (round(statistics.median(intervals), 1)
                                 if intervals else None),
        "min_orders_for_cadence": min_orders,
        "overdue_multiplier": multiplier,
        "note": ("Overdue is measured against each customer's own median gap, "
                 "not a single company-wide interval — a quarterly buyer is not "
                 "late in month two. Customers with fewer than "
                 f"{min_orders} orders have no estimable rhythm and are counted "
                 "separately rather than assumed regular."),
    }
