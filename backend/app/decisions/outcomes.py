"""What the detector layer produced, and what humans did with it.

The detector layer has never had a stated false-alarm rate. Every threshold in
``signals/config.py`` was chosen so the output *reads* right — ``signals/margin``
says so in as many words, explaining that its severity scale was set so a
26%→13% collapse does not band LOW — and none was chosen against a measured rate
of cards a human then threw away. Re-tuning ``queue_margin_drop_pp`` today means
tuning against a number nobody has.

This computes that number from rows that are already persisted. ``Decision.status``
carries the lifecycle and ``DecisionRepository.record_human_action`` already
writes ``DISMISSED`` when somebody rejects a card, so nothing new is stored and
no migration is needed. Deterministic: same rows in, same report out.

**A dismissal rate over nothing is not zero.** The rate is ``None`` until enough
decisions have actually been judged, and the band says ``NOT_REVIEWED`` rather
than ``OK``. A queue nobody has worked produces no dismissals, and reporting that
as a 0% false-alarm rate would be exactly the benign default §1 forbids — the
same shape as the send gate that found no recorded snapshot and answered
"nothing is wrong". The honest answer to "how noisy is this detector" before
anyone has worked the queue is *unknown*.

**Two-sided, following ``ai/metrics.health_band``.** A type dismissed constantly
is a noisy detector. A type *never* dismissed is also a finding: either it is
trivially right, or the cards are being cleared without being read.

**Emission vs. amplification.** Signals are write-once and the detectors run
after every sync, so a condition that persists re-emits nightly; decisions are
keyed to an ISO-week bucket, so the same condition opens a fresh card weekly.
The per-type counts are reported separately for that reason — the ratio between
them is the cost of a persistent finding, and it is not visible from either
number alone.

---

**The second half: was the queue worked, and does it deserve to be.**

``summarize`` above asks whether a *detector* is noisy. ``adoption_summary``
below asks what the *people* did — acceptance by category and by user, the
modify rate, and acceptance against how deep the queue was at the moment
somebody ruled. Both read the same rows, so they live in one module and share
one vocabulary for what a human did. Two modules would mean two definitions of
"judged" that eventually disagree, in two payloads nobody could reconcile.

What it deliberately does **not** recompute: the dismissal rate and its band.
That is this file's own ``dismissal_band``, it is reported per type by
``summarize``, and a second copy under a different name would be the same number
answering to two owners.

Three numbers are the point, and this is honest about which it can produce:

1. *Acceptance by category and by user.* From indexed columns. A category
   nobody accepts should be switched off rather than tuned.
2. *Modify rate, and modify distance.* The rate is here. The distance is
   **not** — a ``Decision`` has no recommended value to measure against,
   because the AI recommendation is prose and the deterministic ``actions`` are
   a set of options rather than a number. It is measured where it is genuinely
   a number: on a priced quote line, against the reference the policy computed.
   Inventing a numeric proxy so the metric existed on both surfaces would be a
   number the interpretation layer had effectively produced.
3. *Acceptance against queue volume.* Past some depth, adding a **correct**
   alert reduces total action taken. That is the ceiling on how many signals
   should exist, and it should govern every future detector.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..clock import now as utc_now
from ..commercial.references import TARGET_MARGIN_PRICE
from ..config import settings
from ..domain import models
from ..domain.enums import DecisionStatus, HumanAction

log = logging.getLogger("pie_portal.decisions.outcomes")


#: Statuses in which a human has formed a view on whether the card was worth
#: raising. ESCALATED counts as a judgement: handing a decision upward asserts
#: it is real and beyond this person's authority, which is the opposite of
#: rejecting it.
#:
#: VIEWED deliberately does not count. Somebody opened the card and left it —
#: that is not a verdict on the detector, and counting it as one would let an
#: unworked queue masquerade as a reviewed one.
JUDGED_STATUSES = (
    DecisionStatus.ACTIONED.value,
    DecisionStatus.DISMISSED.value,
    DecisionStatus.OVERRIDDEN.value,
    DecisionStatus.ESCALATED.value,
)

#: The one status that says "this should not have been raised".
REJECTED_STATUS = DecisionStatus.DISMISSED.value


def _rate(n: int, total: int) -> Optional[float]:
    """A share, or ``None`` when there is nothing to take a share of.

    Returns ``None`` rather than ``0.0`` on an empty denominator. Zero is a
    measurement; this is the absence of one, and the two must not render alike.
    """
    return round(n / total, 4) if total else None


def dismissal_band(rate: Optional[float], judged: int) -> tuple[str, str]:
    """Two-sided band on a signal type's dismissal rate.

    Ordered so the two "we do not know" answers come first: a rate computed over
    two judged cards is not evidence about a detector, however extreme it looks.
    """
    if judged == 0:
        return "NOT_REVIEWED", (
            "No decision of this type has been actioned or dismissed, so the "
            "dismissal rate is unknown — not zero. Nothing about this detector's "
            "noise can be inferred until the queue is worked.")
    if judged < settings.SIGNAL_OUTCOME_MIN_SAMPLE:
        return "INSUFFICIENT_DATA", (
            f"Only {judged} decision(s) judged; fewer than the "
            f"{settings.SIGNAL_OUTCOME_MIN_SAMPLE} needed before a rate means "
            "anything. Reported for completeness, not as a finding.")
    if rate is not None and rate > settings.SIGNAL_DISMISSAL_RATE_MAX:
        return "HIGH", (
            f"{rate:.1%} of judged decisions were dismissed, above the "
            f"{settings.SIGNAL_DISMISSAL_RATE_MAX:.1%} bound — the threshold for "
            "this detector is raising work people do not want.")
    if rate is not None and rate < settings.SIGNAL_DISMISSAL_RATE_MIN:
        return "SUSPICIOUSLY_LOW", (
            f"{rate:.1%} of judged decisions were dismissed, below the "
            f"{settings.SIGNAL_DISMISSAL_RATE_MIN:.1%} bound — either this "
            "detector is trivially right, or cards are being cleared without "
            "being read. Both are worth knowing which.")
    return "OK", "Dismissal rate is within the expected band."


def summarize(decisions: Sequence[models.Decision],
              signals: Sequence[models.Signal], *, days: int) -> dict[str, Any]:
    """Per-type emission, outcome distribution and dismissal rate. Pure."""
    signals_by_type: dict[str, int] = {}
    for s in signals:
        signals_by_type[s.signal_type] = signals_by_type.get(s.signal_type, 0) + 1

    raised: dict[str, int] = {}
    statuses: dict[str, dict[str, int]] = {}
    for d in decisions:
        raised[d.decision_type] = raised.get(d.decision_type, 0) + 1
        by_status = statuses.setdefault(d.decision_type, {})
        by_status[d.status] = by_status.get(d.status, 0) + 1

    # Every type seen on either side. A type that emitted signals but opened no
    # decision is a real state — the decision phase may simply not have run —
    # and dropping it would hide that.
    types = sorted(set(signals_by_type) | set(raised))

    per_type: dict[str, Any] = {}
    total_judged = total_dismissed = 0
    for name in types:
        by_status = statuses.get(name, {})
        judged = sum(by_status.get(s, 0) for s in JUDGED_STATUSES)
        dismissed = by_status.get(REJECTED_STATUS, 0)
        rate = _rate(dismissed, judged)
        band, note = dismissal_band(rate, judged)
        total_judged += judged
        total_dismissed += dismissed

        emitted = signals_by_type.get(name, 0)
        opened = raised.get(name, 0)
        per_type[name] = {
            "signals_emitted": emitted,
            "decisions_raised": opened,
            # How many signal rows stand behind one card. Above 1 is the nightly
            # re-emission; it is not waste on its own, but it is what a persistent
            # finding costs, and it is unreadable from either count alone.
            "signals_per_decision": (round(emitted / opened, 2) if opened else None),
            "by_status": by_status,
            "judged": judged,
            "dismissed": dismissed,
            "dismissal_rate": rate,
            "band": band,
            "note": note,
        }

    overall_rate = _rate(total_dismissed, total_judged)
    overall_band, overall_note = dismissal_band(overall_rate, total_judged)
    return {
        "window_days": days,
        "signals_emitted": len(signals),
        "decisions_raised": len(decisions),
        "judged": total_judged,
        "dismissed": total_dismissed,
        "dismissal_rate": overall_rate,
        "band": overall_band,
        "note": overall_note,
        "by_type": per_type,
    }


def report(decision_repo, signal_repo, *, windows: tuple[int, ...] = (7, 30),
           now: Optional[datetime] = None) -> dict[str, Any]:
    """Rolling per-window report, and a log line for anything out of band.

    ``now`` is injectable so a test states its own reference instant rather than
    depending on the clock — the same reason detectors take ``as_of``.
    """
    ref = now or utc_now()
    out: dict[str, Any] = {"generated_at": ref.isoformat(), "windows": {}}
    for days in windows:
        cutoff = ref - timedelta(days=days)
        summary = summarize(decision_repo.since(cutoff), signal_repo.since(cutoff),
                            days=days)
        out["windows"][f"{days}d"] = summary
        for name, row in summary["by_type"].items():
            if row["band"] in ("HIGH", "SUSPICIOUSLY_LOW"):
                log.warning("detector outcome %s for %s over %dd: %s",
                            row["band"], name, days, row["note"])
    return out


# ── the queue-adoption half ──────────────────────────────────────────────────
#
# Everything below reuses this file's own `_rate` and `JUDGED_STATUSES` rather
# than restating them. `JUDGED_STATUSES` is the shared vocabulary — a human has
# formed a view — and the two questions differ only in what they divide by:
# a false-alarm rate divides dismissals by every judgement, while an acceptance
# rate leaves escalation out of the denominator, because handing a decision
# upward is not a verdict on the recommendation. Both definitions live here, one
# line apart, so the difference is visible instead of being two files' worth of
# drift.

#: The judgements that are a verdict on the recommendation itself.
RULED_STATUSES = tuple(s for s in JUDGED_STATUSES
                       if s != DecisionStatus.ESCALATED.value)

ACCEPTANCE_FLOOR = 0.20
ACCEPTANCE_CEILING = 0.90

#: Queue-depth buckets. Coarse deliberately — the question is whether the curve
#: bends, not where exactly, and narrow buckets on SME volumes produce a shape
#: that is mostly sampling noise.
DEPTH_BUCKETS = ((1, 5), (6, 15), (16, 40), (41, None))

#: Guard on the depth reconstruction, which is quadratic in the number of
#: decisions. Well above any real queue here; present so a pathological dataset
#: degrades to a stated refusal rather than a hang.
DEPTH_MAX_ROWS = 4000


def acceptance_band(rate: Optional[float], ruled: int) -> tuple[str, str]:
    """Two-sided band on an acceptance rate, mirroring ``dismissal_band``.

    Low means the category is not earning the interruption. High means it is
    being rubber-stamped, or firing only on the already-obvious — which costs
    attention without adding judgement, and is the harder failure to notice
    because it looks like success.
    """
    if ruled < settings.SIGNAL_OUTCOME_MIN_SAMPLE or rate is None:
        return "INSUFFICIENT_DATA", (
            f"Fewer than {settings.SIGNAL_OUTCOME_MIN_SAMPLE} decisions ruled on; "
            "no inference drawn in either direction.")
    if rate < ACCEPTANCE_FLOOR:
        return "LOW", (
            f"Accepted {rate:.0%} of the time, below the {ACCEPTANCE_FLOOR:.0%} "
            "floor — the category is not earning the interruption. Switching it "
            "off is a more honest response than tuning it.")
    if rate > ACCEPTANCE_CEILING:
        return "SUSPICIOUSLY_HIGH", (
            f"Accepted {rate:.0%} of the time, above the {ACCEPTANCE_CEILING:.0%} "
            "ceiling — either it is being rubber-stamped, or the detector only "
            "fires on situations the reader already knew about.")
    return "OK", "Acceptance sits within the expected band."


def _trail(d: models.Decision) -> list[dict[str, Any]]:
    ha = d.human_action or {}
    trail = ha.get("trail")
    if isinstance(trail, list) and trail:
        return [t for t in trail if isinstance(t, dict)]
    return [ha] if ha else []


def _when(entry: dict[str, Any]) -> Optional[datetime]:
    raw = entry.get("acted_at")
    if not isinstance(raw, str):
        return None
    try:
        return clock.aware(datetime.fromisoformat(raw))
    except ValueError:
        return None


_CLOSING = {HumanAction.ACT.value, HumanAction.DISMISS.value,
            HumanAction.OVERRIDE.value}


def _ruled_at(d: models.Decision) -> Optional[datetime]:
    """When somebody first ruled on this — not when they first looked at it.

    ``updated_at`` is not this. ``DecisionService.generate`` writes to open
    decisions on every run, so the ORM's ``onupdate`` fires on regeneration and
    the column records the last time a *detector* touched the row. It is the
    same property that makes ``detected_at`` the wrong basis for a window, and
    the trail is the only place a human's clock is honest.
    """
    for entry in _trail(d):
        if entry.get("action") in _CLOSING | {HumanAction.ESCALATE.value}:
            return _when(entry)
    return None


def _closed_at(d: models.Decision) -> Optional[datetime]:
    """When this left the queue, or ``None`` if it is still in it.

    A ``REOPEN`` puts it back, so the last closing action wins rather than the
    first — a decision closed, undone and left open is open.
    """
    out: Optional[datetime] = None
    for entry in _trail(d):
        action = entry.get("action")
        if action == HumanAction.REOPEN.value:
            out = None
        elif action in _CLOSING:
            out = _when(entry) or out
    return out


def semantics_cutover(decisions: Sequence[models.Decision]) -> Optional[str]:
    """The earliest moment the clients are *known* to have distinguished intents.

    Until the capture fix in this branch, ``modify`` posted ``ACT`` exactly as
    ``accept`` did and ``escalate`` posted ``OVERRIDE`` — so ``ACTIONED`` meant
    accept-or-modify and ``OVERRIDDEN`` meant escalate. Rows either side of that
    change do not mean the same thing, and a rate spanning it compares two
    definitions.

    Derived from evidence rather than configured: no client ever sent ``VIEW`` or
    ``ESCALATE`` before the fix, so the earliest one in the data is a lower bound
    on when the new client was in use. A constant would have to be set per
    environment and would be silently wrong on any that deployed on another day.

    ``None`` means *unknown*, not *no cut-over*. The caller says so.
    """
    marks = [when
             for d in decisions
             for entry in _trail(d)
             if entry.get("action") in {HumanAction.VIEW.value,
                                        HumanAction.ESCALATE.value}
             for when in [_when(entry)] if when is not None]
    return min(marks).isoformat() if marks else None


def _tally(rows: Sequence[models.Decision]) -> dict[str, Any]:
    """One group's counts, rates and band.

    Two denominators, both named, because they answer different questions and
    merging them hides the one that matters. ``ruled`` excludes decisions nobody
    touched: a queue nobody opens and a detector nobody agrees with are
    different failures, and dividing by everything raised merges them into one
    number that cannot tell them apart.

    The dismissal rate is deliberately absent — ``summarize`` owns it.
    """
    by_status: dict[str, int] = {}
    for d in rows:
        by_status[d.status] = by_status.get(d.status, 0) + 1

    accepted = by_status.get(DecisionStatus.ACTIONED.value, 0)
    modified = by_status.get(DecisionStatus.OVERRIDDEN.value, 0)
    escalated = by_status.get(DecisionStatus.ESCALATED.value, 0)
    # The dismissal *count*, not its rate. `summarize` owns the rate and the
    # band; this is here because without it `ruled` cannot be decomposed, and a
    # denominator a reader cannot check is a denominator they have to trust.
    dismissed = by_status.get(REJECTED_STATUS, 0)
    ruled = sum(by_status.get(s, 0) for s in RULED_STATUSES)
    untouched = by_status.get(DecisionStatus.OPEN.value, 0)
    viewed = by_status.get(DecisionStatus.VIEWED.value, 0)
    # EXPIRED, SUPERSEDED and RESOLVED. Detector-driven transitions, not
    # judgements — counting them as rejections would blame a person for the
    # passage of time. Reported so the parts sum to `raised`.
    detector_closed = len(rows) - (ruled + escalated + untouched + viewed)

    acceptance = _rate(accepted, ruled)
    band, note = acceptance_band(acceptance, ruled)
    return {
        "raised": len(rows),
        "accepted": accepted,
        "modified": modified,
        "dismissed": dismissed,
        "escalated": escalated,
        "untouched": untouched,
        "viewed_not_ruled": viewed,
        "closed_without_a_human": detector_closed,
        "ruled": ruled,
        "acceptance_rate": acceptance,
        "modify_rate": _rate(modified, ruled),
        "engagement_rate": _rate(accepted + modified + escalated, len(rows)),
        "band": band,
        "note": note,
        "denominators": {
            "acceptance_rate": "accepted ÷ (accepted + modified + dismissed)",
            "engagement_rate": "(accepted + modified + escalated) ÷ raised",
        },
    }


def _grouped(rows: Sequence[models.Decision], key) -> dict[str, Any]:
    groups: dict[str, list[models.Decision]] = {}
    for d in rows:
        groups.setdefault(str(key(d)), []).append(d)
    return {k: _tally(v) for k, v in sorted(groups.items())}


def _bucket(depth: int) -> str:
    for low, high in DEPTH_BUCKETS:
        if depth >= low and (high is None or depth <= high):
            return f"{low}-{high}" if high else f"{low}+"
    return "0"


def acceptance_by_volume(rows: Sequence[models.Decision]) -> dict[str, Any]:
    """Did a longer queue change what people did with it.

    Queue depth at the moment of a ruling is not stored, and ``decisions`` has no
    transition log to replay — unlike ``BusinessState``, which has one. It is
    reconstructed from the trails: how many decisions routed to the same role had
    been detected by then and had not yet closed.

    Approximate, and the report says so rather than implying precision it lacks.
    """
    ruled = [(d, at) for d in rows if (at := _ruled_at(d)) is not None]
    if not ruled:
        return {"buckets": {}, "note": "No ruling carried a usable timestamp."}
    if len(rows) > DEPTH_MAX_ROWS:
        return {"buckets": {},
                "note": (f"Refused: {len(rows)} decisions exceeds the "
                         f"{DEPTH_MAX_ROWS}-row reconstruction guard.")}

    # `clock.aware` on the stored timestamp, never a bare comparison. SQLite has
    # no timezone type, so a `DateTime(timezone=True)` column round-trips naive
    # while every timestamp on the other side of these comparisons was parsed
    # from the trail and carries an offset. Getting this wrong is a TypeError on
    # real rows and invisible against objects built in a test.
    lifetimes = [(d.assigned_role, clock.aware(d.detected_at), _closed_at(d))
                 for d in rows]
    buckets: dict[str, dict[str, int]] = {}
    for d, at in ruled:
        depth = sum(
            1 for role, detected, closed in lifetimes
            if role == d.assigned_role and detected is not None and detected <= at
            # `>=`, so the decision being ruled on counts itself. The queue
            # somebody faced included the row they were about to close.
            and (closed is None or closed >= at))
        slot = buckets.setdefault(_bucket(depth), {"ruled": 0, "accepted": 0})
        slot["ruled"] += 1
        if d.status == DecisionStatus.ACTIONED.value:
            slot["accepted"] += 1

    return {
        "buckets": {
            k: {**v, "acceptance_rate": _rate(v["accepted"], v["ruled"]),
                "band": acceptance_band(_rate(v["accepted"], v["ruled"]), v["ruled"])[0]}
            for k, v in sorted(buckets.items())
        },
        "note": ("Depth is reconstructed from the action trails, not recorded at "
                 "the time, so it is approximate. Read the shape across buckets "
                 "rather than any single figure: a rate that falls as depth rises "
                 "is the attention ceiling, and it bounds how many signals should "
                 "exist."),
    }


def _reference(row: models.QuoteDecision, code: str) -> Optional[Decimal]:
    for ref in (row.references or []):
        if isinstance(ref, dict) and ref.get("code") == code:
            try:
                return Decimal(str(ref.get("value")))
            except (InvalidOperation, ValueError, TypeError):
                return None
    return None


def override_distance(rows: Sequence[models.QuoteDecision]) -> dict[str, Any]:
    """How far a quoted price sat from the price policy computed, and how often.

    The highest-signal usability measurement available, because it is the one
    place a disagreement with the system is a *number* rather than a note — and
    the only place: a queue decision has no recommended value to differ from.

    Direction is kept rather than collapsed to a magnitude. Pricing below the
    recommendation and pricing above it are different diagnoses with different
    fixes, and a mean absolute distance reports a business discounting hard and
    one holding firm as the same finding.

    Lines with no target-margin reference are counted and excluded, never read
    as zero distance. A line the policy could not price is not a line somebody
    priced correctly.
    """
    gated = sum(1 for r in rows if r.requires_approval)
    overridden = sum(1 for r in rows if r.overridden)

    deltas: list[float] = []
    without_reference = 0
    reasons: dict[str, int] = {}
    for r in rows:
        if r.overridden and r.override_reason_code:
            reasons[r.override_reason_code] = reasons.get(r.override_reason_code, 0) + 1
        target = _reference(r, TARGET_MARGIN_PRICE)
        if target is None or target <= 0 or r.quoted_unit_price is None:
            without_reference += 1
            continue
        deltas.append(float((Decimal(str(r.quoted_unit_price)) - target) / target))
    deltas.sort()

    return {
        "lines_priced": len(rows),
        "lines_a_rule_fired_on": gated,
        "lines_overridden": overridden,
        "override_rate": _rate(overridden, gated),
        "override_reason_codes": reasons,
        "distance_from_target_margin_price": {
            "lines_measured": len(deltas),
            "lines_without_a_target_reference": without_reference,
            "median": deltas[len(deltas) // 2] if deltas else None,
            "share_below_target": _rate(sum(1 for d in deltas if d < 0), len(deltas)),
            "share_above_target": _rate(sum(1 for d in deltas if d > 0), len(deltas)),
            "note": ("A signed ratio against the policy's target-margin price. "
                     "Negative is priced under it. Lines the policy could not "
                     "price are excluded and counted, not read as agreement."),
        },
    }


def _value(rows: Sequence[models.Decision]) -> dict[str, Any]:
    """Rupees raised, ruled on and dismissed — the free half of impact.

    Available without the Outcome tracker, because a state-derived decision
    quantifies what it is worth at the moment it is raised. "₹X of flagged
    exposure was dismissed unread" is a sentence an owner can act on; it is
    simply not the same sentence as "₹X was saved".

    Exhaustive on purpose, so the parts sum to ``raised`` and a reader can
    reconcile them. Money is stored as a string to survive JSON without becoming
    a float; an unparseable figure is dropped rather than coerced.
    """
    totals = {"raised": Decimal(0), "accepted": Decimal(0), "modified": Decimal(0),
              "dismissed": Decimal(0), "escalated": Decimal(0),
              "untouched": Decimal(0), "closed_without_a_human": Decimal(0)}
    bucket_of = {
        DecisionStatus.ACTIONED.value: "accepted",
        DecisionStatus.OVERRIDDEN.value: "modified",
        DecisionStatus.DISMISSED.value: "dismissed",
        DecisionStatus.ESCALATED.value: "escalated",
        DecisionStatus.OPEN.value: "untouched",
        DecisionStatus.VIEWED.value: "untouched",
    }
    quantified = 0
    for d in rows:
        raw = (d.impact or {}).get("financial")
        if raw in (None, ""):
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        quantified += 1
        totals["raised"] += value
        totals[bucket_of.get(d.status, "closed_without_a_human")] += value
    return {"decisions_carrying_a_figure": quantified,
            **{k: str(v) for k, v in totals.items()}}


def adoption_summary(decisions: Sequence[models.Decision],
                     quote_lines: Sequence[models.QuoteDecision], *,
                     days: int) -> dict[str, Any]:
    """What the people did with the queue, over one window. Pure."""
    return {
        "window_days": days,
        "decisions_raised": len(decisions),
        "overall": _tally(decisions),
        "by_category": _grouped(decisions, lambda d: d.decision_type),
        # Only the types routed to a person carry one. Every state-derived
        # decision leaves `assigned_user_id` unset and routes to a role, so this
        # is a much smaller population than `by_category` — read the counts
        # before the rates.
        "by_user": _grouped([d for d in decisions if d.assigned_user_id],
                            lambda d: d.assigned_user_id),
        # A natural experiment nobody set up: SIGNAL decisions carry an AI
        # reading, STATE decisions are arithmetic over folded facts and never
        # touch `ai/`. A difference between them is the closest thing to
        # evidence that the interpretation layer earns its cost.
        "by_origin": _grouped(decisions, lambda d: d.origin),
        "by_ai_status": _grouped(decisions,
                                 lambda d: str((d.ai or {}).get("status") or "PENDING")),
        "value_at_risk": _value(decisions),
        "acceptance_by_queue_volume": acceptance_by_volume(decisions),
        "quote_line_overrides": override_distance(quote_lines),
        "semantics_cutover": semantics_cutover(decisions),
        "caveats": [
            "Acceptance excludes decisions nobody ruled on. A queue nobody opens "
            "and a detector nobody agrees with are different failures.",
            "The dismissal rate and its band are not here — /detector-outcomes "
            "owns that number, over every judgement including escalations.",
            "`by_ai_status` is only a trust-calibration reading where accepting "
            "was actually offered. A card whose reading did not pass the "
            "grounding gate has no accept action, so a low rate there measures "
            "the interface rather than the reader.",
            "Rows raised before `semantics_cutover` carry the old capture "
            "meanings — ACTIONED was accept-or-modify, OVERRIDDEN was escalate. "
            "A window spanning it compares two definitions. Null means unknown, "
            "not absent.",
            "Value at risk is what a situation was worth when raised, not what "
            "was realised. Realised impact needs the Outcome tracker.",
        ],
    }


def adoption_report(session: Session, decision_repo, *,
                    windows: tuple[int, ...] = (7, 30),
                    now: Optional[datetime] = None) -> dict[str, Any]:
    """The rolling adoption report, one summary per window.

    Shares ``DecisionRepository.since`` with ``report`` above, so both halves
    window the same rows the same way — on ``created_at``, for the reason that
    method gives.
    """
    ref = now or utc_now()
    out: dict[str, Any] = {"generated_at": ref.isoformat(), "windows": {}}
    for days in windows:
        cutoff = ref - timedelta(days=days)
        quote_lines = session.scalars(
            select(models.QuoteDecision).where(
                models.QuoteDecision.organization_id == decision_repo.org,
                models.QuoteDecision.created_at >= cutoff)).all()
        out["windows"][f"{days}d"] = adoption_summary(
            decision_repo.since(cutoff), quote_lines, days=days)
    return out
