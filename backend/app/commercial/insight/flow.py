"""Where revenue went between two periods, decomposed by cause.

"Revenue fell 8%" is a fact nobody can act on. The same fall broken into *lost
customers took ₹4.1L with them, surviving customers spent ₹90k less, two new
customers brought ₹60k* is three different conversations with three different
owners, and the decomposition is what turns a number into work.

The five causes below are exhaustive and mutually exclusive by construction —
every customer belongs to exactly one, decided by whether they traded in
neither, one, or both periods. They therefore sum exactly to the total movement,
which is the property that makes a waterfall honest: a bar that does not
reconcile is a bar that has quietly dropped something.

  ``NEW``        no revenue in the previous period, revenue in this one
  ``LOST``       revenue previously, none now
  ``GROWN``      traded in both, spending more
  ``SHRUNK``     traded in both, spending less
  ``RECOVERED``  traded long ago, nothing in the previous period, back now

``RECOVERED`` is carved out of ``NEW`` deliberately. A returning customer and a
first-time customer look identical in a two-period comparison and mean opposite
things: one is a win-back that worked, the other is acquisition. Telling them
apart needs history older than the comparison, which is why this function takes
the whole sales history rather than two slices of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ...signals import aggregates as agg
from .periods import Comparison, Period, revenue_in
from .series import TradeRow

NEW = "NEW"
LOST = "LOST"
GROWN = "GROWN"
SHRUNK = "SHRUNK"
RECOVERED = "RECOVERED"
STABLE = "STABLE"

#: Movement smaller than this share of a customer's previous revenue is noise
#: from order timing rather than a change in the relationship. A distributor's
#: customer who orders monthly will always be a few percent either way.
STABLE_BAND = 0.05


@dataclass
class CustomerMove:
    customer_id: str
    label: str
    kind: str
    previous: float
    current: float

    @property
    def delta(self) -> float:
        return self.current - self.previous

    @property
    def pct(self) -> Optional[float]:
        return (self.delta / self.previous) if self.previous else None

    def to_dict(self) -> dict:
        return {"customer_id": self.customer_id, "label": self.label,
                "kind": self.kind, "previous": round(self.previous, 2),
                "current": round(self.current, 2), "delta": round(self.delta, 2),
                "pct": round(self.pct, 4) if self.pct is not None else None}


@dataclass
class Flow:
    comparison: Comparison
    previous_total: float
    current_total: float
    buckets: dict[str, float] = field(default_factory=dict)
    moves: list[CustomerMove] = field(default_factory=list)

    @property
    def delta(self) -> float:
        return self.current_total - self.previous_total

    def reconciles(self) -> bool:
        """The decomposition must sum to the movement it decomposes."""
        return abs(sum(self.buckets.values()) - self.delta) < 0.01

    def top(self, kind: str, limit: int = 5) -> list[CustomerMove]:
        rows = [m for m in self.moves if m.kind == kind]
        rows.sort(key=lambda m: abs(m.delta), reverse=True)
        return rows[:limit]

    def to_dict(self) -> dict:
        return {
            "comparison": self.comparison.to_dict(),
            "previous_total": round(self.previous_total, 2),
            "current_total": round(self.current_total, 2),
            "delta": round(self.delta, 2),
            "pct": (round(self.delta / self.previous_total, 4)
                    if self.previous_total else None),
            # Ordered so a waterfall can render straight from this without
            # deciding an order of its own — losses before gains reads as the
            # story it is: what went out, then what came in.
            "buckets": [
                {"kind": k, "amount": round(self.buckets.get(k, 0.0), 2),
                 "customers": sum(1 for m in self.moves if m.kind == k),
                 "top": [m.to_dict() for m in self.top(k)]}
                for k in (LOST, SHRUNK, STABLE, GROWN, RECOVERED, NEW)
            ],
            "reconciles": self.reconciles(),
        }


def _traded_before(rows: list[TradeRow], before: Period) -> bool:
    return any(r.date < before.start for r in rows)


def classify(previous: float, current: float, traded_earlier: bool) -> str:
    """Which of the five (plus STABLE) this customer's movement is.

    Order matters: the absent-from-one-period cases are decided first, because
    a customer who spent nothing cannot meaningfully be called "shrunk by 100%".
    """
    if previous <= 0 and current > 0:
        return RECOVERED if traded_earlier else NEW
    if previous > 0 and current <= 0:
        return LOST
    if previous <= 0 and current <= 0:
        return STABLE                      # never traded in either window
    change = (current - previous) / previous
    if abs(change) < STABLE_BAND:
        return STABLE
    return GROWN if change > 0 else SHRUNK


def compute(sales: Iterable[TradeRow], names: dict[str, str],
            comparison: Comparison) -> Flow:
    """Decompose the movement between the two periods of ``comparison``."""
    rows = list(sales)
    grouped = agg.by_customer(rows)

    flow = Flow(
        comparison=comparison,
        previous_total=revenue_in(rows, comparison.previous),
        current_total=revenue_in(rows, comparison.current),
    )

    for customer_id, customer_rows in grouped.items():
        prev = revenue_in(customer_rows, comparison.previous)
        cur = revenue_in(customer_rows, comparison.current)
        if prev <= 0 and cur <= 0:
            continue                        # outside both windows entirely
        kind = classify(prev, cur, _traded_before(customer_rows, comparison.previous))
        move = CustomerMove(customer_id=customer_id,
                            label=names.get(customer_id, customer_id),
                            kind=kind, previous=prev, current=cur)
        flow.moves.append(move)
        flow.buckets[kind] = flow.buckets.get(kind, 0.0) + move.delta

    return flow
