"""What is on the shelf, and which of it is a problem.

Four questions, and the platform can now answer three of them honestly:

**Committed beyond what exists.** Zoho reports ``available`` (what can still be
sold) and ``actual_available`` (that, net of what open orders have already
promised). A negative actual against a positive available means more has been
committed than is on the shelf — a delivery date somebody is going to miss.
This is the one finding on this screen that is *already* a decision.

**Sitting still.** Stock on hand that nothing has sold in the window. Not
"dead" — that is a judgement about a future nobody can see — but measured, with
the date of the last sale attached so a person can make the judgement.

**No reorder point.** Zoho's ``reorder_level`` is blank on most of this book's
items. That is a real finding about the master data, and it is reported as one.
It is *not* substituted with a computed reorder point: a reorder level is a
policy somebody chooses with lead time and service level in mind, and inventing
one here would be the platform making a commercial decision in a module whose
whole purpose is not to.

**Cover in weeks.** Deliberately absent. It needs a demand forecast, and the
honest forecast from this data is "what sold in the last N days", which turns
into a weeks-of-cover number that reads like a projection and is not one. Named
in the response so the screen can say what it does not know.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

#: On hand, and nothing sold in this many days. Long, deliberately: a
#: distributor's slow-moving tooling line is not dead in month two.
IDLE_AFTER_DAYS = 180


@dataclass
class StockLine:
    product_id: str
    label: str
    on_hand: float
    available: Optional[float]
    actual_available: Optional[float]
    reorder_level: Optional[float]
    last_sold: Optional[date]
    sold_qty_window: float
    #: Cost. Present only for the roles allowed to see economics — the router
    #: drops the field entirely for a salesperson rather than zeroing it.
    purchase_rate: Optional[float] = None

    @property
    def oversold(self) -> bool:
        """Committed past what exists. Zoho's own arithmetic, not ours."""
        return self.actual_available is not None and self.actual_available < 0

    @property
    def below_reorder(self) -> bool:
        # A blank reorder level is not zero. An item with no reorder point set
        # can never be "below" it, and saying otherwise would flag the whole
        # catalogue the day somebody leaves the field empty.
        if self.reorder_level is None:
            return False
        return self.on_hand <= float(self.reorder_level)

    def idle_days(self, as_of: date) -> Optional[int]:
        return (as_of - self.last_sold).days if self.last_sold else None

    def idle(self, as_of: date) -> bool:
        if self.on_hand <= 0:
            return False
        days = self.idle_days(as_of)
        # Never sold at all, but sitting on the shelf, counts as idle: the
        # absence of a sale date is the strongest version of the finding, not
        # a reason to exclude the row.
        return days is None or days >= IDLE_AFTER_DAYS

    def to_dict(self, as_of: date, *, with_cost: bool) -> dict:
        out = {
            "product_id": self.product_id, "label": self.label,
            "on_hand": self.on_hand, "available": self.available,
            "actual_available": self.actual_available,
            "reorder_level": self.reorder_level,
            "last_sold": self.last_sold.isoformat() if self.last_sold else None,
            "idle_days": self.idle_days(as_of),
            "sold_qty_window": round(self.sold_qty_window, 2),
            "oversold": self.oversold,
            "below_reorder": self.below_reorder,
            "idle": self.idle(as_of),
        }
        if with_cost:
            out["purchase_rate"] = self.purchase_rate
            out["stock_value"] = (round(self.on_hand * self.purchase_rate, 2)
                                  if self.purchase_rate is not None else None)
        return out


def build(lines: Iterable[StockLine], as_of: date, *, with_cost: bool) -> dict:
    """The shelf, grouped by the jobs it implies."""
    rows = [r for r in lines]
    if not rows:
        return {"as_of": as_of.isoformat(), "groups": [], "counts": {},
                "unavailable": _unavailable()}

    oversold = [r for r in rows if r.oversold]
    below = [r for r in rows if r.below_reorder and not r.oversold]
    idle = [r for r in rows if r.idle(as_of) and not r.oversold]
    no_policy = [r for r in rows if r.reorder_level is None]

    # Worst first inside each group, by the thing that makes it worst.
    oversold.sort(key=lambda r: r.actual_available or 0)
    below.sort(key=lambda r: r.on_hand)
    idle.sort(key=lambda r: (r.idle_days(as_of) is None, r.idle_days(as_of) or 0),
              reverse=True)

    groups = [
        {
            "key": "OVERSOLD",
            "label": "Committed beyond stock",
            "meaning": ("Open orders promise more than is on the shelf. Zoho's "
                        "own netting, not an estimate — somebody is going to "
                        "miss a delivery date unless this is bought or "
                        "re-promised."),
            "items": [r.to_dict(as_of, with_cost=with_cost) for r in oversold[:40]],
            "count": len(oversold),
        },
        {
            "key": "BELOW_REORDER",
            "label": "At or below the reorder point",
            "meaning": ("Only for items where a reorder point has actually been "
                        "set. Items without one are counted separately, never "
                        "assumed to reorder at zero."),
            "items": [r.to_dict(as_of, with_cost=with_cost) for r in below[:40]],
            "count": len(below),
        },
        {
            "key": "IDLE",
            "label": f"On the shelf, nothing sold in {IDLE_AFTER_DAYS} days",
            "meaning": ("Measured, not judged. The last sale date is attached so "
                        "the call about whether it is dead stock stays with a "
                        "person who knows the line."),
            "items": [r.to_dict(as_of, with_cost=with_cost) for r in idle[:40]],
            "count": len(idle),
        },
    ]

    counts = {
        "tracked_items": len(rows),
        "oversold": len(oversold),
        "below_reorder": len(below),
        "idle": len(idle),
        "no_reorder_point": len(no_policy),
        "on_hand_items": sum(1 for r in rows if r.on_hand > 0),
    }
    if with_cost:
        counts["stock_value"] = round(
            sum(r.on_hand * r.purchase_rate for r in rows
                if r.purchase_rate is not None and r.on_hand > 0), 2)
        counts["value_unpriced_items"] = sum(
            1 for r in rows if r.on_hand > 0 and r.purchase_rate is None)

    return {
        "as_of": as_of.isoformat(),
        "groups": groups,
        "counts": counts,
        "idle_after_days": IDLE_AFTER_DAYS,
        "unavailable": _unavailable(len(no_policy), len(rows)),
    }


def _unavailable(no_policy: int = 0, total: int = 0) -> list[dict]:
    out = [{
        "series": "weeks_of_cover",
        "reason": ("Cover needs a demand forecast. The only forecast this data "
                   "supports is 'what sold recently, repeated', which would "
                   "print as a projection without being one. Recent sold "
                   "quantity is shown instead, unprojected."),
    }]
    if no_policy:
        out.append({
            "series": "reorder_point",
            "reason": (f"{no_policy} of {total} items have no reorder level set "
                       "in Zoho. They cannot be below a point that does not "
                       "exist, so they are excluded from that group rather "
                       "than assumed to reorder at zero."),
        })
    return out
