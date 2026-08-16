"""The read side: what the baseline was, what the trial has produced, and the report.

Everything here is a set-based query. No rollup iterates the ledger row by row,
because a total assembled in Python over ten thousand events is a total whose
correctness depends on the loop, and because the aggregate this business is
being asked to pay against should be a statement the database itself can make.

Three rules run through all of it.

**The headline is ATTRIBUTED and nothing else.** POTENTIAL, REALIZED and
ESTIMATED come back in their own fields with their own labels and are never
added into it — see ``ValueClass``. The classes describe overlapping facts on
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

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..commercial.config import CommercialThresholds
from ..commercial.policy import load_for_org
from ..domain import models
from ..domain.enums import QuoteOutcomeStatus, ValueClass, ValueEventType
from .calculator import roi
from .detectors import NOT_MEASURABLE_REASONS

#: How far back a baseline looks. Long enough that one quiet month does not
#: define "before", short enough to still describe the business as it is now.
BASELINE_DAYS = 90

# ── evidence-gap reasons ─────────────────────────────────────────────────────
NO_TRIAL_ON_RECORD = "NO_TRIAL_ON_RECORD"
NO_BASELINE_ON_RECORD = "NO_BASELINE_ON_RECORD"
NO_EVENTS_RECORDED = "NO_EVENTS_RECORDED"
NO_INVOICE_LINES_IN_WINDOW = "NO_INVOICE_LINES_IN_WINDOW"
NO_PRICED_LINES_IN_WINDOW = "NO_PRICED_LINES_IN_WINDOW"
NO_COSTED_LINES_IN_WINDOW = "NO_COSTED_LINES_IN_WINDOW"
NO_DECIDED_QUOTES_IN_WINDOW = "NO_DECIDED_QUOTES_IN_WINDOW"
NO_PLATFORM_COST_SUPPLIED = "NO_PLATFORM_COST_SUPPLIED"
NOT_MEASURABLE = "NOT_MEASURABLE"

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
               models.ValueEvent.occurred_at <= end)
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


def _by_event_type(session: Session, org: str,
                   start: datetime, end: datetime) -> list[dict[str, Any]]:
    """The same window broken down by event type *and* class. One query."""
    rows = session.execute(
        select(models.ValueEvent.event_type,
               models.ValueEvent.value_class,
               func.count(),
               func.sum(models.ValueEvent.amount))
        .where(models.ValueEvent.organization_id == org,
               models.ValueEvent.occurred_at >= start,
               models.ValueEvent.occurred_at <= end)
        .group_by(models.ValueEvent.event_type, models.ValueEvent.value_class)
        .order_by(models.ValueEvent.event_type,
                  models.ValueEvent.value_class)).all()
    return [{"event_type": str(event_type), "value_class": str(value_class),
             "events": int(events or 0), "amount": _as_decimal(total)}
            for event_type, value_class, events, total in rows]


def list_events(session: Session, org: str, *,
                event_type: Optional[ValueEventType] = None,
                value_class: Optional[ValueClass] = None,
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
    """
    where = [models.ValueEvent.organization_id == org]
    if event_type is not None:
        where.append(models.ValueEvent.event_type == event_type.value)
    if value_class is not None:
        where.append(models.ValueEvent.value_class == value_class.value)

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
        "uncosted_lines": priced - costed,
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
    """Won and lost counts for quotes decided in the window. One GROUP BY."""
    rows = session.execute(
        select(models.QuoteOutcome.status, func.count())
        .where(models.QuoteOutcome.organization_id == org,
               models.QuoteOutcome.decided_at.is_not(None),
               models.QuoteOutcome.decided_at >= start,
               models.QuoteOutcome.decided_at <= end)
        .group_by(models.QuoteOutcome.status)).all()
    counts = {str(status): int(n or 0) for status, n in rows}
    won = counts.get(QuoteOutcomeStatus.WON.value, 0)
    lost = counts.get(QuoteOutcomeStatus.LOST.value, 0)
    decided = won + lost
    return {"quotes_won": won, "quotes_lost": lost, "quotes_decided": decided,
            # ``None`` rather than 0.0 when nothing was decided: a win rate of
            # zero and no decisions at all are different facts, and only one of
            # them is bad news.
            "quote_win_rate": (round(won / decided, 4) if decided else None)}


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
                         "quote lines exist in the window but none carry a purchase "
                         "cost, so no margin can be stated for them"))
    elif priced["uncosted_lines"]:
        gaps.append(_gap("margin", NO_COSTED_LINES_IN_WINDOW,
                         f"{priced['uncosted_lines']} of {priced['priced_lines']} "
                         "priced lines carry no cost and are excluded from the "
                         "margin above"))

    outcomes = _quote_outcomes(session, org, start_dt, end_dt)
    if not outcomes["quotes_decided"]:
        gaps.append(_gap("quote_win_rate", NO_DECIDED_QUOTES_IN_WINDOW,
                         "no quote was won or lost in the window, so there is no "
                         "baseline win rate"))

    metrics: dict[str, Any] = {
        "invoice_lines": invoice_lines,
        "revenue": str(revenue) if revenue is not None else None,
        "priced_lines": priced["priced_lines"],
        "costed_lines": priced["costed_lines"],
        "uncosted_lines": priced["uncosted_lines"],
        "quoted_margin": priced["margin"],
        "approval_required_lines": priced["approval_required_lines"],
        "approval_required_rate": priced["approval_required_rate"],
        **{k: outcomes[k] for k in
           ("quotes_won", "quotes_lost", "quotes_decided", "quote_win_rate")},
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
def trial_progress(session: Session, org: str) -> dict[str, Any]:
    """The numbers so far in the live trial window.

    Reads the ledger; runs no detector. The report is computed on demand from
    recorded events precisely so it cannot go stale against its own evidence —
    but that also means a window with no events says UNKNOWN rather than zero,
    because nothing here can tell "detected and found nothing" from "detection
    never ran".
    """
    trial = current_trial(session, org)
    if trial is None:
        return {"trial": None, "attributed_value": None,
                "evidence_gaps": [_gap("trial", NO_TRIAL_ON_RECORD,
                                       "this organization has no intelligence trial "
                                       "on record, so there is no window to measure")]}

    started = clock.aware(trial.started_at) or clock.now()
    ends = clock.aware(trial.ends_at) or clock.now()
    until = min(clock.now(), ends)

    classes = _by_class(session, org, started, until)
    total_events = sum(c["events"] for c in classes.values())
    gaps: list[dict[str, str]] = []

    attributed = classes[ValueClass.ATTRIBUTED.value]
    if not total_events:
        headline: Optional[Decimal] = None
        gaps.append(_gap("attributed_value", NO_EVENTS_RECORDED,
                         "no value events are recorded in the trial window. That is "
                         "not a measured zero — it means no detection run has been "
                         "recorded, and the two cannot be told apart from here"))
    else:
        # The window holds events and none of them are ATTRIBUTED: a measured
        # zero, real and reportable, and deliberately not topped up from
        # POTENTIAL to make the window look better.
        #
        # Written out rather than as ``amount or Decimal("0")`` on purpose. That
        # idiom is the exact shape §1 says to distrust, and here it would be
        # doing real work — turning a NULL sum into a zero — behind a form that
        # reads as incidental. The zero is a claim; it gets its own line.
        headline = attributed["amount"]
        if headline is None:
            headline = Decimal("0")
        if attributed["amounts_missing"]:
            gaps.append(_gap("attributed_value", NOT_MEASURABLE,
                             f"{attributed['amounts_missing']} attributed events "
                             "carry no amount and are not in the total"))

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

    return {
        "trial": {"trial_id": trial.trial_id,
                  "started_at": clock.iso(started),
                  "ends_at": clock.iso(ends),
                  "measured_to": clock.iso(until),
                  "days_elapsed": max(0, (until - started).days),
                  "days_remaining": max(0, (ends - clock.now()).days)},
        # The headline, and only ATTRIBUTED is in it.
        "attributed_value": headline,
        "attributed_events": attributed["events"],
        # Reported beside it, never added into it.
        "potential_value": classes[ValueClass.POTENTIAL.value]["amount"],
        "potential_events": classes[ValueClass.POTENTIAL.value]["events"],
        "realized_value": classes[ValueClass.REALIZED.value]["amount"],
        "realized_events": classes[ValueClass.REALIZED.value]["events"],
        "estimated_value": classes[ValueClass.ESTIMATED.value]["amount"],
        "estimated_events": classes[ValueClass.ESTIMATED.value]["events"],
        "class_totals_are_not_summable": (
            "POTENTIAL and ATTRIBUTED can describe the same quote line — one as "
            "the flag, one as the win that followed it. They are separate "
            "statements and must never be added together."),
        "by_event_type": by_type,
        "productivity": _productivity(session, org, started, until),
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
    progress = trial_progress(session, org)
    gaps: list[dict[str, str]] = list(progress.get("evidence_gaps") or [])
    trial = current_trial(session, org)

    report: dict[str, Any] = {
        **progress,
        "baseline": None,
        "comparison": None,
        "roi": None,
        "roi_is_unknown": True,
    }
    if trial is None:
        report["evidence_gaps"] = gaps
        return report

    started = clock.aware(trial.started_at) or clock.now()
    until = min(clock.now(), clock.aware(trial.ends_at) or clock.now())

    baseline = session.scalars(
        select(models.EvaluationBaseline)
        .where(models.EvaluationBaseline.organization_id == org,
               models.EvaluationBaseline.trial_id == trial.trial_id)
        .order_by(models.EvaluationBaseline.captured_at.desc())
        .limit(1)).first()

    during = _priced_lines(session, org, started, until)
    during_outcomes = _quote_outcomes(session, org, started, until)

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
