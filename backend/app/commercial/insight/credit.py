"""What a customer owes, against the limit they were given.

The platform could already say that Rane Madras takes 69 days to pay and that
Bharat Forge is late but predictable. Neither sentence is a decision. What turns
an observation into one is a line: *this* is the credit we extended, *this* is
what is currently outstanding against it, and the difference decides whether the
next order ships. This module owns that arithmetic, and nothing else.

**Absent, zero and over are three different states.** No limit on record means
nobody has decided — which is where most of this book sits, and it is reported
as such rather than as an unlimited line or a zero one. A recorded limit of zero
is a decision: this account ships against cash. An account over its limit is a
number somebody has to act on today. The distinction is the same one
``insight/terms`` makes between an agreed payment term and the ERP's guess, and
it exists for the same reason — a screen that cannot tell "not decided" from
"decided to be nothing" will get one of them wrong every time.

**The outstanding side is read, never recomputed.** It arrives from the
``RECEIVABLES`` fold, which reads ``InvoiceDoc.balance`` — what Zoho says is
still owed. Deriving it here as invoiced-minus-received would be wrong the
moment a credit note is applied, and wrong in the direction that gets a customer
chased for money they do not owe. There is one outstanding balance in this
platform and this module is not a second one.

**Not the same "exposure" as ``state/opportunities/receivables``.** That
detector fires on a customer holding a large *share of the whole book's*
receivables — a concentration question, true even when every invoice of theirs
is current, and answered against the book rather than against any limit. This is
a customer against *their own* line. They can both be true of one account, and
neither is a restatement of the other: one asks "should we be this exposed at
all", this one asks "have they gone past what we agreed".

**Receivables, not cost.** A limit and a balance are money already billed, so
they may reach a salesperson — chasing your own overdue accounts is the job.
Contrast ``insight/supply``, which is purchase spend and does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

#: Rupees to the paisa, quantised once so two screens cannot disagree in the
#: second decimal place. Same rule as ``state/opportunities/base.money``.
_PAISA = Decimal("0.01")


class InvalidLimit(ValueError):
    """A credit limit that cannot mean an amount of money."""


#: A thousand crore. Not a business rule — a guard against a typo that would
#: silently make an account unlimited, which is the one failure mode of a limit
#: that nobody would notice. Same purpose as ``terms.MAX_TERM_DAYS``.
MAX_LIMIT = Decimal("10000000000")


def validate(amount: object) -> Decimal:
    """The one place a limit is checked, so the API and any future importer
    cannot disagree about what is storable.

    Zero is accepted and is not the same as no limit at all — see the module
    docstring. Negative is refused: an account cannot be extended less than
    nothing, and the row that would express it is a withdrawal, which is a
    delete.
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise InvalidLimit("a credit limit must be an amount of money") from e
    if not value.is_finite():
        raise InvalidLimit("a credit limit must be an amount of money")
    if value < 0:
        raise InvalidLimit("a credit limit cannot be negative — an account "
                           "extended nothing has a limit of zero, and one that "
                           "was never given a limit has no row at all")
    if value > MAX_LIMIT:
        raise InvalidLimit(f"a credit limit of {value} is not a limit, it is a typo")
    return value.quantize(_PAISA)


#: How much of a limit can be used before it is worth saying so. A share rather
#: than an amount, because ₹2 lakh of headroom is comfortable on a ₹50 lakh line
#: and almost nothing on a ₹2.5 lakh one.
#:
#: Not a persisted threshold and deliberately not stamped with a
#: ``thresholds_version``: nothing computed here is written to a row. It is a
#: reading of two stored numbers, recomputed on every request, exactly like
#: ``payments.ERRATIC_SPREAD_DAYS``. The value travels in the response so the
#: screen can say what it means rather than implying a rule nobody can see.
NEAR_LIMIT_SHARE = Decimal("0.90")

#: No limit has been recorded for this account. Not zero, not unlimited.
NO_LIMIT = "NO_LIMIT"
WITHIN = "WITHIN"
NEAR = "NEAR"
OVER = "OVER"

