"""Why this line's margin moved: the split between the price decision and the
cost level, with the arithmetic that has to hold before either is asserted.

The engine already says a line sits above or below its band (``rules``) and,
separately, that acquisition cost has moved (``baselines.CostBaseline``). What
it has never said is **how much of the margin movement each one accounts for**.
A cost rise of 7% with a price cut of 3% reported as "cost increase" is the
defect this module exists to prevent: it is true, it is the larger term, and it
excuses the half somebody could have done something about.

**RESTRICTED, in its entirety.** Every figure here is derived from purchase
cost. An ``Attribution`` belongs on ``rules.OwnerDiagnosis`` and has no place on
``rules.OperationsDiagnosis`` — that type has no cost field precisely so a leak
is impossible rather than merely avoided, and a driver field on it would undo
the guarantee. ``COST_DRIVEN_MARGIN_RISK`` is the desk's whole vocabulary for
this: margin compressed by supply cost, no number attached.

── the arithmetic, and the order it is taken in ─────────────────────────────

Margin is ``(P - C) / P``. The movement being explained runs from a baseline
pair ``(P0, C0)`` to this line's pair ``(P1, C1)``:

    P1  the quoted unit price — the line itself
    C1  ``CostBaseline.expected_cost``  — "the cost the quote should have been
        priced against", which is the trimmed median unless a knowable purchase
        moved it to a new level
    P0  ``PriceBaseline.band.median``  — the middle of what this customer has
        actually paid for this thing at this quantity, after trimming
    C0  ``CostBaseline.historical_cost`` — the trimmed median *before* any
        known-change override

Each choice is a judgement and each is argued at ``attribute``.

The decomposition is sequential and therefore **order-dependent**: the price
effect measured at the old cost is not the price effect measured at the new one,
and the difference is the interaction term. This module takes one order and says
so in the output:

    PRICE_THEN_COST
      price effect = m(P1, C0) - m(P0, C0)   measured at the historical cost
      cost  effect = m(P1, C1) - m(P1, C0)   measured at the quoted price

The cost step is last, so it is evaluated at the price the line actually
carries, which is what makes the sentence a person quotes back — "at the
previous purchase cost this line would carry X pp more margin" — a statement
about *this line* rather than a forecast. The price step is evaluated at the
cost level history had established, which is the level the band itself was
priced against; charging the price decision with a supply move measured
afterwards would report a pricing fault that is a supply fact.

── the trap ─────────────────────────────────────────────────────────────────

It is tempting to compute one effect and subtract it from the movement to get
the other. That makes the reconciliation true by construction and hides every
bug it was built to catch — a transposed operand, or two effects measured on
different orders, would both pass silently.

So all three quantities are measured from their own margin evaluations and the
identity is then *checked*. Where it does not hold the split is **refused**: a
confidently wrong attribution is worse than none, and there is no state in which
this module publishes a residual it cannot account for.

The residual is reported either way. It is never forced to zero.

── what this is not ─────────────────────────────────────────────────────────

``metrics.classify_erosion`` and ``metrics.pass_through`` answer a neighbouring
question at a different grain and are deliberately not reused or duplicated
here: those read a *relationship's* quantity-weighted economics across two time
windows and return a label and a ratio. Neither can say how many percentage
points of one quote line's movement belongs to each factor, which is the only
thing this module does. Nothing here re-derives a baseline either — the price
and cost baselines arrive already computed, already trimmed and already
point-in-time filtered.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Optional, Sequence

from ..config import CommercialThresholds
from .baselines import CostBaseline, PriceBaseline
from .evidence import CostObservation, is_knowable
from .rules import (ABOVE_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE,
                    COST_DRIVEN_MARGIN_RISK, INSUFFICIENT, KNOWN_COST_CHANGE,
                    MODERATE, STRONG, WEAK, WITHIN_HISTORICAL_RANGE, _RANK)

_ZERO = Decimal("0")

#: The ranking of the four evidence grades, imported rather than restated. It is
#: one fact about one ladder; a second copy here would agree exactly until
#: somebody inserted a grade, and then disagree silently.
_GRADE_RANK = _RANK


# ── the counterfactual ───────────────────────────────────────────────────────

#: The order the two effects are measured in. Named in the output because the
#: decomposition is order-dependent and a split whose order a reader has to
#: guess is a split they will read as the other one.
PRICE_THEN_COST = "PRICE_THEN_COST"


# ── driver codes: an extension of the ``rules`` vocabulary, not a rival ──────
#
# Two codes, because price and cost are the two factors this comparison
# genuinely isolates. Quantity, discount and mix are not here: the customer axis
# hard-filters on the quantity band at exactly the tiers a diagnosis is allowed
# to be confident from, so quantity is held fixed rather than measured, and
# nothing in these books records a discount or a mix separately from the net
# unit price. A driver invented to fill a taxonomy slot would be a number with
# no comparison behind it.

#: The margin effect of where this line's price sits against the band. The
#: attributable form of the price-side conclusion ``rules`` already draws.
PRICE_POSITION_EFFECT = "PRICE_POSITION_EFFECT"

#: The margin effect of the cost level this line faces against the historical
#: level. The attributable form of the cost-side conclusion.
COST_LEVEL_EFFECT = "COST_LEVEL_EFFECT"

DRIVER_CODES = frozenset({PRICE_POSITION_EFFECT, COST_LEVEL_EFFECT})

#: Which existing diagnosis codes each driver is the attributable form of.
#: Published as data rather than left to a renderer to re-derive, so "extends
#: rather than replaces" is something a reader can check instead of a claim in a
#: docstring. A driver is a *magnitude* for a finding the engine already makes;
#: it never reaches a conclusion the codes do not.
EXTENDS: dict[str, tuple[str, ...]] = {
    PRICE_POSITION_EFFECT: (BELOW_HISTORICAL_RANGE, WITHIN_HISTORICAL_RANGE,
                            ABOVE_HISTORICAL_RANGE),
    COST_LEVEL_EFFECT: (COST_DRIVEN_MARGIN_RISK, KNOWN_COST_CHANGE),
}


# ── severity ─────────────────────────────────────────────────────────────────
#
# How much a driver matters. Distinct words from the four evidence grades on
# purpose: severity and strength are not substitutes and must not be readable as
# one scale. A nine-point effect off a two-row band is severe and unbelievable
# at once, and a single grade would have to lie about one of them.
#
# The boundaries are policy and live in ``CommercialThresholds``
# (``diagnosis_driver_major_pp``, ``diagnosis_driver_minor_pp``), versioned like
# every other number this platform judges against — never module constants here.

MAJOR = "MAJOR"
MINOR = "MINOR"
NEGLIGIBLE = "NEGLIGIBLE"


def severity(effect_pp: Decimal, th: CommercialThresholds) -> str:
    """Grade one effect by magnitude, in either direction.

    Symmetric: a five-point *gain* from a cost that fell is as much a finding as
    a five-point loss, and grading only the losses would make the engine's own
    good news invisible.
    """
    magnitude = abs(effect_pp)
    if magnitude >= Decimal(str(th.diagnosis_driver_major_pp)):
        return MAJOR
    if magnitude >= Decimal(str(th.diagnosis_driver_minor_pp)):
        return MINOR
    return NEGLIGIBLE


# ── reconciliation ───────────────────────────────────────────────────────────

#: The quantum every reported percentage-point figure is rounded to: one
#: millionth of a margin ratio, which is one ten-thousandth of a percentage
#: point. Four orders of magnitude finer than anything rendered — quote_service
#: rounds ``margin_change_pp`` to 1e-4 — so the rounding is invisible to a
#: reader and is here only so that two runs over the same inputs produce the
#: same bytes.
PP_QUANTUM = Decimal("0.000001")

#: How far the effects may miss the movement before the split is refused. Two
#: quanta, and the reasoning is the whole of it:
#:
#: The three figures — the movement and the two effects — are each rounded
#: independently from full-precision margins, and ROUND_HALF_EVEN puts at most
#: half a quantum on each. One and a half quanta is therefore the arithmetic
#: worst case, and two is that with a single quantum of headroom. Nothing else
#: can contribute: the margins are divided at 28 significant digits, whose error
#: is around 1e-28 relative and cannot reach the sixth decimal place.
#:
#: So this is not a tolerance chosen to make a residual go away. Anything above
#: it is an interaction term — the two effects measured on different
#: counterfactual orders — which is a defect in the decomposition and not
#: rounding, and it is refused rather than reported.
RECONCILIATION_TOLERANCE_PP = 2 * PP_QUANTUM

#: Significant digits the three margin divisions are taken at. Pinned rather
#: than inherited from the ambient decimal context, because a caller that had
#: lowered ``getcontext().prec`` would otherwise change what this module
#: computes — and identical inputs must produce identical bytes.
WORKING_PRECISION = 28


def reconcile(movement_pp: Decimal,
              effects: Sequence[Decimal]) -> tuple[bool, Decimal]:
    """Whether the effects account for the movement, and what they miss by.

    One home for the rule, so the check and the number it reports cannot drift
    apart, and public so it can be exercised directly on an inconsistent pair —
    which is the only way a genuine interaction term can be constructed once
    ``attribute`` is measuring both effects on one order.

    The residual is returned whether or not it is within tolerance. It is never
    zeroed, absorbed into a driver, or left out of the output.
    """
    total = _ZERO
    for effect in effects:
        total += effect
    residual = movement_pp - total
    return abs(residual) <= RECONCILIATION_TOLERANCE_PP, residual


# ── the output ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Driver:
    """One attributed factor: what it is, how much it matters, how much to
    believe it, the rows it rests on, and the money.

    Frozen for the reason every other record in this package is: a driver is
    cited by id and replayed, and one that could be edited after the fact would
    make the citation a lie.
    """

    #: One of ``DRIVER_CODES``. ``EXTENDS`` says which ``rules`` codes it is the
    #: attributable form of.
    code: str
    #: ``MAJOR`` / ``MINOR`` / ``NEGLIGIBLE`` — how much this matters. Policy,
    #: graded against versioned thresholds.
    severity: str
    #: ``STRONG`` / ``MODERATE`` / ``WEAK`` / ``INSUFFICIENT`` — how much to
    #: believe it. The ``rules`` ladder, not a second scale.
    strength: str
    #: Margin movement attributed to this factor, in percentage points carried
    #: **as a fraction** — 0.032 is 3.2 points — which is this codebase's ``_pp``
    #: convention throughout. Positive means this factor helped margin.
    effect_pp: Optional[Decimal]
    #: The same effect on gross profit per unit, in money. Exact: it is a
    #: difference of prices and costs with no division in it.
    effect_per_unit: Optional[Decimal]
    #: The evidence row ids this factor's baseline was built from.
    cited: tuple[str, ...]
    #: Why this is attributable to this factor, in words — the counterfactual
    #: the number answers, not a restatement of it.
    basis: str


@dataclass(frozen=True)
class Attribution:
    """The split, or a refusal to assert one.

    There are exactly two shapes. Either ``drivers`` holds both factors,
    ``reconciles`` is true and ``residual_pp`` is the rounding they miss by; or
    ``drivers`` is empty and ``basis`` says what stopped it. A caller decides
    which it is by asking whether ``drivers`` is empty — there is no third state
    where a partial split is published.

    ``movement_pp`` is filled whenever the movement itself could be measured,
    including on a refusal that happened afterwards. A refusal for want of a
    cost baseline leaves it ``None``, because without a cost there is no margin
    to have moved — not zero, and not a default.
    """

    #: The observed margin movement from the baseline pair to this line, in
    #: percentage points carried as a fraction.
    movement_pp: Optional[Decimal]
    drivers: tuple[Driver, ...]
    #: Whether the effects account for the movement within
    #: ``RECONCILIATION_TOLERANCE_PP``. False on every refusal, so a caller that
    #: reads only this field is never told an unasserted split held.
    reconciles: bool
    #: What the effects do not account for. Reported, never hidden and never
    #: forced to zero.
    residual_pp: Optional[Decimal]
    #: ``PRICE_THEN_COST`` where a split is asserted, or the refusal code. The
    #: machine-readable half of ``basis``, carried as its own field so a reader
    #: is handed the sentence and a caller the code — rather than a renderer
    #: recovering one from the other, which is a predicate re-derived downstream
    #: from a published field (CLAUDE.md §1). ``working_capital.WorkingCapital``
    #: and ``intent.Reading`` are the same shape and this is the third of it.
    reason: str
    #: The counterfactual order and what it means, or the refusal in words. No
    #: code prefix: the code is ``reason``, one field above.
    basis: str


# ── refusal reasons ──────────────────────────────────────────────────────────
#
# Named rather than left as prose alone, so the reasons are one vocabulary and
# the sentences that carry them are built from it. They mean different things: a
# missing cost is a data gap somebody can close, a thin band is the engine being
# honest, and a failed reconciliation is a defect in this module.

NO_QUOTED_PRICE = "NO_QUOTED_PRICE"
NO_PRICE_BASELINE = "NO_PRICE_BASELINE"
NO_COST_BASELINE = "NO_COST_BASELINE"
COST_NOT_KNOWABLE = "COST_NOT_KNOWABLE"
#: A purchase whose visibility was *estimated* rather than read. Its own reason
#: because ``rules.strength`` pays for imputation on the price side only, and
#: the cost side is the whole of the claim here.
COST_VISIBILITY_IMPUTED = "COST_VISIBILITY_IMPUTED"
EVIDENCE_TOO_THIN = "EVIDENCE_TOO_THIN"
DOES_NOT_RECONCILE = "DOES_NOT_RECONCILE"


def attribute(*, quoted_unit_price: Optional[Decimal],
              price: PriceBaseline, cost: CostBaseline,
              cost_rows: Sequence[CostObservation],
              strength: str, knowable_by: datetime,
              backfill_before: Optional[date] = None,
              th: CommercialThresholds) -> Attribution:
    """Split this line's margin movement between its price and its cost.

    Pure and deterministic. Every input is already computed: the two baselines
    arrive trimmed and point-in-time filtered from ``baselines``, and
    ``strength`` is the grade ``rules.strength`` produced for this band — one of
    that module's four, and anything else raises rather than being read as a
    convenient default, because an unrankable grade is a programming error and
    not a data condition. Nothing is re-derived here: a second answer to "what
    should this have cost" is the duplication CLAUDE.md §2 is about, and the two
    copies would disagree on the rows nobody looks at.

    **The four values, and why each one.**

    ``P1`` is ``quoted_unit_price``: the line itself.

    ``C1`` is ``cost.expected_cost``. ``CostBaseline`` names it "the cost the
    quote should have been priced against", and it is the only field that has
    already absorbed a knowable cost change — so it is the level this line
    actually faces rather than the level it used to face.

    ``P0`` is ``price.band.median``. Not ``band.low``: the low end is the
    conservative bound the opportunity layer uses to decide whether to interrupt
    somebody, and using it here would attribute a price effect to every line
    priced at the middle of its own band. Not ``band.high`` either, which would
    make almost every line look like a price cut. The median is what this
    customer normally pays for this thing at this quantity, which is the only
    reference a price counterfactual can honestly use.

    ``PriceBaseline.resistance`` is deliberately not applied to it.
    ``highest_accepted`` is the maximum of the same trimmed rows the median is
    taken over, so it is at or above the median by construction and truncating
    against it could never bind. Reaching for it anyway would look like a
    control and be a no-op, which is worse than not having one.

    ``C0`` is ``cost.historical_cost`` — the trimmed median *before* any
    known-change override. ``baselines`` keeps the two fields apart for exactly
    this comparison and says why: read against ``expected_cost`` the question
    "has cost moved" answers itself, because the baseline has already been moved
    to the new level and the difference is silently zero.

    **The refusals, in order.** The line's own price, then the band, then the
    cost baseline, then the cost evidence's visibility, then the strength of
    both sides. Ordered from the most specific gap to the most general so the
    reason a reader gets names the thing closest to being fixable.
    """
    if quoted_unit_price is None or quoted_unit_price <= _ZERO:
        return _refused(
            NO_QUOTED_PRICE,
            "This line carries no quoted unit price, so the margin it earns is "
            "undefined and there is no movement to explain.")

    p0 = price.band.median
    if p0 is None or p0 <= _ZERO:
        return _refused(
            NO_PRICE_BASELINE,
            "No comparable price history supports a band for this line, so "
            "there is no reference price to measure a price effect against.")

    c0 = cost.historical_cost
    c1 = cost.expected_cost
    if c0 is None or c1 is None or c0 <= _ZERO or c1 <= _ZERO:
        # Absence of evidence is not a pass (CLAUDE.md §1). No cost means no
        # margin at all — not a zero cost effect, not last-known, not the
        # price's own median standing in.
        missing = "No purchase was knowable for this item" if not cost.cited \
            else "The knowable purchases produced no usable cost level"
        return _refused(
            NO_COST_BASELINE,
            f"{missing}, so the margin this line earns cannot be "
            "computed and neither half of the movement can be attributed. "
            f"{cost.observations} usable purchase observation"
            f"{'' if cost.observations == 1 else 's'} on record.")

    unknowable = _first_unknowable(cost_rows, knowable_by=knowable_by,
                                   backfill_before=backfill_before)
    if unknowable is not None:
        evidence_id, reason, total = unknowable
        # Refused rather than filtered. Dropping the row would silently rebuild
        # the cost baseline this function was handed — which is the one thing it
        # must not do — and a baseline built partly from evidence the quoter did
        # not have is not repairable from here.
        # Two different claims, because the two reasons are not the same
        # strength. An unknowable row demonstrably post-dates the quote; an
        # imputed one carries an estimated stamp, so what is missing is the
        # confirmation, not the evidence. Saying the stronger sentence for the
        # weaker fact is the kind of overclaim this engine refuses in numbers
        # and must not make in words.
        claim = (
            f"Purchase {evidence_id} carries an estimated rather than a "
            "recorded visibility stamp, so it cannot be confirmed as evidence "
            "this quote could have used"
            if reason == COST_VISIBILITY_IMPUTED else
            f"Purchase {evidence_id} was not evidence this quote could have "
            "used")
        return _refused(
            reason,
            f"{claim} ({total} of {len(cost_rows)} purchase"
            f"{'' if len(cost_rows) == 1 else 's'} handed in). The cost "
            "baseline rests on it, so no split is asserted.")

    cost_grade = cost_strength(cost, th)
    weakest = cost_grade if _GRADE_RANK[cost_grade] < _GRADE_RANK[strength] \
        else strength
    if _GRADE_RANK[weakest] < _GRADE_RANK[MODERATE]:
        # The gate is on both sides because both enter the movement itself: C0
        # and C1 are inside m(P0, C0) and m(P1, C1), so an unbelievable cost
        # level does not merely weaken the cost driver, it makes the number
        # being split wrong. This is not the surfacing gate — that governs
        # interruption and lives in ``rules._surfaces``. This one governs
        # whether anything is asserted at all, however large the money.
        #
        # ``movement_pp`` is left unfilled rather than computed and reported
        # beside the refusal: the movement is measured *from* P0 and C0, so a
        # band or a cost level nobody believes makes the movement itself
        # unbelievable. A figure published next to "there was not enough to
        # judge this" is the one a reader would take away.
        return _refused(
            EVIDENCE_TOO_THIN,
            f"The price band grades {strength} and the cost baseline "
            f"{cost_grade}; below {MODERATE} on either side there is no "
            "reference to measure a movement against, whatever the movement "
            "would have been.")

    p1 = quoted_unit_price

    # Three margins, each measured, none inferred from the others.
    m00 = _margin(p0, c0)
    m10 = _margin(p1, c0)
    m11 = _margin(p1, c1)

    # And three figures, each rounded independently. The movement is *not* the
    # sum of the effects and the effects are *not* the movement less each other
    # — that is the construction that would make the check below vacuous.
    movement_pp = _q(m11 - m00)
    price_pp = _q(m10 - m00)
    cost_pp = _q(m11 - m10)

    # The same split on gross profit per unit. No division, so Decimal makes it
    # exact, and it is a genuinely independent route to the same identity: a
    # transposed operand or a mixed counterfactual order fails here immediately
    # and without any tolerance to hide in.
    movement_money = (p1 - c1) - (p0 - c0)
    price_money = p1 - p0
    cost_money = c0 - c1

    ok_pp, residual_pp = reconcile(movement_pp, (price_pp, cost_pp))
    residual_money = movement_money - (price_money + cost_money)
    if not ok_pp or residual_money != _ZERO:
        return _refused(
            DOES_NOT_RECONCILE,
            "The two effects miss the observed movement by "
            f"{_pp_text(residual_pp)} and the money split by "
            f"{residual_money}, past the {_pp_text(RECONCILIATION_TOLERANCE_PP)} "
            "that rounding can account for. The split is not asserted.",
            movement_pp=movement_pp, residual_pp=residual_pp)

    price_driver = Driver(
        code=PRICE_POSITION_EFFECT,
        severity=severity(price_pp, th),
        strength=strength,
        effect_pp=price_pp,
        effect_per_unit=price_money,
        cited=tuple(price.cited),
        basis=("Where this line's price sits against the median of what this "
               "customer has paid for it, measured at the purchase cost history "
               "had established — the level the band itself was priced against, "
               "so a supply move is not charged to the price decision."),
    )
    cost_driver = Driver(
        code=COST_LEVEL_EFFECT,
        severity=severity(cost_pp, th),
        strength=cost_grade,
        effect_pp=cost_pp,
        effect_per_unit=cost_money,
        cited=tuple(cost.cited),
        basis=("The cost level this line faces against the trimmed historical "
               "level, measured at the price actually quoted — a statement "
               "about this line, not a forecast about the next one."),
    )

    # Price first, then cost: the same order ``rules.diagnose`` puts its codes
    # in, and the order the counterfactual is taken in. One sequence, said once.
    return Attribution(
        movement_pp=movement_pp,
        drivers=(price_driver, cost_driver),
        reconciles=True,
        residual_pp=residual_pp,
        reason=PRICE_THEN_COST,
        basis=(
            "The price effect is measured at the historical purchase cost and "
            "the cost effect at the price actually quoted. "
            f"At the previous purchase cost this line would carry "
            f"{_more_or_less(-cost_pp)}; at that same cost, pricing at the band "
            f"median rather than at the quoted price is worth "
            f"{_more_or_less(-price_pp)}. The two account for the observed "
            f"movement of {_pp_text(movement_pp)} to within "
            f"{_pp_text(residual_pp)}."),
    )


def cost_strength(cost: CostBaseline, th: CommercialThresholds) -> str:
    """How much to believe the cost half, on the one axis a cost baseline has.

    Deliberately the same ladder shape and the same threshold fields as
    ``rules.strength``, and deliberately not that function: ``strength`` grades
    a *price* band by comparison tier, dispersion and recency, and a cost
    baseline has none of the three — its observations are purchases of one item
    from whichever vendor sold it, with no tier to be specific about. What it
    does have is a count and an exclusion rate, which is what is read here.

    The exclusion cap mirrors ``strength``'s and is a downgrade only: a cost
    baseline that had to discard a quarter of its own purchases is not
    describing a normal acquisition level, whichever tail the rows fell in.
    """
    observations = cost.observations
    if observations >= th.diagnosis_strong_min_comparables:
        grade = STRONG
    elif observations >= th.diagnosis_moderate_min_comparables:
        grade = MODERATE
    elif observations >= th.diagnosis_weak_min_comparables:
        grade = WEAK
    else:
        grade = INSUFFICIENT
    if cost.exclusion_rate > th.diagnosis_max_exclusion_rate:
        grade = WEAK if _GRADE_RANK[grade] > _GRADE_RANK[WEAK] else grade
    return grade


# ── internals ────────────────────────────────────────────────────────────────

def _margin(unit_price: Decimal, unit_cost: Decimal) -> Decimal:
    """``(P - C) / P``, at a pinned precision.

    The context is pinned rather than inherited so that the same inputs produce
    the same digits under any caller — a rerun that differs is a defect even
    when the parse looks right (CLAUDE.md §1).
    """
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        return (unit_price - unit_cost) / unit_price


def _q(value: Decimal) -> Decimal:
    return value.quantize(PP_QUANTUM, rounding=ROUND_HALF_EVEN)


def _refused(reason: str, basis: str, *,
             movement_pp: Optional[Decimal] = None,
             residual_pp: Optional[Decimal] = None) -> Attribution:
    """The one shape a refusal takes. ``reconciles`` is false on every one of
    them, so a caller reading that field alone is never told a split held.

    ``reason`` is the code and ``basis`` the sentence, kept apart because they
    are read by different things: the code by a caller, the sentence by a
    person. A sentence that carried its own code would make the two one field
    and leave a renderer to split them back.
    """
    return Attribution(movement_pp=movement_pp, drivers=(), reconciles=False,
                       residual_pp=residual_pp, reason=reason, basis=basis)


def _first_unknowable(rows: Sequence[CostObservation], *, knowable_by: datetime,
                      backfill_before: Optional[date],
                      ) -> Optional[tuple[str, str, int]]:
    """The first purchase this quote could not have seen, and how many there are.

    Calls ``evidence.is_knowable`` rather than restating the test, because that
    is the shared decision the whole engine turns on and a second copy of it
    here would be a second answer to "could the quoter have known this". This is
    a *verification*, not a second filter: ``service`` has already excluded such
    rows, and a set that still contains one reached here another way.

    A purchase whose visibility was imputed counts too. ``rules.strength`` caps
    confidence for an estimated stamp on the price side and nothing does so on
    the cost side, and the cost side is the whole of this claim.

    Sorted by id so the row a refusal names is the same row on every run.
    """
    offenders: list[tuple[str, str]] = []
    for row in sorted(rows, key=lambda r: r.evidence_id):
        reason = is_knowable(row.recorded_at, knowable_by=knowable_by,
                             backfill_before=backfill_before)
        if reason is not None:
            offenders.append((row.evidence_id, f"{COST_NOT_KNOWABLE}/{reason}"))
        elif row.recorded_at_imputed:
            offenders.append((row.evidence_id, COST_VISIBILITY_IMPUTED))
    if not offenders:
        return None
    evidence_id, reason = offenders[0]
    return evidence_id, reason, len(offenders)


def _pp_text(value: Decimal) -> str:
    """A percentage-point figure in points, for the basis sentence."""
    return f"{value * 100:.4f} pp"


def _more_or_less(value: Decimal) -> str:
    """``value`` points more margin, or less, or the same. Signed in words
    because "carry -1.2 pp more margin" is a sentence nobody reads correctly."""
    if value == _ZERO:
        return "the same margin"
    direction = "more" if value > _ZERO else "less"
    return f"{_pp_text(abs(value))} {direction} margin"
