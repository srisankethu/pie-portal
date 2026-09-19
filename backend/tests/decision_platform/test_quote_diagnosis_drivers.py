"""Driver attribution: the split of a line's margin movement, and its refusals.

The engine already says a line is below its band and, separately, that cost
moved. These tests are about the step that says *how much of the movement* each
one accounts for — and, more often, about the cases where it declines to say.

Both baselines are built with the real ``baselines`` builders from real evidence
rows rather than hand-assembled. A hand-built ``CostBaseline`` can be given any
pair of ``historical_cost`` and ``expected_cost``, including pairs the trim
would never produce, and a suite that can only speak in the output's vocabulary
agrees with a wrong predicate for as long as it stands — which is the lesson
CLAUDE.md §1 draws from ``_identity_candidate``.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.quantity import band_for, bands
from app.commercial.quote_diagnosis import (baselines, comparables, drivers,
                                            evidence, rules)

TH = CommercialThresholds()

QUOTE_DAY = date(2026, 6, 1)
KNOWABLE_BY = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


# Local builders rather than the ones in ``test_quote_diagnosis``: this file
# needs a purchase with *no* recorded stamp and one with an imputed stamp, and
# that builder can express neither. The price-row builder is local for symmetry
# with it.

def price_row(evidence_id: str, *, price: str, day: date,
              customer: str = "cst_a", qty: str = "10") -> evidence.EvidenceRow:
    return evidence.EvidenceRow(
        evidence_id=evidence_id, source_table="sales_txns",
        customer_id=customer, product_id="prd_1", event_date=day,
        recorded_at=_at(day), evidence_class=evidence.REALIZED,
        qty=Decimal(qty), unit_price=Decimal(price), unit="Nos",
        source_ref={comparables.FAMILY_KEY: "Turning"})


def purchase(evidence_id: str, *, unit_cost: str, day: date,
             recorded: date | None = None, no_stamp: bool = False,
             imputed: bool = False) -> evidence.CostObservation:
    """One purchase. ``recorded`` defaults to the event date, same day."""
    stamp = None if no_stamp else _at(recorded if recorded is not None else day)
    return evidence.CostObservation(
        evidence_id=evidence_id, source_table="cost_records", product_id="prd_1",
        vendor_id="vnd_1", event_date=day, recorded_at=stamp,
        qty=Decimal("10"), unit_cost=Decimal(unit_cost), unit="Nos",
        source_ref={}, recorded_at_imputed=imputed)


def _price_band(price: str = "1000", n: int = 8) -> baselines.PriceBaseline:
    """``n`` invoices at one price, weekly, all inside the recency window."""
    rows = [price_row(f"s{i}", price=price,
                      day=date(2026, 1, 5) + timedelta(days=7 * i))
            for i in range(n)]
    return baselines.price_baseline(rows, as_of=QUOTE_DAY, th=TH)


def _steady_cost(unit_cost: str = "600", n: int = 5,
                 ) -> list[evidence.CostObservation]:
    return [purchase(f"c{i}", unit_cost=unit_cost,
                     day=date(2026, 1, 8) + timedelta(days=21 * i))
            for i in range(n)]


def _risen_cost(old: str = "600", new: str = "700", n: int = 4,
                **kwargs) -> list[evidence.CostObservation]:
    """``n`` purchases at the old level and one, latest, at the new one."""
    rows = _steady_cost(old, n)
    rows.append(purchase("c_new", unit_cost=new,
                         day=date(2026, 4, 20), **kwargs))
    return rows


def _attribute(*, quoted: str | None, price=None, costs=None,
               strength: str = rules.STRONG, th: CommercialThresholds = TH,
               knowable_by: datetime = KNOWABLE_BY) -> drivers.Attribution:
    cost_rows = _steady_cost() if costs is None else list(costs)
    cost = baselines.cost_baseline(cost_rows, as_of=QUOTE_DAY, th=th)
    return drivers.attribute(
        quoted_unit_price=Decimal(quoted) if quoted is not None else None,
        price=price if price is not None else _price_band(),
        cost=cost, cost_rows=cost_rows, strength=strength,
        knowable_by=knowable_by, th=th)


def _of(attribution: drivers.Attribution, code: str) -> drivers.Driver:
    return next(d for d in attribution.drivers if d.code == code)


# ── the headline: both factors moved ─────────────────────────────────────────

def test_a_cost_rise_and_a_price_cut_are_split_not_reported_as_the_larger_one():
    """The defect this module exists to prevent.

    Cost 600 → 700 and price 1000 → 950. Reporting "cost increase" alone is
    true, is the larger term, and excuses the half somebody chose.
    """
    got = _attribute(quoted="950", costs=_risen_cost())

    assert {d.code for d in got.drivers} == drivers.DRIVER_CODES
    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    cost = _of(got, drivers.COST_LEVEL_EFFECT)

    # Percentage points carried as a fraction, this codebase's `_pp` convention:
    # -0.105263 is 10.53 points of margin, not 10.53%.
    assert got.movement_pp == Decimal("-0.136842")
    assert price.effect_pp == Decimal("-0.031579")
    assert cost.effect_pp == Decimal("-0.105263")

    # Both are asserted. Neither is zero, and neither was dropped for being the
    # smaller term.
    assert price.effect_pp < 0 and cost.effect_pp < 0
    assert got.reconciles is True


def test_the_effects_account_for_the_observed_movement():
    """The invariant, asserted on the figures the caller actually receives."""
    got = _attribute(quoted="950", costs=_risen_cost())

    total = sum((d.effect_pp for d in got.drivers), Decimal("0"))
    assert got.movement_pp - total == got.residual_pp
    assert abs(got.residual_pp) <= drivers.RECONCILIATION_TOLERANCE_PP


def test_the_money_split_is_exact_because_it_has_no_division_in_it():
    """Gross profit per unit: 950-700 against 1000-600 is -150, and the two
    effects are -50 of price and -100 of cost. Decimal subtraction, no
    tolerance, and a transposed operand fails here with nowhere to hide."""
    got = _attribute(quoted="950", costs=_risen_cost())

    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    cost = _of(got, drivers.COST_LEVEL_EFFECT)
    assert price.effect_per_unit == Decimal("-50")
    assert cost.effect_per_unit == Decimal("-100")
    assert price.effect_per_unit + cost.effect_per_unit == Decimal("-150")


def test_severity_and_strength_are_not_the_same_answer():
    """A 10.5 point cost effect is MAJOR; a 3.2 point price effect is MINOR.
    Strength is a separate grade on a separate ladder and says how much to
    believe each, which is the distinction a single number would have to lie
    about."""
    got = _attribute(quoted="950", costs=_risen_cost())

    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    cost = _of(got, drivers.COST_LEVEL_EFFECT)
    assert (price.severity, cost.severity) == (drivers.MINOR, drivers.MAJOR)
    assert price.strength == rules.STRONG          # eight tier-1 comparables
    assert cost.strength == rules.MODERATE         # four kept purchases
    assert price.severity not in {rules.STRONG, rules.MODERATE, rules.WEAK}


def test_each_driver_cites_the_rows_it_rests_on():
    """A figure whose evidence cannot be named is not auditable."""
    got = _attribute(quoted="950", costs=_risen_cost())

    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    cost = _of(got, drivers.COST_LEVEL_EFFECT)
    assert price.cited == tuple(f"s{i}" for i in range(8))
    # The purchase the trim removed as an outlier is not cited, and the four it
    # kept are.
    assert cost.cited == ("c0", "c1", "c2", "c3")


def test_the_counterfactual_order_is_named_in_the_output():
    """The decomposition is order-dependent, so a split whose order a reader
    has to guess is one they will read as the other one."""
    got = _attribute(quoted="950", costs=_risen_cost())

    assert drivers.PRICE_THEN_COST in got.basis
    # Contract §3's sentence: a statement about this line, never a forecast.
    assert "At the previous purchase cost this line would carry" in got.basis
    assert "10.5263 pp more margin" in got.basis


# ── the degenerate cases still reconcile ─────────────────────────────────────

def test_only_the_price_moved_and_the_cost_effect_is_a_measured_zero():
    """Reported rather than omitted. Leaving the flat factor out is the mirror
    image of reporting only the larger one: the reader cannot tell whether the
    other side was measured or forgotten."""
    got = _attribute(quoted="950", costs=_steady_cost())

    cost = _of(got, drivers.COST_LEVEL_EFFECT)
    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    assert cost.effect_pp == Decimal("0")
    assert cost.effect_per_unit == Decimal("0")
    assert cost.severity == drivers.NEGLIGIBLE
    assert price.effect_pp == got.movement_pp
    assert got.reconciles is True
    assert got.residual_pp == Decimal("0")


def test_only_the_cost_moved_and_the_price_effect_is_a_measured_zero():
    """Quoted at the band median, so there is no price position to attribute."""
    got = _attribute(quoted="1000", costs=_risen_cost())

    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    cost = _of(got, drivers.COST_LEVEL_EFFECT)
    assert price.effect_pp == Decimal("0")
    assert price.effect_per_unit == Decimal("0")
    assert cost.effect_pp == got.movement_pp
    assert cost.effect_per_unit == Decimal("-100")
    assert got.reconciles is True


def test_nothing_moved_at_all_and_both_effects_are_zero():
    got = _attribute(quoted="1000", costs=_steady_cost())

    assert got.movement_pp == Decimal("0")
    assert [d.effect_pp for d in got.drivers] == [Decimal("0"), Decimal("0")]
    assert got.reconciles is True


def test_a_price_above_the_band_is_attributed_as_readily_as_one_below():
    """Symmetric on purpose. Grading only the losses makes the engine's own good
    news invisible, and a tool whose errors all point one way is not trusted."""
    got = _attribute(quoted="1050", costs=_steady_cost())

    price = _of(got, drivers.PRICE_POSITION_EFFECT)
    assert price.effect_pp == Decimal("0.028571")
    assert price.effect_per_unit == Decimal("50")
    assert price.severity == drivers.MINOR
    assert got.reconciles is True


def test_severity_grades_a_gain_exactly_as_it_grades_a_loss():
    assert drivers.severity(Decimal("0.06"), TH) == drivers.MAJOR
    assert drivers.severity(Decimal("-0.06"), TH) == drivers.MAJOR
    assert drivers.severity(Decimal("0.02"), TH) == drivers.MINOR
    assert drivers.severity(Decimal("-0.02"), TH) == drivers.MINOR
    assert drivers.severity(Decimal("-0.001"), TH) == drivers.NEGLIGIBLE


def test_the_cost_effect_can_never_be_positive_under_this_cost_baseline():
    """Not a rule of this module — a property of ``baselines.cost_baseline``,
    pinned here because the split reads both of its cost fields. It moves
    ``expected_cost`` *up* to a knowable new level and never down, deliberately:
    an unexplained cheap purchase becoming the baseline would diagnose every
    subsequent normal one as cost-driven erosion. So C1 >= C0 always, and a
    falling cost shows up as no cost effect rather than a favourable one. If a
    future change lets the baseline fall, this test is where it surfaces."""
    cheap = _steady_cost("700", 4) + [
        purchase("c_cheap", unit_cost="400", day=date(2026, 4, 20))]
    cost = baselines.cost_baseline(cheap, as_of=QUOTE_DAY, th=TH)

    assert cost.expected_cost == cost.historical_cost == Decimal("700")
    assert cost.unexplained_low_purchase is True

    got = _attribute(quoted="1000", costs=cheap)
    assert _of(got, drivers.COST_LEVEL_EFFECT).effect_pp == Decimal("0")


# ── refusals ─────────────────────────────────────────────────────────────────

def test_no_cost_on_record_refuses_and_names_what_is_missing():
    """Absence of evidence is not a pass. Not a zero cost effect, not the last
    known figure, not the price's own median standing in for it."""
    got = _attribute(quoted="950", costs=[])

    assert got.drivers == ()
    assert got.reconciles is False
    assert got.movement_pp is None          # no cost, so no margin to have moved
    assert got.residual_pp is None
    assert drivers.NO_COST_BASELINE in got.basis
    assert "no purchase was knowable for this item" in got.basis


