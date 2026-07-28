"""Quantity bands — the part of a price's identity that averages destroy.

A Customer × Item relationship is not one price. The same customer buying the
same insert at 5 pieces and at 500 is answering two different commercial
questions, and a single quantity-weighted average across both is the wrong
reference for either. Every price reference this package produces is therefore
band-aware: "what has this customer paid *at roughly this quantity*".

Bands are edges from configuration, not literals in a detector, so the shape of
the ladder is a policy decision with a version hash attached. They are inclusive
upper bounds: edges ``(1, 10, 50, 200)`` give 1 / 2–10 / 11–50 / 51–200 / 201+.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from .config import CommercialThresholds
from .economics import LineEconomics


@dataclass(frozen=True)
class QuantityBand:
    """One rung of the quantity ladder."""

    index: int
    low: int                      # inclusive
    high: Optional[int]           # inclusive; None = open-ended top band

    @property
    def label(self) -> str:
        if self.high is None:
            return f"{self.low}+"
        if self.low == self.high:
            return str(self.low)
        return f"{self.low}–{self.high}"

    def contains(self, qty: Decimal) -> bool:
        if qty < self.low:
            return False
        return self.high is None or qty <= self.high

    def to_dict(self) -> dict:
        return {"index": self.index, "low": self.low, "high": self.high,
                "label": self.label}


def bands(th: CommercialThresholds) -> list[QuantityBand]:
    """The full ladder, ascending. Always at least one (open-ended) band."""
    edges = [e for e in sorted(set(th.quantity_band_edges)) if e > 0]
    out: list[QuantityBand] = []
    low = 1
    for i, edge in enumerate(edges):
        out.append(QuantityBand(index=i, low=low, high=edge))
        low = edge + 1
    out.append(QuantityBand(index=len(edges), low=low, high=None))
    return out


def band_for(qty: Decimal, th: CommercialThresholds) -> QuantityBand:
    """The band a quantity falls in.

    A zero or negative quantity is not a commercial quantity; it is clamped into
    the lowest band rather than given a band of its own, because a band nobody
    can legitimately buy in would then appear in references.
    """
    ladder = bands(th)
    if qty < 1:
        return ladder[0]
    for band in ladder:
        if band.contains(qty):
            return band
    return ladder[-1]


def lines_in_band(lines: Iterable[LineEconomics], band: QuantityBand) -> list[LineEconomics]:
    return [ln for ln in lines if band.contains(ln.qty)]
