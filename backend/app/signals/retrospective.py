"""What a book's own synced history already contains — the first-run look-back.

The proof instrument for somebody who has just connected their books and has
never used this platform. It runs the four proactive detectors over the history
that was pulled and reports three things: **how far back the record actually
goes**, **what the detectors found in it**, and — the part that makes the other
two worth anything — **how much of the book they were able to judge at all**.

Nothing here computes a new number. The detectors already produce every finding,
``Coverage`` already carries every denominator, and this assembles them into one
answer. It is a rollup, deliberately: a second opinion about what a decline is
would drift from the queue that raises them.

**Coverage comes before findings, and that ordering is the design.** A
retrospective that leads with "3 issues found" over a book where 94% of products
had no cost on record has told the reader something false while stating only
true numbers. So ``judged_share`` travels with every detector's result and the
verdict is computed from what was examined rather than from what was found.

**There is deliberately no coverage threshold.** The obvious design bands the
judged share — "below 30% is unreliable" — and that 30 would be a guess. The
codebase has one of those already and its own config comment admits it
(``wallet_min_quote_coverage``). So the three verdicts here turn on structural
zeroes only, which need no tuning and cannot be wrong:

    UNEXAMINED  nothing was judged. Every finding count is meaningless and the
                screen must say so instead of showing them.
    PARTIAL     something was judged and something was not. The findings are
                real; the silence is not evidence.
    EXAMINED    every subject was judged. Only here does "nothing found" mean
                nothing is wrong.

PARTIAL will be the common answer on a real book and that is correct rather than
unhelpful — some product always lacks a cost. The share and the reasons carry
the detail; the band only stops the silence being read as good news.

RESTRICTED, manager or owner. A count of MARGIN_DETERIORATION findings is a
count of products whose margin fell, which answers a margin question even with
no rupee figure attached — the ``filterCounts.MFLOOR`` lesson in §1. There is no
salesperson-safe projection of this screen and the router refuses them outright,
as ``/weather`` and the value ledger already do.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from .aggregates import load_snapshot
from .base import WITHHOLDING_REASONS, Coverage, Snapshot
from .config import SignalThresholds
from .engine import thresholds_for_org, examine_all

#: Nothing was judged, so no finding count on the screen means anything.
UNEXAMINED = "UNEXAMINED"
#: Some of the book was judged and some was not.
PARTIAL = "PARTIAL"
#: Every subject was judged. Only here is silence evidence.
EXAMINED = "EXAMINED"

#: What each verdict means, in the sentence a screen shows under the heading.
VERDICT_DETAIL: dict[str, str] = {
    UNEXAMINED: ("Nothing in this history could be judged, so the absence of "
                 "findings below says nothing about the business. The reasons "
                 "against each check are what to fix first."),
    PARTIAL: ("Part of this history could be judged and part could not. What was "
              "found is real; what was not found is not the same as nothing "
              "being there."),
    EXAMINED: ("Every subject in this history was judged, so a check with no "
               "findings genuinely found nothing to raise."),
}


def _history(snapshot: Snapshot) -> dict[str, Any]:
    """How far back the record actually goes, from the rows rather than a promise.

    The landing page says 18 months and ``DEFAULT_HISTORY_MONTHS`` says 18, but
    what a given book *has* is whatever Zoho held and the sync managed to pull.
    (The page used to hedge it as "about 18 months"; the hedge is gone and the
    figure is not, which changes nothing here — a configured intent is no more
    an observation for being stated precisely.) Reporting the configured intent instead of the observed span is how
    a screen ends up claiming a year and a half of evidence over four months of
    invoices.
    """
    dates: list[date] = [s.date for s in snapshot.sales] + [c.date for c in snapshot.costs]
    if not dates:
        return {"first_document": None, "last_document": None, "months": None,
                "sales_lines": 0, "cost_lines": 0,
                "detail": "no invoice or bill lines are synced for this "
                          "organization yet"}
    first, last = min(dates), max(dates)
    return {
        "first_document": first.isoformat(),
        "last_document": last.isoformat(),
        # Months to one place. The span is a fact about the rows; rounding it
        # further would let "0.9 months" print as "1 month of history".
        "months": round((last - first).days / 30.44, 1),
        "sales_lines": len(snapshot.sales),
        "cost_lines": len(snapshot.costs),
        "detail": None,
    }


def _detector_view(coverage: Coverage) -> dict[str, Any]:
    """One detector's result with its denominator attached, never without."""
    judged = len(coverage.drafts) + coverage.clear
    return {
        "detector": coverage.detector,
        "considered": coverage.considered,
        "found": len(coverage.drafts),
        "clear": coverage.clear,
        "judged": judged,
        # ``None`` rather than 0.0 where nothing was considered: no share exists,
        # and a 0% that means "no subjects" reads as "judged none of many".
        "judged_share": (round(judged / coverage.considered, 3)
                         if coverage.considered else None),
        "withheld": [
            {"reason": reason, "count": count,
             "detail": WITHHOLDING_REASONS.get(reason, reason)}
            for reason, count in coverage.withheld_counts().items()
        ],
    }


def look_back(session: Session, organization_id: str, *,
              as_of: Optional[date] = None,
              thresholds: Optional[SignalThresholds] = None) -> dict[str, Any]:
    """The first-run retrospective over one organization's synced history.

    Reads the book and runs the detectors; writes nothing. Deliberately not
    served from the persisted ``Signal`` rows: those accumulate across runs and
    carry no denominator, so a count over them could say what was found and
    never what was looked at — which is the whole question this answers.

    The cost is one snapshot load and one detector pass, the same work every
    sync already does. That is acceptable for a screen read once or twice after
    connecting and would not be for a dashboard polled every minute.
    """
    th = thresholds if thresholds is not None else thresholds_for_org(
        session, organization_id)
    snapshot = load_snapshot(session, organization_id)
    reference = as_of or snapshot.as_of()
    coverages = examine_all(snapshot, th, reference)

    detectors = [_detector_view(c) for c in coverages.values()]
    considered = sum(d["considered"] for d in detectors)
    judged = sum(d["judged"] for d in detectors)
    found = sum(d["found"] for d in detectors)

    if judged == 0:
        verdict = UNEXAMINED
    elif judged < considered:
        verdict = PARTIAL
    else:
        verdict = EXAMINED

    return {
        "as_of": reference.isoformat() if reference else None,
        "history": _history(snapshot),
        # Ahead of the findings in the payload as well as on the screen. The
        # order is not cosmetic: a reader who takes the count first has already
        # formed the wrong impression by the time they reach the coverage.
        "verdict": verdict,
        "verdict_detail": VERDICT_DETAIL[verdict],
        "considered": considered,
        "judged": judged,
        "judged_share": round(judged / considered, 3) if considered else None,
        "found": found,
        "detectors": detectors,
        "thresholds_version": th.version,
    }