def test_a_single_purchase_is_not_a_cost_baseline_either():
    """One observation gives ``expected_cost`` a value and no belief at all."""
    got = _attribute(quoted="950", costs=_steady_cost("600", 1))

    assert got.drivers == ()
    assert drivers.EVIDENCE_TOO_THIN in got.basis
    assert rules.INSUFFICIENT in got.basis


def test_a_thin_band_asserts_nothing_however_large_the_money():
    """A 20-point movement on a WEAK band. The gate is not the surfacing gate —
    that governs interruption. This one governs whether there is a reference to
    measure against at all."""
    got = _attribute(quoted="500", costs=_steady_cost(), strength=rules.WEAK)

    assert got.drivers == ()
    assert got.reconciles is False
    assert got.movement_pp is None
    assert drivers.EVIDENCE_TOO_THIN in got.basis
    assert rules.WEAK in got.basis


def test_a_thin_cost_baseline_refuses_even_behind_a_strong_band():
    """Both sides gate, because both are inside the movement itself: C0 and C1
    sit in m(P0,C0) and m(P1,C1), so an unbelievable cost level does not merely
    weaken one driver, it makes the number being split wrong."""
    got = _attribute(quoted="950", costs=_steady_cost("600", 3),
                     strength=rules.STRONG)

    assert got.drivers == ()
    assert drivers.EVIDENCE_TOO_THIN in got.basis
    assert rules.WEAK in got.basis


