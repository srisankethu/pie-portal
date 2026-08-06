"""What the committed book does to cash over the next quarter.

Every figure here is an obligation somebody already entered into: an invoice
raised and unpaid, a bill received and unpaid. Nothing is a forecast of trade
that has not happened, nothing is weighted by how likely it is to be honoured,
and nothing is derived from a rate or a trend. Read the ``CASH_SCHEDULE`` fold,
put each amount in the week its own document says it falls due, subtract one
side from the other.

**This is net movement, and it is never a cash position.** PIE reads payments,
not balances — ``state/opportunities/supply.py`` already refuses liquidity risk
on exactly that ground, and one side of a ledger is not cash. Without an
opening balance the honest output is "the committed book moves cash by −₹4.2L
over thirteen weeks, worst in week six", and the dishonest one is "you run out
on 12 October". The cumulative column therefore starts at zero and is labelled
as movement; nothing in this module ever says runway.

**Overdue sits outside the timeline.** Money that was due last month is real
and it is not week-one inflow — putting it there asserts it arrives now, which
is the one thing its being overdue disproves. It is reported ahead of the
buckets, as its own figure, and left out of the cumulative.

**Four totals are named rather than absorbed**, because a projection whose
parts do not add up to the book is a projection people stop trusting:

``overdue``          already due, not yet settled
``beyond_horizon``   dated, but past the last week shown
``undated``          owed, with no terms on record — cannot be placed at all
``unscheduled``      open orders: real exposure, but no due date exists yet

**Open orders are not on the timeline.** A sales order is a promise to ship and
a purchase order a promise to buy; neither carries a due date, and only the
invoice or bill that follows does. Scheduling them would need an assumed
delivery date, and ``expected_delivery_date`` is blank on effectively every
order in this book. They are shown as unscheduled totals beside the chart —
present, quantified, and not pretended to have a date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

# The key format belongs to the fold that writes it, both directions. Three
# string literals here instead would be three literals nobody thinks to check
# against the reducer, and the first one to drift produces a chart that is
# silently missing a side.
from ...state.reducers.cash import IN, OUT, week_start

#: Thirteen weeks. Long enough that the dated obligations in this book reach
#: the end of it, short enough that every bar is something already committed
#: rather than something assumed.
WEEKS = 13

_ZERO = Decimal(0)


def _money(raw: Any) -> Decimal:
    """A stored state value back to ``Decimal``. Money is written as a string by
    the fold precisely so it survives the round trip exactly."""
    if raw is None or raw == "":
        return _ZERO
    return raw if isinstance(raw, Decimal) else Decimal(str(raw))


def _out(value: Decimal) -> float:
    return float(round(value, 2))


def _monday_of(on: date) -> date:
    """The Monday that opens the ISO week containing ``on``."""
    return on - timedelta(days=on.isoweekday() - 1)


@dataclass
class Flow:
    """Money moving in one week, or in one of the named side-buckets."""

    inflow: Decimal = _ZERO
    outflow: Decimal = _ZERO
    inflow_documents: int = 0
    outflow_documents: int = 0

    def add(self, direction: str, amount: Decimal, documents: int) -> None:
        if direction == IN:
            self.inflow += amount
            self.inflow_documents += documents
        else:
            self.outflow += amount
            self.outflow_documents += documents

    @property
    def net(self) -> Decimal:
        return self.inflow - self.outflow

    @property
    def empty(self) -> bool:
        return self.inflow == _ZERO and self.outflow == _ZERO

    def to_dict(self) -> dict:
        return {
            "inflow": _out(self.inflow),
            "outflow": _out(self.outflow),
            "net": _out(self.net),
            "inflow_documents": self.inflow_documents,
            "outflow_documents": self.outflow_documents,
        }


def project(schedule: dict[str, dict[str, Any]],
            commitments: dict[str, dict[str, Any]],
            receivables: dict[str, dict[str, Any]],
            *, as_of: date, weeks: int = WEEKS) -> dict:
    """The committed book's effect on cash, week by week.

    ``schedule`` is the ``CASH_SCHEDULE`` fold; ``commitments`` and
    ``receivables`` are their own states, read for the unscheduled totals and
    for the one reconciliation that matters — how much of what is scheduled
    could not be attributed to a party anyone can name.
    """
    first_monday = _monday_of(as_of)
    mondays = [first_monday + timedelta(weeks=i) for i in range(weeks)]
    horizon_end = mondays[-1] + timedelta(days=6)

    by_week: dict[date, Flow] = {m: Flow() for m in mondays}
    overdue, beyond, undated = Flow(), Flow(), Flow()
    scheduled_rows = 0

    for key, row in schedule.items():
        direction, _, bucket = key.partition(":")
        if direction not in (IN, OUT):
            continue
        amount = _money(row.get("amount"))
        documents = int(row.get("documents") or 0)
        if amount == _ZERO:
            continue
        scheduled_rows += 1
        starts_on = week_start(bucket)
        if starts_on is None:
            undated.add(direction, amount, documents)
        elif starts_on < first_monday:
            overdue.add(direction, amount, documents)
        elif starts_on in by_week:
            by_week[starts_on].add(direction, amount, documents)
        else:
            beyond.add(direction, amount, documents)

    # The cumulative runs from zero across the horizon only. Overdue is
    # deliberately not its opening value: see the module docstring.
    buckets: list[dict] = []
    running = _ZERO
    lowest, lowest_week = None, None
    for monday in mondays:
        flow = by_week[monday]
        running += flow.net
        if lowest is None or running < lowest:
            lowest, lowest_week = running, monday
        buckets.append({
            "week": f"{monday.isocalendar()[0]}-W{monday.isocalendar()[1]:02d}",
            "starts_on": monday.isoformat(),
            "cumulative": _out(running),
            **flow.to_dict(),
        })

    result = {
        "as_of": as_of.isoformat(),
        "weeks": weeks,
        "horizon_ends_on": horizon_end.isoformat(),
        "buckets": buckets,
        "net_over_horizon": _out(running),
        # The worst point of the committed book, which is the one number this
        # screen exists to produce. Deterministic: it is an argmin over the
        # cumulative column, not a judgement about it.
        "lowest_cumulative": _out(lowest if lowest is not None else _ZERO),
        "lowest_week_starts_on": lowest_week.isoformat() if lowest_week else None,
        "overdue": overdue.to_dict(),
        "beyond_horizon": beyond.to_dict(),
        "undated": undated.to_dict(),
        "unscheduled": _unscheduled(commitments),
        "unattributed": _unattributed(schedule, commitments, receivables),
        "empty_reason": None,
    }
    if not scheduled_rows:
        result["empty_reason"] = (
            "No invoice or bill with an outstanding balance has synced yet, so "
            "there is nothing committed to schedule. Run a sync from Data & "
            "connection.")
    elif not buckets or all(by_week[m].empty for m in mondays):
        result["empty_reason"] = (
            f"Nothing falls due between now and {horizon_end.isoformat()}. "
            "What is outstanding is either already overdue, dated past this "
            "horizon, or carries no terms — each is counted beside the chart.")
    return result


def _unscheduled(commitments: dict[str, dict[str, Any]]) -> dict:
    """Open orders in both directions: committed, and with no due date to place.

    Read from ``COMMITMENTS`` rather than folded into the schedule, because an
    order genuinely has no date to be scheduled at. Reported so the exposure is
    visible; kept off the timeline so it is not asserted to land in a week
    nobody chose.
    """
    sales, purchases = _ZERO, _ZERO
    sales_orders = purchase_orders = 0
    for row in commitments.values():
        if row.get("direction") == "customer":
            sales += _money(row.get("open_sales_value"))
            sales_orders += int(row.get("open_sales_orders") or 0)
        else:
            purchases += _money(row.get("open_purchase_value"))
            purchase_orders += int(row.get("open_purchase_orders") or 0)
    return {
        "open_sales_value": _out(sales),
        "open_sales_orders": sales_orders,
        "open_purchase_value": _out(purchases),
        "open_purchase_orders": purchase_orders,
    }


def _unattributed(schedule: dict[str, dict[str, Any]],
                  commitments: dict[str, dict[str, Any]],
                  receivables: dict[str, dict[str, Any]]) -> dict:
    """How much of the schedule belongs to nobody the platform can name.

    ``CASH_SCHEDULE`` counts an obligation whether or not its party resolved;
    ``RECEIVABLES`` and ``COMMITMENTS`` are keyed by party and so cannot. The
    difference is exactly the unattributable portion, and stating it is what
    keeps two screens showing different totals from looking like a bug in one
    of them. It is real money and it is in the projection — it simply has no
    name to file it under.
    """
    scheduled_in = sum((_money(r.get("amount")) for k, r in schedule.items()
                        if k.startswith(f"{IN}:")), _ZERO)
    scheduled_out = sum((_money(r.get("amount")) for k, r in schedule.items()
                         if k.startswith(f"{OUT}:")), _ZERO)
    named_in = sum((_money(r.get("outstanding")) for r in receivables.values()),
                   _ZERO)
    named_out = sum((_money(r.get("payables_balance"))
                     for r in commitments.values()
                     if r.get("direction") == "supplier"), _ZERO)
    # Clamped at zero. The two states are folded from the same events under the
    # same guards, so the schedule cannot honestly be the smaller of the pair —
    # and a negative "unattributed" would read as a claim about the data rather
    # than what it would actually be, which is a bug in one of these folds.
    return {
        "inflow": _out(max(scheduled_in - named_in, _ZERO)),
        "outflow": _out(max(scheduled_out - named_out, _ZERO)),
    }
