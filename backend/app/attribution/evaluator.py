"""The read side: what the baseline was, what the trial has produced, and the report.

Everything here is a set-based query. No rollup iterates the ledger row by row,
because a total assembled in Python over ten thousand events is a total whose
correctness depends on the loop, and because the aggregate this business is
being asked to pay against should be a statement the database itself can make.

Three rules run through all of it.

**The headline is ATTRIBUTED and nothing else.** POTENTIAL and REALIZED come
back in their own fields with their own labels and are never added into it — see ``ValueClass``. The classes describe overlapping facts on
purpose (a line flagged during a live quote and re-detected after the quote is
won is recorded under both), so summing them would double count the same line
under the guise of being generous.

**An empty window is UNKNOWN, not ₹0.** If the ledger holds no events at all for
a period, this reports ``None`` and names the gap, because "no detector output
has been recorded" and "the detectors ran and found nothing worth money" are
indistinguishable from the outside and mean opposite things. Where events *do*
exist and none of them are ATTRIBUTED, the headline is a real, measured zero and
is reported as such — an honest zero is the correct answer and is not topped up
from a weaker class.

**Productivity is counted, never valued.** Approvals turned round and quotes
priced come back under ``productivity`` as integers with no rupee figure
anywhere near them. This business holds no hourly rate; multiplying a count by
an invented one would be a fabricated number that happens to have been computed
deterministically, which is not the same thing as a defensible one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..commercial.config import CommercialThresholds
from ..commercial.insight import periods
from ..commercial.policy import load_for_org
from ..domain import models
from ..domain.enums import QuoteOutcomeStatus, ValueClass, ValueEventType
from .calculator import MONEY_EXPONENT, roi
from .detectors import NOT_MEASURABLE_REASONS

#: How far back a baseline looks. Long enough that one quiet month does not
#: define "before", short enough to still describe the business as it is now.
BASELINE_DAYS = 90

#: How far back the value summary looks once there is no trial framing it.
#: The same 90 days as the baseline, and deliberately a separate constant: they
#: answer different questions (what the book looked like *before* the platform
#: versus what the platform has done *lately*) and moving one must not silently
#: move the other.
SUMMARY_DAYS = 90

#: How many calendar months ``value_rollup`` covers when the caller does not
#: say. Twelve because the question it answers is a renewal one, and a renewal
#: is argued over a year rather than over a quarter.
ROLLUP_MONTHS = 12

#: The most it will cover. A ceiling rather than a preference: the roll-up runs
#: one indexed aggregate per month, so an unbounded ``months`` is an unbounded
#: query count on a route any owner can call.
MAX_ROLLUP_MONTHS = 36

#: What framed the window a summary measured. Strings rather than an enum, as
#: the gap reasons above are, because nothing persists them.
WINDOW_TRIAL = "TRIAL"
WINDOW_RECENT = "RECENT"


@dataclass(frozen=True)
class Window:
    """The period a summary measured, and what put it there.

    Carried rather than implied. The summary used to be the trial window and
    nothing else, so a reader could assume it; now that it can be either, a
    figure whose period is inferred from context is a figure that gets compared
    against one measured over a different period.
    """

    start: datetime
    end: datetime
    basis: str
    label: str
    #: Set when ``end`` was cut short by what the plan entitles this
    #: organization to read, rather than by the clock. ``None`` otherwise.
    frozen_at: Optional[datetime] = None

    def as_dict(self) -> dict[str, Any]:
        return {"basis": self.basis, "label": self.label,
                "start": clock.iso(self.start), "end": clock.iso(self.end),
                "days": max(0, (self.end - self.start).days),
                "frozen_at": clock.iso(self.frozen_at) if self.frozen_at else None}


def summary_window(session: Session, org: str, *, days: int = SUMMARY_DAYS,
                   readable_until: Optional[datetime] = None) -> Window:
    """The period the value summary should measure for this organization.

    Three cases, and the middle one is the defect this function exists to fix.

    **A running trial** frames the window, because that is what the trial is
    for and what the countdown on every other screen refers to.

    **A finished trial on a live plan** must *not* keep framing it. It did, and
    the consequence was that a paying customer's headline stopped advancing on
    the day their trial ended: the window stayed pinned at ``ends_at``, so value
    attributed months later fell outside it and the screen reported no events —
    not a measured zero but the "no detection run is on record" gap, while the
    ledger held the events. A customer paying for the intelligence layer read
    that as the platform having found nothing.

    **A finished trial on a lapsed plan** keeps the trial window, and that is
    not the bug wearing a different hat. ``readable_until`` says the plan
    entitles this organization to read up to its trial end and no further, so a
    trailing window would be capped to nothing and report an emptiness that is
    an entitlement, not a fact about the business. The window it may see is the
    window it is shown, marked ``frozen_at``.

    An organization that never had a trial gets the trailing window too, rather
    than the refusal it used to get. A tenant provisioned by an operator has no
    trial row and never will, and telling it to "connect a Zoho company to start
    one" when it has been connected for a year is a dead end, not an answer.
    """
    now = clock.now()
    trial = current_trial(session, org)
    ends = clock.aware(trial.ends_at) if trial is not None else None
    started = clock.aware(trial.started_at) if trial is not None else None

    if trial is not None and started is not None and ends is not None:
        if ends > now:
            return Window(start=started, end=now, basis=WINDOW_TRIAL,
                          label="Your Commercial Intelligence trial")
        if readable_until is not None:
            return Window(start=started, end=min(ends, readable_until),
                          basis=WINDOW_TRIAL,
                          label="Your Commercial Intelligence trial",
                          frozen_at=readable_until)

    end = min(now, readable_until) if readable_until is not None else now
    return Window(start=end - timedelta(days=days), end=end,
                  basis=WINDOW_RECENT, label=f"The last {days} days",
                  frozen_at=readable_until)

#: The value classes this module will report. ESTIMATED is deliberately absent:
#: no detector produces one, so every field and every breakdown row carrying it
#: would state a measurement nobody attempted. Enforced in the queries rather
#: than by remembering to drop a key, because dropping the top-level fields and
#: leaving the per-type breakdown alone is exactly how it came back.
REPORTED_VALUE_CLASSES = (
    ValueClass.ATTRIBUTED.value,
    ValueClass.REALIZED.value,
    ValueClass.POTENTIAL.value,
)

# ── evidence-gap reasons ─────────────────────────────────────────────────────
NO_TRIAL_ON_RECORD = "NO_TRIAL_ON_RECORD"
NO_BASELINE_ON_RECORD = "NO_BASELINE_ON_RECORD"
NO_EVENTS_RECORDED = "NO_EVENTS_RECORDED"
NO_INVOICE_LINES_IN_WINDOW = "NO_INVOICE_LINES_IN_WINDOW"
NO_PRICED_LINES_IN_WINDOW = "NO_PRICED_LINES_IN_WINDOW"
NO_COSTED_LINES_IN_WINDOW = "NO_COSTED_LINES_IN_WINDOW"
NO_DECIDED_QUOTES_IN_WINDOW = "NO_DECIDED_QUOTES_IN_WINDOW"
WINS_NOT_RECORDABLE_IN_WINDOW = "WINS_NOT_RECORDABLE_IN_WINDOW"
NO_PLATFORM_COST_SUPPLIED = "NO_PLATFORM_COST_SUPPLIED"
NOT_MEASURABLE = "NOT_MEASURABLE"
#: A complete month inside a roll-up span that the ledger holds no event for.
#: Named per month rather than counted, because "which months" is the question
#: an owner asks next and the answer is usually "the ones before we connected".
PERIOD_NOT_MEASURED = "PERIOD_NOT_MEASURED"
#: A span with no complete calendar month in it at all — an organization in its
#: first weeks. Distinct from an empty ledger: there is nothing to roll up yet,
#: which is not the same as having rolled up and found nothing.
NO_COMPLETE_PERIOD = "NO_COMPLETE_PERIOD"

#: What is said about an event type that recorded nothing and has no registered
#: structural reason. Deliberately refuses to guess between the two cases: from
#: the ledger alone, "the detector ran and found nothing worth valuing" and "no
#: detection run has happened" are the same silence, and only one of them is
#: good news.
UNRECORDED_TYPE = (
    "no event of this type is recorded in the window. That is not a measured "
    "zero: either detection found nothing worth valuing or no detection run is "
    "on record, and the ledger cannot tell the two apart.")


def _gap(subject: str, reason: str, detail: str) -> dict[str, str]:
    """One named hole in the evidence. Never a silent omission."""
    return {"subject": subject, "reason": reason, "detail": detail}


def _as_decimal(value: Any) -> Optional[Decimal]:
    """A SQL aggregate as ``Decimal``.

    ``func.sum`` over a ``Numeric`` column comes back as ``Decimal`` on
    PostgreSQL and, depending on the driver, as a float on SQLite — where the
    column is stored as a REAL. Normalising through ``str`` here keeps money out
    of binary floating point for the one step that would otherwise reintroduce
    it, at the cost of the precision SQLite already lost.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None