def test_a_line_with_no_quoted_price_has_no_movement_to_explain():
    got = _attribute(quoted=None, costs=_risen_cost())

    assert got.drivers == ()
    assert drivers.NO_QUOTED_PRICE in got.basis


def test_no_comparable_price_history_leaves_no_reference_price():
    got = _attribute(quoted="950", costs=_risen_cost(),
                     price=baselines.EMPTY_PRICE_BASELINE)

    assert got.drivers == ()
    assert drivers.NO_PRICE_BASELINE in got.basis


# ── point-in-time correctness ────────────────────────────────────────────────

def test_a_bill_entered_after_the_quote_refuses_rather_than_moving_the_cost():
    """The real defect class. A purchase dated 20 April and keyed in on 22 June
    is not evidence the quoter had, and here it is the row that would move
    ``expected_cost`` to the new level and manufacture a cost driver."""
    late = _risen_cost(recorded=date(2026, 6, 22))
    got = _attribute(quoted="950", costs=late)

    assert got.drivers == ()
    assert drivers.COST_NOT_KNOWABLE in got.basis
    assert evidence.RECORDED_AFTER in got.basis
    assert "c_new" in got.basis


def test_the_refusal_names_a_reason_rather_than_answering_a_bool():
    """``is_knowable`` returns *why*, and the three reasons call for three
    different fixes: a connector gap, a migration artefact, and the engine
    working. A refusal that flattened them would send somebody to the wrong one."""
    no_stamp = _risen_cost(no_stamp=True)
    got = _attribute(quoted="950", costs=no_stamp)

    assert got.drivers == ()
    assert evidence.NO_RECORDED_AT in got.basis


