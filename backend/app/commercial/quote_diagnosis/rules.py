"""Which diagnosis fires, how strong the evidence is, and who gets to see it.

Deterministic rules over numbers computed elsewhere. No model call, no wall
clock, no database: the same inputs must produce the same bytes, because a
diagnosis that appears on Tuesday and not on Wednesday is not a control, it is
noise, and a salesperson learns within a week to ignore it.

**Two output types, not one output filtered twice.** ``OwnerDiagnosis`` holds
everything, cost included. ``OperationsDiagnosis`` is built from it and declares
no cost, margin, opportunity-value or peer field at all — so a leak is
structurally impossible rather than merely avoided.

That distinction is the whole of I3, and this repository is why it is taken
literally. ``filterCounts.MFLOOR`` was a correct role guard with one un-guarded
line below it; the rule-code leak was a correct redaction of a rule's reasoning
that left a predicate a caller could walk. Both were filters that were right
except where they were not. A type with no field cannot be wrong in that way.

**Default state is silent.** Everything is computed and stored; almost none of it
renders. Alert fatigue kills this product faster than a wrong diagnosis does.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Sequence

from ..config import CommercialThresholds
from .baselines import CostBaseline, PriceBaseline
from .comparables import (CustomerAxis, PeerBand, SPECIFIC_TIERS,
                          TIER_SAME_CUSTOMER_SKU_ADJACENT,
                          TIER_SAME_CUSTOMER_SKU_BAND)
from .evidence import EvidenceSet

if TYPE_CHECKING:  # ``drivers``, ``working_capital`` and ``intent`` all import
    # this module's code vocabulary, so the dependency can only run one way at
    # module scope. The annotations are strings under ``from __future__ import
    # annotations`` and are never evaluated; ``diagnose`` imports the functions
    # themselves at call time, the same arrangement ``service`` uses for
    # ``render``.
    from ..insight.payments import Settlement
    from ..insight.terms import Term
    from ..source_concepts import Taxonomy
    from .drivers import Attribution
    from .intent import PricingIntent, Reading, SourceRecord
    from .working_capital import WorkingCapital

_ZERO = Decimal("0")

#: This module's own version, stamped on every diagnosis beside the thresholds
#: hash. The two answer different questions — "which policy judged this" and
#: "which code produced it" — and a rule change that did not move a version
#: would make an old diagnosis unexplainable. Bump it when a rule changes.
#:
#: **Deliberately not bumped for driver attribution.** The stamp lives on a
#: stored row and answers "which code produced the columns in *this row*".
#: ``service.record`` writes no attribution column; ``service.evidence_hash``
#: covers the cited evidence ids and nothing else; ``replay.rediagnose``
#: compares that hash and then ``codes`` and ``strength``. Attribution touches
#: none of the three — it is computed from the baselines after they are settled
#: and changes no code, no grade and no cited id — so every row written after
#: this change is byte-identical to the row that would have been written before
#: it, and every row written before it still replays.
#:
#: Moving the stamp would mark every pre-existing row as the product of
#: different code when the rows are identical, and a difference that is always
#: there is a difference nobody reads. The bump belongs to whichever change
#: first *persists* an attribution, because that is the change after which two
#: rows carrying one stamp have different shapes.
#:
#: **And deliberately not bumped for the working-capital reading either**, by
#: the same three facts checked again rather than by analogy. ``service.record``
#: writes no column for it; ``service.evidence_hash`` covers the cited price and
#: cost ids and nothing else; ``replay.rediagnose`` compares that hash, then
#: ``codes`` and ``strength``. The reading adds no code, sits outside the
#: strength ladder this row publishes — it grades itself, and the grade goes
#: nowhere near ``owner.strength`` — and cites only the purchase ids the cost
#: baseline already cited. Every column written after this change is byte-
#: identical to the column written before it.
#:
#: That is worth stating rather than assuming, because this reading takes rows
#: the diagnosis never touched before: settled invoices and a supplier's payment
#: term, both of which move under it. A replay recomputes the reading from
#: today's rows and can legitimately get a different figure from the one an
#: owner saw — which is exactly why it is not stored and not compared. The bump
#: belongs to whichever change first persists one.
#:
#: **And deliberately not bumped for the recorded-reason reading**, by the same
#: three facts checked a third time — and this one needed checking rather than
#: assuming, because the reading's codes look at first like ``context``, and
#: ``context`` *is* a persisted column. The three facts:
#:
#: * ``service.record`` writes ``codes`` and ``context`` and no column for this
#:   reading. It rides on ``OwnerDiagnosis.intent`` and
#:   ``OperationsDiagnosis.intent``, which are not written, so nothing about a
#:   stored row's shape changes and ``render.INTENT_NOT_STORED`` is what a row
#:   read back says instead of answering with silence.
#: * ``service.evidence_hash`` covers the cited price and cost ids. A declaration
#:   is not an evidence row and cannot move it.
#: * ``replay.rediagnose`` raises on that hash, then reports ``verdict_matches``
#:   over ``codes`` and ``strength``. It does **not** compare ``context`` — no
#:   reader of a stored diagnosis does; ``replay.evaluate`` scores ``codes``
#:   alone — so context would have been the quiet place to put this, which is
#:   exactly why it is not there either.
#:
#: The reason it is not in ``codes`` is sharper than convenience, and it is the
#: one fact about A4.2 that changes this calculation. **A taxonomy can move
#: retroactively under a stored diagnosis.** ``source_concepts.declare`` makes a
#: *first* declaration effective from the beginning of time, on purpose, so that
#: reading the existing book works at all — and on a live book nothing is
#: declared yet, so the first declaration an organization ever makes is
#: guaranteed to land after quotes have already been diagnosed. A correction may
#: also be back-dated past a stored quote, which ``declare`` permits and argues
#: for. Either way the concept read at replay is a legitimately different concept
#: from the one an owner saw, with nothing wrong with the engine and no version
#: to name the difference. In ``codes`` that would come back as
#: ``verdict_matches: False`` on every row written before an organization
#: finished its configuration — a failure report for somebody filling in a
#: settings screen, which is a check people learn to ignore.
#:
#: So the reading is computed on every diagnosis, published to both roles, and
#: stored nowhere. Every column written after this change is byte-identical to
#: the column written before it. The bump belongs, as before, to whichever
#: change first persists one.
ENGINE_VERSION = "qd-1"


# ── evidence strength (§11) ──────────────────────────────────────────────────
#
# Module constants rather than an enum in ``domain/``, following
# ``quote_exceptions``' severities: they are persisted inside a JSON payload, not
# as a typed column, and an enum with no model behind it is a type nobody checks.

STRONG = "STRONG"
MODERATE = "MODERATE"
WEAK = "WEAK"
INSUFFICIENT = "INSUFFICIENT"

_RANK = {INSUFFICIENT: 0, WEAK: 1, MODERATE: 2, STRONG: 3}


# ── diagnosis codes ──────────────────────────────────────────────────────────
#
# Stable strings; they are persisted and read by the renderer.

#: The quote sits inside the range comparable evidence supports. The common
#: case, and the one that must never render a card.
WITHIN_HISTORICAL_RANGE = "WITHIN_HISTORICAL_RANGE"

#: Below the supported range, with cost at or near its baseline. A pricing
#: opportunity — the money is on this line.
BELOW_HISTORICAL_RANGE = "BELOW_HISTORICAL_RANGE"

#: Above the supported range. Reported, not celebrated: it is as much a
#: risk of losing the order as it is a win, and the engine does not know which.
ABOVE_HISTORICAL_RANGE = "ABOVE_HISTORICAL_RANGE"

#: The quote sits inside *this customer's* own range, and that whole range sits
#: materially below what comparable customers pay. An account-level pricing
#: issue, not a this-quote issue. Routes to the owner and never to the desk —
#: there is nothing a salesperson can do about it on this line, and telling them
#: the account is under-priced is how a customer finds out.
BELOW_PEER_BAND_STRUCTURAL = "BELOW_PEER_BAND_STRUCTURAL"

#: Price is consistent with history; acquisition cost is materially above its
#: baseline. **Not pricing leakage**, and it must never be worded as though it
#: were. The operations view renders it with no cost figure at all.
COST_DRIVEN_MARGIN_RISK = "COST_DRIVEN_MARGIN_RISK"

#: A cost change that was already recorded before this quote was written, so the
#: expected-cost baseline has moved to the new level. Distinct from a subsequent
#: change, which belongs entirely to the outcome layer and may never touch a
#: stored diagnosis.
KNOWN_COST_CHANGE = "KNOWN_COST_CHANGE"

#: Not enough comparable evidence to say anything about the price. A valid,
#: expected and frequent answer. Silence beats a fabricated baseline.
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

#: The price side is sound but no usable purchase history was knowable, so the
#: cost half of the diagnosis is withheld rather than guessed.
NO_COST_EVIDENCE = "NO_COST_EVIDENCE"

# ── context codes ────────────────────────────────────────────────────────────
# Qualifiers, never conclusions. They attach evidence to an observation without
# asserting a reason, because the reason is not in the ledger.

#: Enough of the comparables were trimmed as outliers that the band is not
#: describing a population. Fires in either direction, deliberately.
POSSIBLE_EXCEPTIONAL_PRICE = "POSSIBLE_EXCEPTIONAL_PRICE"

#: An unexplained purchase well below the normal acquisition band was removed
#: from the cost baseline. Context with rows attached — never an assertion about
#: why the purchase was cheap, because nothing in the ERP records that.
POSSIBLE_COST_DRIVEN = "POSSIBLE_COST_DRIVEN"

#: This customer has declined quotes at or below the top of the band, so the
#: top of the band is not evidence that the top is reachable here.
PRICE_RESISTANCE_OBSERVED = "PRICE_RESISTANCE_OBSERVED"

#: Some of the evidence this diagnosis could have used was excluded because its
#: visibility could not be established. Surfaced so a thin band is read as a
#: data gap rather than as a quiet account.
EVIDENCE_WITHHELD = "EVIDENCE_WITHHELD"

# ── recorded-reason codes ────────────────────────────────────────────────────
#
# What this quote's own record says about why it was priced as it was, read
# through ``commercial/source_concepts`` — a concept, never a source key. They
# live in this vocabulary rather than in a second one of ``intent``'s own,
# because a code the desk may see is decided by exactly one allowlist and a
# second list would be a second answer.
#
# **They are not appended to ``codes`` or to ``context``**, and that is a
# decision rather than an omission: both are persisted columns and a taxonomy
# can move retroactively under a stored diagnosis. See ``ENGINE_VERSION``. They
# ride on ``OwnerDiagnosis.intent`` and ``OperationsDiagnosis.intent``, which
# ``service.record`` writes no column for.

#: At least one concept was read from this quote's own fields. Somebody recorded
#: why this was priced as it was, and the diagnosis can say what they wrote.
PRICING_REASON_RECORDED = "PRICING_REASON_RECORDED"

#: This organization has a declared field for a pricing reason and this quote is
#: blank in it. **Not "there was no reason"** — the record holds whether anybody
#: wrote one down, which is a different fact. It fires only where a field was
#: actually declared, because a book with nothing declared has no absence to
#: observe and reporting one would be this engine's own *absence of evidence is
#: not a pass*, on every row.
NO_PRICING_REASON_RECORDED = "NO_PRICING_REASON_RECORDED"

#: Nothing at all has been declared for this system's quotes, so no field on
#: this quote is read as a pricing reason. A gap in the configuration rather
#: than in the data, and the ordinary answer on a live book today — which is why
#: it is its own code and not collapsed into the one above.
PRICING_REASON_NOT_DECLARED = "PRICING_REASON_NOT_DECLARED"

#: This quote holds a value the organization's declaration does not give a
#: meaning for. Somebody entered something and nobody finished the declaration,
#: which is neither "recorded" nor "blank".
PRICING_REASON_UNRECOGNISED = "PRICING_REASON_UNRECOGNISED"

#: The line sits below the range this customer's own history supports, the band
#: is believable, and the field this organization records a pricing reason in is
#: empty. **A possibility and never a finding** — the strongest form permitted,
#: in the register ``POSSIBLE_EXCEPTIONAL_PRICE`` and ``POSSIBLE_COST_DRIVEN``
#: already set.
#:
#: Deliberately **outside** the allowlist below. It is a claim about the money on
#: the line rather than about the record, so it is a margin claim, and the desk
#: gets the reading without it — a salesperson told "no pricing reason has been
#: recorded" can go and record one, and told "this may be leaking margin" has
#: been handed a judgement about a number they may not see. ``intent`` declares
#: the sentence on ``PricingIntent`` alone, so the type stops it as well as the
#: allowlist does, and the word "margin" in this code is itself caught by
#: ``tests/cost_sweep.WORDS`` if either guard is ever undone.
POSSIBLE_MARGIN_LEAKAGE = "POSSIBLE_MARGIN_LEAKAGE"

#: The codes an operations view may carry. An **allowlist**, so a code added
#: later is silent to the desk until somebody decides otherwise — the opposite
#: failure direction from a denylist, which leaks anything nobody remembered.
#:
#: Membership is decided by one question: can a caller who varies the quoted
#: price and watches this code appear and disappear learn a cost? Every code
#: here turns on the *price band*, which is made of prices this customer has
#: already seen. ``COST_DRIVEN_MARGIN_RISK`` is here because cost enters its
#: condition as a predicate that does not move with the quoted price — walking
#: the price cannot find a cost boundary, because there is not one in it.
#: ``BELOW_PEER_BAND_STRUCTURAL`` is absent for a commercial reason rather than
#: a disclosure one, and ``KNOWN_COST_CHANGE`` because its boundary *is* a cost.
#:
#: The four recorded-reason codes are here because every one of them is a fact
#: about a field on a document — what was written down, or that nothing was —
#: and none of them is about the money. ``quote_intent = TENDER`` explains a low
#: price to a salesperson without revealing anything about cost, and a desk that
#: knows the quote was a tender argues it better. ``POSSIBLE_MARGIN_LEAKAGE`` is
#: absent for the opposite reason: it is a claim about this line's margin.
OPERATIONS_CODES = frozenset({
    WITHIN_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE, ABOVE_HISTORICAL_RANGE,
    COST_DRIVEN_MARGIN_RISK, INSUFFICIENT_EVIDENCE,
    POSSIBLE_EXCEPTIONAL_PRICE, PRICE_RESISTANCE_OBSERVED, EVIDENCE_WITHHELD,
    PRICING_REASON_RECORDED, NO_PRICING_REASON_RECORDED,
    PRICING_REASON_NOT_DECLARED, PRICING_REASON_UNRECOGNISED,
})


def strength(axis: CustomerAxis, baseline: PriceBaseline, *,
             as_of: date, th: CommercialThresholds) -> str:
    """How much the band is worth believing (§11).

    Read against the tiers rather than the raw count: eight transactions at tier
    6 are eight prices for *something in the same category*, which is not the
    same claim as eight prices for this item at this quantity, and a strength
    grade that could not tell them apart would put a STRONG badge on a guess.
    """
    specific = axis.at_tiers(frozenset({TIER_SAME_CUSTOMER_SKU_BAND,
                                        TIER_SAME_CUSTOMER_SKU_ADJACENT}))
    moderate_set = axis.at_tiers(SPECIFIC_TIERS)
    total = len(axis.comparables)

    iqr = baseline.band.iqr_over_median
    recent_12 = _recent(specific, as_of, th.diagnosis_recent_days)
    recent_18 = _recent(moderate_set, as_of, th.diagnosis_moderate_recent_days)

    grade = INSUFFICIENT
    if (len(specific) >= th.diagnosis_strong_min_comparables
            and recent_12 >= th.diagnosis_strong_min_recent
            and iqr is not None and iqr <= th.diagnosis_strong_max_iqr_ratio):
        grade = STRONG
    elif (len(moderate_set) >= th.diagnosis_moderate_min_comparables
            and recent_18 >= th.diagnosis_moderate_min_recent
            and iqr is not None and iqr <= th.diagnosis_moderate_max_iqr_ratio):
        grade = MODERATE
    elif total >= th.diagnosis_weak_min_comparables:
        grade = WEAK

    # Two caps, and both are downgrades only — a cap can never promote.
    #
    # A band that had to throw away more than a quarter of its own evidence is
    # not describing a population, whichever way the rows fell. And a
    # specific-tier row whose visibility was estimated rather than read makes
    # the whole band partly an assumption; the assumption is paid for here.
    if baseline.over_exclusion_limit or _any_imputed(specific):
        grade = WEAK if _RANK[grade] > _RANK[WEAK] else grade
    return grade


def _recent(rows: Sequence, as_of: date, days: int) -> int:
    cutoff = as_of - timedelta(days=days)
    return sum(1 for r in rows if r.event_date > cutoff)


def _any_imputed(rows: Sequence) -> bool:
    return any(getattr(r, "recorded_at_imputed", False) for r in rows)


# ── the diagnosis ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OwnerDiagnosis:
    """Everything the engine concluded, economics included. RESTRICTED.

    Immutable, and the outcome layer has no write path into it: what actually
    happened is a different object and may never edit a judgement made before it
    was known.
    """

    line_id: str
    customer_id: Optional[str]
    product_id: str
    qty: Decimal
    quantity_band: str
    #: The commercial date of the quote. What the narrative speaks in.
    as_of: date
    #: The instant visibility was cut off at. What the evidence was filtered on.
    knowable_by: datetime

    quoted_unit_price: Optional[Decimal]
    codes: tuple[str, ...]
    context: tuple[str, ...]
    strength: str

    price: PriceBaseline
    peer: PeerBand
    #: RESTRICTED. The reason this type exists separately from the one below.
    cost: CostBaseline
    #: RESTRICTED. How much of this line's margin movement belongs to the price
    #: decision and how much to the cost level — the attributable form of the
    #: two conclusions ``codes`` already draws, with the arithmetic that has to
    #: hold before either half is asserted. Cost-derived throughout, which is
    #: why it sits on this type and has no counterpart on the one below.
    #:
    #: Always present and never ``None``. ``drivers.attribute`` returns an
    #: ``Attribution`` on every path and a refusal is one of its two shapes, so
    #: a reader is always told either the split or why there is not one. A field
    #: that could simply be missing would read as "nothing to report", which is
    #: the failure CLAUDE.md §1 names.
    attribution: "Attribution"
    #: RESTRICTED. What the cash tied up in this line costs: how long the money
    #: is out — the customer's days to pay less the supplier's credit — and what
    #: funding the purchase over that window takes off this line's margin.
    #:
    #: The most directly cost-revealing field on this type. ``capital_per_unit``
    #: **is** the purchase cost, carried rather than re-derived, and the charge
    #: divides straight back to it against one organization-wide rate. It has no
    #: counterpart on the type below and may never grow one.
    #:
    #: Always present and never ``None``, for the reason ``attribution`` is:
    #: ``working_capital.assess`` returns a reading on every path and a refusal
    #: is one of its two shapes, so a reader is told either the figure or which
    #: field would finish it. A key that could simply be absent would read as
    #: "nothing to report", which is the failure CLAUDE.md §1 names.
    working_capital: "WorkingCapital"
    #: RESTRICTED. What this quote's own record says about why it was priced as
    #: it was — read as a *concept* through ``commercial/source_concepts``, never
    #: as a source key — plus the one observation the desk may not have: that a
    #: line below its band has an empty pricing-reason field on it.
    #:
    #: Restricted for that last part alone. ``PricingIntent.reading`` is a fact
    #: about fields on a document and reaches both roles; ``exposure`` is a claim
    #: about this line's margin and is declared on this object's type and on no
    #: other, which is why ``operations_view`` hands over the reading rather than
    #: filtering this.
    #:
    #: Always present and never ``None``, for the reason ``attribution`` and
    #: ``working_capital`` are: ``intent.assess`` returns an object on every path
    #: and a refusal is one of its two shapes, so a reader is told either what
    #: was recorded or why nothing could be read. A key that could simply be
    #: absent would read as "no reason was recorded", which is the one thing it
    #: does not mean.
    intent: "PricingIntent"

    #: Per unit, positive when the quote is below the bottom of the band. The
    #: gate reads this; the opportunity *range* is computed downstream.
    deviation_per_unit: Optional[Decimal]
    #: That deviation across the line. Deliberately the conservative end — the
    #: band's bottom, not its median — because it is a threshold for
    #: interrupting somebody, and the optimistic figure is the wrong one to
    #: decide an interruption with.
    line_deviation_value: Optional[Decimal]

    evidence: dict
    tier_counts: dict
    surfaces: bool
    thresholds_version: str
    engine_version: str = ENGINE_VERSION

    @property
    def primary(self) -> str:
        return self.codes[0] if self.codes else INSUFFICIENT_EVIDENCE


@dataclass(frozen=True)
class OperationsDiagnosis:
    """The same conclusion, for somebody who may not see cost.

    **Every field on this type is a price the customer has already seen, a count,
    or a word.** There is no cost field, no margin field, no opportunity value
    and no peer band — not withheld, not masked, absent. That is the guarantee,
    and ``test_operations_view_has_no_economics_field`` reads the dataclass's own
    field list to hold it, so adding one is a failing test rather than a review
    that somebody has to catch.

    ``headline`` and ``why`` are prose the renderer fills. A cost-driven
    diagnosis arrives here already worded as what it is — margin compressed by
    supply cost, no price change needed — with no figure to withhold.
    """

    line_id: str
    quoted_unit_price: Optional[Decimal]
    historical_low: Optional[Decimal]
    historical_high: Optional[Decimal]
    comparable_count: int
    recent_comparable_count: int
    strength: str
    codes: tuple[str, ...]
    context: tuple[str, ...]
    surfaces: bool
    #: What this quote's own record says about why it was priced as it was.
    #: An ``intent.Reading`` and never a ``PricingIntent``: every sentence on
    #: that type is a fact about a field on a document, so there is no margin
    #: claim here to withhold — the type has no field for one. A salesperson
    #: told the quote was recorded as a tender has been given the reason a price
    #: is low without being given a number.
    #:
    #: Required and never defaulted, although two fields below it are. A
    #: projection built by hand — ``routers.quote_diagnosis._project_stored``
    #: builds one from a stored row that has no column for this — has to state
    #: its refusal rather than inherit a silence, which is the same reason
    #: ``Resolution.identity_candidate`` proposes nothing until it says so.
    intent: "Reading"
    #: Every card carries it: history may contain exceptional pricing that was
    #: never recorded in the ERP, and a reader who is not told that will read a
    #: band as a rule.
    qualification: str = (
        "Historical prices may include exceptional deals not recorded in the ERP.")


#: The field names an operations view may never grow. Checked by a test rather
#: than trusted to review — the list is short and the failure is expensive.
FORBIDDEN_OPERATIONS_FIELDS = frozenset({
    "cost", "unit_cost", "expected_cost", "cost_range", "landed_cost",
    "margin", "gross_profit", "cogs", "margin_floor", "min_margin",
    "opportunity", "opportunity_value", "peer", "peer_band", "peer_price",
    # Driver attribution, in every spelling it could arrive under. These are
    # cost fields wearing other names: an effect in percentage points beside
    # the price the caller sent is a margin, and a margin with a price is the
    # cost in one step — P x (1 - m). They are on this list for the reason the
    # ones above are, not because the words look economic.
    "attribution", "drivers", "driver", "movement_pp", "residual_pp",
    "effect_pp", "effect_per_unit",
    # Working capital, in every spelling it could arrive under. This is the most
    # direct of the three: ``capital_per_unit`` **is** the purchase cost, and
    # ``capital_at_risk`` is that times a quantity the caller sent. The charge
    # fields are no better — one organization-wide rate and a day count turn any
    # of them back into the cost with one division, which is the same shape as
    # MFLOOR and as the attribution above. ``rate`` is on the list because the
    # cost of capital is itself RESTRICTED policy.
    "working_capital", "capital_per_unit", "capital_at_risk",
    "charge_per_unit", "line_charge", "funded_days", "receivable_days",
    "supplier_credit_days", "rate", "cost_of_capital", "financing", "funding",
    # The recorded-reason reading's margin claim, in every spelling it could
    # arrive under. The reading itself is on the desk's type and is meant to be:
    # every sentence in it is a fact about a field on a document, which is why
    # ``intent`` is deliberately NOT on this list. What may never join it there
    # is the sentence saying a line with no recorded reason may be leaking
    # margin — a judgement about the money on the line, and a judgement about
    # margin beside the price the caller sent is the same shape as MFLOOR.
    "exposure", "leakage", "margin_leakage", "pricing_intent",
})


def operations_view(owner: OwnerDiagnosis, *,
                    historical_low: Optional[Decimal] = None,
                    historical_high: Optional[Decimal] = None,
                    ) -> OperationsDiagnosis:
    """Build the desk's view from the owner's. A construction, not a filter.

    The codes are intersected with ``OPERATIONS_CODES`` — an allowlist — so a
    structural peer finding or a known cost change simply is not present. Where
    that empties the list the card does not render, which is correct: there is
    nothing the desk can act on.

    The recorded-reason reading crosses whole, because it is not a filtering
    question: ``PricingIntent.reading`` is the half with no margin claim on it,
    so what the desk is handed is a *sub-object the engine built*, not the
    owner's object with a sentence removed. ``exposure`` stays behind because
    there is no field here to put it in.
    """
    codes = tuple(c for c in owner.codes if c in OPERATIONS_CODES)
    context = tuple(c for c in owner.context if c in OPERATIONS_CODES)
    band = owner.price.band
    return OperationsDiagnosis(
        line_id=owner.line_id,
        quoted_unit_price=owner.quoted_unit_price,
        historical_low=(historical_low if historical_low is not None else band.low),
        historical_high=(historical_high if historical_high is not None
                         else band.high),
        comparable_count=band.sample_count,
        recent_comparable_count=band.recent_sample_count,
        strength=owner.strength,
        codes=codes,
        context=context,
        intent=owner.intent.reading,
        # A card the owner sees is not automatically a card the desk sees: the
        # surfacing gate already ran, but a diagnosis whose only operational
        # code is WITHIN_HISTORICAL_RANGE has nothing to say here.
        surfaces=bool(owner.surfaces and codes
                      and codes != (WITHIN_HISTORICAL_RANGE,)),
    )


def operations_field_names() -> frozenset[str]:
    """The operations view's own field names, for the structural test."""
    return frozenset(f.name for f in fields(OperationsDiagnosis))


