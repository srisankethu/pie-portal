"""Deterministic price references for a quote line.

A salesperson pricing a line is answering "what should this cost them?" — and
the honest inputs to that question are all already in the data: what this
customer last paid, what they pay at this quantity, what everyone else pays,
and what the item has to earn. This module computes those references. It does
not choose between them, does not recommend, and contains no model call: every
value is a function of invoice and bill lines that have already been costed.

Two references are derived from purchase cost (``…_MARGIN_PRICE``) and are
therefore RESTRICTED — handing a salesperson "the floor is ₹1,798" alongside a
known 12% floor is handing them the cost. The rest are prices the customer has
already seen, or that a peer has, and are classified accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .benchmark import ItemBenchmark
from .config import CommercialThresholds
from .economics import LineEconomics, aggregate, in_window
from .quantity import QuantityBand, lines_in_band

OPERATIONAL = "OPERATIONAL"
RESTRICTED = "RESTRICTED"

_ZERO = Decimal("0")

# Reference codes. Stable strings — they are persisted in quote snapshots.
LAST_PRICE_PAID = "LAST_PRICE_PAID"
BAND_PRICE = "BAND_PRICE"
RECENT_AVG_PRICE = "RECENT_AVG_PRICE"
PEER_MEDIAN_PRICE = "PEER_MEDIAN_PRICE"
HISTORICAL_MARGIN_PRICE = "HISTORICAL_MARGIN_PRICE"
TARGET_MARGIN_PRICE = "TARGET_MARGIN_PRICE"
MARGIN_FLOOR_PRICE = "MARGIN_FLOOR_PRICE"
MIN_MARGIN_PRICE = "MIN_MARGIN_PRICE"


@dataclass(frozen=True)
class PriceReference:
    """One comparable price, with the evidence it rests on."""

    code: str
    label: str
    value: Decimal
    basis: str                       # a plain sentence — where this came from
    data_class: str
    as_of: Optional[date] = None
    txn_count: int = 0
    qty_band: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code, "label": self.label,
            "value": float(round(self.value, 2)), "basis": self.basis,
            "data_class": self.data_class,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "txn_count": self.txn_count, "qty_band": self.qty_band,
        }


def price_at_margin(cost: Decimal, margin: float) -> Optional[Decimal]:
    """The selling price that earns ``margin`` on ``cost``.

    Margin here is on the *selling* price (gross profit ÷ revenue), the same
    definition used everywhere else in this package, so the inverse is
    ``cost / (1 - margin)`` and not ``cost * (1 + margin)``. Getting that
    backwards understates every floor by several points.
    """
    if cost <= _ZERO or not (0 <= margin < 1):
        return None
    return cost / (Decimal("1") - Decimal(str(margin)))


def build_references(
    *,
    lines: list[LineEconomics],
    band: QuantityBand,
    unit_cost: Optional[Decimal],
    benchmark: Optional[ItemBenchmark],
    historical_margin: Optional[float],
    family: Optional[str],
    as_of: date,
    th: CommercialThresholds,
) -> list[PriceReference]:
    """Every reference that can be computed, in the order a quoter reads them.

    ``lines`` is this customer's costed history for this item. References that
    cannot be computed are omitted rather than defaulted — a reference price of
    zero is worse than a missing one.
    """
    out: list[PriceReference] = []
    ordered = sorted(lines, key=lambda ln: ln.date)

    # 1. What they last actually paid. The single most persuasive number in the
    #    room, and the one a customer will quote back.
    if ordered:
        last = ordered[-1]
        out.append(PriceReference(
            code=LAST_PRICE_PAID, label="Last price paid",
            value=last.net_unit_price,
            basis=(f"net of discount on {last.date.isoformat()}, "
                   f"{_qty(last.qty)} units"),
            data_class=OPERATIONAL, as_of=last.date, txn_count=1))

    # 2. What they pay *at this quantity*. Compared against an all-quantities
    #    average, a bulk line always looks under-priced and a single piece
    #    always looks generous.
    banded = lines_in_band(ordered, band)
    if len(banded) >= th.min_band_transactions:
        period = aggregate(banded)
        if period.qty > _ZERO:
            out.append(PriceReference(
                code=BAND_PRICE, label=f"Typical price at {band.label}",
                value=period.revenue / period.qty,
                basis=(f"quantity-weighted across {period.txn_count} orders in "
                       f"the {band.label} band"),
                data_class=OPERATIONAL,
                as_of=max(ln.date for ln in banded),
                txn_count=period.txn_count, qty_band=band.label))

    # 3. Where the relationship has been trading lately, all quantities.
    recent = in_window(ordered, as_of - timedelta(days=th.recent_days), as_of)
    if recent:
        period = aggregate(recent)
        if period.qty > _ZERO:
            out.append(PriceReference(
                code=RECENT_AVG_PRICE,
                label=f"Average, last {th.recent_days} days",
                value=period.revenue / period.qty,
                basis=(f"quantity-weighted across {period.txn_count} orders "
                       f"since {(as_of - timedelta(days=th.recent_days)).isoformat()}"),
                data_class=OPERATIONAL,
                as_of=max(ln.date for ln in recent), txn_count=period.txn_count))

    # 4. What the rest of the book pays. Only where the peer population is large
    #    enough to be a market rather than an anecdote.
    if benchmark is not None and benchmark.is_reliable(th) and benchmark.median_price:
        out.append(PriceReference(
            code=PEER_MEDIAN_PRICE, label="Median across other customers",
            value=benchmark.median_price,
            basis=(f"median of {benchmark.peer_count} other customers buying "
                   f"this item in the last {th.peer_recency_days} days"),
            data_class=RESTRICTED, as_of=as_of, txn_count=benchmark.peer_count))

    # 5–8. Cost-derived references — the ladder a manager prices against:
    #      target, then the review floor, then the approval floor. Absent
    #      entirely when cost is unknown: a floor invented without a cost is not
    #      a floor.
    if unit_cost is not None and unit_cost > _ZERO:
        if historical_margin is not None and 0 <= historical_margin < 1:
            value = price_at_margin(unit_cost, historical_margin)
            if value is not None:
                out.append(PriceReference(
                    code=HISTORICAL_MARGIN_PRICE,
                    label="Holds this relationship's usual margin",
                    value=value,
                    basis=(f"today's cost at the {historical_margin * 100:.1f}% "
                           f"margin this relationship historically earned"),
                    data_class=RESTRICTED, as_of=as_of))

        target = th.target_margin(family)
        value = price_at_margin(unit_cost, target)
        if value is not None:
            out.append(PriceReference(
                code=TARGET_MARGIN_PRICE, label="Target margin price",
                value=value,
                basis=(f"today's cost at the {target * 100:.0f}% target margin"
                       + (f" for {family.replace('_', ' ')}" if family else "")),
                data_class=RESTRICTED, as_of=as_of))

        value = price_at_margin(unit_cost, th.margin_floor)
        if value is not None:
            out.append(PriceReference(
                code=MARGIN_FLOOR_PRICE, label="Review floor",
                value=value,
                basis=(f"today's cost at the {th.margin_floor * 100:.0f}% margin "
                       f"below which a line is flagged for review"),
                data_class=RESTRICTED, as_of=as_of))

        value = price_at_margin(unit_cost, th.min_margin)
        if value is not None:
            out.append(PriceReference(
                code=MIN_MARGIN_PRICE, label="Approval floor",
                value=value,
                basis=(f"today's cost at the {th.min_margin * 100:.0f}% minimum "
                       f"margin — below this needs approval"),
                data_class=RESTRICTED, as_of=as_of))
    return out


def by_code(refs: list[PriceReference]) -> dict[str, PriceReference]:
    return {r.code: r for r in refs}


def _qty(q: Decimal) -> str:
    return f"{q:.0f}" if q == q.to_integral_value() else f"{q}"
