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
from typing import Optional, Sequence

from ..config import CommercialThresholds
from .baselines import CostBaseline, PriceBaseline
from .comparables import (CustomerAxis, PeerBand, SPECIFIC_TIERS,
                          TIER_SAME_CUSTOMER_SKU_ADJACENT,
                          TIER_SAME_CUSTOMER_SKU_BAND)
from .evidence import EvidenceSet

_ZERO = Decimal("0")

#: This module's own version, stamped on every diagnosis beside the thresholds
#: hash. The two answer different questions — "which policy judged this" and
#: "which code produced it" — and a rule change that did not move a version
#: would make an old diagnosis unexplainable. Bump it when a rule changes.
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
OPERATIONS_CODES = frozenset({
    WITHIN_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE, ABOVE_HISTORICAL_RANGE,
    COST_DRIVEN_MARGIN_RISK, INSUFFICIENT_EVIDENCE,
    POSSIBLE_EXCEPTIONAL_PRICE, PRICE_RESISTANCE_OBSERVED, EVIDENCE_WITHHELD,
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
             ) -> OwnerDiagnosis:
    """Run every rule over already-computed baselines. Pure and total.

    Order matters in one place only: ``codes[0]`` is what the renderer leads
    with, so the price-side conclusion comes first and the cost-side and
    account-level findings follow it. Everything else is a set.
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

    return OwnerDiagnosis(
        line_id=line_id, customer_id=subject_customer_id, product_id=product_id,
        qty=qty, quantity_band=quantity_band, as_of=as_of,
        knowable_by=knowable_by, quoted_unit_price=quoted_unit_price,
        codes=tuple(codes), context=tuple(sorted(set(context))), strength=grade,
        price=price, peer=peer, cost=cost,
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
