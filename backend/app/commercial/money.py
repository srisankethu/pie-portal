"""Rendering an already-computed amount as money, in the tenant's currency.

This module formats. It does not compute — no rounding of prices, no rate
arithmetic, nothing that changes a number's value. `commercial/` owns the
numbers; this owns how one is spelled.

It exists because the rupee sign used to be hardcoded in six places (two
``_rupees`` helpers here, four ``inr`` helpers in the front end). That is
cheap to live with while there is one tenant in one country and expensive
the moment there are two, because the symbol is not the only thing that
differs — India groups digits as 4,00,000 where most of the world groups
them as 400,000, and a "₹" glued onto a Western grouping is wrong twice.

Unknown currency codes are rendered as ``MXN 4,00,000``-style prefixes rather
than guessed at. A wrong symbol on a real amount is worse than no symbol.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

Amount = Union[Decimal, float, int]

#: Symbols for the currencies this platform has actually been pointed at.
#: Deliberately short — an unlisted code falls back to the ISO code, which is
#: unambiguous, rather than to a symbol that might belong to another currency
#: ("$" is at least six different currencies).
_SYMBOL: dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AED": "AED ",
    "SGD": "S$",
    "AUD": "A$",
    "CAD": "C$",
}

#: Currencies grouped in the Indian system (last three digits, then pairs):
#: 4,00,000 rather than 400,000. Same convention across the subcontinent.
_LAKH_GROUPED = frozenset({"INR", "PKR", "BDT", "NPR", "LKR"})


def symbol(currency: str) -> str:
    """The prefix for a currency code. Falls back to the code itself."""
    code = (currency or "").strip().upper()
    return _SYMBOL.get(code) or (f"{code} " if code else "")


def group(value: Decimal, currency: str) -> str:
    """Digit grouping for the currency's convention, no symbol, no decimals."""
    whole = int(value.to_integral_value(rounding="ROUND_HALF_UP"))
    sign = "-" if whole < 0 else ""
    digits = str(abs(whole))

    if (currency or "").strip().upper() not in _LAKH_GROUPED:
        return sign + f"{abs(whole):,}"

    # Indian grouping: the last three digits, then two at a time.
    if len(digits) <= 3:
        return sign + digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts + [tail])


def money(amount: Optional[Amount], currency: str,
          unknown: str = "unknown") -> str:
    """``₹4,00,000`` / ``$400,000`` / ``MXN 400,000``.

    Whole units only. Every caller here is writing a sentence a person reads
    ("you are ₹1,650 under their last price"), and paise in that sentence is
    noise that makes the number harder to hold, not more accurate.
    """
    if amount is None:
        return unknown
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return symbol(currency) + group(value, currency)
