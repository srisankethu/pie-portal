"""How fast a line has actually moved, and how long the shelf covers that rate.

**Quantity only.** Nothing in this module reads a purchase rate, a selling
price or a carrying rate, and nothing it returns is denominated in money. That
is not incidental — it is the reason a days-of-cover figure is safe on an
operations screen where ``inventory_value`` and the carrying rate are withheld.
``CLAUDE.md`` §1 records the precedent in the other direction: the monthly
carrying drain *is* withheld once the carrying rate is published, because
quantity x cost x rate recovers the cost. Cover has no cost term to recover, so
it stays.

**A measurement, never a forecast.** ``days_of_cover`` is a ratio of two
recorded facts — what is on the shelf, and the rate at which units have left it
since this item first sold. It says "you hold 240 days at the rate this has
actually moved", which is arithmetic over the past. It does *not* say "this will
last 240 days", which would be a projection this data cannot support. The
difference is the whole reason ``insight/stock.py`` can show the first while
still refusing the second, and that refusal is still in the response — see
``_unavailable`` there.

**Measured from the first sale, not the last movement.** The window is
``first_sold_on`` to ``as_of``. Measuring from the last movement instead would
give a period of days, an enormous implied daily rate, and a cover figure that
never fires — the arithmetic version of measuring a year's rainfall with this
morning's bucket. This is the one modelling choice in the module and it is not
an accident; do not "improve" it to a trailing window.

**Never sold is UNKNOWN.** An item with no sale on record has no offtake rate,
so it has no cover — ``None``, never zero and never an infinity that sorts to
the top of a "most cover" list. ``CLAUDE.md`` §1: absence of evidence is not a
pass, in either direction.

One copy, two callers: ``commercial/insight/stock.py`` renders the column and
``state/opportunities/inventory.py``'s ``ExcessCoverDetector`` raises the
decision card. Both used to be one calculation with one copy; the second copy
was written here rather than pasted, so the screen and the queue cannot come to
disagree about how much of something the business holds.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional, Union

Quantity = Union[Decimal, float, int]

#: A month, for turning a daily offtake rate into months of cover. 30 rather
#: than 30.4: cover is a coarse measure and a spurious decimal in the divisor
#: would imply a precision the underlying "units sold since we first saw it"
#: does not have.
DAYS_PER_MONTH = Decimal(30)


def _decimal(value: Quantity) -> Decimal:
    """A quantity from either side of the wire as an exact Decimal.

    ``str()`` first, deliberately: ``Decimal(0.1)`` is 0.1000000000000000055…,
    and ``insight/stock.py`` works in float while the state detectors work in
    Decimal. One conversion, here, so the two callers cannot get different
    answers out of the same shelf.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


def observed_days(first_sold: date, as_of: date) -> Decimal:
    """The window an offtake rate is measured over, in days.

    Floored at one. A line that first sold today has a one-day window rather
    than a zero-day one — the alternative is a division by zero, and the honest
    reading of "it sold today" is a rate over one day, not an undefined one.
    """
    return Decimal(max((as_of - first_sold).days, 1))


def daily_offtake(units_sold: Optional[Quantity], first_sold: Optional[date],
                  as_of: date) -> Optional[Decimal]:
    """Units leaving the shelf per day, measured from the first sale to ``as_of``.

    ``None`` when there is no rate to measure: nothing sold, or no first-sale
    date on record. Not zero — a zero rate divides into an infinite cover, and
    "we hold forever of this" is a statement about an item nobody has ever
    bought rather than about the shelf.
    """
    if units_sold is None or first_sold is None:
        return None
    sold = _decimal(units_sold)
    if sold <= 0:
        return None
    rate = sold / observed_days(first_sold, as_of)
    return rate if rate > 0 else None


def days_of_cover(on_hand: Optional[Quantity], units_sold: Optional[Quantity],
                  first_sold: Optional[date], as_of: date) -> Optional[Decimal]:
    """How many days of measured offtake the current shelf amounts to.

    ``None`` where the rate is unknown, which is the only honest answer for an
    item that has never sold. Zero where the rate is known and the shelf is
    empty — that is a measurement, and a real one: no cover at all.
    """
    rate = daily_offtake(units_sold, first_sold, as_of)
    if rate is None:
        return None
    quantity = _decimal(on_hand) if on_hand is not None else Decimal(0)
    if quantity <= 0:
        return Decimal(0)
    return quantity / rate


def months_of_cover(on_hand: Optional[Quantity], units_sold: Optional[Quantity],
                    first_sold: Optional[date], as_of: date) -> Optional[Decimal]:
    """``days_of_cover`` in months, for the thresholds that are set in months.

    ``excess_cover_months`` is a policy figure an owner sets in months, so the
    comparison happens in months rather than converting the threshold — a
    threshold converted at each of two call sites is two chances to convert it
    differently.
    """
    days = days_of_cover(on_hand, units_sold, first_sold, as_of)
    return None if days is None else days / DAYS_PER_MONTH