# ── assembly ─────────────────────────────────────────────────────────────────

def diagnose(*, line_id: str, subject_customer_id: Optional[str],
             product_id: str, qty: Decimal, quantity_band: str,
             quoted_unit_price: Optional[Decimal],
             as_of: date, knowable_by: datetime,
             axis: CustomerAxis, peer: PeerBand,
             price: PriceBaseline, cost: CostBaseline,
             evidence: EvidenceSet, th: CommercialThresholds,
             backfill_before: Optional[date] = None,
             settlements: Sequence["Settlement"] = (),
             supplier_term: Optional["Term"] = None,
             supplier_term_recorded_at: Optional[datetime] = None,
             supplier_erp_days: Optional[int] = None,
             source_record: Optional["SourceRecord"] = None,
             taxonomy: Optional["Taxonomy"] = None,
             ) -> OwnerDiagnosis:
    """Run every rule over already-computed baselines. Pure and total.

    Order matters in one place only: ``codes[0]`` is what the renderer leads
    with, so the price-side conclusion comes first and the cost-side and
    account-level findings follow it. Everything else is a set.

    The last four arguments are the working-capital reading's own evidence, and
    they are arguments rather than a load because this function holds no
    ``Session`` and must not start: ``service`` reads them and threads them
    through, exactly as it does the cost rows. They default to nothing and the
    reading then refuses and says which of them is missing, which is the correct
    answer for a caller that has none — the alternative is a plausible-looking
    stand-in, and this package does not have those.

    ``settlements`` are **one customer's**, unfiltered. ``working_capital``
    applies the window and the visibility cut itself, and refuses a mixed-party
    list outright; pre-filtering here would put two windows in one product.

    ``source_record`` and ``taxonomy`` are the recorded-reason reading's own
    evidence and arrive as a pair from one row, for the reason the four above
    arrive at all: this function holds no ``Session``. Both defaulting to
    ``None`` means "the caller handed no record", and the reading then refuses
    with ``NO_SOURCE_RECORD`` — the correct answer for a caller that has none,
    and not a plausible-looking stand-in.
    """
    codes: list[str] = []
    context: list[str] = []
    grade = strength(axis, price, as_of=as_of, th=th)

    band = price.band
    deviation: Optional[Decimal] = None
    line_value: Optional[Decimal] = None

    if quoted_unit_price is None or band.median is None or band.low is None:
        codes.append(INSUFFICIENT_EVIDENCE)
        grade = INSUFFICIENT if band.median is None else grade
    else:
        if quoted_unit_price < band.low:
            codes.append(BELOW_HISTORICAL_RANGE)
            deviation = band.low - quoted_unit_price
        elif band.high is not None and quoted_unit_price > band.high:
            codes.append(ABOVE_HISTORICAL_RANGE)
            deviation = quoted_unit_price - band.high
        else:
            codes.append(WITHIN_HISTORICAL_RANGE)
            deviation = _ZERO
        line_value = deviation * qty

    # ── the account-level finding (§6.2) ────────────────────────────────────
    # Fires only when the quote is *fine* against this customer's own history.
    # That is the whole point: a customer quoted low for two years has a
    # perfectly consistent history, and the customer axis alone would report
    # "consistent" and say nothing at all.
    if (WITHIN_HISTORICAL_RANGE in codes
            and peer.customer_count >= th.diagnosis_min_peer_customers
            and peer.stats.median is not None and band.high is not None
            and peer.stats.median > _ZERO
            and (peer.stats.median - band.high) / peer.stats.median
            >= Decimal(str(th.diagnosis_peer_gap_pct))):
        codes.append(BELOW_PEER_BAND_STRUCTURAL)

    # ── the cost side (§10) ─────────────────────────────────────────────────
    #
    # A missing cost baseline is a *qualifier*, never a conclusion and never a
    # reason to withhold the price finding. §9 is explicit: emit low confidence
    # on the cost side and still produce a price-side diagnosis if the price
    # evidence is sound. "You are quoting below what this customer has paid" is
    # true and actionable whether or not the ledger knows what the item cost.
    #
    # What it does forbid is a money figure: the opportunity layer refuses to
    # put a value on a below-band line with no cost baseline, because that value
    # would be a margin claim and there is nothing behind it.
    if not cost.known:
        context.append(NO_COST_EVIDENCE)
    else:
        if cost.known_cost_change:
            codes.append(KNOWN_COST_CHANGE)
        # Cost-driven risk is asserted only where the price is *not* the
        # problem. A line that is both below its band and facing a higher cost
        # is a pricing finding first; saying "cost" there would excuse the part
        # somebody can actually do something about.
        if (WITHIN_HISTORICAL_RANGE in codes
                and cost.expected_cost is not None
                and cost.historical_cost is not None
                and cost.historical_cost > _ZERO
                and (cost.expected_cost - cost.historical_cost)
                / cost.historical_cost
                >= Decimal(str(th.meaningful_cost_increase_pct))):
            codes.append(COST_DRIVEN_MARGIN_RISK)
        if cost.unexplained_low_purchase:
            context.append(POSSIBLE_COST_DRIVEN)

    # ── qualifiers ──────────────────────────────────────────────────────────
    if price.over_exclusion_limit:
        context.append(POSSIBLE_EXCEPTIONAL_PRICE)
    if price.resistance.observed:
        context.append(PRICE_RESISTANCE_OBSERVED)
    if evidence.excluded:
        context.append(EVIDENCE_WITHHELD)

    surfaces = _surfaces(codes=codes, grade=grade, band_median=band.median,
                         deviation=deviation, line_value=line_value, th=th)

    # ── the attributable form of the two conclusions above ──────────────────
    #
    # Computed on every line and surfaced on almost none: ``_surfaces`` gates
    # interruption, not calculation (§11). ``grade`` is the grade this
    # diagnosis publishes, so the split is believed exactly as much as the band
    # it is measured against — a second grader here would disagree with the
    # first on the rows nobody looks at.
    #
    # Imported at call time rather than at module scope because ``drivers``
    # reads this module's code vocabulary; the dependency can only run one way.
    from .drivers import attribute
    attribution = attribute(
        quoted_unit_price=quoted_unit_price, price=price, cost=cost,
        # The cost rows the baselines were built from, already visibility
        # filtered by ``service``. ``attribute`` re-checks them against the same
        # ``evidence.is_knowable`` — a verification rather than a second filter,
        # which is why ``backfill_before`` is threaded through instead of being
        # re-derived: asking the question with a different cut-over would make
        # the check answer something the filter never asked.
        cost_rows=evidence.costs, strength=grade, knowable_by=knowable_by,
        backfill_before=backfill_before, th=th)

    # ── what the cash on this line costs ────────────────────────────────────
    #
    # Computed on every line and surfaced on almost none, for the reason the
    # attribution above is: the gate governs interruption, not calculation.
    # ``assess`` decides its own ``surfaces`` from its own strength and
    # severity — a second gate here would be a second answer to "should this
    # interrupt somebody" and could only disagree with the first.
    #
    # Imported at call time rather than at module scope because
    # ``working_capital`` reads this module's grade ladder; the dependency can
    # only run one way.
    from .working_capital import assess as _assess_working_capital
    capital = _assess_working_capital(
        quoted_unit_price=quoted_unit_price, qty=qty, cost=cost,
        settlements=settlements,
        # Told rather than inferred from an empty settlement list: a line with
        # no customer and an account that has settled nothing arrive here
        # looking identical, and they are not the same refusal.
        has_customer=subject_customer_id is not None,
        supplier_term=supplier_term,
        supplier_term_recorded_at=supplier_term_recorded_at,
        supplier_erp_days=supplier_erp_days,
        as_of=as_of, knowable_by=knowable_by, th=th)

    # ── what the record says about why this was priced as it was ────────────
    #
    # Computed on every line and, unlike the two above, published on every card
    # the engine already decided to show — it adds nothing to ``_surfaces`` and
    # could not: the gate reads the price finding, and a reading of a source
    # field is not one. An intent signal that fired on its own would be alert
    # fatigue with a new name.
    #
    # It is given ``codes`` and ``grade`` because the one claim it may make —
    # that a line below its band has an empty pricing-reason field — rests on
    # the price finding this function has just made and on the grade it
    # publishes. A second grader here would disagree with the first on the rows
    # nobody looks at, which is the reason the attribution above is handed one
    # too.
    #
    # Imported at call time rather than at module scope because ``intent`` reads
    # this module's code vocabulary; the dependency can only run one way.
    from .intent import NO_RECORD, assess as _assess_intent
    recorded_reason = _assess_intent(
        record=source_record if source_record is not None else NO_RECORD,
        taxonomy=taxonomy, codes=codes, strength=grade)

    return OwnerDiagnosis(
        line_id=line_id, customer_id=subject_customer_id, product_id=product_id,
        qty=qty, quantity_band=quantity_band, as_of=as_of,
        knowable_by=knowable_by, quoted_unit_price=quoted_unit_price,
        codes=tuple(codes), context=tuple(sorted(set(context))), strength=grade,
        price=price, peer=peer, cost=cost, attribution=attribution,
        working_capital=capital, intent=recorded_reason,
        deviation_per_unit=deviation, line_deviation_value=line_value,
        evidence=evidence.summary(), tier_counts=axis.tier_counts(),
        surfaces=surfaces, thresholds_version=th.version)


