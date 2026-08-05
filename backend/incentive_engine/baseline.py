"""Rolling-12 baselines, and the aged-stock exclusion.

The measurement window is rolling 12 months recomputed monthly, and that choice
does more work than any anti-gaming rule could. There is no period boundary, so
there is nothing to sandbag into, nothing to pull forward, and no month-end
worth splitting an order across. Exploits 18-21 die structurally rather than by
policing.

**Aged-stock CAF is excluded from the baseline (Q1(b)).** Clearing dead stock is
a one-off. Letting a five-lakh clearance into a customer's baseline would
punish the salesperson next year for a windfall they cannot repeat, and the
rational response to that is to *not clear the stock* — the exact opposite of
what the recovery pool exists to cause.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from .caf import LineCAF

_ZERO = Decimal("0")


@dataclass(frozen=True)
class CustomerBaseline:
    customer_group_id: str
    baseline_caf: Decimal
    current_caf: Decimal
    incremental_caf: Decimal
    #: Reported so a reader can see what was held out and why, rather than
    #: wondering why the baseline does not tie to the invoice total.
    aged_excluded: Decimal

    @property
    def yoy_change(self) -> Decimal:
        if self.baseline_caf <= _ZERO:
            return _ZERO
        return (self.current_caf - self.baseline_caf) / self.baseline_caf


def _window(items: Iterable[tuple[LineCAF, Decimal]], start: date,
            end: date) -> tuple[Decimal, Decimal]:
    """Returns (countable CAF, aged CAF excluded) for the window."""
    countable = _ZERO
    aged = _ZERO
    for line_caf, collected in items:
        day = line_caf.line.invoice_date
        if not (start <= day <= end):
            continue
        value = line_caf.caf * collected
        if line_caf.is_aged:
            aged += value
        else:
            countable += value
    return countable, aged


def build(collected: Sequence[tuple[LineCAF, Decimal]],
          customer_group_id: str, current_start: date, current_end: date,
          prior_start: date, prior_end: date) -> CustomerBaseline:
    """Baseline is the PRIOR rolling 12; incremental is current minus it."""
    mine = [(lc, c) for lc, c in collected
            if lc.line.customer_group_id == customer_group_id]
    current, aged = _window(mine, current_start, current_end)
    baseline, _ = _window(mine, prior_start, prior_end)
    return CustomerBaseline(
        customer_group_id=customer_group_id,
        baseline_caf=baseline,
        current_caf=current,
        # Floored at zero: a declining account earns w_base on what it still
        # produces, never a negative incremental on top of it. The retention
        # gate is the instrument for decline, not a negative multiplier.
        incremental_caf=max(_ZERO, current - baseline),
        aged_excluded=aged,
    )