def test_a_purchase_whose_visibility_was_estimated_is_refused_too():
    """``rules.strength`` pays for an imputed stamp on the price side and
    nothing pays for one on the cost side — and the cost side is the whole of
    this claim."""
    got = _attribute(quoted="950", costs=_risen_cost(imputed=True))

    assert got.drivers == ()
    assert drivers.COST_VISIBILITY_IMPUTED in got.basis
    assert "c_new" in got.basis


def test_the_same_evidence_before_the_quote_is_used_normally():
    """The counterpart, so the test above is not passing because everything is
    refused. The same 700 purchase, recorded the day it happened."""
    got = _attribute(quoted="950", costs=_risen_cost())

    assert _of(got, drivers.COST_LEVEL_EFFECT).effect_per_unit == Decimal("-100")


# ── the residual ─────────────────────────────────────────────────────────────

def test_two_effects_measured_on_different_orders_do_not_reconcile():
    """The residual case, constructed where it can genuinely occur.

    ``attribute`` measures both effects on one order, so the only way to build a
    real interaction term is to mix them — which is exactly the bug the check
    exists for, and exactly what deriving the second effect by subtracting the
    first from the movement would have hidden.

    P0=1000, P1=950, C0=600, C1=700. Price effect taken at the *old* cost and
    cost effect taken at the *old* price is a pair no order produces.
    """
    def m(p: str, c: str) -> Decimal:
        return (Decimal(p) - Decimal(c)) / Decimal(p)

    movement = m("950", "700") - m("1000", "600")
    price_at_old_cost = m("950", "600") - m("1000", "600")   # order A
    cost_at_old_price = m("1000", "700") - m("1000", "600")  # order B

    ok, residual = drivers.reconcile(
        movement, (price_at_old_cost, cost_at_old_price))

    assert ok is False
    # The interaction term: five parts in a thousand of margin, a quarter of a
    # percentage point, and about 2,600 times the tolerance.
    assert residual == pytest.approx(Decimal("-0.00526315789"), abs=1e-9)
    assert abs(residual) > drivers.RECONCILIATION_TOLERANCE_PP * 1000