def _bounds(window_start: date, window_end: date) -> tuple[datetime, datetime]:
    """A date window as the UTC datetime bounds a timestamp column compares to."""
    return (datetime.combine(window_start, time.min, tzinfo=timezone.utc),
            datetime.combine(window_end, time.max, tzinfo=timezone.utc))


def current_trial(session: Session, org: str) -> Optional[models.IntelligenceTrial]:
    """This organization's trial, or ``None``.

    Most recent first, because an organization that reconnected different books
    can hold more than one row and the live one is the one being evaluated.
    """
    return session.scalars(
        select(models.IntelligenceTrial)
        .where(models.IntelligenceTrial.organization_id == org)
        .order_by(models.IntelligenceTrial.started_at.desc())
        .limit(1)).first()


# ── ledger rollups ───────────────────────────────────────────────────────────
def _by_class(session: Session, org: str,
              start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
    """The ledger for a window, grouped by value class. One query.

    ``events`` and ``amounts`` are counted separately on purpose. ``func.sum``
    skips NULL amounts — correct SQL, and it would quietly turn "four events, one
    of which carries no defensible money" into a total that reads as complete.
    Reporting both counts means a caller can see that a class total covers fewer
    rows than the class holds.
    """
    rows = session.execute(
        select(models.ValueEvent.value_class,
               func.count(),
               func.count(models.ValueEvent.amount),
               func.sum(models.ValueEvent.amount))
        .where(models.ValueEvent.organization_id == org,
               models.ValueEvent.occurred_at >= start,
               models.ValueEvent.occurred_at <= end,
               # Superseded rows are history. A re-measured line must count
               # once, at its current amount, or a reprice inflates the total.
               models.ValueEvent.superseded_at.is_(None))
        .group_by(models.ValueEvent.value_class)).all()

    out: dict[str, dict[str, Any]] = {
        cls.value: {"events": 0, "amounts": 0, "amounts_missing": 0, "amount": None}
        for cls in ValueClass}
    for value_class, events, amounts, total in rows:
        out[str(value_class)] = {
            "events": int(events or 0),
            "amounts": int(amounts or 0),
            "amounts_missing": int(events or 0) - int(amounts or 0),
            "amount": _as_decimal(total),
        }
    return out


def _headline(classes: dict[str, dict[str, Any]],
              total_events: int) -> tuple[Optional[Decimal], int]:
    """The ATTRIBUTED total for one window, and how many of its events lack one.

    The single place this codebase decides the difference between **UNKNOWN**
    and a **measured zero**, because the two are one line apart and the wrong
    one reads as good news. ``None`` means the window holds no events at all and
    nothing here can tell "detection ran and found nothing worth valuing" from
    "no detection run is on record". ``Decimal("0")`` means events exist and none
    of them were attributed — a real, reportable zero.

    Written out rather than as ``amount or Decimal("0")`` on purpose. That idiom
    is the exact shape §1 says to distrust, and here it would be doing real work
    — turning a NULL sum into a zero — behind a form that reads as incidental.
    The zero is a claim; it gets its own line.

    The second element is the count of attributed events carrying no defensible
    amount, so a caller can say that its total covers fewer rows than the class
    holds. It is zero where the answer is UNKNOWN, because there is nothing for
    it to be missing from.
    """
    if not total_events:
        return None, 0
    attributed = classes[ValueClass.ATTRIBUTED.value]
    amount = attributed["amount"]
    if amount is None:
        amount = Decimal("0")
    return amount, int(attributed["amounts_missing"])


def _by_event_type(session: Session, org: str,
                   start: datetime, end: datetime) -> list[dict[str, Any]]:
    """The same window broken down by event type *and* class. One query.

    Restricted to the classes the surface reports. ESTIMATED left this API
    because nothing can produce a defensible one, and this breakdown was the way
    back in: it groups over whatever classes it finds, so a seeded ESTIMATED row
    reached the screen inside ``by_event_type`` with its amount intact, past the
    top-level fields that had been removed to keep it out. Withdrawing a class
    from the headline and leaving it in the breakdown is not withdrawing it.
    """
    rows = session.execute(
        select(models.ValueEvent.event_type,
               models.ValueEvent.value_class,
               func.count(),
               func.sum(models.ValueEvent.amount))
        .where(models.ValueEvent.organization_id == org,
               models.ValueEvent.occurred_at >= start,
               models.ValueEvent.occurred_at <= end,
               models.ValueEvent.superseded_at.is_(None),
               models.ValueEvent.value_class.in_(REPORTED_VALUE_CLASSES))
        .group_by(models.ValueEvent.event_type, models.ValueEvent.value_class)
        .order_by(models.ValueEvent.event_type,
                  models.ValueEvent.value_class)).all()
    return [{"event_type": str(event_type), "value_class": str(value_class),
             "events": int(events or 0), "amount": _as_decimal(total)}
            for event_type, value_class, events, total in rows]


def list_events(session: Session, org: str, *,
                event_type: Optional[ValueEventType] = None,
                value_class: Optional[ValueClass] = None,
                readable_until: Optional[datetime] = None,
                limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """One page of the ledger itself, newest business fact first.

    The only read in this module that is not an aggregate, and it exists so the
    headline can be opened rather than believed: each row carries the ``basis``
    it was computed from and the ``evidence_refs`` it was computed over, so a
    reader re-derives the amount from its operands instead of taking the total on
    trust. A figure nobody can drill into is an assertion however carefully it
    was computed.

    ``total`` is counted under the same filters rather than read off the page.
    A page that says "100 events" while the filter matches four thousand invites
    exactly one mistake, which is adding the page up and calling it the total —
    hence ``page_is_not_a_total`` beside it.

    Ordered by ``occurred_at`` with ``value_event_id`` as the tie-break, because
    several events can share a timestamp to the second and a paged list whose
    order is not total will show one row twice and skip another.

    ``readable_until`` caps the window at a moment the caller is entitled to see
    up to, and is the one filter that is *not* a user choice: an organization
    whose intelligence plan has lapsed keeps its trial on record, and the rule
    the router applies is that it may still read what PIE did for it *then*.
    ``None`` means unbounded. The bound is applied to ``total`` as well as to the
    page, so the count and the rows agree — a total counted past a cap the reader
    cannot page to is a number that cannot be opened, which is the one thing this
    surface exists to avoid. It is echoed back as ``readable_until`` so a screen
    can say the view is frozen rather than leaving a truncated ledger looking
    like the whole one.
    """
    where = [models.ValueEvent.organization_id == org,
             models.ValueEvent.superseded_at.is_(None)]
    if event_type is not None:
        where.append(models.ValueEvent.event_type == event_type.value)
    if value_class is not None:
        where.append(models.ValueEvent.value_class == value_class.value)
    if readable_until is not None:
        where.append(models.ValueEvent.occurred_at <= readable_until)

    total = int(session.scalar(
        select(func.count()).select_from(models.ValueEvent).where(*where)) or 0)
    rows = session.scalars(
        select(models.ValueEvent).where(*where)
        .order_by(models.ValueEvent.occurred_at.desc(),
                  models.ValueEvent.value_event_id)
        .limit(limit).offset(offset)).all()

    return {
        "events": [
            {
                "value_event_id": row.value_event_id,
                "event_type": row.event_type,
                "value_class": row.value_class,
                # ``None`` where the class carries no defensible money. Never
                # coerced to zero on the way out — see ``ValueEventDraft.amount``.
                "amount": _as_decimal(row.amount),
                "currency": row.currency,
                "basis": row.basis or {},
                "evidence_refs": row.evidence_refs or [],
                "occurred_at": clock.iso(row.occurred_at),
                # Per row, not per response: the ledger is append-only, so a
                # window can legitimately span two policies and there is no one
                # version that judged all of it.
                "thresholds_version": row.thresholds_version,
                "created_at": clock.iso(row.created_at),
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        # Named even when it is ``None``, so a client reads "unbounded" from the
        # payload rather than from the key being absent.
        "readable_until": clock.iso(readable_until) if readable_until else None,
        "currency": "INR",
        "page_is_not_a_total": (
            "These are ledger rows, not a rollup. Summing them adds POTENTIAL "
            "to ATTRIBUTED — two statements about the same line — and would "
            "double count it. The headline comes from the summary."),
    }


# ── window metrics over the priced-line evidence ─────────────────────────────
def _priced_lines(session: Session, org: str,
                  start: datetime, end: datetime) -> dict[str, Any]:
    """Aggregated margin and the counts behind it, for one window. Two queries.

    Margin is ``sum(gross_profit) / sum(line_revenue)`` — the aggregate
    definition, never the mean of per-line margins — and it is computed over
    lines that carry *both* figures. Lines missing either are excluded from the
    arithmetic and counted, so the caller can see how much of the window the
    ratio actually speaks for. Guarding the arithmetic rather than the objection
    is the whole difference between a margin that is unknown and one that reads
    as healthy because the loss-making rows had no cost on them.
    """
    priced = session.scalar(
        select(func.count())
        .select_from(models.QuoteDecision)
        .where(models.QuoteDecision.organization_id == org,
               models.QuoteDecision.created_at >= start,
               models.QuoteDecision.created_at <= end)) or 0

    # Everything below is over the *costed* lines only, numerator and
    # denominator alike. Counting approvals across all priced lines and dividing
    # by the costed ones produced a rate above 1 — two different populations in
    # one ratio, which is the same class of mistake as dividing profit earned on
    # costed revenue by all revenue.
    costed, approval_required, revenue, gross_profit = session.execute(
        select(func.count(),
               func.count().filter(models.QuoteDecision.requires_approval.is_(True)),
               func.sum(models.QuoteDecision.line_revenue),
               func.sum(models.QuoteDecision.gross_profit))
        .where(models.QuoteDecision.organization_id == org,
               models.QuoteDecision.created_at >= start,
               models.QuoteDecision.created_at <= end,
               models.QuoteDecision.unit_cost.is_not(None),
               models.QuoteDecision.line_revenue.is_not(None),
               models.QuoteDecision.gross_profit.is_not(None))).one()

    revenue = _as_decimal(revenue)
    gross_profit = _as_decimal(gross_profit)
    # Rounded because this is persisted in a baseline row: an unrounded binary
    # float would differ in its last digits between two recomputes over
    # identical evidence, and a stored metric that moves without its inputs
    # moving is unexplainable.
    margin = (round(float(gross_profit / revenue), 4)
              if revenue is not None and revenue > 0 and gross_profit is not None
              else None)

    priced = int(priced)
    costed = int(costed or 0)
    approval_required = int(approval_required or 0)
    return {
        "priced_lines": priced,
        "costed_lines": costed,
        # Named for what it counts: a line is excluded from the margin figures
        # if it is missing cost, revenue OR gross profit — not cost alone. The
        # sentence in the gap has to say the same thing, or a reader chases a
        # missing purchase cost on a line whose revenue is what is absent.
        "uncostable_lines": priced - costed,
        "approval_required_lines": approval_required,
        # A rate over the costed lines, because an uncosted line could not have
        # been judged against a floor in the first place.
        "approval_required_rate": (round(approval_required / costed, 4)
                                   if costed else None),
        "quoted_revenue": revenue,
        "gross_profit": gross_profit,
        "margin": margin,
    }


def _quote_outcomes(session: Session, org: str,
                    start: datetime, end: datetime) -> dict[str, Any]:
    """Won and lost counts for quotes decided in the window. One GROUP BY.

    Sent quotes are counted alongside them because a win rate needs a book that
    could have produced a win. ``QUOTE_OUTCOME_TRANSITIONS`` reaches WON only
    from SENT while LOST is reachable straight from DRAFT, and SENT is stamped
    only once a quote has actually been written out to the customer's system —
    so an organization whose connector cannot write accumulates losses and
    structurally never records a win. Dividing that 0 by its losses states a 0%
    win rate for a question the evidence could not have answered either way.
    """
    rows = session.execute(
        select(models.QuoteOutcome.status, func.count(),
               func.count().filter(models.QuoteOutcome.sent_at.is_not(None)))
        .where(models.QuoteOutcome.organization_id == org,
               models.QuoteOutcome.decided_at.is_not(None),
               models.QuoteOutcome.decided_at >= start,
               models.QuoteOutcome.decided_at <= end)
        .group_by(models.QuoteOutcome.status)).all()
    counts = {str(status): int(n or 0) for status, n, _ in rows}
    sent_counts = {str(status): int(n or 0) for status, _, n in rows}
    won = counts.get(QuoteOutcomeStatus.WON.value, 0)
    lost = counts.get(QuoteOutcomeStatus.LOST.value, 0)
    decided = won + lost
    # Of the quotes decided here, the ones that ever reached SENT — the only
    # ones a win could have come from. A WON row always carries ``sent_at``; a
    # quote that went DRAFT -> LOST never can.
    decided_quotes_ever_sent = (sent_counts.get(QuoteOutcomeStatus.WON.value, 0)
                   + sent_counts.get(QuoteOutcomeStatus.LOST.value, 0))
    return {"quotes_won": won, "quotes_lost": lost, "quotes_decided": decided,
            # Reported rather than left implicit, so the UNKNOWN below can be
            # read: none of 3 decided quotes was ever sent is the whole reason.
            "decided_quotes_ever_sent": decided_quotes_ever_sent,
            # ``None`` rather than 0.0 in two cases, and neither is a measured
            # zero. Nothing decided at all is the first. The second is a book
            # that decided plenty and could not have won any of it — 0/n over a
            # numerator nothing could reach. A quote that *was* sent and then
            # lost is a real zero and stays one: the two must not collapse.
            #
            # ``won`` is read beside ``decided_quotes_ever_sent`` rather than trusted to
            # imply it. A recorded win proves a win was recordable whatever the
            # sent stamp says, so a row written before that column meant
            # anything cannot turn its own book into an UNKNOWN.
            "quote_win_rate": (round(won / decided, 4)
                               if decided and (won or decided_quotes_ever_sent) else None)}


def _wins_unrecordable_gap(outcomes: dict[str, Any]) -> Optional[dict[str, str]]:
    """Named where a window decided quotes that could never have been won.

    Two shapes, one gap, because they are one defect at different strengths.
    Where *no* decided quote was ever sent there is no win rate to report at
    all. Where only *some* were, a rate is still computed — and the ones that
    could not have won sit in its denominator, pulling it down. 1 win from 2
    sendable quotes is a 50% book; the same rows beside eight quotes that never
    reached SENT report 10%, and nothing on the screen says the difference.
    That number is not wrong the way a bug is wrong, it is wrong the way a
    benign default is: it answers confidently where the evidence does not
    reach. So it carries what would be needed to read it.

    One wording for both windows the report draws: the baseline and the trial
    are the same claim about two periods, and a second copy of the sentence
    would drift from this one.
    """
    decided = outcomes["quotes_decided"]
    sendable = outcomes["decided_quotes_ever_sent"]
    if not decided:
        return None
    if not (outcomes["quotes_won"] or sendable):
        return _gap("quote_win_rate", WINS_NOT_RECORDABLE_IN_WINDOW,
                    f"{decided} quotes were decided in this window and none of them "
                    "was ever recorded as sent, so no win could have been recorded "
                    "either. The win rate is UNKNOWN, not 0%")
    if decided > sendable > 0:
        return _gap("quote_win_rate", WINS_NOT_RECORDABLE_IN_WINDOW,
                    f"{decided - sendable} of {decided} decided quotes were never "
                    f"recorded as sent, so no win could have been recorded against "
                    f"them. The win rate is over all {decided}; among the {sendable} "
                    "that could have been won it is higher")
    return None


def _productivity(session: Session, org: str,
                  start: datetime, end: datetime) -> dict[str, Any]:
    """Counted work, with no rupee value attached anywhere. Two queries."""
    quotes, lines = session.execute(
        select(func.count(func.distinct(models.QuoteDecision.quote_id)), func.count())
        .where(models.QuoteDecision.organization_id == org,
               models.QuoteDecision.created_at >= start,
               models.QuoteDecision.created_at <= end)).one()
    approvals = session.scalar(
        select(func.count())
        .select_from(models.ApprovalRequest)
        .where(models.ApprovalRequest.organization_id == org,
               models.ApprovalRequest.decided_at.is_not(None),
               models.ApprovalRequest.decided_at >= start,
               models.ApprovalRequest.decided_at <= end)) or 0
    return {
        "note": ("Counted, never valued. This business holds no hourly rate, so "
                 "these are reported as work done and never converted to rupees."),
        "quotes_priced": int(quotes or 0),
        "lines_priced": int(lines or 0),
        "approvals_turned_round": int(approvals),
    }


# ── baseline ─────────────────────────────────────────────────────────────────
def capture_baseline(session: Session, org: str,
                     trial: models.IntelligenceTrial,
                     thresholds: Optional[CommercialThresholds] = None,
                     ) -> models.EvaluationBaseline:
    """What the numbers looked like in the 90 days before this trial started.

    Rewritten in place if one already exists for the trial. That is safe here
    and only here: a baseline is derived state a full re-sync rebuilds, which is
    exactly why it is not a column on ``IntelligenceTrial`` — the trial row is
    an entitlement fact and a recompute must never be able to touch it.

    A metric that cannot be computed is stored as ``None`` and named in
    ``evidence_gaps``. It is never defaulted, and the comparison in the report
    is skipped rather than being drawn against a window with nothing in it. A
    business that connected its books a week before the trial has no meaningful
    "before", and the honest report says so.
    """
    th = thresholds or load_for_org(session, org)
    started = clock.aware(trial.started_at) or clock.now()
    window_end = started.date() - timedelta(days=1)
    window_start = window_end - timedelta(days=BASELINE_DAYS - 1)
    start_dt, end_dt = _bounds(window_start, window_end)

    gaps: list[dict[str, str]] = []

    invoice_lines, revenue = session.execute(
        select(func.count(), func.sum(models.SalesTxn.line_revenue))
        .where(models.SalesTxn.organization_id == org,
               models.SalesTxn.date >= window_start,
               models.SalesTxn.date <= window_end)).one()
    invoice_lines = int(invoice_lines or 0)
    revenue = _as_decimal(revenue)
    if not invoice_lines:
        gaps.append(_gap("revenue", NO_INVOICE_LINES_IN_WINDOW,
                         f"no invoice lines are synced between {window_start} and "
                         f"{window_end}, so there is no 'before' revenue to compare "
                         "against"))

    priced = _priced_lines(session, org, start_dt, end_dt)
    if not priced["priced_lines"]:
        gaps.append(_gap("margin", NO_PRICED_LINES_IN_WINDOW,
                         "no quote lines were priced through the platform before the "
                         "trial, which is expected — it means quoted margin has no "
                         "baseline and the report compares nothing"))
    elif not priced["costed_lines"]:
        gaps.append(_gap("margin", NO_COSTED_LINES_IN_WINDOW,
                         "quote lines exist in the window but none of them carry the "
                         "cost, revenue and gross profit a margin needs, so no margin "
                         "can be stated for them"))
    elif priced["uncostable_lines"]:
        gaps.append(_gap("margin", NO_COSTED_LINES_IN_WINDOW,
                         f"{priced['uncostable_lines']} of {priced['priced_lines']} "
                         "priced lines are missing cost, revenue or gross profit and "
                         "are excluded from the "
                         "margin above"))

    outcomes = _quote_outcomes(session, org, start_dt, end_dt)
    unrecordable = _wins_unrecordable_gap(outcomes)
    if not outcomes["quotes_decided"]:
        gaps.append(_gap("quote_win_rate", NO_DECIDED_QUOTES_IN_WINDOW,
                         "no quote was won or lost in the window, so there is no "
                         "baseline win rate"))
    elif unrecordable:
        gaps.append(unrecordable)

    metrics: dict[str, Any] = {
        "invoice_lines": invoice_lines,
        "revenue": str(revenue) if revenue is not None else None,
        "priced_lines": priced["priced_lines"],
        "costed_lines": priced["costed_lines"],
        "uncostable_lines": priced["uncostable_lines"],
        "quoted_margin": priced["margin"],
        "approval_required_lines": priced["approval_required_lines"],
        "approval_required_rate": priced["approval_required_rate"],
        **{k: outcomes[k] for k in
           ("quotes_won", "quotes_lost", "quotes_decided", "decided_quotes_ever_sent",
            "quote_win_rate")},
    }

    row = session.scalars(
        select(models.EvaluationBaseline)
        .where(models.EvaluationBaseline.organization_id == org,
               models.EvaluationBaseline.trial_id == trial.trial_id)).first()
    if row is None:
        row = models.EvaluationBaseline(organization_id=org, trial_id=trial.trial_id)
        session.add(row)
    row.captured_at = clock.now()
    row.window_start = window_start
    row.window_end = window_end
    row.metrics = metrics
    row.evidence_gaps = gaps
    row.thresholds_version = th.version
    session.flush()
    return row


# ── live progress and the report ─────────────────────────────────────────────
def value_summary(session: Session, org: str, *,
                  window: Optional[Window] = None,
                  days: int = SUMMARY_DAYS,
                  readable_until: Optional[datetime] = None) -> dict[str, Any]:
    """What the platform is measured to have been worth over one window.

    Reads the ledger; runs no detector. The figures are computed on demand from
    recorded events precisely so they cannot go stale against their own evidence
    — but that also means a window with no events says UNKNOWN rather than zero,
    because nothing here can tell "detected and found nothing" from "detection
    never ran".

    ``window`` is chosen by ``summary_window`` when the caller does not supply
    one; ``thirty_day_report`` supplies the trial window explicitly, because a
    before-and-after report is about the trial by definition and must not follow
    the summary's default when that default changes.

    Was ``trial_progress``, and the rename is the fix rather than tidying. The
    old name was accurate — it measured the trial and only ever the trial — and
    the screen used it as the general value summary, so a customer's headline
    froze on the day their trial ended. Naming it for the window it is given
    makes the caller state which period it wants.
    """
    if window is None:
        window = summary_window(session, org, days=days,
                                readable_until=readable_until)
    trial = current_trial(session, org)
    started, until = window.start, window.end

    classes = _by_class(session, org, started, until)
    total_events = sum(c["events"] for c in classes.values())
    gaps: list[dict[str, str]] = []

    attributed = classes[ValueClass.ATTRIBUTED.value]
    headline, missing = _headline(classes, total_events)
    if headline is None:
        gaps.append(_gap("attributed_value", NO_EVENTS_RECORDED,
                         f"no value events are recorded in {window.label.lower()}. "
                         "That is not a measured zero — it means no detection run "
                         "has been recorded in this window, and the two cannot be "
                         "told apart from here"))
    elif missing:
        gaps.append(_gap("attributed_value", NOT_MEASURABLE,
                         f"{missing} attributed events carry no amount and are "
                         "not in the total"))

    # Every event type that produced nothing in this window is *named*. An
    # event type simply missing from the breakdown is the absence-of-evidence
    # failure in its quietest form: the reader sees four types and no fifth, and
    # reads the fifth as ₹0 rather than as unmeasured. Two of the five have no
    # detector at all and one has a detector nothing writes evidence for, so the
    # reason is looked up where it is known and falls back to naming the
    # ambiguity honestly where it is not.
    by_type = _by_event_type(session, org, started, until)
    measured = {row["event_type"] for row in by_type}
    for event_type in ValueEventType:
        if event_type.value in measured:
            continue
        gaps.append(_gap(event_type.value, NOT_MEASURABLE,
                         NOT_MEASURABLE_REASONS.get(event_type, UNRECORDED_TYPE)))

    trial_ends = clock.aware(trial.ends_at) if trial is not None else None
    return {
        # The window is the authority for what was measured; the trial block is
        # the trial's own facts and is ``None`` where there has never been one.
        # They were one object while the two were always the same period, and
        # keeping them fused is what let a stale window pass as a live one.
        "window": window.as_dict(),
        "trial": None if trial is None else {
            "trial_id": trial.trial_id,
            "started_at": clock.iso(clock.aware(trial.started_at)),
            "ends_at": clock.iso(trial_ends),
            "is_running": bool(trial_ends and trial_ends > clock.now()),
            "days_remaining": (max(0, (trial_ends - clock.now()).days)
                               if trial_ends else 0)},
        "measured_to": clock.iso(until),
        # The headline, and only ATTRIBUTED is in it.
        "attributed_value": headline,
        "attributed_events": attributed["events"],
        # Reported beside it, never added into it.
        "potential_value": classes[ValueClass.POTENTIAL.value]["amount"],
        "potential_events": classes[ValueClass.POTENTIAL.value]["events"],
        "realized_value": classes[ValueClass.REALIZED.value]["amount"],
        "realized_events": classes[ValueClass.REALIZED.value]["events"],
        # ESTIMATED is deliberately absent. No detector produces it — nothing in
        # the evidence supports a defensible estimate today — and a tile reading
        # "Estimated ₹0" is a fabricated zero in the exact sense §1 forbids: it
        # states a measurement where none was made. The enum member stays for
        # the day something can produce one; the surface does not.
        "class_totals_are_not_summable": (
            "POTENTIAL and ATTRIBUTED can describe the same quote line — one as "
            "the flag, one as the win that followed it. They are separate "
            "statements and must never be added together."),
        "by_event_type": by_type,
        "productivity": _productivity(session, org, started, until),
        "currency": "INR",
        "evidence_gaps": gaps,
    }


def _period_row(session: Session, org: str, period: periods.Period,
                start: datetime, end: datetime,
                complete: bool) -> dict[str, Any]:
    """One month of the roll-up, measured over the part of it that has happened.

    ``attributed_value`` is ``None`` — not ``0`` — for a month the ledger holds
    no events for, and a chart must render that as a **break in the line rather
    than a point at zero**. This is the one place the roll-up deliberately
    parts company with ``periods.bucket_by_month``, which fills a missing month
    with ``0.0`` and is right to: an empty month of *revenue* is a fact about
    the business, while an empty month of *value events* cannot be told apart
    from a month detection never ran over.
    """
    classes = _by_class(session, org, start, end)
    events = sum(c["events"] for c in classes.values())
    amount, missing = _headline(classes, events)
    return {
        "period": f"{period.start.year:04d}-{period.start.month:02d}",
        "label": period.label,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "measured_to": clock.iso(end),
        # A month whose last instant has not arrived yet, or one cut short by
        # the plan freeze. Its value is real and its *cost* is not comparable to
        # it, which is why it is reported beside the total and never inside it.
        "complete": complete,
        "measured": bool(events),
        "events": events,
        "attributed_value": amount,
        "attributed_events": classes[ValueClass.ATTRIBUTED.value]["events"],
        "amounts_missing": missing,
    }


def value_rollup(session: Session, org: str, *,
                 months: int = ROLLUP_MONTHS,
                 monthly_cost: Optional[Decimal] = None,
                 readable_until: Optional[datetime] = None) -> dict[str, Any]:
    """What the platform has been worth month by month, and the return on it.

    ``thirty_day_report`` answers this for the trial and only for the trial, so
    the day a trial ends the one ROI figure this platform states disappears and
    never comes back — the customer deciding whether to keep paying in month
    fourteen has the same evidence as the one deciding in month one, minus the
    baseline. This is that figure over an arbitrary span, and it is the surface
    a renewal after the first year is argued from.

    **Complete calendar months only, in the total and in the ratio.** The month
    in progress is reported separately as ``in_progress`` and is never added to
    ``attributed_value``. A subscription bills a whole month; three days of it
    have produced three days of value; dividing one by the other understates the
    return by however far through the month the reader happens to be, and it
    would understate it differently every time the page was opened.

    **A month with no events on record refuses the ratio outright.** This is the
    §1 rule at the level a time series makes easy to miss: ``func.sum`` skips a
    month that recorded nothing, so the numerator quietly covers eight months
    while ``monthly_cost x 12`` covers twelve, and the result is a real-looking
    number computed over a span nobody measured. That understates rather than
    flatters, which is exactly why it would survive review — a conservative
    fabricated number is still a fabricated number. So the unmeasured months are
    *named* and ``roi`` is ``None``.

    ``monthly_cost`` is supplied by the caller for the same reason
    ``thirty_day_report`` takes ``pie_cost``: this platform holds no price for
    its own plans, and a default here would put a return figure nobody entered
    on the screen a renewal is signed against. It is a **rate** — what one month
    costs — because the span is many months; passing a total would silently
    divide a year of value by a month of cost.

    **The months are UTC months, and that is a stated limitation rather than an
    oversight.** ``commercial/insight`` cuts its calendar months in the tenant's
    own zone (``clock.today(tz)``), which is the better frame for a business
    calendar; everything in this module — ``_bounds``, ``summary_window``, the
    trial itself — is UTC. Following ``insight`` here would put two definitions
    of "a month" on one screen, where the summary's ninety days and the
    roll-up's twelve months would disagree about which side of a boundary an
    event fell on. The error is bounded by the tenant's offset, so an event in
    the last few hours of a month can land in the next one; it never
    double-counts and never drops a row, because the months tile exactly.
    Moving the whole module onto tenant-local windows is the fix, and it is a
    larger change than this function.
    """
    months = max(1, min(int(months), MAX_ROLLUP_MONTHS))
    now = clock.now()
    end = min(now, readable_until) if readable_until is not None else now

    rows: list[tuple[periods.Period, dict[str, Any]]] = []
    for period in periods.months_back(end.date(), months):
        start_at, close_at = _bounds(period.start, period.end)
        if start_at > end:
            # Only reachable when the freeze lands mid-month: ``months_back``
            # ends at ``end``'s own month, so nothing after it is generated.
            continue
        rows.append((period, _period_row(session, org, period, start_at,
                                         min(close_at, end),
                                         complete=close_at <= end)))

    whole = [(period, row) for period, row in rows if row["complete"]]
    complete = [row for _, row in whole]
    in_progress = next((row for _, row in rows if not row["complete"]), None)
    gaps: list[dict[str, str]] = []

    span_from = whole[0][0].start if whole else None
    span_to = whole[-1][0].end if whole else None

    total: Optional[Decimal] = None
    attributed_events = 0
    if whole:
        # The total is its own query over the whole span rather than a sum of
        # the rows above. Both give the same number today; only one of them is
        # a statement the database makes, and the module docstring's rule about
        # totals assembled in Python is there because the loop is what breaks
        # when a class filter or a supersession rule changes in one place.
        span_start, span_end = _bounds(span_from, span_to)
        classes = _by_class(session, org, span_start, span_end)
        span_events = sum(c["events"] for c in classes.values())
        total, missing = _headline(classes, span_events)
        attributed_events = classes[ValueClass.ATTRIBUTED.value]["events"]
        if missing:
            gaps.append(_gap("attributed_value", NOT_MEASURABLE,
                             f"{missing} attributed events carry no amount and "
                             "are not in the total"))
    else:
        gaps.append(_gap("periods", NO_COMPLETE_PERIOD,
                         "no complete calendar month falls inside this span, so "
                         "there is nothing to roll up. A month still running is "
                         "reported on its own and is not a total"))

    unmeasured = [row["label"] for row in complete if not row["measured"]]
    if unmeasured:
        gaps.append(_gap("periods", PERIOD_NOT_MEASURED,
                         "no value event is recorded for "
                         + ", ".join(unmeasured)
                         + ". Those months are not measured zeros, so they are "
                         "not in the total and no return is stated over a span "
                         "that contains them"))

    platform_cost: Optional[Decimal] = None
    if monthly_cost is not None and complete:
        platform_cost = (monthly_cost * len(complete)).quantize(
            MONEY_EXPONENT, rounding=ROUND_HALF_UP)

    ratio = None if unmeasured else roi(total, platform_cost)
    if ratio is None:
        gaps.append(_gap(
            "roi",
            NO_PLATFORM_COST_SUPPLIED if monthly_cost is None else NOT_MEASURABLE,
            "no monthly platform cost was supplied, or the span is not fully "
            "measured, so return on investment is UNKNOWN. Render it as "
            "UNKNOWN — not as 0x"))

    return {
        "span": {
            "months_requested": months,
            "complete_months": len(complete),
            "measured_months": sum(1 for row in complete if row["measured"]),
            "start": span_from.isoformat() if span_from else None,
            "end": span_to.isoformat() if span_to else None,
            "label": (periods.label_for(span_from, span_to)
                      if span_from and span_to else None),
            "frozen_at": clock.iso(readable_until) if readable_until else None,
        },
        "periods": complete,
        # Beside the total, never inside it — the same discipline POTENTIAL gets
        # next to ATTRIBUTED, and for the same reason: one of them is comparable
        # to a month of cost and the other is not.
        "in_progress": in_progress,
        "attributed_value": total,
        "attributed_events": attributed_events,
        "monthly_cost": monthly_cost,
        "platform_cost": platform_cost,
        "roi": ratio,
        "roi_is_unknown": ratio is None,
        "currency": "INR",
        "evidence_gaps": gaps,
    }


def thirty_day_report(session: Session, org: str, *,
                      pie_cost: Optional[Decimal] = None) -> dict[str, Any]:
    """The full evaluation report: before, during, and what it was worth.

    ``pie_cost`` is supplied by the caller because this platform holds no price
    for its own plans. Left out, ROI comes back ``None`` and the gap says why —
    the caller must render that as UNKNOWN. A default cost would make every ROI
    on every screen a number nobody entered.
    """
    trial = current_trial(session, org)
    if trial is None:
        # The summary would happily measure a trailing window here, and that is
        # right for the summary and wrong for this. A before-and-after report
        # with no "before" is not a thinner report, it is a different claim.
        return {"window": None, "trial": None, "attributed_value": None,
                "baseline": None, "comparison": None,
                "roi": None, "roi_is_unknown": True,
                "evidence_gaps": [_gap(
                    "trial", NO_TRIAL_ON_RECORD,
                    "this organization has no intelligence trial on record, so "
                    "there is no before-and-after to draw")]}

    started = clock.aware(trial.started_at) or clock.now()
    until = min(clock.now(), clock.aware(trial.ends_at) or clock.now())

    # Pinned to the trial explicitly rather than taken from the summary's
    # default: this report is about the trial by definition, and it must not
    # start measuring a trailing window the day that default changes.
    progress = value_summary(session, org, window=Window(
        start=started, end=until, basis=WINDOW_TRIAL,
        label="Your Commercial Intelligence trial"))
    gaps: list[dict[str, str]] = list(progress.get("evidence_gaps") or [])

    report: dict[str, Any] = {
        **progress,
        "baseline": None,
        "comparison": None,
        "roi": None,
        "roi_is_unknown": True,
    }

    baseline = session.scalars(
        select(models.EvaluationBaseline)
        .where(models.EvaluationBaseline.organization_id == org,
               models.EvaluationBaseline.trial_id == trial.trial_id)
        .order_by(models.EvaluationBaseline.captured_at.desc())
        .limit(1)).first()

    during = _priced_lines(session, org, started, until)
    during_outcomes = _quote_outcomes(session, org, started, until)
    # Named because the counts beside it do not explain it: quotes decided and a
    # blank win rate reads as a bug otherwise. "Nothing decided" needs no gap —
    # the screen reads that straight off ``quotes_decided``.
    unrecordable = _wins_unrecordable_gap(during_outcomes)
    if unrecordable:
        gaps.append(unrecordable)

    if baseline is None:
        gaps.append(_gap("comparison", NO_BASELINE_ON_RECORD,
                         "no baseline was captured for this trial, so nothing here "
                         "is compared against how the business ran before"))
    else:
        report["baseline"] = {
            "baseline_id": baseline.baseline_id,
            "captured_at": clock.iso(baseline.captured_at),
            "window_start": baseline.window_start.isoformat(),
            "window_end": baseline.window_end.isoformat(),
            "metrics": baseline.metrics or {},
            "evidence_gaps": baseline.evidence_gaps or [],
            "thresholds_version": baseline.thresholds_version,
        }
        before = (baseline.metrics or {}).get("quoted_margin")
        after = during["margin"]
        if before is None or after is None:
            gaps.append(_gap("comparison", NOT_MEASURABLE,
                             "quoted margin is missing on one side of the "
                             "comparison, so no movement is stated. A one-sided "
                             "figure is not an improvement"))
        else:
            report["comparison"] = {
                "quoted_margin_before": before,
                "quoted_margin_after": after,
                # Percentage POINTS, per the house convention for a margin move.
                "quoted_margin_movement_pp": round((after - before) * 100, 2),
            }

    report["during"] = {**during, **during_outcomes}

    value = report.get("attributed_value")
    ratio = roi(value, pie_cost)
    report["roi"] = ratio
    report["roi_is_unknown"] = ratio is None
    if ratio is None:
        gaps.append(_gap(
            "roi", NO_PLATFORM_COST_SUPPLIED if pie_cost is None else NOT_MEASURABLE,
            "no platform cost was supplied, or no attributed value is measurable, "
            "so return on investment is UNKNOWN. Render it as UNKNOWN — not as 0x"))

    report["evidence_gaps"] = gaps
    return report
