"""Floor price resolution, including the aged schedule.

F is the device that resolves I1 against I4. S sees F and P and computes their
own points exactly; F = cost x (1 + m_floor) with m_floor unpublished and
varying by family, so a single observed line does not invert to cost. Two lines
in different families do not invert either, because the two m_floor values
differ and the salesperson knows neither.

**Nothing in this module needs cost.** The aged schedule scales the *normal
floor*, not the cost, which keeps the entire aged path inside the operations
zone. Expressing it as a fraction of cost would have required cost at the point
of computing an operations number, and that is exactly the leak the design is
built to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from .config import Config
from .models import FloorPriceSchedule

_ZERO = Decimal("0")


@dataclass(frozen=True)
class ResolvedFloor:
    floor_price: Decimal
    is_aged: bool
    age_days: Optional[int]
    floor_fraction: Decimal
    basis: str


def published_floor(schedules: Iterable[FloorPriceSchedule], entity_id: str,
                    item_id: str, on: date) -> Optional[Decimal]:
    """The floor in force for this item on this date, or None if unpublished.

    None is a real answer and must not become zero. An item with no published
    floor cannot earn CAF, because ``P - 0`` would pay full price as
    contribution and turn every unmastered item into a jackpot — which is
    exploit territory and also exactly what the G2 data-integrity gate exists
    to prevent.
    """
    best: Optional[FloorPriceSchedule] = None
    for s in schedules:
        if s.entity_id != entity_id or s.item_id != item_id:
            continue
        if on < s.effective_from:
            continue
        if s.effective_to is not None and on > s.effective_to:
            continue
        if best is None or s.effective_from > best.effective_from:
            best = s
    return best.floor_price if best else None


def resolve(cfg: Config, base_floor: Decimal,
            stock_age_days: Optional[int]) -> ResolvedFloor:
    """Apply the aged schedule to a published floor."""
    if stock_age_days is None:
        return ResolvedFloor(base_floor, False, None, Decimal("1"), "published floor")

    for band in cfg.get("floor", "aged_floor_schedule"):
        lo, hi = band["from_days"], band["to_days"]
        if stock_age_days >= lo and (hi is None or stock_age_days <= hi):
            fraction = Decimal(str(band["floor_fraction"]))
            return ResolvedFloor(
                floor_price=(base_floor * fraction),
                is_aged=bool(band["is_aged"]),
                age_days=stock_age_days,
                floor_fraction=fraction,
                basis=(f"aged {stock_age_days}d, floor at {fraction} of published"
                       if band["is_aged"] else "published floor"),
            )
    # Unreachable with a well-formed schedule; loud rather than silently full.
    raise ValueError(f"no aged floor band covers {stock_age_days} days")


class UnknownFamily(KeyError):
    """A family name that has no entry in the floor table."""

    def __init__(self, family: str, known: Iterable[str]) -> None:
        self.family = family
        self.known = sorted(known)
        super().__init__(family)


def m_floor_for_family(cfg: Config, family: str, *, strict: bool = False) -> Decimal:
    """OWNER ZONE. The floor multiplier for a family.

    ``strict`` decides what an unrecognised family means, and the two callers
    genuinely need different answers.

    Reporting over *recorded* lines is lenient: ``item_family`` comes off a sold
    line, an item mastered without a family must still appear in the owner's
    reconciliation, and falling back to the default multiplier is the honest
    reading of "no family was set".

    Resolving a floor from a *request* is strict. The docstring at the top of
    this module rests on the salesperson not knowing ``m_floor`` — but if they
    choose the family, they can price one item under two families and read the
    ratio of the two multipliers straight off the two floors, then sweep the
    name to enumerate the table. Silently substituting the default is what makes
    that free: an unknown name has to be refused for the parameter to stay
    unpublished.
    """
    table = cfg.get("floor", "m_floor_by_family")
    if family not in table:
        if strict:
            raise UnknownFamily(family, table.keys())
        return Decimal(str(table["default"]))
    return Decimal(str(table[family]))
