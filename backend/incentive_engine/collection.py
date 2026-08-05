"""c(d) — collection as the earning condition, not a KPI.

CAF is banked on invoice and earned on receipt. This one table replaces an
entire collections KPI and removes any gain from billing early, from selling to
a customer who will not pay, or from agreeing informal credit that never
appears in the system: none of it changes the invoice date, and all of it moves
the receipt date.

**Partial payment is weighted, not all-or-nothing.** An invoice settled 60% on
time and 40% at day 100 earns 0.6 x 1.00 + 0.4 x 0.25. Taking the last receipt
would let a token final payment destroy a well-collected invoice; taking the
first would let a token early payment rescue a bad one.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .config import Config
from .models import Payment

_ZERO = Decimal("0")


def factor(cfg: Config, days_late: int) -> Decimal:
    """The conversion for a receipt this many days past the due date."""
    for band in cfg.get("collection", "bands"):
        lo, hi = band["from"], band["to"]
        if lo is None and days_late <= hi:
            return Decimal(str(band["factor"]))
        if lo is not None and days_late >= lo and (hi is None or days_late <= hi):
            return Decimal(str(band["factor"]))
    raise ValueError(f"no collection band covers {days_late} days")


def collected_fraction(cfg: Config, invoice_value: Decimal,
                       payments: Iterable[Payment]) -> Decimal:
    """Value-weighted c(d) across every receipt against one invoice.

    An invoice with nothing received earns nothing — not because it is late,
    but because it has not been earned yet. That is the definition, not a
    penalty, and it is why there is no separate collections objective.
    """
    if invoice_value <= _ZERO:
        return _ZERO
    total = _ZERO
    weighted = _ZERO
    for p in payments:
        total += p.amount
        weighted += p.amount * factor(cfg, p.days_late)
    if total <= _ZERO:
        return _ZERO
    # Over-receipt (advance plus settlement) must not scale points above the
    # invoice: the cap is the invoice, the conversion is the average.
    settled = min(total, invoice_value)
    return (weighted / total) * (settled / invoice_value)


def triggers_clawback(cfg: Config, days_late: int) -> bool:
    return days_late > cfg.int_("collection", "clawback_after_days")