def test_the_consistent_pair_for_that_same_line_does_reconcile():
    """So the test above is measuring the order and not the line."""
    got = _attribute(quoted="950", costs=_risen_cost())

    assert got.reconciles is True
    assert abs(got.residual_pp) <= drivers.RECONCILIATION_TOLERANCE_PP


def test_the_residual_is_reported_rather_than_forced_to_zero():
    """It is a field on every asserted attribution, including the ones where it
    is zero, so a reader never has to infer whether it was checked."""
    got = _attribute(quoted="950", costs=_risen_cost())

    assert got.residual_pp is not None
    assert dataclasses.fields(drivers.Attribution)[3].name == "residual_pp"


def test_the_tolerance_is_three_roundings_wide_and_not_a_convenient_band():
    """Two quanta. Three figures are rounded independently and ROUND_HALF_EVEN
    puts at most half a quantum on each, so one and a half is the arithmetic
    worst case."""
    assert drivers.RECONCILIATION_TOLERANCE_PP == 2 * drivers.PP_QUANTUM
    assert drivers.PP_QUANTUM == Decimal("0.000001")
    # Finer than anything rendered, and far finer than any policy threshold.
    assert drivers.RECONCILIATION_TOLERANCE_PP < Decimal(
        str(TH.diagnosis_driver_minor_pp)) / 1000


# ── policy, versioning and determinism ───────────────────────────────────────

def test_severity_boundaries_are_versioned_policy_not_module_constants():
    """Move the boundary and the grade moves with it — and the thresholds
    version moves too, so a stored grade still says which policy judged it."""
    strict = dataclasses.replace(TH, diagnosis_driver_major_pp=0.02)

    default = _of(_attribute(quoted="950", costs=_risen_cost()),
                  drivers.PRICE_POSITION_EFFECT)
    moved = _of(_attribute(quoted="950", costs=_risen_cost(), th=strict),
                drivers.PRICE_POSITION_EFFECT)

    assert default.severity == drivers.MINOR
    assert moved.severity == drivers.MAJOR
    assert strict.version != TH.version
    assert not [n for n in vars(drivers)
                if n.endswith("_PP") and n not in
                {"PP_QUANTUM", "RECONCILIATION_TOLERANCE_PP"}]