#: What each state means as a job of work. A status with no consequence attached
#: is a label; these are the four different things somebody does about an
#: account.
STATUSES: dict[str, dict[str, str]] = {
    NO_LIMIT: {
        "label": "No limit recorded",
        "meaning": "Nobody has decided what this account may owe. Not the same "
                   "as unlimited, and not the same as zero — there is simply "
                   "nothing to hold the balance against yet.",
    },
    WITHIN: {
        "label": "Within limit",
        "meaning": "Owed less than the line they were given. Nothing to do; "
                   "worth knowing before anyone tightens their terms.",
    },
    NEAR: {
        "label": "Near the limit",
        "meaning": "Most of the line is used. The next order is the one that "
                   "takes them over, so this is the conversation to have "
                   "before it is a hold rather than after.",
    },
    OVER: {
        "label": "Over the limit",
        "meaning": "Owed more than the credit extended to them. A decision, "
                   "not an observation: further supply is a choice somebody "
                   "has to make deliberately.",
    },
}


@dataclass(frozen=True)
class Balance:
    """What one customer currently owes. The narrow record this module needs.

    Three fields rather than a fold row or an ORM object, for the reason
    ``terms.Bill`` is three fields: the caller does the narrowing, and this
    module cannot grow a dependency on how receivables happen to be stored.
    """

    customer_id: str
    outstanding: Decimal
    #: The part of it past its due date. Carried because "₹8 lakh over the
    #: limit, none of it late" and "₹8 lakh over, all of it overdue" are two
    #: different phone calls.
    overdue: Decimal = Decimal(0)


@dataclass(frozen=True)
class Exposure:
    """One customer's balance against their limit, and what that means.

    ``limit`` is ``None`` when none is on record. Every figure derived from it
    is then ``None`` as well rather than a default, which is what stops a screen
    reporting infinite headroom for an account nobody has assessed.
    """

    customer_id: str
    outstanding: Decimal
    overdue: Decimal
    limit: Optional[Decimal]

    @property
    def has_limit(self) -> bool:
        return self.limit is not None

    @property
    def headroom(self) -> Optional[Decimal]:
        """What is left of the line. Negative when they are past it — the
        signed figure, so a caller that wants "how far over" asks ``over_by``
        and never has to know which way the subtraction ran."""
        if self.limit is None:
            return None
        return (self.limit - self.outstanding).quantize(_PAISA)

    @property
    def over_by(self) -> Optional[Decimal]:
        """How far past the limit, or zero when inside it. ``None`` when there
        is no limit — an account nobody assessed is not an account that is
        comfortably within one."""
        room = self.headroom
        if room is None:
            return None
        return -room if room < 0 else Decimal("0.00")

    @property
    def utilisation(self) -> Optional[Decimal]:
        """Share of the line used, as a ratio.

        ``None`` against a zero limit as well as an absent one: a share of
        nothing is not a number, and reporting it as 0 or as some large multiple
        would both be inventions. The status still answers the question there.
        """
        if self.limit is None or self.limit == 0:
            return None
        return (self.outstanding / self.limit).quantize(Decimal("0.0001"))

    @property
    def status(self) -> str:
        if self.limit is None:
            return NO_LIMIT
        if self.outstanding > self.limit:
            return OVER
        if self.limit == 0:
            # A zero line with nothing owed against it is being honoured, not
            # breached. "Near" is meaningless here — there is no room to be
            # near the end of.
            return WITHIN
        share = self.outstanding / self.limit
        return NEAR if share >= NEAR_LIMIT_SHARE else WITHIN


def exposure(balance: Balance, limit: Optional[Decimal]) -> Exposure:
    """One customer's exposure. The limit is passed in rather than looked up,
    because this module does not know where limits are stored."""
    return Exposure(customer_id=balance.customer_id,
                    outstanding=balance.outstanding.quantize(_PAISA),
                    overdue=balance.overdue.quantize(_PAISA),
                    limit=None if limit is None else limit.quantize(_PAISA))


def exposures(balances: Iterable[Balance],
              limits: dict[str, Decimal]) -> dict[str, Exposure]:
    """Every customer's exposure, keyed by customer id.

    A customer with no limit is **present** with ``limit=None``, unlike
    ``terms.shifts`` where a supplier without an agreement is absent. The
    difference is what the caller does next: there, absent means "leave the
    schedule alone"; here, an account owing money against no recorded limit is
    precisely the row somebody needs to see, because it is the one nobody has
    made a decision about.
    """
    return {b.customer_id: exposure(b, limits.get(b.customer_id))
            for b in balances}


def over_limit(rows: Iterable[Exposure]) -> list[Exposure]:
    """The accounts past their line, worst first.

    A rank, because that is how the collections screen reads it: the largest
    breach is the first call. Ties fall back to the customer id so the order is
    stable across requests rather than dependent on dictionary insertion.
    """
    return sorted((e for e in rows if e.status == OVER),
                  key=lambda e: (-(e.over_by or Decimal(0)), e.customer_id))
