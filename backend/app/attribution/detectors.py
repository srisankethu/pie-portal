"""Turning evidence that already exists into candidate value events.

Nothing here invents a fact. Every event a detector emits is computed from rows
the Quote Desk already writes — an immutable ``QuoteDecision`` snapshot and the
``QuoteOutcome`` that later says whether the customer accepted it — and every
row a detector *cannot* turn into an event comes back named, with the reason it
was skipped. That second half is the point. A detector that returns three events
and says nothing about the four hundred lines it passed over produces a number
nobody can size; a rollup built on it reports the absence of evidence as good
news, which is the single mistake CLAUDE.md §1 records three separate instances
of.

**Loaded once, detected many.** ``load_evidence`` does two queries and hands
every detector the same in-memory structure, so adding a fourth detector adds no
database work — the same reason ``commercial/quote_service`` loads a quote's
history once and then assesses N lines in memory.

**The detectors are pure over that structure.** No ``Session`` past the loader,
so a detector is testable against a hand-built ``QuoteEvidence`` with no
database at all, and two runs over the same evidence produce the same drafts in
the same order.

Two of the five ``ValueEventType`` members have no detector, and that is a
finding rather than an omission — see ``UNMEASURABLE_EVENT_TYPES`` at the foot
of this module. Writing a detector for them would mean inventing the evidence
they need, which is exactly what this module exists not to do.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..commercial.config import CommercialThresholds
from ..commercial.policy import load_for_org
from ..commercial.quote_exceptions import (
    BELOW_MARGIN_FLOOR,
    BELOW_MIN_MARGIN,
    NEGATIVE_MARGIN,
)
from ..commercial.references import MARGIN_FLOOR_PRICE, MIN_MARGIN_PRICE
from ..domain import models
from ..domain.enums import QuoteOutcomeStatus, ValueClass, ValueEventType
from .calculator import discount_leakage_prevented, equivalent_saving, margin_protected
from .ledger import ValueEventDraft, event_key

# ── skip reasons ─────────────────────────────────────────────────────────────
#
# Stable strings. They travel into a report a person reads, so each one has to
# say what is *missing*, not that something went wrong.

#: The line carried no purchase cost, so no margin statement about it is
#: defensible. The rule this enforces is absolute: a line with ``unit_cost IS
#: NULL`` produces no event, never a zero-amount one.
NO_COST_ON_RECORD = "NO_COST_ON_RECORD"
#: The snapshot holds no quoted price — an unpriced line was recorded.
NO_PRICE_ON_RECORD = "NO_PRICE_ON_RECORD"
#: The exception fired but the snapshot carries no floor reference to measure
#: the gap against, so the amount cannot be re-derived.
NO_FLOOR_IN_POLICY = "NO_FLOOR_IN_POLICY"
#: The control was overridden on this line: the price went out despite the flag,
#: so nothing was protected. Counting it would be the feature claiming credit
#: for a guardrail somebody stepped over.
FLAG_OVERRIDDEN = "FLAG_OVERRIDDEN"
#: The quote was lost. The money did not move, so there is no realized value —
#: and no potential value either, because the opportunity is closed.
QUOTE_LOST = "QUOTE_LOST"
#: The arithmetic came out at or below zero: the price already cleared its
#: floor, or the reprice moved down. Real, and worth nothing.
NOTHING_AT_STAKE = "NOTHING_AT_STAKE"
#: The line was repriced, but nothing was flagged on the opening snapshot — so
#: there is no intervention on record and the move cannot be attributed to one.
NO_INTERVENTION_ON_RECORD = "NO_INTERVENTION_ON_RECORD"
#: Purchase cost moved between the two snapshots, so a higher final price is at
#: least partly cost pass-through rather than recovered discount. Not separable
#: from the evidence, so not claimed.
COST_MOVED_BETWEEN_SNAPSHOTS = "COST_MOVED_BETWEEN_SNAPSHOTS"
#: The quantity changed between snapshots, so the two prices are not comparable.
QUANTITY_CHANGED = "QUANTITY_CHANGED"
#: An alternative product is referenced but carries no cost, so the saving
#: cannot be computed. Assuming one would be the fabrication this module bans.
NO_COST_ON_ALTERNATIVE = "NO_COST_ON_ALTERNATIVE"

#: The three policy exceptions that place a line under a floor, mapped to the
#: reference whose value *is* that floor. Read from the exception's own
#: ``reference_code`` where the snapshot recorded one; this map is the fallback
#: for rows written before that field carried a value.
FLOOR_EXCEPTION_REFERENCES = {
    NEGATIVE_MARGIN: MIN_MARGIN_PRICE,
    BELOW_MIN_MARGIN: MIN_MARGIN_PRICE,
    BELOW_MARGIN_FLOOR: MARGIN_FLOOR_PRICE,
}

#: How a substituted equivalent must be recorded on a quote decision for
#: ``EquivalentSavingDetector`` to value it: an ``evidence_refs`` entry of this
#: record type carrying both costs. Nothing writes one today — see that
#: detector's docstring, which explains why it stays that way until something
#: does rather than being softened into a guess.
ALTERNATIVE_RECORD_TYPE = "alternative"


def _dec(value: Any) -> Optional[Decimal]:
    """A stored or serialized number as ``Decimal``, or ``None``.

    Via ``str`` deliberately. ``references`` round-trips through JSON as a
    float, and ``Decimal(0.15)`` is 0.1499999999999999944488848768742172978818
    — which would put a floor comparison one paisa out on some lines and not
    others, unreproducibly.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ── the evidence, loaded once ────────────────────────────────────────────────
