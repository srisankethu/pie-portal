"""CASH_SCHEDULE — when money already promised is due to move.

Keyed by direction, the ISO week the obligation falls due, and the party that
owes it or is owed::

    in:2026-W32:cus_41    that customer's invoices due that week
    out:2026-W32:sup_7    that supplier's bills due that week
    in:undated:cus_41     invoiced, owed, no terms on record
    out:2026-W32:-        billed, owed, vendor never resolved

**Why the party is on the key.** This state used to be keyed by week alone, and
this docstring used to say that shifting a due date by how late a customer
actually pays "needs the customer on the key… joining the two is the next
version". This is that version. ``insight/payments.py`` measures days-late per
customer from settled invoices; the projection reads it per party and moves that
party's money, so the cash line can be drawn at each customer's own observed
speed rather than at the speed their terms claim.

The week stays *second* so the key is parseable without knowing what an id can
contain: a week bucket is never anything but ``YYYY-Www`` or ``undated``, so
``split(":", 2)`` puts the whole id — colons and all — in the remainder.

A dash is the party for an obligation whose customer or vendor never resolved.
Those rows are still on the timeline, unlike in the party-keyed states, because
money owed by somebody the contact pull never returned is still money that will
arrive. They simply cannot be shifted, and the projection reports how much of
the schedule that is.

**Why this is a state and not a query over ``RECEIVABLES``.** That state
answers *who owes what*, so it aggregates a customer's invoices into one row
and keeps a single ``earliest_due_on``. A customer with four invoices spread
across a quarter therefore has one date, and the other three are gone by the
time anything can read them. Placing money on a timeline needs every due date,
which is a different grain of the same events — the same reason
``CUSTOMER_MONTH`` sits beside the per-customer trade facts. Neither state can
be derived from the other, which is the test worth applying before giving a
fact a second home.

**The bucket is an absolute week, not an offset.** A key like ``in:week_3``
would mean something different every day the fold ran, and a state whose
meaning depends on when it was built is a state nobody can reconcile against
last week's build. The reader maps its own ``as_of`` onto these weeks; what
falls before that week is *already* due, which is a reading rather than a fold.

**A party is not required here, and that is the one difference from
``RECEIVABLES``.** Money owed by a customer the contact pull never returned is
still money that will arrive, and a schedule that dropped it would understate
the inflow. The party-keyed states do drop it, so this state's total is the
larger of the two by exactly the unattributable portion — which the projection
reports as its own line rather than leaving as an arithmetic mystery. The
status guards are *imported* from those two reducers for the same reason: three
copies of "what counts as owed" is three chances for the totals to stop
agreeing.

Deliberately absent:

**No expected-payment date *here*.** The fold still records what the document
says: the due date, unshifted. Which week the money is *expected* in is a
reading of that plus the party's measured behaviour, and it belongs to the
projection — a fold that baked in a lag would have to be re-run every time
another invoice settled and changed the median.

**No open sales or purchase orders.** An order carries no due date — only a
bill or an invoice does. Scheduling one needs an assumed delivery date, and
``expected_delivery_date`` is blank on effectively every order in this book.
They are real exposure and belong beside the timeline as an unscheduled total,
never as a bar on it.

**No probability.** Nothing here is weighted by whether it will actually be
paid. There are no defaults in this book to calibrate against, so a weight
would be a guess wearing a decimal point.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from ...domain import models
from .. import events as ev
from ..engine import ADD, SET, Delta, Masters, as_decimal, register
from .commitments import NOT_A_PROMISE
from .receivables import NOT_OWED

CASH_SCHEDULE = "CASH_SCHEDULE"

#: Money in and money out, as the first half of a key.
IN = "in"
OUT = "out"

#: The bucket for an obligation with no due date on record. Named rather than
#: dropped, and never spread across the timeline: defaulting the date would put
#: a customer on a collections list for terms nobody gave them, and there is no
#: better reason to invent one for a supplier.
UNDATED = "undated"

#: The party for an obligation whose customer or vendor never resolved. A single
#: character rather than an empty segment so the key always has three parts and
#: cannot be mistaken for the old two-part shape.
NO_PARTY = "-"


def week_key(on: date) -> str:
    """The ISO week a date falls in, as a sortable string.

    ISO rather than "weeks since January": ``isocalendar`` is the numbering
    Python and every spreadsheet this will be checked against already agree on.
    Zero-padding keeps the sort lexicographic across a year boundary.
    """
    year, week, _ = on.isocalendar()
    return f"{year}-W{week:02d}"


def week_start(bucket: str) -> Optional[date]:
    """The Monday a bucket names, or ``None`` when it names no week.

    The inverse of ``week_key``, and here rather than with the reader because a
    key format written in one module and parsed in another is a format that
    changes in one of them. ``UNDATED`` is a legitimate bucket, not a parse
    failure, and returns ``None`` for the same reason it exists.
    """
    if bucket == UNDATED:
        return None
    try:
        year, week = bucket.split("-W")
        return date.fromisocalendar(int(year), int(week), 1)
    except (ValueError, AttributeError):
        return None


class CashScheduleReducer:
    state = CASH_SCHEDULE
    handles = frozenset({ev.RECEIVABLE_RECORDED, ev.PAYABLE_RECORDED})

    #: No detector reads this state — it feeds the cash projection, which is a
    #: screen. Per-event working would be written and never read: ``why()`` is
    #: only ever asked of a state some decision was built from. The flag sits
    #: beside ``handles`` because it becomes wrong the moment that changes.
    records_transitions = False

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        payload = event.payload or {}
        inbound = event.event_type == ev.RECEIVABLE_RECORDED
        # An invoice and a bill disagree about which statuses mean "not owed" —
        # an invoice can be written off and an order can be rejected. Each side
        # keeps its own guard rather than being flattened into a union that
        # would quietly exclude a live document from one of them.
        if str(payload.get("status") or "").lower() in (
                NOT_OWED if inbound else NOT_A_PROMISE):
            return ()
        balance = as_decimal(payload.get("balance"))
        # ``None`` is "the source did not say", not "settled", and a negative
        # balance is an over-payment or an applied credit — neither is money
        # scheduled to move in the direction this row would claim.
        if balance is None or balance <= 0:
            return ()

        direction = IN if inbound else OUT
        party = self._party(event, payload, ctx, inbound)
        return (Delta(
            CASH_SCHEDULE,
            f"{direction}:{self._bucket(payload)}:{party}",
            (
                # Stored as well as encoded in the key, so a row read on its own
                # says what it is. The projection parses the key for the week
                # and reads these for the side and the party.
                (SET, "direction", direction),
                (SET, "party_id", party),
                (ADD, "amount", balance),
                (ADD, "documents", 1),
            ),
        ),)

    @staticmethod
    def _party(event: models.BusinessEvent, payload: dict[str, Any],
               ctx: Masters, inbound: bool) -> str:
        """Whose obligation this is, resolved the same way the party-keyed
        states resolve it — through ``Masters``, never from the raw external id.

        The difference from those states is what happens when it fails: they
        drop the row, because a state keyed by party has nowhere to put money
        owed by nobody. This one keeps it under ``NO_PARTY``, because the
        timeline's job is to be complete. Such a row can never be shifted by a
        payment history it has no name to look up, and the projection says how
        much of the schedule is in that position rather than leaving the band
        looking tighter than the evidence supports.
        """
        if inbound:
            external_id = str(payload.get("customer_external_id") or "")
            resolved = ctx.customer(event, external_id) if external_id else None
        else:
            external_id = str(payload.get("vendor_external_id") or "")
            resolved = ctx.vendor(event, external_id) if external_id else None
        return resolved or NO_PARTY

    @staticmethod
    def _bucket(payload: dict[str, Any]) -> str:
        due = payload.get("due_date")
        if not due:
            return UNDATED
        return week_key(date.fromisoformat(str(due)))


register(CashScheduleReducer())
