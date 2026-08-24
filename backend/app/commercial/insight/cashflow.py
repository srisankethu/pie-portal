"""What the committed book does to cash over the next quarter, and what the
book has actually been doing over the quarter behind it.

``project`` is the forward half and is the whole of what this module used to
be; ``actual`` is the backward one, added so the committed channel can be read
against the route that arrived at it rather than out of nowhere. They share a
window length, a week convention and a rule — movement, never a position — and
they never share a number: nothing measured behind the datum is used to shift,
weight or scale anything in front of it.

Every figure in the forward half is an obligation somebody already entered
into: an invoice raised and unpaid, a bill received and unpaid. Nothing is a
forecast of trade that has not happened, nothing is weighted by how likely it
is to be honoured, and nothing is derived from a rate or a trend. Read the
``CASH_SCHEDULE`` fold, put each amount in the week its own document says it
falls due, subtract one side from the other.

**Three timings, one book.** Due dates answer "when is this money promised",
which is not the question somebody funding a week is asking — and drawing only
that line understates what the week actually needs. The same obligations are
therefore placed three times, shifted by each party's *own* measured days-late
(tenth percentile, median, ninetieth, from ``insight/payments``). Nothing is
invented for a party without enough settled documents to measure: their money
stays on its due date, and the response reports what share of each side that is,
so a narrow band can be read as "these parties are punctual" rather than "we
know very little". This is still not probability — no obligation is weighted by
whether it will be honoured. It is the same money, on the dates the payer has
actually used.

**The scenarios are named for the cash they need, not for anybody's speed**, and
that distinction only became necessary once the outflow side could move too.
Late money in is worse for the week; late money out is better for it. So a
single "everyone is slow" line is not a corner of anything — it mixes one bad
assumption with one good one, and the deepest trough would sit outside all three
lines drawn. The corners are taken instead:

``best``      customers at their fastest, we pay at our slowest
``expected``  everybody at their own median
``worst``     customers at their slowest, we pay at our fastest

``worst`` is the number to fund a week against, and it is a genuine worst case
rather than a mixed one. Both ends are measured behaviour, not invention: the
fast end of our own payables is a speed this business has actually settled at.

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
from typing import Any, Optional

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


#: The three timings the projection is run at, named for what each one costs the
#: week rather than for how fast anybody is. The baseline `buckets` column is
#: separate from all three: it is the committed book read literally, every
#: document on its own due date, and it is kept because it is the only reading
#: that asserts nothing beyond what the source says.
SCENARIOS = ("best", "expected", "worst")

#: Which end of each party's own distribution a scenario stands on, per
#: direction. A table rather than a chain of conditionals because the whole
#: subtlety of this module is in these six cells: reading down the ``worst``
#: column says "customers at their slowest, us at our fastest", which is the
#: sentence somebody has to be able to check.
_ENDS: dict[str, dict[str, str]] = {
    "best":     {IN: "early_days", OUT: "late_days"},
    "expected": {IN: "expected_days", OUT: "expected_days"},
    "worst":    {IN: "late_days", OUT: "early_days"},
}


def _shift(lag: Any, scenario: str, direction: str) -> int:
    """Days to move one party's money by, under one scenario.

    Zero when nothing is known about them: an unmeasured party stays on the date
    their terms give, which is the assumption the whole chart used to make about
    everybody.
    """
    if lag is None:
        return 0
    return int(getattr(lag, _ENDS[scenario][direction], 0) or 0)


def project(schedule: dict[str, dict[str, Any]],
            commitments: dict[str, dict[str, Any]],
            receivables: dict[str, dict[str, Any]],
            *, as_of: date, weeks: int = WEEKS,
            lags: Optional[dict[str, Any]] = None,
            payable_lags: Optional[dict[str, Any]] = None,
            term_shifts: Optional[dict[str, Any]] = None) -> dict:
    """The committed book's effect on cash, week by week.

    ``schedule`` is the ``CASH_SCHEDULE`` fold; ``commitments`` and
    ``receivables`` are their own states, read for the unscheduled totals and
    for the one reconciliation that matters — how much of what is scheduled
    could not be attributed to a party anyone can name.

    ``lags`` maps a customer id, and ``payable_lags`` a vendor id, to that
    party's measured days-late distribution (``insight/payments.lags``). Given
    them, the same committed book is placed on the timeline three times, at the
    corners described in the module docstring, so the answer to "how much do I
    need that week" is a range rather than a single line drawn on the assumption
    that everybody pays to terms. Omitted, or empty, and every scenario
    collapses onto the due dates — which is exactly what this function did
    before it took the arguments.

    **Two dictionaries rather than one.** Customer ids and vendor ids are
    separate namespaces and a merged map would depend on them never colliding,
    which is a property nothing enforces. Keeping them apart also keeps the
    direction explicit at the one place it matters: a bill is never shifted by
    how its supplier pays *us*, which is a different fact about a party that can
    be both.

    ``term_shifts`` (``insight/terms.shifts``) corrects the *due date itself*
    for a supplier whose agreed term Zoho could not express, and it is a
    different kind of correction from a lag. A lag says how far past a due date
    money actually moves; a term shift says the due date was wrong. It is
    therefore applied to the baseline column as well as to the scenarios — a
    supplier we agreed net-45 with does not have a net-30 bar on the chart under
    *any* reading — while a lag deliberately leaves the baseline alone, because
    the baseline is what the documents claim.
    """
    lags = lags or {}
    payable_lags = payable_lags or {}
    term_shifts = term_shifts or {}
    first_monday = _monday_of(as_of)
    mondays = [first_monday + timedelta(weeks=i) for i in range(weeks)]
    horizon_end = mondays[-1] + timedelta(days=6)

    by_week: dict[date, Flow] = {m: Flow() for m in mondays}
    # One set of buckets per scenario, filled from the same rows. The `on_terms`
    # pass writes `by_week` above, which is what the headline flow columns and
    # every side-bucket total are still computed from — the scenarios move
    # *when* money lands, never how much of it there is.
    shifted: dict[str, dict[date, Flow]] = {
        s: {m: Flow() for m in mondays} for s in SCENARIOS}
    shifted_beyond = {s: Flow() for s in SCENARIOS}
    overdue, beyond, undated = Flow(), Flow(), Flow()
    scheduled_rows = 0
    measured = {IN: _ZERO, OUT: _ZERO}
    unmeasured = {IN: _ZERO, OUT: _ZERO}
    retimed, retimed_documents, retimed_inexact = _ZERO, 0, 0

    for key, row in schedule.items():
        direction, bucket, party = _parse(key)
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
            continue

        # The agreed term corrects the due date itself, so it is applied before
        # anything decides which bucket this money belongs in — including
        # whether it is already overdue. A term shorter than the one Zoho
        # assumed genuinely does make a bill overdue that the ERP thinks is not,
        # and that is a finding rather than an edge case to suppress.
        moved = term_shifts.get(party) if direction == OUT else None
        if moved is not None:
            days = int(getattr(moved, "days", 0) or 0)
            starts_on = _monday_of(starts_on + timedelta(days=days))
            retimed += amount
            retimed_documents += documents
            if not getattr(moved, "exact", True):
                retimed_inexact += 1

        if starts_on < first_monday:
            overdue.add(direction, amount, documents)
            continue
        if starts_on in by_week:
            by_week[starts_on].add(direction, amount, documents)
        else:
            beyond.add(direction, amount, documents)

        # How much of each side the band is actually able to move. Reported so
        # a narrow band is readable as "these parties are punctual" rather than
        # mistaken for "we know less than we do".
        lag = (lags if direction == IN else payable_lags).get(party)
        if lag is None:
            unmeasured[direction] += amount
        else:
            measured[direction] += amount

        for scenario in SCENARIOS:
            landed = starts_on + timedelta(days=_shift(lag, scenario, direction))
            monday = _monday_of(landed)
            if monday in shifted[scenario]:
                shifted[scenario][monday].add(direction, amount, documents)
            elif monday < first_monday:
                # A measured lag can be negative — a party that habitually
                # settles before the due date — which can pull money back past
                # the first Monday. It is placed in week one rather than
                # silently dropped: money already inside the horizon does not
                # leave it by being early, and the alternative would make the
                # scenarios stop summing to the same book.
                shifted[scenario][first_monday].add(direction, amount, documents)
            else:
                shifted_beyond[scenario].add(direction, amount, documents)

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

    scenarios = {s: _series(shifted[s], mondays, shifted_beyond[s])
                 for s in SCENARIOS}
    # The requirement, which is the number the screen exists to produce: the
    # deepest the cumulative goes, under the timing that makes it deepest.
    # `worst` is that timing by construction — it stands on the slow end of
    # every inflow and the fast end of every outflow — but it is still taken as
    # a minimum rather than assumed, because with nothing measured all three
    # are identical and asserting a winner between equals is how a comment
    # stops matching its code.
    requirement = min(scenarios[s]["lowest_cumulative"] for s in SCENARIOS)

    result = {
        "scenarios": scenarios,
        "requirement": requirement,
        "basis": _basis(measured, unmeasured, lags, payable_lags,
                        term_shifts, retimed, retimed_documents,
                        retimed_inexact),
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


def _parse(key: str) -> tuple[str, str, str]:
    """``in:2026-W32:cus_41`` → direction, week bucket, party.

    Split from the left with the week second, so an id containing a colon is
    still one id. A two-part key is a row folded before the party was added:
    read as unattributed rather than skipped, so a state built by older code
    still draws the chart it always drew — flat scenarios, because nothing can
    be looked up for a party that is not there.
    """
    parts = key.split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return "", "", ""


def _series(by_week: dict[date, Flow], mondays: list[date],
            beyond: Flow) -> dict:
    """One scenario's weekly flows and its running total.

    Same arithmetic as the baseline column, over rows placed at a different
    week. `beyond_horizon` is what this timing pushed off the end — money that
    was inside the horizon on its due date and is not once a customer's own
    lateness is applied. Reported per scenario rather than merged into the
    single side-bucket, which describes the due dates only.
    """
    rows: list[dict] = []
    running = _ZERO
    lowest, lowest_week = None, None
    for monday in mondays:
        flow = by_week[monday]
        running += flow.net
        if lowest is None or running < lowest:
            lowest, lowest_week = running, monday
        rows.append({
            "starts_on": monday.isoformat(),
            "cumulative": _out(running),
            **flow.to_dict(),
        })
    return {
        "buckets": rows,
        "net_over_horizon": _out(running),
        "lowest_cumulative": _out(lowest if lowest is not None else _ZERO),
        "lowest_week_starts_on": lowest_week.isoformat() if lowest_week else None,
        "beyond_horizon": beyond.to_dict(),
    }


def _basis(measured: dict[str, Decimal], unmeasured: dict[str, Decimal],
           lags: dict[str, Any], payable_lags: dict[str, Any],
           term_shifts: dict[str, Any], retimed: Decimal,
           retimed_documents: int, retimed_inexact: int) -> dict:
    """What the band is standing on, in the reader's terms.

    A range is only worth as much as the evidence under it, and the two ways it
    can mislead are opposite: a *narrow* band because these parties really are
    punctual, and a narrow band because almost nothing about them is measured.
    The share of scheduled money that could be shifted is what separates the
    two, so it is returned rather than left to be inferred from the shape — and
    per side, because the two halves are measured from different evidence and
    one of them can be thin while the other is not.
    """
    def share(direction: str) -> float:
        total = measured[direction] + unmeasured[direction]
        return (float(round(measured[direction] / total, 4))
                if total > _ZERO else 0.0)

    return {
        "customers_measured": len(lags),
        "vendors_measured": len(payable_lags),
        "inflow_measured": _out(measured[IN]),
        "inflow_unmeasured": _out(unmeasured[IN]),
        "share_measured": share(IN),
        "outflow_measured": _out(measured[OUT]),
        "outflow_unmeasured": _out(unmeasured[OUT]),
        "outflow_share_measured": share(OUT),
        # No longer "stated rather than implied: the outflow has no measured
        # behaviour behind it". It does, whenever any bill has been settled
        # often enough to clear the evidence floor.
        "outflow_shifted": bool(payable_lags),
        # What was re-dated because the agreed term differs from the one the
        # ERP could express. Reported separately from the lag, and never folded
        # into it, because they are different claims: a lag says money moves
        # late, a re-dating says the due date was wrong. `retimed_inexact` is
        # how many suppliers had more than one stated term, where the shift is
        # a summary rather than an exact correction.
        "vendors_retimed": len(term_shifts),
        "outflow_retimed": _out(retimed),
        "outflow_retimed_documents": retimed_documents,
        "vendors_retimed_inexactly": retimed_inexact,
    }


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


# ── what actually happened, behind the datum ────────────────────────────────
#
# ``project`` above answers "what has this book already promised to do next".
# This answers the other half of the same question — "what has it actually been
# doing" — and the two are drawn either side of one reference line so the
# committed future can be read against the route that arrived at it.
#
# **Payment grain, not application grain, and the difference is cash.**
# ``payments.Settlement`` splits one bank transfer across the ten bills it
# settled, which is exactly right for measuring how late each of those ten was
# and exactly wrong for measuring money. Three kinds of real movement are
# missing from that grain: an advance, which is money received against no
# invoice; a payment to a supplier the contact pull never returned, which left
# our account whether or not the vendor resolved; and a receipt not yet applied
# to anything. Each omission understates the movement, and all three understate
# it in the direction that reads as calm.
#
# **The running total is measured back from the datum, and it is still not a
# balance.** The last past week ends at zero — the point the committed channel
# opens from — so each earlier week reads "the book has moved this much cash
# since then". PIE reads payments, never bank balances; there is no opening
# figure here any more than there is in the projection, and the same sentence
# governs both. What this series can honestly say is "cash moved +₹6.4L across
# the last thirteen weeks"; what it must never say is where that left anyone.
#
# **A week with no payment in it is a real zero, not a gap.** Unlike the cash
# cycle, whose stock leg genuinely cannot be stated for a month nobody observed
# stock in, a week the platform holds no payment for is a week no payment
# synced — and the honest reading of that is nothing moved. The first week any
# payment was seen at all is reported as ``observed_from`` so a short history
# is legible as a short history rather than as a quiet start.


@dataclass(frozen=True)
class Movement:
    """One payment that actually happened.

    Deliberately not ``payments.Settlement``: that row is a document being
    settled, keyed to the party and the invoice or bill it clears, and it is
    the grain lateness is measured at. This is money leaving or entering the
    account — one transfer, one row — which is the grain cash is measured at.
    """

    on: date
    #: ``IN`` or ``OUT``, from the reducer that owns both spellings.
    direction: str
    amount: Decimal


def actual(movements: Any, *, as_of: date, weeks: int = WEEKS) -> dict:
    """Cash that actually moved, week by week, over the weeks before ``as_of``.

    ``weeks`` buckets ending with the week ``as_of`` falls in, so the horizon
    the projection looks forward over is the horizon this looks back over — the
    two halves of one drawing, at one scale.

    **The last bucket is the current week, and it is short.** It runs to
    ``as_of`` rather than to Sunday, because the route has to arrive at the
    datum: ending it last Sunday would leave every payment made since then
    counted nowhere at all. It is flagged ``partial`` so a bar drawn from four
    days is not read against twelve bars drawn from seven.

    **It cannot double-count against the projection, and the reason is the
    fold rather than the dates.** Both halves span the current week, but a
    settled document schedules nothing — what has already moved has left the
    balances the forward half is built from. The two series are disjoint in
    money even where they overlap in time.

    Nothing here is shifted, weighted or attributed. A payment is placed in the
    week it was made, and the only arithmetic is addition.
    """
    first_monday = _monday_of(as_of)
    mondays = [first_monday - timedelta(weeks=weeks - 1 - i)
               for i in range(weeks)]
    window_opens = mondays[0]

    by_week: dict[date, Flow] = {m: Flow() for m in mondays}
    observed: Optional[date] = None
    before_window = Flow()
    for move in movements:
        if move.direction not in (IN, OUT):
            continue
        amount = _money(move.amount)
        if amount == _ZERO:
            continue
        if observed is None or move.on < observed:
            observed = move.on
        monday = _monday_of(move.on)
        if monday in by_week:
            by_week[monday].add(move.direction, amount, 1)
        elif monday < window_opens:
            # Older than the window. Counted, never folded into its first week:
            # a quarter of payments piled onto one Monday would be a spike this
            # business never had.
            before_window.add(move.direction, amount, 1)

    total = sum((by_week[m].net for m in mondays), _ZERO)
    buckets: list[dict] = []
    running = _ZERO
    for monday in mondays:
        flow = by_week[monday]
        running += flow.net
        buckets.append({
            "week": f"{monday.isocalendar()[0]}-W{monday.isocalendar()[1]:02d}",
            "starts_on": monday.isoformat(),
            # Short, because it runs to the datum rather than to Sunday. Said
            # on the row so a chart can mark it rather than infer it from a
            # date it would have to reconstruct.
            "partial": monday == first_monday,
            # Back from the datum: the final week lands on zero, which is where
            # the committed channel opens. Movement relative to now — never a
            # position, for the reason the module docstring gives.
            "cumulative": _out(running - total),
            **flow.to_dict(),
        })

    moved = any(not by_week[m].empty for m in mondays)
    return {
        "weeks": weeks,
        "starts_on": window_opens.isoformat(),
        "ends_on": as_of.isoformat(),
        "buckets": buckets,
        "net_over_window": _out(total),
        "observed_from": observed.isoformat() if observed else None,
        "before_window": before_window.to_dict(),
        "empty_reason": None if moved else (
            "No customer receipt or supplier payment has synced with a date in "
            "these weeks, so there is no route behind the datum to draw. The "
            "committed side of the drawing is unaffected."),
    }
