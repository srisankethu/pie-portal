"""Value attribution — what this platform is worth to the business, measured.

A deterministic layer, and one of the layers §1 names: it must never import
``ai/``, and ``ai/`` must never import it. Nothing here is interpreted, phrased
or estimated by a model — every rupee figure is arithmetic over rows the Quote
Desk already wrote, carrying the operands that produced it.

    calculator   pure functions. Decimal in, Decimal or None out. No session.
    detectors    existing evidence -> candidate events, plus the rows skipped
                 and why. Never a benign default.
    ledger       the single writer of ``ValueEvent``. Upserts on ``event_key``,
                 which is what makes double counting structurally impossible.
    evaluator    set-based rollups: the pre-trial baseline, live progress, and
                 the report. ATTRIBUTED alone is the headline.

The rule that shapes the whole module, and the reason it is worth having at all:
**no fabricated numbers**. There is no hourly rate here, no assumed win
probability, no modelled uplift. A missing operand produces UNKNOWN or no event —
never zero, and never a benign default that reads as good news.

The whole surface is cost and margin arithmetic, so it is management-only: an
attributed amount on a quote line *is* gross profit. Callers enforce that at the
boundary, and enforce it by omitting fields rather than by hiding them.
"""
from .calculator import (
    discount_leakage_prevented,
    equivalent_saving,
    margin_protected,
    roi,
)
from .detectors import (
    DETECTORS,
    NOT_MEASURABLE_REASONS,
    UNMEASURABLE_EVENT_TYPES,
    DetectionResult,
    Detector,
    QuoteEvidence,
    SkippedRow,
    load_evidence,
    run_all,
    skip_summary,
)
from .evaluator import (SUMMARY_DAYS, Window, capture_baseline, current_trial,
                        list_events, summary_window, thirty_day_report,
                        value_summary)
from .ledger import (
    FACT_OF,
    LedgerRefusal,
    ValueEventDraft,
    ValueFact,
    event_key,
    record,
    record_all,
    supersede_closed_opportunities,
)

__all__ = [
    "DETECTORS",
    "DetectionResult",
    "Detector",
    "LedgerRefusal",
    "NOT_MEASURABLE_REASONS",
    "QuoteEvidence",
    "SkippedRow",
    "UNMEASURABLE_EVENT_TYPES",
    "ValueEventDraft",
    "capture_baseline",
    "current_trial",
    "discount_leakage_prevented",
    "equivalent_saving",
    "FACT_OF",
    "ValueFact",
    "event_key",
    "list_events",
    "load_evidence",
    "margin_protected",
    "record",
    "record_all",
    "supersede_closed_opportunities",
    "roi",
    "run_all",
    "skip_summary",
    "thirty_day_report",
    "summary_window",
    "value_summary",
    "Window",
    "SUMMARY_DAYS",
]