@dataclass(frozen=True)
class QuoteEvidence:
    """Every priced line and quote outcome a detection run may read.

    ``lines`` is keyed on ``(quote_id, quote_line_id)`` and each value is that
    line's snapshots **in the order they were written**, oldest first. The order
    is load-bearing: ``QuoteDecision`` is append-only and re-pricing a line
    writes a new row, so the first snapshot is what was first proposed and the
    last is what actually went out. A detector that read them as an unordered
    set would be unable to tell a corrected price from an uncorrected one.
    """

    lines: "OrderedDict[tuple[str, str], list[models.QuoteDecision]]"
    outcomes: dict[str, models.QuoteOutcome]

    def outcome_for(self, quote_id: str) -> Optional[models.QuoteOutcome]:
        return self.outcomes.get(quote_id)


def load_evidence(session: Session, org: str, *,
                  since: Optional[datetime] = None,
                  until: Optional[datetime] = None) -> QuoteEvidence:
    """Two queries: the priced lines in the window, and their quotes' outcomes.

    The window filters on ``QuoteDecision.created_at`` — when the line was
    priced — because that is when the intervention happened. The *outcome* may
    well be recorded after ``until``, and is loaded regardless: a quote priced
    inside the window and won a week later is exactly the case attribution
    exists to catch, and filtering outcomes by the same window would classify it
    as POTENTIAL forever.
    """
    stmt = select(models.QuoteDecision).where(models.QuoteDecision.organization_id == org)
    if since is not None:
        stmt = stmt.where(models.QuoteDecision.created_at >= since)
    if until is not None:
        stmt = stmt.where(models.QuoteDecision.created_at <= until)
    # Ordered in SQL rather than in Python so the grouping below is stable
    # across backends; the id is a tie-break for rows written in the same
    # microsecond, which SQLite manages more often than you would expect.
    rows = list(session.scalars(
        stmt.order_by(models.QuoteDecision.created_at,
                      models.QuoteDecision.quote_decision_id)))

    lines: "OrderedDict[tuple[str, str], list[models.QuoteDecision]]" = OrderedDict()
    for row in rows:
        lines.setdefault((row.quote_id, row.quote_line_id), []).append(row)

    outcomes: dict[str, models.QuoteOutcome] = {}
    quote_ids = {row.quote_id for row in rows}
    if quote_ids:
        for outcome in session.scalars(
                select(models.QuoteOutcome).where(
                    models.QuoteOutcome.organization_id == org,
                    models.QuoteOutcome.quote_id.in_(quote_ids))):
            outcomes[outcome.quote_id] = outcome

    return QuoteEvidence(lines=lines, outcomes=outcomes)


