"""What a supplier's agreed payment term means as a date.

Zoho's payment terms are a fixed dropdown. A real agreement of "net 37", or
"45 days from the end of the month", gets filed under the nearest entry on that
list — so every due date derived from it is wrong by days, in a direction
nobody chose, and the cash projection places money on those dates. This module
owns the correction: given a bill and the term actually agreed with its
supplier, when is it really due.

**The document's own date is never overwritten.** ``BillDoc.due_date`` keeps
saying what Zoho says, and this produces an *expected* date beside it. That is
the whole discipline of the change: a schedule that quietly disagreed with the
ERP, with no way to see where the disagreement came from, is a schedule nobody
can reconcile against the books it was drawn from. Both dates travel together,
and the difference between them is reported as its own figure.

**Why the correction is a shift, and why that is exact.** ``CASH_SCHEDULE``
buckets a bill by the week its stated due date falls in, aggregated per
supplier — so an individual bill's date is not recoverable from the fold, and a
re-dating cannot be applied row by row downstream. What *is* recoverable is how
far each supplier's bills move, which is what ``shift`` computes from the bills
themselves. For a supplier whose Zoho bills all carry one term — the normal
case, because Zoho picks one default per vendor — every bill moves by the same
number of days and the shift is exact. Where it is not, ``spread_days`` says
so rather than letting a mixed supplier be silently averaged.

**Not folded into the state.** ``state/reducers/cash.py`` refuses to record an
expected date, and this does not change that: the fold still records what the
document says. A term is edited by a person, and a fold that baked one in would
leave the chart stale until the next rebuild — an edit whose effect nobody can
see is an edit nobody trusts.
"""
from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

#: Counted from the bill's own date. The ordinary case.
NET = "NET"
#: Counted from the last day of the month the bill was raised in. Common in
#: Indian B2B and not expressible as a day count — "30 days" and "30 days from
#: month end" are up to a month apart on the same bill.
END_OF_MONTH = "END_OF_MONTH"

BASES = (NET, END_OF_MONTH)

#: How each basis reads, for a screen that has to explain itself.
BASIS_LABELS: dict[str, str] = {
    NET: "days from the bill date",
    END_OF_MONTH: "days from the end of the bill's month",
}


@dataclass(frozen=True)
class Term:
    """One supplier's agreed term. Days, and what the days are counted from."""

    days: int
    basis: str = NET

    def due(self, document_date: date) -> date:
        """When a bill raised on ``document_date`` is really due.

        An unrecognised basis counts from the document date rather than raising:
        the value is validated on write, and a schedule that refuses to draw
        because one supplier's row is odd is worse than one that draws it the
        ordinary way. It cannot arrive through the API — see ``validate``.
        """
        if self.basis == END_OF_MONTH:
            last = calendar.monthrange(document_date.year, document_date.month)[1]
            return date(document_date.year, document_date.month, last) + timedelta(
                days=self.days)
        return document_date + timedelta(days=self.days)


class InvalidTerm(ValueError):
    """A term that cannot mean a date."""


#: Ten years. Not a business rule — a guard against a typo that would push a
#: supplier's money off the end of every horizon the product can draw.
MAX_TERM_DAYS = 3650


def validate(days: int, basis: str) -> Term:
    """The one place a term is checked, so the API and any future importer
    cannot disagree about what is storable."""
    if basis not in BASES:
        raise InvalidTerm(f"basis must be one of {', '.join(BASES)}")
    if days < 0:
        raise InvalidTerm("a payment term cannot be negative — a bill is not "
                          "due before it is raised")
    if days > MAX_TERM_DAYS:
        raise InvalidTerm(f"a payment term of {days} days is not a term, it is "
                          "a typo")
    return Term(days=days, basis=basis)


@dataclass(frozen=True)
class Bill:
    """The two dates a re-dating needs, and the money that moves with them."""

    vendor_id: str
    document_date: date
    stated_due: Optional[date]
    amount: float


@dataclass(frozen=True)
class Shift:
    """How far one supplier's money moves, once their real term is applied.

    ``days`` is the median across their bills, and ``spread_days`` is how much
    those bills disagree. Zero spread means every bill moved by the same amount
    and the shift is exact; anything else means Zoho held more than one term for
    this supplier and a single number is a summary. Reported rather than hidden,
    because a summary presented as an exact answer is the failure this module
    exists to fix.
    """

    vendor_id: str
    days: int
    spread_days: int
    bills: int
    #: Bills with no stated due date at all. They are re-dated too — a term is
    #: exactly what an undated bill was missing — but they cannot contribute to
    #: a *difference*, so they are counted separately.
    undated: int

    @property
    def exact(self) -> bool:
        return self.spread_days == 0


def shift(bills: Iterable[Bill], term: Term) -> Optional[Shift]:
    """How many days this supplier's bills move under their agreed term.

    ``None`` when no bill of theirs carries a stated due date to move *from*.
    That is not "no change": it means the correction cannot be expressed as a
    shift off the schedule, so the caller leaves their money where the schedule
    put it rather than inventing a displacement.
    """
    rows = list(bills)
    if not rows:
        return None
    datable = [b for b in rows if b.stated_due is not None]
    if not datable:
        return None
    deltas = [(term.due(b.document_date) - b.stated_due).days   # type: ignore[operator]
              for b in datable]
    middle = int(statistics.median(deltas))
    return Shift(
        vendor_id=rows[0].vendor_id,
        days=middle,
        spread_days=max(deltas) - min(deltas),
        bills=len(datable),
        undated=len(rows) - len(datable),
    )


def shifts(bills: Iterable[Bill],
           terms: dict[str, Term]) -> dict[str, Shift]:
    """Every supplier's shift, keyed by vendor id.

    Suppliers with no agreed term on record are absent, not present with zero.
    The distinction matters downstream in the same way it does for a measured
    lag: absent means "the schedule's date stands", which is a different claim
    from "we agreed to exactly what Zoho assumed".
    """
    by_vendor: dict[str, list[Bill]] = {}
    for b in bills:
        if b.vendor_id in terms:
            by_vendor.setdefault(b.vendor_id, []).append(b)
    out: dict[str, Shift] = {}
    for vendor_id, rows in by_vendor.items():
        moved = shift(rows, terms[vendor_id])
        if moved is not None:
            out[vendor_id] = moved
    return out


def effective_days(term: Optional[Term], zoho_days: Optional[int]) -> Optional[int]:
    """The term to reason with: ours if we recorded one, else the ERP's.

    Here rather than at each call site because three screens ask the same
    question and a fourth will. ``END_OF_MONTH`` collapses to its day count,
    which is what a "typical days to pay" comparison wants — the basis changes
    the date of one bill, not the length of the credit being described.
    """
    if term is not None:
        return term.days
    return zoho_days