def had_enough_to_compare(d) -> bool:
    """Whether there was enough comparable history to judge this line at all.

    ``strength`` is the field that answers this; ``codes`` are not, and reading
    them for it is the re-derivation §1 warns about. A single prior transaction
    still produces a usable band, so ``_diagnose`` takes the price branch and
    appends ``ABOVE_HISTORICAL_RANGE`` — ``INSUFFICIENT_EVIDENCE`` never lands
    in ``codes`` — while ``strength`` correctly grades it INSUFFICIENT. Asking
    the codes therefore reported "compared" on a line the engine had just
    graded as having too little to compare, and the quote summary counted it.

    The code is still consulted for the case the grade cannot see: a band with
    no usable median or low, where the grade may survive but there is nothing
    to compare against.

    Takes either projection — both carry ``strength`` and ``codes``, and the
    two roles must answer this identically.
    """
    return d.strength != INSUFFICIENT and INSUFFICIENT_EVIDENCE not in d.codes


def _surfaces(*, codes: Sequence[str], grade: str,
              band_median: Optional[Decimal], deviation: Optional[Decimal],
              line_value: Optional[Decimal], th: CommercialThresholds) -> bool:
    """Whether this renders a card at all. Every condition must hold (§11).

    Computed, stored and available on demand regardless — the gate governs
    *interruption*, not calculation. Default silent.
    """
    if not codes or WITHIN_HISTORICAL_RANGE in codes:
        return False
    if INSUFFICIENT_EVIDENCE in codes:
        return False
    if _RANK[grade] < _RANK[MODERATE]:
        return False
    if deviation is None or band_median is None or band_median <= _ZERO:
        return False
    if deviation / band_median < Decimal(str(th.diagnosis_min_deviation_pct)):
        return False
    if deviation < Decimal(str(th.diagnosis_min_deviation_per_unit)):
        return False
    if line_value is None or line_value < Decimal(
            str(th.diagnosis_min_line_opportunity)):
        return False
    return True