# ── the detector contract ────────────────────────────────────────────────────
@dataclass(frozen=True)
class SkippedRow:
    """One row that could have been an event and was not, and why."""

    reason: str
    evidence_ref: dict[str, Any]
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "evidence_ref": dict(self.evidence_ref),
                "detail": self.detail}


@dataclass(frozen=True)
class DetectionResult:
    """What one detector found, what it had to skip, and how much it looked at.

    ``considered`` is the denominator. Without it "two events, no skips" is
    ambiguous between a clean run over two hundred candidates and a detector
    that found nothing to look at — and those two mean opposite things about
    whether the number below the headline can be trusted.
    """

    event_type: ValueEventType
    events: list[ValueEventDraft] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    considered: int = 0

    def skip_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.skipped:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return counts


class Detector:
    """The contract every detector satisfies, in full and not in part.

    Substitutability here is not decoration: ``run_all`` treats these
    interchangeably and a rollup adds their results together, so an override
    that quietly narrows the contract corrupts a total rather than breaking a
    call. Concretely, every implementation must

    * return a ``DetectionResult`` — never ``None``, never a bare list, and
      never raise for ordinary missing evidence (missing evidence is a skip);
    * name every candidate it rejected, using one of the reasons above, rather
      than dropping it silently;
    * count in ``considered`` every candidate it looked at, rejected included;
    * emit only drafts the ledger will accept — at least one evidence ref, a
      ``thresholds_version``, and an amount that is ``Decimal`` or ``None``.

    A detector that returned an empty result where evidence was missing would
    read, from the outside, exactly like a detector that found nothing wrong.
    """

    event_type: ClassVar[ValueEventType]

    def detect(self, evidence: QuoteEvidence,
               th: CommercialThresholds) -> DetectionResult:
        raise NotImplementedError


# ── shared reading of the evidence ───────────────────────────────────────────
def _line_ref(row: models.QuoteDecision) -> dict[str, Any]:
    """The pointer at one real snapshot row, in the shape the ledger stores."""
    return {"record_type": "quote_decision",
            "record_id": row.quote_decision_id,
            "quote_id": row.quote_id,
            "quote_line_id": row.quote_line_id}


def _outcome_ref(outcome: models.QuoteOutcome) -> dict[str, Any]:
    return {"record_type": "quote_outcome",
            "record_id": outcome.quote_outcome_id,
            "quote_id": outcome.quote_id,
            "status": outcome.status}


@dataclass(frozen=True)
class _Classification:
    """How strong a claim this quote's outcome supports, and when it happened."""

    value_class: ValueClass
    occurred_at: Optional[datetime]
    evidence_ref: Optional[dict[str, Any]]


def _classify(outcome: Optional[models.QuoteOutcome]) -> Optional[_Classification]:
    """POTENTIAL, ATTRIBUTED, or ``None`` for "do not record this at all".

    One implementation shared by every detector, because the mapping from an
    outcome to a value class is the single most consequential rule in this
    module — it decides what may enter the headline — and three copies of it
    would be three chances for one of them to widen.

    * No outcome row, DRAFT or SENT — the intervention is on record and nothing
      has happened yet. POTENTIAL.
    * WON — the money moved *and* the flag preceded the win, which is the whole
      definition of ATTRIBUTED. The event is dated to the decision, not to the
      pricing, so a rollup for the month the deal closed contains it.
    * LOST — ``None``. Not a zero-amount event and not a POTENTIAL one: the
      opportunity is closed, so recording it would leave a permanent row
      claiming something is still available.
    """
    if outcome is None:
        return _Classification(ValueClass.POTENTIAL, None, None)
    status = (outcome.status or "").upper()
    if status == QuoteOutcomeStatus.WON.value:
        return _Classification(ValueClass.ATTRIBUTED,
                               clock.aware(outcome.decided_at),
                               _outcome_ref(outcome))
    if status == QuoteOutcomeStatus.LOST.value:
        return None
    return _Classification(ValueClass.POTENTIAL, None, _outcome_ref(outcome))


