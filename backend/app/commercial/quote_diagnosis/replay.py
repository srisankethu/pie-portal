"""Recompute an old diagnosis and prove it comes back the same. And measure it.

Two halves that must not be confused, which is why they share a module: one
checks that the engine is reproducible, the other checks whether it was *right*.
Neither may change a stored diagnosis, and only the first can fail.

``rediagnose`` re-runs a stored diagnosis at its own recorded moment and compares
the evidence hash. A mismatch raises. That is the point — a replay that quietly
returned a different answer would be worse than no replay, because the number it
returned would look like the original.

**Why this is reproducible at all.** The evidence is filtered on when the source
recorded each row, not on when the event happened. So a re-sync that brings in
invoices dated before the quote but keyed in afterwards adds rows the filter
excludes, and the cited set does not move. A diagnosis built on event dates would
drift every time the ledger caught up, and the hash would be noise.

``evaluate`` reads diagnoses against outcomes and reports how each rule did. It
computes on read and persists nothing, the same arrangement
``outcome_tracker.evaluate`` uses, and for the same reason: a stored evaluation
becomes a second thing to keep true, and a late-arriving outcome should correct
the figure rather than contradict a saved one.

**This is not a feedback loop into history.** Nothing here writes to
``quote_diagnoses``, and nothing here is an input to a future diagnosis. It is
how a human decides to move a threshold.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from ..config import CommercialThresholds
from . import service
from .rules import (BELOW_HISTORICAL_RANGE, INSUFFICIENT_EVIDENCE,
                    WITHIN_HISTORICAL_RANGE)


class EvidenceDrift(AssertionError):
    """A replay saw a different evidence set than the diagnosis was built on.

    An ``AssertionError`` on purpose: this is a broken invariant, not a condition
    a caller should branch on. Somebody has changed what the engine can see, and
    every stored diagnosis from before that change is now unexplainable.
    """

    def __init__(self, line_id: str, stored: str, recomputed: str,
                 added: list[str], removed: list[str]) -> None:
        super().__init__(
            f"evidence drift on line {line_id}: stored {stored}, recomputed "
            f"{recomputed}. {len(added)} row(s) appeared, {len(removed)} "
            f"disappeared. First added: {added[:3]}; first removed: {removed[:3]}")
        self.line_id = line_id
        self.stored = stored
        self.recomputed = recomputed
        self.added = added
        self.removed = removed


@dataclass(frozen=True)
class Replay:
    """A recomputation that matched. There is no shape for one that did not."""

    stored: models.QuoteDiagnosis
    result: service.DiagnosisResult
    #: True when the recomputed verdict is identical, not merely the evidence.
    #: Evidence can match while a rule change moves the answer — which is a
    #: legitimate thing to have happened, and is exactly what ``engine_version``
    #: is for, so it is reported rather than raised.
    verdict_matches: bool
    stored_engine_version: str
    current_engine_version: str


def rediagnose(session: Session, org: str, *, quote_line_id: str,
               th: CommercialThresholds,
               as_of: Optional[date] = None) -> Replay:
    """Recompute a stored diagnosis at its own moment and check the hash.

    ``as_of`` is accepted for the specification's signature and must match what
    was stored; passing a different date is asking a different question, and the
    answer to that question is not a reproduction of anything. It raises rather
    than silently re-diagnosing, because "rediagnose this line as of today" is a
    reasonable thing to want and a catastrophic thing to get by accident.
    """
    stored = service.latest(session, org, quote_line_id=quote_line_id)
    if stored is None:
        raise LookupError(f"no diagnosis on record for line {quote_line_id}")
    if as_of is not None and as_of != stored.as_of:
        raise ValueError(
            f"line {quote_line_id} was diagnosed as of {stored.as_of}, not "
            f"{as_of}. Replaying at another date is a new diagnosis, not a "
            f"reproduction — call diagnose_line for that.")

    result = service.diagnose_line(
        session, org, quote_id=stored.quote_id, line_id=stored.quote_line_id,
        customer_id=stored.customer_id, product_id=stored.product_id or "",
        qty=Decimal(stored.quantity),
        quoted_unit_price=(Decimal(stored.quoted_unit_price)
                           if stored.quoted_unit_price is not None else None),
        as_of=stored.as_of, knowable_by=stored.knowable_by, th=th,
        segment=service.segment_roster(session, org, stored.customer_id),
        backfill_before=service.backfill_cutover(session, org))

    if result.evidence_hash != stored.evidence_hash:
        was = set(stored.evidence_ids or [])
        now = set(result.evidence_ids)
        raise EvidenceDrift(quote_line_id, stored.evidence_hash,
                            result.evidence_hash,
                            sorted(now - was), sorted(was - now))

    return Replay(
        stored=stored, result=result,
        verdict_matches=(list(result.owner.codes) == list(stored.codes)
                         and result.owner.strength == stored.strength),
        stored_engine_version=stored.engine_version,
        current_engine_version=result.owner.engine_version)


# ── diagnosis versus outcome ─────────────────────────────────────────────────

@dataclass
class CodeScore:
    """How one diagnosis code did, against what actually happened."""

    code: str
    diagnosed: int = 0
    surfaced: int = 0
    dismissed: int = 0
    won: int = 0
    lost: int = 0
    #: Neither — the quote is open, expired, or nobody recorded an answer. The
    #: largest bucket on these books by a distance, and it is reported rather
    #: than dropped: a win rate computed over the decided minority, presented as
    #: if it covered the book, is the kind of number that ends an argument
    #: wrongly.
    unrecorded: int = 0
    dismissal_reasons: Counter = field(default_factory=Counter)

    @property
    def decided(self) -> int:
        return self.won + self.lost

    def to_dict(self) -> dict:
        return {"code": self.code, "diagnosed": self.diagnosed,
                "surfaced": self.surfaced, "dismissed": self.dismissed,
                "won": self.won, "lost": self.lost,
                "unrecorded": self.unrecorded, "decided": self.decided,
                "dismissal_reasons": dict(sorted(
                    self.dismissal_reasons.items()))}


@dataclass
class Evaluation:
    """The tuning instrument. Computed on read, written nowhere.

    Read it as a measurement of the *rules*, never of the salespeople. A code
    that fires on lines that go on to be won is not thereby vindicated — the
    customer may simply not have minded — and one that fires on lines that are
    lost has not necessarily caused anything. What it is genuinely good for is
    the dismissal column: a rule that people keep dismissing for the same
    recorded reason is a rule with something wrong in it.
    """

    organization_id: str
    since: Optional[date]
    lines: int = 0
    by_code: dict[str, CodeScore] = field(default_factory=dict)
    #: Diagnoses whose quote outcome is not on record at all. Named separately
    #: from ``unrecorded`` inside a code so a reader can tell "we have no
    #: outcomes" from "this code lands on undecided quotes".
    without_outcome: int = 0

    def to_dict(self) -> dict:
        return {"organization_id": self.organization_id,
                "since": self.since.isoformat() if self.since else None,
                "lines": self.lines,
                "without_outcome": self.without_outcome,
                "by_code": [s.to_dict() for _, s in sorted(self.by_code.items())]}


def evaluate(session: Session, org: str, *,
             since: Optional[date] = None) -> Evaluation:
    """Score every diagnosis against the outcome of the quote it was on.

    One row per line — the diagnosis in force, which is the most recent written
    for it. Superseded rows are history and counting them would weight a line
    that was re-diagnosed three times three times as heavily.
    """
    query = select(models.QuoteDiagnosis).where(
        models.QuoteDiagnosis.organization_id == org)
    if since is not None:
        query = query.where(models.QuoteDiagnosis.as_of >= since)
    rows = session.scalars(
        query.order_by(models.QuoteDiagnosis.created_at.desc(),
                       models.QuoteDiagnosis.quote_diagnosis_id.desc())).all()

    latest_per_line: dict[str, models.QuoteDiagnosis] = {}
    for row in rows:
        latest_per_line.setdefault(row.quote_line_id, row)

    outcomes = _outcomes(session, org)
    dismissals = _dismissals(session, org)

    out = Evaluation(organization_id=org, since=since,
                     lines=len(latest_per_line))
    for row in latest_per_line.values():
        status = outcomes.get(row.quote_id)
        if status is None:
            out.without_outcome += 1
        reasons = dismissals.get(row.quote_diagnosis_id, [])
        for code in (row.codes or []):
            score = out.by_code.setdefault(code, CodeScore(code=code))
            score.diagnosed += 1
            if row.surfaces:
                score.surfaced += 1
            if reasons:
                score.dismissed += 1
                score.dismissal_reasons.update(reasons)
            if status == "WON":
                score.won += 1
            elif status == "LOST":
                score.lost += 1
            else:
                score.unrecorded += 1
    return out


def _outcomes(session: Session, org: str) -> dict[str, str]:
    """Quote id → WON / LOST, for the quotes where somebody said.

    Anything else is absent from the map rather than defaulted, so the caller
    counts it as unrecorded. A quote that expired is not a loss — it spans
    "nobody chased it", "the customer never answered" and a genuine loss, and
    only one of those is a loss. ``ingestion.normalize.classify_outcome`` makes
    the same refusal on the ERP side.
    """
    rows = session.execute(
        select(models.QuoteOutcome.quote_id, models.QuoteOutcome.status)
        .where(models.QuoteOutcome.organization_id == org,
               models.QuoteOutcome.quote_id.is_not(None))).all()
    return {qid: status for qid, status in rows if status in ("WON", "LOST")}


def _dismissals(session: Session, org: str) -> dict[str, list[str]]:
    rows = session.execute(
        select(models.QuoteDiagnosisDismissal.quote_diagnosis_id,
               models.QuoteDiagnosisDismissal.reason_code)
        .where(models.QuoteDiagnosisDismissal.organization_id == org)).all()
    out: dict[str, list[str]] = {}
    for diagnosis_id, reason in rows:
        out.setdefault(diagnosis_id, []).append(reason)
    return out


#: The codes worth watching when tuning. Not a filter the evaluation applies —
#: it scores everything — but the shortlist a reader starts from: the one that
#: should be almost everything, the one that costs money when wrong, and the one
#: whose frequency says whether the evidence is there at all.
HEADLINE_CODES = (WITHIN_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE,
                  INSUFFICIENT_EVIDENCE)
