"""Management-side pricing engine.

Mirrors the artifact's private pricing rules. These computations are the
"full economics" the design keeps out of the salesperson client entirely: a
recommended price and a margin, derived from landed cost and target margins by
tool family. The API layer is responsible for never serialising any of this to
a sales-role response — this module just computes it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# Management-defined pricing policy (never sent to a sales client).
DEFAULT_TARGET = 0.24
FAMILY_TARGET: Dict[str, float] = {
    "solid_carbide_drill": 0.28,
    "solid_carbide_endmill": 0.28,
    "milling_insert": 0.30,
    "drill_tip": 0.30,
    "reamer": 0.27,
}
SALES_BAND = 0.03          # salesperson may move ±3% off recommended without approval
MIN_MARGIN = 0.12          # hard commercial floor
MARGIN_FLOOR = 0.15        # soft floor: lines below this are flagged for review


@dataclass
class Economics:
    """Management-only economics for a priced line."""

    cost: Optional[float]
    list_price: Optional[float]
    recommended: Optional[float]
    quoted: Optional[float]
    margin: Optional[float]           # on the quoted price
    below_floor: bool

    def to_dict(self) -> dict:
        return {
            "cost": self.cost, "list_price": self.list_price,
            "recommended": self.recommended, "quoted": self.quoted,
            "margin": self.margin, "below_floor": self.below_floor,
        }


def target_margin(family: Optional[str]) -> float:
    if family and family in FAMILY_TARGET:
        return FAMILY_TARGET[family]
    return DEFAULT_TARGET


def recommend_price(cost: Optional[float], family: Optional[str]) -> Optional[float]:
    """Recommended selling price to hit the family target margin, rounded to ₹5."""
    if cost is None or cost <= 0:
        return None
    rec = cost / (1 - target_margin(family))
    return round(rec / 5) * 5


def margin_pct(price: Optional[float], cost: Optional[float]) -> Optional[float]:
    if price is None or cost is None or price <= 0:
        return None
    return (price - cost) / price


def compute_economics(
    cost: Optional[float], list_price: Optional[float],
    quoted: Optional[float], family: Optional[str],
) -> Economics:
    rec = recommend_price(cost, family)
    margin = margin_pct(quoted, cost)
    below = margin is not None and margin < MARGIN_FLOOR
    return Economics(cost=cost, list_price=list_price, recommended=rec,
                     quoted=quoted, margin=margin, below_floor=below)


def within_authority(requested: Optional[float], recommended: Optional[float]) -> bool:
    """Is a salesperson's requested price within their discretionary band?"""
    if requested is None or recommended is None:
        return False
    return requested >= recommended * (1 - SALES_BAND)