def _floor_flag(row: models.QuoteDecision) -> Optional[dict[str, Any]]:
    """The first below-floor policy exception on this snapshot, if any."""
    for exc in (row.exceptions or []):
        if isinstance(exc, dict) and exc.get("code") in FLOOR_EXCEPTION_REFERENCES:
            return exc
    return None


def _reference_value(row: models.QuoteDecision, code: str) -> Optional[Decimal]:
    for ref in (row.references or []):
        if isinstance(ref, dict) and ref.get("code") == code:
            return _dec(ref.get("value"))
    return None


def _was_overridden(snapshots: Iterable[models.QuoteDecision]) -> bool:
    """Whether the control was stepped over anywhere on this line.

    Any snapshot, not just the flagged one: an override recorded on a later
    re-price is still the price going out despite the flag, and scoping the
    check to one row would let a line be flagged, re-priced with an override,
    and still counted as protected.
    """
    return any(bool(row.overridden) for row in snapshots)


# ── MARGIN_PROTECTED ─────────────────────────────────────────────────────────
class MarginProtectedDetector(Detector):
    """Margin the floor kept on a line that was flagged and not overridden.

    The evidence chain is: a ``QuoteDecision`` snapshot carrying a below-floor
    policy exception (``NEGATIVE_MARGIN``, ``BELOW_MIN_MARGIN`` or
    ``BELOW_MARGIN_FLOOR``), no override on any snapshot of that line, and the
    floor reference the exception fired against recorded on the same row.

    The amount is the shortfall between that floor and the price that was
    flagged, across the quantity — deliberately the same arithmetic
    ``quote_exceptions`` already shows the manager as the exception's
    ``impact_amount``, so the ledger and the quote screen cannot disagree about
    what one flag was worth.

    The *earliest* flagged snapshot is used rather than the latest, because that
    is the moment the intervention happened. A later snapshot on the same line
    is the response to it, and dating the event to the response would put the
    intervention after its own outcome.
    """

    event_type: ClassVar[ValueEventType] = ValueEventType.MARGIN_PROTECTED

    def detect(self, evidence: QuoteEvidence,
               th: CommercialThresholds) -> DetectionResult:
        events: list[ValueEventDraft] = []
        skipped: list[SkippedRow] = []
        considered = 0

        for (quote_id, line_id), snapshots in evidence.lines.items():
            flagged: Optional[models.QuoteDecision] = None
            exception: Optional[dict[str, Any]] = None
            for row in snapshots:
                exception = _floor_flag(row)
                if exception is not None:
                    flagged = row
                    break
            if flagged is None or exception is None:
                # Never flagged. Not a candidate, so not a skip — recording it
                # would drown the real gaps in every ordinary line.
                continue

            considered += 1
            ref = _line_ref(flagged)

            if _was_overridden(snapshots):
                skipped.append(SkippedRow(
                    FLAG_OVERRIDDEN, ref,
                    "the price went out despite the flag, so nothing was protected"))
                continue
            if flagged.unit_cost is None:
                skipped.append(SkippedRow(
                    NO_COST_ON_RECORD, ref,
                    "no purchase cost on the snapshot, so no margin claim is defensible"))
                continue
            price = _dec(flagged.quoted_unit_price)
            if price is None:
                skipped.append(SkippedRow(NO_PRICE_ON_RECORD, ref,
                                          "the snapshot records no quoted price"))
                continue

            code = (exception.get("reference_code")
                    or FLOOR_EXCEPTION_REFERENCES[exception["code"]])
            floor = _reference_value(flagged, code)
            if floor is None:
                skipped.append(SkippedRow(
                    NO_FLOOR_IN_POLICY, ref,
                    f"{exception['code']} fired but {code} is not on the snapshot"))
                continue

            qty = _dec(flagged.quantity)
            amount = margin_protected(floor, price, qty)
            if amount is None:
                skipped.append(SkippedRow(
                    NOTHING_AT_STAKE, ref,
                    "the quoted price is not below the floor it was measured against"))
                continue

            outcome = evidence.outcome_for(quote_id)
            classification = _classify(outcome)
            if classification is None:
                skipped.append(SkippedRow(QUOTE_LOST, ref,
                                          "the quote was lost; the money did not move"))
                continue

            refs = [ref]
            if classification.evidence_ref is not None:
                refs.append(classification.evidence_ref)

            events.append(ValueEventDraft(
                event_type=self.event_type,
                value_class=classification.value_class,
                event_key=event_key(flagged.organization_id, self.event_type,
                                    classification.value_class,
                                    (quote_id, line_id, flagged.quote_decision_id)),
                amount=amount,
                basis={
                    "formula": "(floor_price - quoted_unit_price) x quantity",
                    "exception_code": exception["code"],
                    "floor_reference_code": code,
                    "floor_price": str(floor),
                    "quoted_unit_price": str(price),
                    "quantity": str(qty),
                },
                evidence_refs=refs,
                occurred_at=(classification.occurred_at
                             or clock.aware(flagged.created_at) or clock.now()),
                thresholds_version=flagged.thresholds_version or th.version,
                notes={"quote_outcome_status": outcome.status if outcome else None},
            ))

        return DetectionResult(self.event_type, events, skipped, considered)


