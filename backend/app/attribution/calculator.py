"""The arithmetic of value attribution, and nothing else.

Pure functions over ``Decimal``. No ``Session``, no query, no clock, no I/O —
so every number this platform claims to have delivered can be re-derived from
the operands recorded alongside it, by a test, on a laptop, without a database.

Two rules shape every signature here, and both come from CLAUDE.md §1.

**A missing operand is UNKNOWN, never zero.** Every argument is ``Optional`` and
every function returns ``None`` the moment one is missing. That is deliberately
inconvenient: it forces the caller to decide what a missing cost *means* instead
of letting ``0`` flow into a total that then reads as a measured fact. A zero is
an amount. It sums. UNKNOWN does not.

**A non-positive result is also ``None``, not zero.** A price that already
clears its floor protected nothing, and a line that recorded no protection is
not a ₹0 entry in a ledger — it is not an entry at all. Returning ``None`` keeps
the ledger's row count meaning "interventions that were worth something", which
is what a renewal conversation is actually arguing about.

The three money functions are all the same shape — a gap between two prices,
multiplied by a quantity — and they are three functions rather than one because
the *operands* differ and ``basis`` records them by name. ``_gap`` is the shared
implementation, so there is one rounding rule and one sign convention.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

#: ``ValueEvent.amount`` is ``Numeric(18, 4)``. Quantizing here rather than at
#: the database boundary means the value a test asserts is the value stored, and
#: that two runs over the same rows produce identical bytes.
MONEY_EXPONENT = Decimal("0.0001")

#: ROI is a ratio (``2.5`` is 2.5×), never a percentage, matching the margin
#: convention used everywhere else in this codebase.
RATIO_EXPONENT = Decimal("0.0001")

_ZERO = Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_EXPONENT, rounding=ROUND_HALF_UP)


def _gap(higher: Optional[Decimal], lower: Optional[Decimal],
         quantity: Optional[Decimal]) -> Optional[Decimal]:
    """``(higher - lower) x quantity``, or ``None`` if that is not a real number.

    ``None`` covers three distinct situations on purpose, because all three mean
    "do not put a number in the ledger": an operand is missing, the quantity is
    not a positive count of units, or the gap does not favour the business. The
    caller distinguishes them by checking its own operands *before* calling —
    which is why every detector here names its skip reason rather than inferring
    it from a ``None`` coming back.
    """
    if higher is None or lower is None or quantity is None:
        return None
    if quantity <= _ZERO:
        return None
    gap = (higher - lower) * quantity
    if gap <= _ZERO:
        return None
    return _money(gap)


def margin_protected(floor_price: Optional[Decimal],
                     final_price: Optional[Decimal],
                     quantity: Optional[Decimal]) -> Optional[Decimal]:
    """The gross profit standing between a quoted price and the floor under it.

    This is the same shortfall arithmetic ``quote_exceptions._shortfall`` already
    uses for a below-floor exception's ``impact_amount``, and deliberately so: the
    figure a manager sees on the quote screen when a line is flagged, and the
    figure the value ledger records for that same flag, must be the same number.
    Two implementations would eventually disagree, and the day they did, the one
    on the renewal invoice would be the wrong one.

    ``None`` when the price is already at or above the floor — nothing was
    protected, and a ₹0 row would count an intervention that did not happen.
    """
    return _gap(floor_price, final_price, quantity)


def discount_leakage_prevented(opening_price: Optional[Decimal],
                               final_price: Optional[Decimal],
                               quantity: Optional[Decimal]) -> Optional[Decimal]:
    """What a line recovered between the price first proposed and the price sent.

    Note the operand order is the reverse of ``margin_protected``: there the
    business number is the *higher* one (the floor) and the quoted price is
    below it; here the business number is the *final* price and the opening
    proposal is below it. ``None`` when the price did not move up, which is the
    ordinary case and not a failure.
    """
    return _gap(final_price, opening_price, quantity)


def equivalent_saving(original_unit_cost: Optional[Decimal],
                      alternative_unit_cost: Optional[Decimal],
                      quantity: Optional[Decimal]) -> Optional[Decimal]:
    """What buying the alternative saved against the originally-specified item.

    Both operands are *costs*, not prices: the saving is on the buy side, and
    computing it from selling prices would silently measure a margin change
    instead. ``None`` when the alternative is not cheaper — an equivalent
    offered for availability rather than price saved nothing, and saying so is
    the honest answer.
    """
    return _gap(original_unit_cost, alternative_unit_cost, quantity)


def roi(attributed_value: Optional[Decimal],
        pie_cost: Optional[Decimal]) -> Optional[Decimal]:
    """Attributed value per rupee of platform cost, or ``None`` for UNKNOWN.

    ``None`` when the cost is missing or zero, and the caller **must render that
    as UNKNOWN rather than as 0x or as infinity**. A zero cost does not make the
    return infinite; it means nobody has told this platform what it costs, and a
    ratio computed against a number nobody supplied is a fabricated number
    however deterministically it was divided.

    A genuine ``0`` comes back only when ``attributed_value`` is a measured zero
    against a real cost — that is a true and useful statement, and unlike the
    cases above it is one the evidence supports.
    """
    if attributed_value is None or pie_cost is None:
        return None
    if pie_cost <= _ZERO:
        return None
    if attributed_value < _ZERO:
        return None
    return (attributed_value / pie_cost).quantize(RATIO_EXPONENT,
                                                  rounding=ROUND_HALF_UP)
