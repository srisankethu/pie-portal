"""Who supplies this book, what is still outstanding, and how long it takes.

**Lead time is measured only where a receipt was logged.** Zoho records a
purchase order's receives; where there are none the order is either still open
or the receipt was never entered, and those are not the same thing. Neither is
converted into a lead time. An "average lead time" computed over orders that
were never marked received would be an average of the orders somebody
remembered to close, which is a statement about admin, not about suppliers.

**Promised dates are absent from this book.** ``expected_delivery_date`` is
blank on effectively every order here, so "late against promise" has no promise
side and is reported as unanswerable rather than measured against an assumed
lead time. Order *age* is answerable and is what the open list ranks by.

**Concentration is by spend, and the tail is not folded.** Unlike the customer
mix, a supplier list is short and every name on it is someone with a phone
number — an "Other (14)" band would hide exactly the second sources a
concentration question exists to find.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from . import absence

#: Orders older than this with stock still to come. Not a promise breach —
#: there is no promise — but long enough to be worth a phone call.
STALE_ORDER_DAYS = 45

#: Fewer received orders than this and a "typical lead time" is one delivery.
MIN_RECEIPTS_FOR_LEAD_TIME = 3


@dataclass
class SupplierOrder:
    vendor_id: Optional[str]
    vendor_label: str
    number: Optional[str]
    ordered_on: date
    expected_on: Optional[date]
    received_on: Optional[date]
    pending_qty: float
    ordered_qty: float
    total: Optional[float]
    status: str

    @property
    def open(self) -> bool:
        return self.pending_qty > 0

    @property
    def lead_time_days(self) -> Optional[int]:
        """Only where a receipt exists. See the module docstring."""
        if self.received_on is None:
            return None
        return (self.received_on - self.ordered_on).days

    def age_days(self, as_of: date) -> int:
        return (as_of - self.ordered_on).days

    def to_dict(self, as_of: date) -> dict:
        return {
            "vendor_id": self.vendor_id, "vendor_label": self.vendor_label,
            "number": self.number,
            "ordered_on": self.ordered_on.isoformat(),
            "expected_on": self.expected_on.isoformat() if self.expected_on else None,
            "received_on": self.received_on.isoformat() if self.received_on else None,
            "ordered_qty": self.ordered_qty, "pending_qty": self.pending_qty,
            "total": self.total, "status": self.status,
            "age_days": self.age_days(as_of),
            "lead_time_days": self.lead_time_days,
        }


@dataclass
class SupplierSpend:
    vendor_id: Optional[str]
    label: str
    spend: float
    orders: int
    open_orders: int
    lead_times: list[int]
    payment_terms_days: Optional[int]

    @property
    def typical_lead_time(self) -> Optional[float]:
        if len(self.lead_times) < MIN_RECEIPTS_FOR_LEAD_TIME:
            return None
        return round(statistics.median(self.lead_times), 1)

    def to_dict(self, total_spend: float) -> dict:
        return {
            "vendor_id": self.vendor_id, "label": self.label,
            "spend": round(self.spend, 2),
            "share": round(self.spend / total_spend, 4) if total_spend else 0.0,
            "orders": self.orders, "open_orders": self.open_orders,
            "typical_lead_time_days": self.typical_lead_time,
            "receipts_seen": len(self.lead_times),
            "min_receipts_for_lead_time": MIN_RECEIPTS_FOR_LEAD_TIME,
            "payment_terms_days": self.payment_terms_days,
        }


def build(orders: Iterable[SupplierOrder], as_of: date, *,
          terms_by_vendor: Optional[dict[str, int]] = None) -> dict:
    """Supplier concentration, what is outstanding, and measured lead times."""
    rows = list(orders)
    terms = terms_by_vendor or {}
    if not rows:
        return {"as_of": as_of.isoformat(), "suppliers": [], "open_orders": [],
                "counts": {}, "unavailable": _unavailable(0, 0)}

    grouped: dict[Optional[str], SupplierSpend] = {}
    for o in rows:
        entry = grouped.get(o.vendor_id)
        if entry is None:
            entry = SupplierSpend(
                vendor_id=o.vendor_id, label=o.vendor_label, spend=0.0, orders=0,
                open_orders=0, lead_times=[],
                payment_terms_days=terms.get(o.vendor_id or ""))
            grouped[o.vendor_id] = entry
        entry.spend += float(o.total or 0.0)
        entry.orders += 1
        if o.open:
            entry.open_orders += 1
        lead = o.lead_time_days
        if lead is not None and lead >= 0:
            entry.lead_times.append(lead)

    total_spend = sum(e.spend for e in grouped.values())
    suppliers = sorted(grouped.values(), key=lambda e: e.spend, reverse=True)

    open_orders = [o for o in rows if o.open]
    # Oldest first: with no promised date, age is the only thing that ranks
    # "which of these should I chase".
    open_orders.sort(key=lambda o: o.ordered_on)

    received = [o for o in rows if o.lead_time_days is not None]
    promised = [o for o in rows if o.expected_on is not None]

    return {
        "as_of": as_of.isoformat(),
        "suppliers": [e.to_dict(total_spend) for e in suppliers],
        "open_orders": [o.to_dict(as_of) for o in open_orders[:40]],
        "counts": {
            "suppliers": len(suppliers),
            "orders": len(rows),
            "open_orders": len(open_orders),
            "stale_open_orders": sum(1 for o in open_orders
                                     if o.age_days(as_of) >= STALE_ORDER_DAYS),
            "orders_with_a_receipt": len(received),
            "orders_with_a_promised_date": len(promised),
        },
        "total_spend": round(total_spend, 2),
        # Concentration, stated rather than left to be read off a chart.
        "top_supplier_share": (round(suppliers[0].spend / total_spend, 4)
                               if suppliers and total_spend else None),
        "median_lead_time_days": (
            round(statistics.median(o.lead_time_days for o in received), 1)
            if len(received) >= MIN_RECEIPTS_FOR_LEAD_TIME else None),
        "stale_after_days": STALE_ORDER_DAYS,
        "unavailable": _unavailable(len(promised), len(rows)),
    }


def _unavailable(promised: int, total: int) -> list[dict]:
    out = []
    if promised == 0:
        out.append({
            "series": "delivery_against_promise",
            # A field nobody fills, not a limit. ``simulate`` blocks a whole
            # scenario on the same blank, which makes this the highest-value
            # COLLECTABLE in the package.
            "kind": absence.COLLECTABLE,
            "reason": ("No purchase order in this book carries a promised "
                       "delivery date, so there is nothing to be late against. "
                       "Order age is shown instead — it is answerable, and an "
                       "assumed lead time would not be."),
        })
    elif promised < total:
        out.append({
            "series": "delivery_against_promise",
            "kind": absence.COLLECTABLE,
            "reason": (f"Only {promised} of {total} orders carry a promised "
                       "date. The rest are ranked by age, which is what the "
                       "data supports."),
        })
    return out