# ── DISCOUNT_LEAKAGE_PREVENTED ───────────────────────────────────────────────
class DiscountLeakagePreventedDetector(Detector):
    """Price a line recovered between what was first proposed and what went out.

    Only a line with more than one snapshot is a candidate, because a line
    priced once had nothing to recover. The opening snapshot must carry at least
    one exception: without an intervention on record, a price that moved up is
    just a price that moved up, and claiming it would be the platform taking
    credit for a salesperson's own second thoughts.

    Purchase cost is required on both snapshots even though it is not an operand
    of the amount, and the reason is the one absence-of-evidence lesson this
    module is built around. If cost rose between the two snapshots, the higher
    final price is at least partly cost pass-through — real revenue, but not
    recovered discount, and nothing in the evidence separates the two. Without
    cost the check cannot even be made, so the honest answer is a named skip
    rather than an amount that reads as recovered margin.
    """

    event_type: ClassVar[ValueEventType] = ValueEventType.DISCOUNT_LEAKAGE_PREVENTED

    def detect(self, evidence: QuoteEvidence,
               th: CommercialThresholds) -> DetectionResult:
        events: list[ValueEventDraft] = []
        skipped: list[SkippedRow] = []
        considered = 0

        for (quote_id, line_id), snapshots in evidence.lines.items():
            if len(snapshots) < 2:
                continue  # never re-priced; nothing to compare against

            considered += 1
            opening, final = snapshots[0], snapshots[-1]
            ref = _line_ref(final)

            if _was_overridden(snapshots):
                skipped.append(SkippedRow(
                    FLAG_OVERRIDDEN, ref,
                    "a control was overridden on this line"))
                continue
            if not (opening.exceptions or []):
                skipped.append(SkippedRow(
                    NO_INTERVENTION_ON_RECORD, ref,
                    "nothing was flagged on the opening price, so the move "
                    "cannot be attributed to an intervention"))
                continue
            if opening.unit_cost is None or final.unit_cost is None:
                skipped.append(SkippedRow(
                    NO_COST_ON_RECORD, ref,
                    "cost is missing on one of the snapshots, so recovered "
                    "discount cannot be told apart from cost pass-through"))
                continue

            opening_price = _dec(opening.quoted_unit_price)
            final_price = _dec(final.quoted_unit_price)
            if opening_price is None or final_price is None:
                skipped.append(SkippedRow(NO_PRICE_ON_RECORD, ref,
                                          "one of the snapshots records no price"))
                continue

            opening_qty, final_qty = _dec(opening.quantity), _dec(final.quantity)
            if opening_qty != final_qty:
                skipped.append(SkippedRow(
                    QUANTITY_CHANGED, ref,
                    "the quantity changed between snapshots, so the two unit "
                    "prices are not comparable"))
                continue

            if _dec(final.unit_cost) > _dec(opening.unit_cost):
                skipped.append(SkippedRow(
                    COST_MOVED_BETWEEN_SNAPSHOTS, ref,
                    "purchase cost rose between the two prices, so the increase "
                    "is at least partly pass-through"))
                continue

            amount = discount_leakage_prevented(opening_price, final_price, final_qty)
            if amount is None:
                skipped.append(SkippedRow(
                    NOTHING_AT_STAKE, ref,
                    "the final price is not above the opening price"))
                continue

            outcome = evidence.outcome_for(quote_id)
            classification = _classify(outcome)
            if classification is None:
                skipped.append(SkippedRow(QUOTE_LOST, ref,
                                          "the quote was lost; the money did not move"))
                continue

            refs = [_line_ref(opening), ref]
            if classification.evidence_ref is not None:
                refs.append(classification.evidence_ref)

            events.append(ValueEventDraft(
                event_type=self.event_type,
                value_class=classification.value_class,
                event_key=event_key(final.organization_id, self.event_type,
                                    classification.value_class,
                                    (quote_id, line_id, opening.quote_decision_id,
                                     final.quote_decision_id)),
                amount=amount,
                basis={
                    "formula": "(final_price - opening_price) x quantity",
                    "opening_price": str(opening_price),
                    "final_price": str(final_price),
                    "quantity": str(final_qty),
                    "opening_exception_codes": sorted(
                        str(e.get("code")) for e in (opening.exceptions or [])
                        if isinstance(e, dict) and e.get("code")),
                },
                evidence_refs=refs,
                occurred_at=(classification.occurred_at
                             or clock.aware(final.created_at) or clock.now()),
                thresholds_version=final.thresholds_version or th.version,
                notes={"quote_outcome_status": outcome.status if outcome else None,
                       "snapshot_count": len(snapshots)},
            ))

        return DetectionResult(self.event_type, events, skipped, considered)


