"""When a supplier crosses the 194Q threshold, and nothing else.

Section 194Q: a buyer whose own turnover exceeded the statutory gate in the
preceding year deducts tax on purchases from one resident supplier beyond the
party threshold in a financial year. Miss the crossing and the deduction is
short; notice it late and it is paid with interest.

**There is no longer a seller-side mirror to reconcile against.** Section
206C(1H) — the collection obligation that used to sit opposite this one, with
the two applying to the same transaction and each disapplying the other — was
omitted with effect from 1 April 2025. The interaction that made this area
genuinely hard is gone, which is why this module is one function and not a
decision table.

Two deliberate restraints.

**Nothing is emitted until somebody confirms the gate.** Whether *our* turnover
crossed the statutory limit last year is a fact about three legal entities
whose accounts live in Tally, not here, and it cannot be derived from a
two-year window of synced documents. ``s194q_org_gate_met`` is off by default
and this returns nothing while it is — an alert derived from an unverified gate
is a confident statement about a duty nobody established applies.

**One event per supplier per year, not a running tally.** The obligation begins
at the crossing and the useful moment is that one. A screen showing everybody's
year-to-date purchases against a line is a report nobody reads daily; a row
that appears when a supplier crosses is a thing to do.

The basis is stated on every row and is deliberately conservative: bill totals
are GST-inclusive until the tax split is ingested, so a crossing here fires
slightly *early*. Early is the safe direction for this alert, and saying so is
better than implying a precision the figures do not have.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ..jurisdiction import INDIA
from .msme import fy_of

#: How much of the threshold has to be used up before a supplier is worth
#: watching. Not a statutory number — the statute has one line and it is the
#: threshold itself — but a crossing nobody saw coming is one somebody has to
#: fix retrospectively.
APPROACHING_SHARE = 0.9


@dataclass(frozen=True)
class Purchase:
    """One bill, reduced to what this question needs."""

    vendor_id: str
    date: date
    amount: float


@dataclass(frozen=True)
class Crossing:
    vendor_id: str
    vendor_name: str
    fy: str
    #: Purchases from this supplier in the year, on the stated basis.
    purchases: float
    threshold: float
    #: The bill that took them over, and when. The actionable pair: deduction
    #: begins on the excess from this point.
    crossed_on: Optional[date]
    excess: float
    approaching: bool

    def to_dict(self) -> dict:
        return {
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "financial_year": self.fy,
            "purchases": round(self.purchases, 2),
            "threshold": self.threshold,
            "crossed": self.crossed_on is not None,
            "crossed_on": self.crossed_on.isoformat() if self.crossed_on else None,
            "excess": round(self.excess, 2),
            "approaching": self.approaching,
        }


def fy_bounds(fy_label: str) -> tuple[date, date]:
    """``FY2026-27`` as a half-open date range, on the Indian calendar.

    Read from ``commercial/jurisdiction`` rather than restated: like ``msme``,
    this module *is* the Indian statute, and the routers refuse it for any
    other country rather than this function taking a calendar it would never
    legitimately vary.
    """
    return INDIA.fy_bounds(fy_label)


def crossings(purchases: Iterable[Purchase], names: dict[str, str], *,
              as_of: date, th) -> dict:
    """Suppliers who have crossed, or are close to crossing, this year.

    Returns an envelope rather than a bare list so the "gate not confirmed"
    state can be reported as itself. A caller receiving ``[]`` could not tell
    "nobody crossed" from "we are not allowed to say", and those need different
    things done about them.
    """
    current_fy = fy_of(as_of)
    if not th.s194q_org_gate_met:
        return {
            "as_of": as_of.isoformat(),
            "financial_year": current_fy,
            "gate_confirmed": False,
            "crossings": [],
            "note": ("Section 194Q applies only if this entity's own turnover "
                     "exceeded the statutory limit in the preceding financial "
                     "year. That figure is not in this platform. Confirm it in "
                     "Settings and this list will populate; until then nothing "
                     "is asserted."),
        }

    start, end = fy_bounds(current_fy)
    threshold = float(th.s194q_party_threshold)

    running: dict[str, float] = {}
    crossed_on: dict[str, date] = {}
    for purchase in sorted(
            (p for p in purchases if start <= p.date < end and p.vendor_id),
            # Sorted by date, then by a stable tiebreak, so "which bill took
            # them over" is the same answer on every run over the same book.
            key=lambda p: (p.date, p.vendor_id, p.amount)):
        before = running.get(purchase.vendor_id, 0.0)
        after = before + float(purchase.amount)
        running[purchase.vendor_id] = after
        if before <= threshold < after and purchase.vendor_id not in crossed_on:
            crossed_on[purchase.vendor_id] = purchase.date

    rows = [
        Crossing(
            vendor_id=vendor_id,
            vendor_name=names.get(vendor_id, "Supplier not on record"),
            fy=current_fy,
            purchases=total,
            threshold=threshold,
            crossed_on=crossed_on.get(vendor_id),
            excess=max(0.0, total - threshold),
            approaching=(total <= threshold
                         and total >= threshold * APPROACHING_SHARE),
        )
        for vendor_id, total in running.items()
        if total >= threshold * APPROACHING_SHARE
    ]
    # Crossed before approaching, then by how far over. A supplier already past
    # the line needs a deduction on the next payment; one at 92% needs a note.
    rows.sort(key=lambda r: (r.crossed_on is None, -r.purchases))

    return {
        "as_of": as_of.isoformat(),
        "financial_year": current_fy,
        "gate_confirmed": True,
        "threshold": threshold,
        "crossings": [r.to_dict() for r in rows],
        "basis_note": (
            "Purchases are totalled from bill values as recorded, which include "
            "GST until the tax split is ingested — so a crossing here is "
            "reported slightly earlier than the taxable figure would. Dates and "
            "amounts are a reading of your own records, not tax advice."),
    }