def test_the_same_inputs_produce_the_same_bytes_under_any_decimal_context():
    """The margin divisions run at a pinned precision. A caller that had lowered
    ``getcontext().prec`` would otherwise change what this module computes, and
    a rerun that differs is a defect even when the answer looks right."""
    first = _attribute(quoted="950", costs=_risen_cost())
    with localcontext() as ctx:
        ctx.prec = 6
        second = _attribute(quoted="950", costs=_risen_cost())

    assert first == second


# ── the vocabulary, and who may see it ───────────────────────────────────────

def test_every_driver_code_extends_a_code_the_engine_already_draws():
    """Extends rather than replaces, checked rather than claimed. A driver is a
    magnitude for a finding ``rules`` already makes; it never reaches a
    conclusion the codes do not."""
    assert set(drivers.EXTENDS) == drivers.DRIVER_CODES
    engine_codes = {
        rules.WITHIN_HISTORICAL_RANGE, rules.BELOW_HISTORICAL_RANGE,
        rules.ABOVE_HISTORICAL_RANGE, rules.BELOW_PEER_BAND_STRUCTURAL,
        rules.COST_DRIVEN_MARGIN_RISK, rules.KNOWN_COST_CHANGE,
        rules.INSUFFICIENT_EVIDENCE, rules.NO_COST_EVIDENCE,
    }
    for targets in drivers.EXTENDS.values():
        assert targets and set(targets) <= engine_codes


def test_a_driver_strength_is_a_rules_grade_and_not_a_new_scale():
    got = _attribute(quoted="950", costs=_risen_cost())

    ladder = {rules.STRONG, rules.MODERATE, rules.WEAK, rules.INSUFFICIENT}
    assert {d.strength for d in got.drivers} <= ladder


def test_attribution_is_owner_only_and_the_desk_type_has_no_field_for_it():
    """Cost-derived throughout. ``OperationsDiagnosis`` has no cost field so a
    leak is impossible rather than avoided, and a driver field on it would undo
    exactly that."""
    ops = rules.operations_field_names()

    assert not [f for f in ops
                if "driver" in f or "attribution" in f or "effect" in f]
    assert not (ops & rules.FORBIDDEN_OPERATIONS_FIELDS)
    # And nothing in a driver is renderable to the desk without a decision:
    # every code this module emits is outside the operations allowlist.
    assert not (drivers.DRIVER_CODES & rules.OPERATIONS_CODES)


# ── the strength input is the engine's own grade ─────────────────────────────

def test_the_band_grade_comes_from_the_engine_rather_than_being_recomputed():
    """``attribute`` takes the grade ``rules.strength`` produced. A second
    grader for a price band would disagree with the first on the rows nobody
    looks at — and the whole point of the diagnosis engine is that one band has
    one grade."""
    rows = [price_row(f"s{i}", price="1000",
                      day=date(2026, 1, 5) + timedelta(days=7 * i))
            for i in range(8)]
    subject = comparables.Subject(
        customer_id="cst_a", product_id="prd_1", family="Turning",
        qty=Decimal("10"), band=band_for(Decimal("10"), TH), unit="each",
        as_of=QUOTE_DAY)
    axis = comparables.select_customer_axis(rows, subject=subject,
                                            bands=bands(TH))
    price = baselines.price_baseline(rows, as_of=QUOTE_DAY, th=TH)
    grade = rules.strength(axis, price, as_of=QUOTE_DAY, th=TH)
    cost_rows = _risen_cost()
    cost = baselines.cost_baseline(cost_rows, as_of=QUOTE_DAY, th=TH)

    got = drivers.attribute(
        quoted_unit_price=Decimal("950"), price=price, cost=cost,
        cost_rows=cost_rows, strength=grade, knowable_by=KNOWABLE_BY, th=TH)

    assert grade == rules.STRONG
    assert _of(got, drivers.PRICE_POSITION_EFFECT).strength == grade