# ── EQUIVALENT_SAVING ────────────────────────────────────────────────────────
class EquivalentSavingDetector(Detector):
    """What a substituted equivalent saved against the item originally specified.

    A substitution is only valuable if there is an alternative *on record with a
    real cost*. This detector therefore looks for an ``evidence_refs`` entry of
    record type ``alternative`` carrying ``original_unit_cost`` and
    ``alternative_unit_cost``, and values nothing else.

    **Nothing in this codebase writes that entry today**, so this detector
    currently returns ``considered=0`` on every organization, and the report says
    equivalent saving is not measurable rather than showing ₹0. That is the
    intended behaviour and it is stated here so the next reader does not treat
    the empty result as a bug and "fix" it by inferring an alternative from a
    ``pie_service`` equivalence suggestion. They must not: a suggestion is a
    scored candidate under this organization's bands, not a purchase that
    happened, and a saving computed against a product nobody bought is a
    fabricated number with a citation attached. When the Quote Desk records an
    accepted substitution, it records it in this shape and this detector starts
    finding them — with no change to the arithmetic.
    """

    event_type: ClassVar[ValueEventType] = ValueEventType.EQUIVALENT_SAVING

    def detect(self, evidence: QuoteEvidence,
               th: CommercialThresholds) -> DetectionResult:
        events: list[ValueEventDraft] = []
        skipped: list[SkippedRow] = []
        considered = 0

        for (quote_id, line_id), snapshots in evidence.lines.items():
            final = snapshots[-1]
            alternative = self._alternative(final)
            if alternative is None:
                continue  # no substitution on this line; nothing to measure

            considered += 1
            ref = _line_ref(final)

            original_cost = _dec(alternative.get("original_unit_cost"))
            alternative_cost = _dec(alternative.get("alternative_unit_cost"))
            if original_cost is None or alternative_cost is None:
                skipped.append(SkippedRow(
                    NO_COST_ON_ALTERNATIVE, ref,
                    "the alternative is named but one of the two costs is missing"))
                continue

            qty = _dec(final.quantity)
            amount = equivalent_saving(original_cost, alternative_cost, qty)
            if amount is None:
                skipped.append(SkippedRow(
                    NOTHING_AT_STAKE, ref,
                    "the alternative is not cheaper than the item specified"))
                continue

            outcome = evidence.outcome_for(quote_id)
            classification = _classify(outcome)
            if classification is None:
                skipped.append(SkippedRow(QUOTE_LOST, ref,
                                          "the quote was lost; the money did not move"))
                continue

            refs = [ref, dict(alternative)]
            if classification.evidence_ref is not None:
                refs.append(classification.evidence_ref)

            events.append(ValueEventDraft(
                event_type=self.event_type,
                value_class=classification.value_class,
                event_key=event_key(final.organization_id, self.event_type,
                                    classification.value_class,
                                    (quote_id, line_id, final.quote_decision_id)),
                amount=amount,
                basis={
                    "formula": "(original_unit_cost - alternative_unit_cost) x quantity",
                    "original_unit_cost": str(original_cost),
                    "alternative_unit_cost": str(alternative_cost),
                    "quantity": str(qty),
                },
                evidence_refs=refs,
                occurred_at=(classification.occurred_at
                             or clock.aware(final.created_at) or clock.now()),
                thresholds_version=final.thresholds_version or th.version,
                notes={"quote_outcome_status": outcome.status if outcome else None},
            ))

        return DetectionResult(self.event_type, events, skipped, considered)

    @staticmethod
    def _alternative(row: models.QuoteDecision) -> Optional[dict[str, Any]]:
        for ref in (row.evidence_refs or []):
            if isinstance(ref, dict) and ref.get("record_type") == ALTERNATIVE_RECORD_TYPE:
                return ref
        return None


