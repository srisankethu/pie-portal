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
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from ..clock import now as utc_now
from ..config import settings
from ..domain import models
from ..domain.enums import DecisionStatus

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
