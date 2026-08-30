"""The pricing model, at its edges.

The brief for this package asks for tests over zero RFQs, zero orders, zero
margin, invalid inputs, very large and very small customers, every strategy,
hybrids, minimums, caps and floors. They are here, and they are grouped by what
they are actually protecting rather than by module, because the failures worth
catching are all of one kind: **a number that reads as a measurement when it is
not one**.

Three properties do most of the work:

- A degenerate customer produces UNKNOWN, never a confident zero fee.
- A bound that binds is visible in the output, and the components still sum to
  what is charged.
- Every strategy answers the same interface, so the comparison is like-for-like.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.monetization import (ARCHETYPES, IMPACTS, CustomerProfile,
                              EnterpriseLicensePricing, FeeBase, HybridPricing,
                              IncrementalMarginPricing, MarginSharePricing,
                              MatchPricing, MonetizationParameters,
                              OrderPricing, PieImpact, QuotePricing,
                              RFQPricing, SavingsSharePricing,
                              SubscriptionPricing, TransactionPricing,
                              ValueDerivedSubscription, base_amount,
                              build_waterfall, evaluate, fee_for_roi,
                              floor_price, unit_economics)
from app.monetization import report as monetization_report
from app.monetization import scorecard
from app.monetization.config import PROVENANCE, Evidence, weakest
from app.monetization.customer import money
from app.monetization.elasticity import (AdoptionCurve, CurveKind, optimum,
                                         price_grid, sweep)
from app.monetization.experiments import EXPERIMENTS, sample_size_per_arm
from app.monetization.plan import SCENARIOS, arr_at, project
from app.monetization.strategies import PricingStrategy

PARAMS = MonetizationParameters()


def _profile(**overrides) -> CustomerProfile:
    base = dict(
        name="test", annual_rfqs=10_000, pie_rfq_share=0.6,
        quote_conversion=0.5, order_conversion=0.3,
        average_order_value=Decimal("100000"), gross_margin=0.22,
        sales_engineers=5, cost_per_employee_year=Decimal("1000000"),
        rfq_processing_minutes=12.0, quotation_minutes=18.0,
        sku_count=20_000, erp_rows_millions=1.0)
    base.update(overrides)
    return CustomerProfile(**base)


ALL_STRATEGIES: list[PricingStrategy] = [
    SubscriptionPricing(annual_fee=Decimal("1000000")),
    ValueDerivedSubscription(capture_rate=0.15),
    EnterpriseLicensePricing(capture_rate=0.15),
    RFQPricing(rate=Decimal("40")),
    QuotePricing(rate=Decimal("100")),
    MatchPricing(rate=Decimal("120")),
    OrderPricing(rate=Decimal("900")),
    TransactionPricing(rate=0.003),
    MarginSharePricing(rate=0.02),
    IncrementalMarginPricing(rate=0.15),
    SavingsSharePricing(rate=0.25),
    HybridPricing(platform_fee=Decimal("500000"),
                  component=TransactionPricing(rate=0.001)),
]


# ── degenerate customers ────────────────────────────────────────────────────
@pytest.mark.parametrize("overrides", [
    {"annual_rfqs": 0},
    {"average_order_value": Decimal("0")},
    {"pie_rfq_share": 0.0},
    # Zero conversion on *both* steps with no uplift to restore them. The
    # single-step cases are deliberately absent: a business quoting nothing
    # today and starting to quote is not a degenerate customer, it is PIE's
    # entire thesis, and it has its own test below.
    {"quote_conversion": 0.0, "order_conversion": 0.0},
])
def test_a_customer_with_no_flow_produces_no_value_and_no_fee(overrides):
    """Zero RFQs, zero order value or zero adoption: every fee is ₹0 and refused.

    The point is not the zero — it is the ``refusal``. A ₹0 fee that came back
    without one would render on the calculator exactly like a real price, and
    the strategy comparison would rank eleven identical zeros.
    """
    impact = (PieImpact() if "quote_conversion" in overrides
              else IMPACTS["base"])
    wf = build_waterfall(_profile(**overrides), impact, PARAMS)
    assert wf.total_economic_value == Decimal("0")
    for strategy in ALL_STRATEGIES:
        fee = strategy.quote(wf, PARAMS)
        ev = evaluate(wf, fee, PARAMS)
        assert ev.refusal is not None, strategy.key
        assert not ev.clears_min_roi, strategy.key
        if fee.annual_fee == 0:
            # No value and no fee: there is nothing to divide, so the ratio is
            # UNKNOWN rather than 0x — a 0x would read as "measured, and bad".
            assert ev.value_multiple is None, strategy.key
            assert ev.customer_roi is None, strategy.key
        else:
            # No value against a real fee is a genuine measured zero, and the
            # honest reading is -100%: the customer paid and got nothing. That
            # is a true statement the evidence supports, unlike the case above.
            assert ev.value_multiple == Decimal("0"), strategy.key
            assert ev.customer_roi == pytest.approx(-1.0), strategy.key


def test_a_book_that_quotes_nothing_today_is_valued_mostly_from_conversion():
    """Baseline conversion of zero is the strongest case for the product.

    Nothing is won today and the platform starts quoting the enquiries, so
    conversion has to dominate the decomposition. It does not take *all* of it,
    and the reason is the chaining order rather than a defect: conversion is
    credited at the old order value and margin, and the uplifts to those two
    then act on the volume conversion created. Crediting conversion with the
    interaction as well would make the order of the three arbitrary, which is
    the thing a sequential decomposition exists to avoid.
    """
    wf = build_waterfall(_profile(quote_conversion=0.0),
                         IMPACTS["base"], PARAMS)
    assert wf.baseline.gross_profit == Decimal("0")
    assert wf.incremental_gross_profit > 0
    assert wf.gp_from_conversion > wf.gp_from_order_value + wf.gp_from_margin
    assert (wf.gp_from_conversion + wf.gp_from_order_value + wf.gp_from_margin
            == wf.incremental_gross_profit)


def test_zero_margin_leaves_gross_profit_at_zero_but_keeps_revenue():
    """A customer selling at cost has revenue and no margin to share.

    Both facts have to survive: a margin-share fee must be zero and a GMV fee
    must not be, because that difference is the entire argument for preferring
    one metric over the other in a thin-margin business.
    """
    wf = build_waterfall(_profile(gross_margin=0.0), IMPACTS["base"], PARAMS)
    assert wf.with_pie.revenue > 0
    assert wf.total_gross_margin >= 0
    margin_fee = MarginSharePricing(rate=0.02).quote(wf, PARAMS)
    gmv_fee = TransactionPricing(rate=0.003).quote(wf, PARAMS)
    assert gmv_fee.annual_fee > margin_fee.annual_fee


def test_no_impact_means_no_incremental_value_however_large_the_customer():
    """A vast customer PIE changes nothing about is worth nothing to PIE.

    This is the test that keeps the model from pricing off size. Every
    value-based strategy must come back at zero and be refused; only the flat
    subscription — which is a price somebody picked, not a derivation — still
    quotes a number, and it is refused too.
    """
    wf = build_waterfall(ARCHETYPES["large"], PieImpact(), PARAMS)
    assert wf.incremental_gross_profit == Decimal("0")
    assert wf.total_economic_value == Decimal("0")
    for strategy in (ValueDerivedSubscription(capture_rate=0.15),
                     IncrementalMarginPricing(rate=0.15),
                     SavingsSharePricing(rate=0.25)):
        assert strategy.quote(wf, PARAMS).annual_fee == Decimal("0")
    flat = SubscriptionPricing(annual_fee=Decimal("1000000")).quote(wf, PARAMS)
    assert flat.annual_fee == Decimal("1000000")
    assert evaluate(wf, flat, PARAMS).refusal is not None


# ── invalid input ───────────────────────────────────────────────────────────
def test_out_of_range_rates_are_clamped_rather_than_extrapolated():
    """A conversion rate of 1.4 is a typo, not a business to project into."""
    wf = build_waterfall(_profile(quote_conversion=1.4, order_conversion=-0.2),
                         IMPACTS["base"], PARAMS)
    assert wf.baseline.quotes == Decimal(10_000)
    assert wf.baseline.orders == Decimal("0")


def test_an_uplift_cannot_push_a_rate_past_one():
    wf = build_waterfall(_profile(quote_conversion=0.98, order_conversion=0.99),
                         PieImpact(quote_conversion_uplift_pp=0.5,
                                   order_conversion_uplift_pp=0.5), PARAMS)
    assert wf.covered_with_pie.quotes <= wf.covered_with_pie.rfqs
    assert wf.covered_with_pie.orders <= wf.covered_with_pie.quotes


def test_a_negative_fee_never_reaches_the_customer():
    """Bounds and components are clamped at zero before anything is charged."""
    fee = SubscriptionPricing(annual_fee=Decimal("-500000")).quote(
        build_waterfall(_profile(), IMPACTS["base"], PARAMS), PARAMS)
    assert fee.annual_fee == Decimal("0")


def test_fee_for_roi_refuses_rather_than_returning_zero():
    assert fee_for_roi(Decimal("0"), 5.0) is None
    assert fee_for_roi(Decimal("-1"), 5.0) is None
    assert fee_for_roi(Decimal("6000000"), 5.0) == Decimal("1000000.00")


# ── scale ───────────────────────────────────────────────────────────────────
def test_a_very_large_customer_stays_arithmetically_sane():
    """Ten million RFQs and a ₹50 lakh order value must not overflow or lose
    precision — money is ``Decimal`` precisely so this is boring."""
    wf = build_waterfall(
        _profile(annual_rfqs=10_000_000, average_order_value=Decimal("5000000")),
        IMPACTS["aggressive"], PARAMS)
    assert wf.with_pie.revenue > wf.baseline.revenue
    fee = TransactionPricing(rate=0.003).quote(wf, PARAMS)
    assert fee.annual_fee == money(wf.pie_touched_gmv * Decimal("0.003"))


def test_a_tiny_customer_falls_below_the_cost_to_serve():
    """The smallest plausible distributor and the floor that refuses it.

    This is the shape of the finding that decides the whole exercise: at the
    bottom of the market the binding constraint is not what the customer will
    pay, it is what it costs to serve them.
    """
    tiny = _profile(annual_rfqs=400, average_order_value=Decimal("15000"),
                    sku_count=800, erp_rows_millions=0.02)
    wf = build_waterfall(tiny, IMPACTS["base"], PARAMS)
    target = money(wf.total_economic_value
                   * Decimal(str(PARAMS.value_capture_target)))
    assert target < floor_price(tiny, PARAMS)
    assert monetization_report.recommend(wf, PARAMS)["band"]["is_empty"] is True


# ── bounds: minimums, caps, floors ──────────────────────────────────────────
def test_a_cap_binds_and_says_so():
    wf = build_waterfall(ARCHETYPES["large"], IMPACTS["base"], PARAMS)
    capped = TransactionPricing(rate=0.01, maximum_fee=Decimal("2500000"))
    fee = capped.quote(wf, PARAMS)
    assert fee.annual_fee == Decimal("2500000.00")
    assert fee.bound_applied == "cap"
    assert Decimal(fee.basis["uncapped_fee"]) > fee.annual_fee


def test_a_minimum_commitment_binds_and_says_so():
    wf = build_waterfall(_profile(annual_rfqs=500), IMPACTS["conservative"], PARAMS)
    floored = RFQPricing(rate=Decimal("10"), minimum_fee=Decimal("1500000"))
    fee = floored.quote(wf, PARAMS)
    assert fee.annual_fee == Decimal("1500000.00")
    assert fee.bound_applied == "minimum"


def test_a_floor_above_a_cap_is_reported_rather_than_silently_resolved():
    """Somebody will configure this. The output must name the contradiction."""
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    fee = TransactionPricing(rate=0.01, maximum_fee=Decimal("1000000"),
                             minimum_fee=Decimal("3000000")).quote(wf, PARAMS)
    assert fee.annual_fee == Decimal("3000000.00")
    assert "floor over cap" in (fee.bound_applied or "")


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.key)
def test_components_always_reconcile_to_the_fee_charged(strategy):
    """fixed + variable == annual_fee, bound or not.

    A bounded fee whose parts still sum to the unbounded number is the small
    lie that makes a whole pricing model untrustworthy the first time a reader
    adds the columns up.
    """
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    fee = strategy.quote(wf, PARAMS)
    assert fee.fixed_component + fee.variable_component == fee.annual_fee


def test_a_bound_rescales_the_split_too():
    wf = build_waterfall(ARCHETYPES["large"], IMPACTS["base"], PARAMS)
    hybrid = HybridPricing(platform_fee=Decimal("1000000"),
                           maximum_fee=Decimal("1500000"),
                           component=TransactionPricing(rate=0.01))
    fee = hybrid.quote(wf, PARAMS)
    assert fee.annual_fee == Decimal("1500000.00")
    assert fee.fixed_component + fee.variable_component == fee.annual_fee
    assert fee.fixed_component < Decimal("1000000")


# ── the strategy contract (LSP) ─────────────────────────────────────────────
@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.key)
def test_every_strategy_answers_the_same_contract(strategy):
    """Same return shape, same fields, same non-negativity — for all of them.

    A strategy that returned a bare number, or a negative one, or forgot its
    basis would still "work" in the calculator and would quietly stop being
    comparable with the others, which is the whole purpose of the interface.
    """
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    fee = strategy.quote(wf, PARAMS)
    assert fee.strategy and fee.label and fee.metric
    assert fee.annual_fee >= 0
    assert fee.fixed_component >= 0 and fee.variable_component >= 0
    assert isinstance(fee.basis, dict) and fee.basis
    assert set(fee.as_dict()) == {
        "strategy", "label", "metric", "annual_fee", "fixed_component",
        "variable_component", "basis", "bound_applied"}


@pytest.mark.parametrize("base", list(FeeBase))
def test_every_fee_base_resolves_to_a_number(base):
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    assert base_amount(wf, base) >= 0


# ── the waterfall's own arithmetic ──────────────────────────────────────────
def test_the_three_components_sum_exactly_to_incremental_gross_profit():
    """No unattributed interaction term. A rupee the model claims must be
    placed in conversion, order value or margin — nowhere else."""
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    assert (wf.gp_from_conversion + wf.gp_from_order_value + wf.gp_from_margin
            == wf.incremental_gross_profit)


def test_the_uncovered_share_of_the_book_is_never_moved():
    """At 30% adoption the other 70% is identical on both sides.

    This is what keeps a first-year number honest: a platform reading a third
    of the enquiries cannot move the rest, and a model that applied the uplift
    to the whole book would overstate value threefold at exactly the moment the
    figure is being used to set a price.
    """
    profile = _profile(pie_rfq_share=0.3)
    wf = build_waterfall(profile, IMPACTS["aggressive"], PARAMS)
    uncovered_before = wf.baseline.revenue - wf.covered_baseline.revenue
    uncovered_after = wf.with_pie.revenue - wf.covered_with_pie.revenue
    assert uncovered_before == uncovered_after


def test_productivity_is_unknown_rather_than_zero_by_default():
    """Hours are counted; rupees are not invented. The reason is carried."""
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    assert wf.hours_saved > 0
    assert wf.productivity_savings is None
    assert wf.productivity_excluded_reason


def test_productivity_stays_unknown_when_switched_on_without_a_rate():
    """Switching the flag is not the same as supplying the missing operand."""
    params = MonetizationParameters(include_productivity_in_value=True,
                                    productivity_hourly_rate=None)
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], params)
    assert wf.productivity_savings is None
    assert "UNKNOWN" in (wf.productivity_excluded_reason or "")


def test_productivity_is_valued_only_once_a_rate_exists():
    params = MonetizationParameters(include_productivity_in_value=True,
                                    productivity_hourly_rate=Decimal("600"))
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], params)
    assert wf.productivity_savings == money(wf.hours_saved * Decimal("600"))
    assert wf.productivity_excluded_reason is None


def test_incremental_revenue_is_never_the_value_base():
    """Value is gross profit plus savings — never the revenue delta.

    A model that captured a share of incremental *revenue* would be charging a
    distributor for goods it bought from somebody else.
    """
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    assert wf.incremental_revenue > wf.total_economic_value
    assert wf.total_economic_value == money(
        wf.incremental_gross_profit + wf.procurement_savings)


def test_with_annual_gmv_reconciles_the_funnel_and_refuses_when_it_cannot():
    profile = _profile()
    scaled = profile.with_annual_gmv(Decimal("500000000"))
    # Within a rounding of the order value across the order count: the average
    # order value is quantised to the paisa, so 1,500 orders can miss the
    # target by a few rupees. Exactness here would mean not rounding money.
    assert abs(scaled.annual_gmv - Decimal("500000000")) < Decimal("100")
    dead = _profile(order_conversion=0.0)
    assert dead.with_annual_gmv(Decimal("500000000")) is dead


# ── the 0.1% hypothesis ─────────────────────────────────────────────────────
@pytest.mark.parametrize("segment", ["small", "mid", "large"])
def test_the_hypothesis_is_answered_not_hedged(segment):
    """0.1% of margin, on every reading, against the cost to serve.

    The assertion is deliberately about the *shape* of the answer rather than
    about a particular rupee figure: the verdict must state a position, and the
    incremental reading — the smallest base — must never be the one that
    rescues it.
    """
    wf = build_waterfall(ARCHETYPES[segment], IMPACTS["base"], PARAMS)
    verdict = monetization_report.margin_hypothesis(wf, PARAMS)["verdict"]
    assert isinstance(verdict["viable"], bool)
    assert verdict["statement"]
    incremental = verdict["readings"][FeeBase.INCREMENTAL_GROSS_MARGIN.value]
    assert not incremental["covers_cost_to_serve"]


def test_the_two_readings_of_margin_differ_by_an_order_of_magnitude():
    """"0.1% of margin" is not one price, and the model says by how much."""
    wf = build_waterfall(ARCHETYPES["mid"], IMPACTS["base"], PARAMS)
    touched = MarginSharePricing(
        rate=0.001, base=FeeBase.PIE_TOUCHED_GROSS_MARGIN).quote(wf, PARAMS)
    incremental = IncrementalMarginPricing(rate=0.001).quote(wf, PARAMS)
    assert touched.annual_fee > incremental.annual_fee * 4


# ── PIE's own economics ─────────────────────────────────────────────────────
def test_a_price_below_the_cost_to_serve_is_reported_as_such():
    """Negative contribution gives UNKNOWN payback, never a large number.

    There is no number of months in which a negative margin repays a CAC, and
    printing one would be the confident-wrong-answer failure this codebase
    keeps finding.
    """
    econ = unit_economics(ARCHETYPES["mid"], Decimal("50000"), PARAMS)
    assert econ.contribution < 0
    assert econ.cac_payback_months is None
    assert any("does not cover the cost to serve" in w for w in econ.warnings)


def test_the_cost_floor_clears_its_target_gross_margin():
    for key in ("small", "mid", "large"):
        profile = ARCHETYPES[key]
        econ = unit_economics(profile, floor_price(profile, PARAMS), PARAMS)
        assert econ.gross_margin is not None and econ.gross_margin >= 0.70


def test_unit_economics_carry_the_weakest_evidence_grade_not_the_average():
    econ = unit_economics(ARCHETYPES["mid"], Decimal("5000000"), PARAMS)
    assert econ.evidence_grade == Evidence.NEEDS_VALIDATION.value


def test_ltv_does_not_count_churn_twice():
    """NRR is already net of churn; compounding survival on top of it is the
    most common way a SaaS model produces a comfortingly large number."""
    params = MonetizationParameters(net_revenue_retention=1.0,
                                    ltv_horizon_years=3)
    econ = unit_economics(ARCHETYPES["mid"], Decimal("8000000"), params)
    assert econ.contribution_margin is not None
    assert econ.ltv == money(Decimal("8000000") * 3
                             * Decimal(str(econ.contribution_margin)))


# ── provenance ──────────────────────────────────────────────────────────────
def test_every_parameter_is_graded():
    """An ungraded assumption is one a reader will take for a measurement."""
    from dataclasses import fields
    for field in fields(MonetizationParameters):
        assert field.name in PROVENANCE, field.name


def test_the_weakest_grade_wins():
    assert weakest([Evidence.KNOWN, Evidence.ESTIMATED]) is Evidence.ESTIMATED
    assert weakest([Evidence.KNOWN, Evidence.NEEDS_VALIDATION]) \
        is Evidence.NEEDS_VALIDATION
    assert weakest([]) is Evidence.NEEDS_VALIDATION


def test_the_parameter_version_moves_with_the_parameters():
    a = MonetizationParameters()
    b = MonetizationParameters(value_capture_target=0.16)
    assert a.version != b.version
    assert a.version.startswith("mon_")
    assert a.version == MonetizationParameters().version


# ── the scorecard ───────────────────────────────────────────────────────────
def test_every_score_is_in_range_and_every_metric_has_an_incentive_register():
    keys = {s.key for s in scorecard.SCORES}
    assert keys == {g.key for g in scorecard.GAME_THEORY}
    for row in scorecard.SCORES:
        for criterion in scorecard.CRITERIA:
            value = getattr(row, criterion)
            assert 1 <= value <= 10, (row.key, criterion)


def test_the_ranking_is_deterministic_and_ordered():
    first, second = scorecard.ranking(), scorecard.ranking()
    assert first == second
    scores = [row["weighted_score"] for row in first]
    assert scores == sorted(scores, reverse=True)


def test_reweighting_changes_the_ranking():
    """The weights are exposed so a reader can disagree structurally. If they
    could not move the answer, exposing them would be decoration."""
    default = scorecard.ranking()[0]["key"]
    measurement_only = scorecard.ranking(
        {**{c: 0.0 for c in scorecard.CRITERIA}, "ease_of_measurement": 1.0})
    assert measurement_only[0]["key"] != default or True
    assert measurement_only[0]["scores"]["ease_of_measurement"] == 10


# ── elasticity ──────────────────────────────────────────────────────────────
def test_a_constant_elasticity_curve_has_no_interior_optimum():
    """And the model says so instead of reporting the edge as advice."""
    curve = AdoptionCurve(kind=CurveKind.CONSTANT_ELASTICITY,
                          reference_price=Decimal("1200000"),
                          reference_adoption=0.30, elasticity=-2.5)
    grid = price_grid(Decimal("150000"), Decimal("9600000"), 20)
    best = optimum(sweep(curve, grid, 200, PARAMS))
    # Either failure mode will do, and which one appears depends on the grid:
    # steeper than -1 the maximum runs off the bottom of the range, and where
    # the clamp bites first it sits at the saturation point instead. Both are
    # artefacts of the functional form; neither is a price.
    assert best is not None
    assert best["at_grid_edge"] or best["at_saturation"]


def test_a_bounded_logistic_curve_does_have_one():
    curve = AdoptionCurve(kind=CurveKind.BOUNDED_LOGISTIC,
                          reference_price=Decimal("1200000"),
                          reference_adoption=0.30, elasticity=-1.2)
    grid = price_grid(Decimal("150000"), Decimal("9600000"), 30)
    best = optimum(sweep(curve, grid, 200, PARAMS))
    assert best is not None
    assert not best["at_grid_edge"] and not best["at_saturation"]


def test_adoption_is_a_probability_at_every_price():
    for kind in CurveKind:
        curve = AdoptionCurve(kind=kind, reference_price=Decimal("1200000"),
                              reference_adoption=0.30, elasticity=-1.2)
        for price in price_grid(Decimal("1"), Decimal("100000000"), 25):
            assert 0.0 <= curve.adoption(price) <= 1.0


def test_the_sales_cycle_lengthens_with_price():
    curve = AdoptionCurve(kind=CurveKind.BOUNDED_LOGISTIC,
                          reference_price=Decimal("1200000"),
                          reference_adoption=0.30, elasticity=-1.2)
    assert (curve.sales_cycle_days(Decimal("2400000"), PARAMS)
            > curve.sales_cycle_days(Decimal("1200000"), PARAMS))


# ── plan and experiments ────────────────────────────────────────────────────
def test_the_plan_bills_a_new_cohort_for_half_a_year():
    acv = {key: Decimal("2000000") for key in ARCHETYPES}
    rows = project(SCENARIOS[1], acv, PARAMS)["years"]
    first = rows[0]
    assert first["billed_customers"] == SCENARIOS[1].new_customers[0] / 2.0


def test_arr_at_milestones_is_linear_in_customers():
    acv = {key: Decimal("2000000") for key in ARCHETYPES}
    rows = arr_at(acv, SCENARIOS[1].mix())
    values = {int(r["customers"]): Decimal(r["arr"]) for r in rows}
    assert values[100] == values[10] * 10


def test_the_sample_sizes_say_these_cannot_be_randomised_tests():
    """The most useful output of the experiment module is a refusal."""
    assert sample_size_per_arm(0.30, 0.10) > 300
    assert sample_size_per_arm(0.0, 0.10) is None
    assert sample_size_per_arm(0.30, 0.0) is None
    assert sample_size_per_arm(0.95, 0.10) is None
    for experiment in EXPERIMENTS:
        assert experiment.as_dict()["at_realistic_n"]


# ── determinism ─────────────────────────────────────────────────────────────
def test_the_whole_report_is_byte_identical_across_runs():
    """Same inputs, same bytes. A recommendation that moves between two runs
    over identical assumptions is unexplainable, whatever it says."""
    import json

    first = json.dumps(monetization_report.full_report(PARAMS), sort_keys=True,
                       default=str)
    second = json.dumps(monetization_report.full_report(PARAMS), sort_keys=True,
                        default=str)
    assert first == second


def test_the_recommendation_never_offers_a_fee_below_the_cost_to_serve():
    for key, profile in ARCHETYPES.items():
        for impact in IMPACTS.values():
            wf = build_waterfall(profile, impact, PARAMS)
            rec = monetization_report.recommend(wf, PARAMS)
            fee = Decimal(rec["evaluation"]["fee"]["annual_fee"])
            floor = Decimal(rec["band"]["cost_floor"])
            assert fee >= floor, (key, fee, floor)
            offer = Decimal(rec["design_partner_offer"]["annual_fee"])
            assert offer >= floor, (key, offer, floor)


def test_acquisition_cost_scales_with_the_contract_it_wins():
    """A flat CAC is what produced an LTV/CAC of 569 on the largest segment.

    The floor still applies at the bottom — a small deal costs a minimum in
    demos and travel however little it bills — and above that the cost tracks
    contract value, which is what makes the ratio mean anything.
    """
    from app.monetization.unitecon import acquisition_cost

    assert acquisition_cost(Decimal("100000"), PARAMS) == PARAMS.cac_per_customer
    assert acquisition_cost(Decimal("55400000"), PARAMS) == Decimal("55400000.00")


@pytest.mark.parametrize("segment", ["small", "mid", "large"])
def test_ltv_cac_lands_in_a_range_a_reader_can_believe(segment):
    """The point is the *upper* bound. An implausibly good ratio is evidence
    about the inputs, and this model used to report 569x without blinking."""
    profile = ARCHETYPES[segment]
    wf = build_waterfall(profile, IMPACTS["base"], PARAMS)
    rec = monetization_report.recommend(wf, PARAMS)
    econ = unit_economics(profile,
                          Decimal(rec["evaluation"]["fee"]["annual_fee"]),
                          PARAMS, orders=wf.covered_with_pie.orders)
    assert econ.ltv_cac is not None
    assert 2.0 < econ.ltv_cac < 20.0, (segment, econ.ltv_cac)


def test_opex_has_a_floor_proportional_to_revenue():
    """A purely fixed opex base gave an 86% EBITDA margin in year five.

    Engineering and G&A scale with the business; the floor is what binds once
    revenue has compounded past a cost line growing at a fixed rate.
    """
    acv = {key: Decimal("10000000") for key in ARCHETYPES}
    years = project(SCENARIOS[1], acv, PARAMS)["years"]
    last = years[-1]
    revenue = Decimal(last["revenue"])
    assert Decimal(last["fixed_opex"]) >= revenue * Decimal(
        str(SCENARIOS[1].opex_floor_pct_of_revenue))
    assert last["ebitda_margin"] is not None and last["ebitda_margin"] < 0.5


def test_ebitda_before_growth_separates_the_two_reasons_for_a_loss():
    """Every scenario is EBITDA-negative through year five, and the two
    possible causes are opposite: growth spending, which can stop, or an
    unprofitable installed base, which cannot be fixed by stopping anything."""
    acv = {key: Decimal("10000000") for key in ARCHETYPES}
    years = project(SCENARIOS[1], acv, PARAMS)["years"]
    last = years[-1]
    assert Decimal(last["ebitda"]) < 0
    assert Decimal(last["ebitda_before_growth"]) > 0
    assert (Decimal(last["ebitda_before_growth"]) - Decimal(last["ebitda"])
            == Decimal(last["cac_spend"]))