# ── the registry ─────────────────────────────────────────────────────────────
#
# A registry rather than a chain of ``if event_type == …``. A fourth detector is
# one entry here and no edit to ``run_all``, which is the open/closed shape
# CLAUDE.md §5 asks for on exactly this kind of list — one that grows every time
# a new kind of value becomes measurable.
DETECTORS: tuple[Detector, ...] = (
    MarginProtectedDetector(),
    DiscountLeakagePreventedDetector(),
    EquivalentSavingDetector(),
)

#: The event types with no detector, and the evidence each one would need. Not
#: empty detectors returning nothing: an empty detector reports "measured, found
#: none", and these are "not measurable from what this schema holds", which is a
#: different sentence and the one the report has to print. ``ValueEventType``'s
#: own docstring states the rule — name the rows first, and if there are none,
#: the value is not measurable yet rather than zero.
UNMEASURABLE_EVENT_TYPES: dict[ValueEventType, str] = {
    ValueEventType.LOST_SALE_RECOVERED: (
        "no evidence links a lost quote to a later recovered order. "
        "QuoteOutcome.LOST is terminal by design, and nothing records a "
        "re-quote as the successor of a specific loss."),
    ValueEventType.PROCUREMENT_OPPORTUNITY: (
        "no evidence links a purchasing recommendation to a purchase order "
        "actually placed against it. Bill lines record what was bought, not "
        "which recommendation preceded it."),
}


def run_all(session: Session, org: str, *,
            since: Optional[datetime] = None,
            until: Optional[datetime] = None,
            thresholds: Optional[CommercialThresholds] = None,
            ) -> dict[ValueEventType, DetectionResult]:
    """Every detector over one load of the evidence. Writes nothing.

    Detection and recording are kept apart on purpose: a caller can run this to
    *see* what would be recorded — which is what makes the skip reasons useful
    in a diagnostic — and pass the drafts to ``ledger.record_all`` when it means
    it. It also keeps the cycle out of the imports: detectors know the ledger's
    draft shape, and the ledger knows nothing about detectors.
    """
    evidence = load_evidence(session, org, since=since, until=until)
    th = thresholds or load_for_org(session, org)
    return {d.event_type: d.detect(evidence, th) for d in DETECTORS}


def skip_summary(results: dict[ValueEventType, DetectionResult]) -> list[dict[str, Any]]:
    """What a detection run could not measure, as a report reads it."""
    out: list[dict[str, Any]] = []
    for event_type, result in results.items():
        counts = result.skip_counts()
        if not counts and not result.considered:
            continue
        out.append({"event_type": event_type.value,
                    "considered": result.considered,
                    "recorded": len(result.events),
                    "skipped": counts})
    return out
