"""Management-side pricing policy, applied to a Quote Builder line.

The *policy* — target margins by tool family, the hard minimum, the soft floor,
the salesperson's discretionary band — is no longer defined here. It lives in
``app.commercial.config.CommercialThresholds`` alongside every other commercial
threshold, so there is exactly one place to change it and exactly one version
hash covering it. This module is the thin application of that policy to a line.

That consolidation matters: before it, the Quote Builder recommended a price
from constants in this file while the Customer × Item analysis judged the very
same margin against a different set, and nothing kept the two in step.

These computations are the "full economics" the design keeps out of the
salesperson client entirely. The API layer is responsible for never serialising
any of it to a sales-role response — this module just computes it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .commercial.config import CommercialThresholds, load_commercial_thresholds


def _th() -> CommercialThresholds:
    return load_commercial_thresholds()


def __getattr__(name: str):
    """Module-level policy constants, resolved from the central thresholds.

    Kept as attributes (``pricing.MARGIN_FLOOR``) because callers and tests read
    them that way, but resolved on access so an env override applies without a
    reimport — and so there is no second copy of the number to drift.
    """
    th = _th()
    if name == "DEFAULT_TARGET":
        return th.target_margin_default
    if name == "FAMILY_TARGET":
        return dict(th.target_margin_by_family)
    if name == "SALES_BAND":
        return th.sales_discretion_band
    if name == "MIN_MARGIN":
        return th.min_margin
    if name == "MARGIN_FLOOR":
        return th.margin_floor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


def target_margin(family: Optional[str],
                  th: Optional[CommercialThresholds] = None) -> float:
    return (th or _th()).target_margin(family)


def recommend_price(cost: Optional[float], family: Optional[str],
                    th: Optional[CommercialThresholds] = None) -> Optional[float]:
    """Recommended selling price to hit the family target margin, rounded.

    The rounding increment is policy, not a constant: it used to be a literal 5,
    which reads as "nearest ₹5" and is right for a rupee-priced insert, but the
    same literal against a $100 tool rounds away 5% of the price. An increment
    of 0 returns the unrounded figure.
    """
    if cost is None or cost <= 0:
        return None
    rec = cost / (1 - target_margin(family, th))
    step = (th or _th()).price_rounding_increment
    return round(rec / step) * step if step > 0 else rec


def margin_pct(price: Optional[float], cost: Optional[float]) -> Optional[float]:
    if price is None or cost is None or price <= 0:
        return None
    return (price - cost) / price


def compute_economics(
    cost: Optional[float], list_price: Optional[float],
    quoted: Optional[float], family: Optional[str],
    th: Optional[CommercialThresholds] = None,
) -> Economics:
    th = th or _th()
    rec = recommend_price(cost, family, th)
    margin = margin_pct(quoted, cost)
    below = margin is not None and margin < th.margin_floor
    return Economics(cost=cost, list_price=list_price, recommended=rec,
                     quoted=quoted, margin=margin, below_floor=below)


def within_authority(requested: Optional[float], recommended: Optional[float],
                     th: Optional[CommercialThresholds] = None) -> bool:
    """Is a salesperson's requested price within their discretionary band?"""
    if requested is None or recommended is None:
        return False
    return requested >= recommended * (1 - (th or _th()).sales_discretion_band)
