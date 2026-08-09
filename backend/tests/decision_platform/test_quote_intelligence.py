"""The deterministic quote intelligence engine: bands, references, exceptions.

Pure-function tests. The cases that matter are the ones where a plausible
implementation is confidently wrong: comparing a 500-piece quote against a
single-piece price, inverting the margin formula, inventing a floor with no
cost, letting a critical flag disappear because the line was small, or letting
a cost figure leak through an exception written for a salesperson.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal


from app.commercial.config import CommercialThresholds
from app.commercial.economics import line_economics
from app.commercial.metrics import compute_relationship
from app.commercial.quantity import band_for, bands
from app.commercial.quote_exceptions import (
    ABOVE_PEER_MEDIAN,
    BELOW_LAST_PRICE,
    BELOW_MARGIN_FLOOR,
    BELOW_MIN_MARGIN,
    COST_INCREASE_NOT_PASSED,
    CRITICAL,
    INFO,
    NEGATIVE_MARGIN,
    NEW_RELATIONSHIP,
    NO_COST_BASIS,
    NO_PRICE_SET,
    THIN_HISTORY,
    WARNING,
    evaluate,
)
from app.commercial.quote_intelligence import assess_line
from app.commercial.references import (
    BAND_PRICE,
    LAST_PRICE_PAID,
    MIN_MARGIN_PRICE,
    OPERATIONAL,
    PEER_MEDIAN_PRICE,
    RESTRICTED,
    TARGET_MARGIN_PRICE,
    build_references,
    by_code,
)
from app.domain.enums import EvidenceSufficiency
from app.signals.base import CostRow, SaleRow

AS_OF = date(2026, 7, 1)
TH = CommercialThresholds()


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _cost(days_ago: int, unit_cost) -> CostRow:
    return CostRow(product_id="p1", date=_d(days_ago), qty=Decimal("1"),
                   unit_cost=Decimal(str(unit_cost)),
                   source_ref={"record_type": "bill", "record_id": f"B-{days_ago}"},
                   external_ref=f"B-{days_ago}:1")


def _line(days_ago, qty, price, cost=None, customer="c1"):
    q, p = Decimal(str(qty)), Decimal(str(price))
    sale = SaleRow(customer_id=customer, product_id="p1", date=_d(days_ago), qty=q,
                   unit_price=p, line_revenue=q * p,
                   source_ref={"record_type": "invoice", "record_id": f"INV-{days_ago}"},
                   external_ref=f"INV-{days_ago}:1")
    costs = [_cost(days_ago + 1, cost)] if cost is not None else []
    return line_economics(sale, costs)


def _assess(*, qty, price, lines=(), costs=(), family=None, benchmark=None, th=TH,
            item_master_cost=None):
    lines = list(lines)
    metrics = (compute_relationship("c1", "p1", lines, AS_OF, th) if lines else None)
    return assess_line(
        line_id="L1", customer_id="c1", product_id="p1",
        qty=Decimal(str(qty)),
        proposed_price=(Decimal(str(price)) if price is not None else None),
        lines=lines, costs=list(costs), metrics=metrics, benchmark=benchmark,
        family=family, as_of=AS_OF, th=th,
        item_master_cost=(Decimal(str(item_master_cost))
                          if item_master_cost is not None else None))


def _codes(intel):
    return [e.code for e in intel.exceptions]


# ── quantity bands ──────────────────────────────────────────────────────────
def test_the_band_ladder_covers_every_quantity_without_a_gap():
    ladder = bands(TH)
    assert [b.label for b in ladder] == ["1", "2–10", "11–50", "51–200", "201+"]
    for qty in range(1, 400):
        matching = [b for b in ladder if b.contains(Decimal(qty))]
        assert len(matching) == 1, f"qty {qty} landed in {len(matching)} bands"


def test_a_nonsensical_quantity_lands_in_the_lowest_band_not_a_band_of_its_own():
    assert band_for(Decimal("0"), TH).label == "1"
    assert band_for(Decimal("-5"), TH).label == "1"


def test_band_edges_are_configuration_not_literals():
    th = CommercialThresholds(quantity_band_edges=(5, 100))
    assert [b.label for b in bands(th)] == ["1–5", "6–100", "101+"]
    assert band_for(Decimal("50"), th).label == "6–100"


# ── price references ────────────────────────────────────────────────────────
def test_the_band_price_is_not_the_all_quantities_average():
    """The whole point of banding. Small orders at ₹200 must not drag the
    reference for a 500-piece line that has always gone out at ₹120."""
    history = [
        _line(200, 2, 200), _line(150, 3, 200), _line(120, 5, 200),   # small
        _line(90, 400, 120), _line(40, 600, 120),                     # bulk
        _line(20, 3, 200),                                            # small, recent
    ]
    refs = by_code(build_references(
        lines=history, band=band_for(Decimal("500"), TH), unit_cost=None,
        benchmark=None, historical_margin=None, family=None, as_of=AS_OF, th=TH))

    assert refs[BAND_PRICE].value == Decimal("120")
    assert refs[BAND_PRICE].qty_band == "201+"
    # the all-quantities recent average is pulled by the bulk lines but is a
    # different, separately-labelled reference
    assert refs[BAND_PRICE].value != refs["RECENT_AVG_PRICE"].value


def test_a_single_past_order_is_not_a_band_reference():
    """One line at a quantity is a coincidence, not a pattern."""
    refs = by_code(build_references(
        lines=[_line(30, 500, 120)], band=band_for(Decimal("500"), TH),
        unit_cost=None, benchmark=None, historical_margin=None, family=None,
        as_of=AS_OF, th=TH))
    assert BAND_PRICE not in refs
    assert LAST_PRICE_PAID in refs, "but the one order is still a fact"


def test_margin_references_invert_the_margin_correctly():
    """cost / (1 - margin), not cost * (1 + margin). Getting this backwards
    understates every floor by several points and nobody notices."""
    refs = by_code(build_references(
        lines=[], band=band_for(Decimal("1"), TH), unit_cost=Decimal("100"),
        benchmark=None, historical_margin=None, family=None, as_of=AS_OF, th=TH))

    # 24% target margin on a ₹100 cost is ₹131.58 — a price at which gross
    # profit ÷ revenue is exactly 24%.
    target = refs[TARGET_MARGIN_PRICE].value
    assert abs(target - Decimal("131.5789")) < Decimal("0.001")
    assert abs(float((target - 100) / target) - 0.24) < 1e-9

    floor = refs[MIN_MARGIN_PRICE].value
    assert abs(float((floor - 100) / floor) - TH.min_margin) < 1e-9


def test_no_cost_means_no_floor_rather_than_a_floor_of_zero():
    refs = by_code(build_references(
        lines=[_line(30, 10, 200)], band=band_for(Decimal("10"), TH), unit_cost=None,
        benchmark=None, historical_margin=None, family=None, as_of=AS_OF, th=TH))
    assert MIN_MARGIN_PRICE not in refs
    assert TARGET_MARGIN_PRICE not in refs
    assert LAST_PRICE_PAID in refs


def test_the_family_target_comes_from_central_thresholds():
    refs = by_code(build_references(
        lines=[], band=band_for(Decimal("1"), TH), unit_cost=Decimal("100"),
        benchmark=None, historical_margin=None, family="milling_insert",
        as_of=AS_OF, th=TH))
    price = refs[TARGET_MARGIN_PRICE].value
    assert abs(float((price - 100) / price) - 0.30) < 1e-9


def test_cost_derived_references_are_restricted_and_customer_prices_are_not():
    refs = by_code(build_references(
        lines=[_line(30, 10, 200)], band=band_for(Decimal("10"), TH),
        unit_cost=Decimal("100"), benchmark=None, historical_margin=None,
        family=None, as_of=AS_OF, th=TH))
    assert refs[LAST_PRICE_PAID].data_class == OPERATIONAL
    assert refs[MIN_MARGIN_PRICE].data_class == RESTRICTED
    assert refs[TARGET_MARGIN_PRICE].data_class == RESTRICTED


# ── exception rules ─────────────────────────────────────────────────────────
def test_a_price_under_cost_is_critical_and_needs_approval():
    intel = _assess(qty=100, price=90, costs=[_cost(10, 100)])
    assert NEGATIVE_MARGIN in _codes(intel)
    assert intel.blocking and intel.requires_approval
    assert intel.exceptions[0].severity == CRITICAL


def test_the_item_master_cost_is_a_cost_basis_when_no_bill_has_landed():
    """A below-cost line must not pass as "within policy" for want of a bill.

    The Quote Builder shows the books' landed cost on the line and flagged it;
    this assessment read only bill-derived cost records, found none, raised no
    exception and reported `requires_approval: False` — and the send gate
    believed the blind one. A line losing money went out with a green chip.
    """
    intel = _assess(qty=10, price=399, costs=[], item_master_cost=420)
    assert intel.economics.unit_cost == Decimal("420")
    assert NEGATIVE_MARGIN in _codes(intel)
    assert intel.blocking and intel.requires_approval
    # Traceable to where it came from, and distinguishable from a purchase.
    assert intel.economics.cost_source_ref["basis"] == "item_master_landed_cost"


def test_a_bill_outranks_the_item_master_cost():
    """A bill is what we actually paid; the item master is what the books think.

    Precedence matters both ways round: without it, a stale item-master figure
    would silently overrule the purchase history the margin policy is built on.
    """
    intel = _assess(qty=10, price=399, costs=[_cost(10, 300)], item_master_cost=420)
    assert intel.economics.unit_cost == Decimal("300")
    assert intel.economics.cost_source_ref["record_type"] == "bill"
    assert NEGATIVE_MARGIN not in _codes(intel)


def test_no_cost_anywhere_still_means_no_cost():
    """The fallback adds a second basis; it does not invent one."""
    intel = _assess(qty=10, price=399, costs=[], item_master_cost=None)
    assert intel.economics.unit_cost is None
    assert NO_COST_BASIS in _codes(intel)
    assert NEGATIVE_MARGIN not in _codes(intel)


def test_a_placeholder_item_master_cost_is_not_a_cost():
    """Zero is what an unfilled field holds, and a zero cost reads as 100% margin
    — the same rule `line_economics` applies to a zero bill rate."""
    intel = _assess(qty=10, price=399, costs=[], item_master_cost=0)
    assert intel.economics.unit_cost is None
    assert NO_COST_BASIS in _codes(intel)


def test_a_critical_exception_survives_the_materiality_floor():
    """A ₹6 loss is still a loss. A flag that quietly vanishes on small lines
    is a flag nobody trusts on large ones."""
    intel = _assess(qty=1, price=97, costs=[_cost(10, 100)])
    assert NEGATIVE_MARGIN in _codes(intel)


def test_an_immaterial_warning_is_suppressed():
    """₹2 below the last price on two units is noise, not an exception."""
    history = [_line(60, 2, 200), _line(30, 2, 200)]
    intel = _assess(qty=2, price=195, lines=history)
    assert BELOW_LAST_PRICE not in _codes(intel)


def test_the_same_gap_on_a_material_line_does_fire():
    history = [_line(60, 400, 200), _line(30, 400, 200)]
    intel = _assess(qty=400, price=195, lines=history)
    assert BELOW_LAST_PRICE in _codes(intel)
    fired = next(e for e in intel.exceptions if e.code == BELOW_LAST_PRICE)
    assert fired.impact_amount == Decimal("2000")     # (200 - 195) × 400


def test_the_hard_floor_and_the_review_floor_are_different_severities():
    # ₹100 cost: min margin 12% → ₹113.64, review floor 15% → ₹117.65
    thin = _assess(qty=100, price=116, costs=[_cost(10, 100)])
    assert BELOW_MARGIN_FLOOR in _codes(thin)
    assert thin.exceptions[0].severity == WARNING
    assert not thin.requires_approval

    hard = _assess(qty=100, price=112, costs=[_cost(10, 100)])
    assert BELOW_MIN_MARGIN in _codes(hard)
    assert hard.requires_approval


def test_ranking_is_by_money_not_by_percentage():
    """A 2 pp shortfall on a huge line must outrank a large percentage
    shortfall on a trivial one, or the screen sorts the work backwards."""
    history = [_line(200, 500, 200), _line(60, 500, 200)]
    intel = _assess(qty=500, price=150, lines=history, costs=[_cost(10, 120)])
    severities = [e.severity for e in intel.exceptions]
    assert severities == sorted(severities, key=lambda s: {"CRITICAL": 0, "WARNING": 1,
                                                           "INFO": 2}[s])
    warnings = [e for e in intel.exceptions if e.severity == WARNING
                and e.impact_amount is not None]
    impacts = [e.impact_amount for e in warnings]
    assert impacts == sorted(impacts, reverse=True)


def test_a_cost_rise_that_was_not_passed_on_is_named():
    history = [_line(d, 100, 200, cost=140) for d in (700, 600, 500, 400)]
    history += [_line(d, 100, 200, cost=175) for d in (80, 50, 20)]
    intel = _assess(qty=100, price=200, lines=history, costs=[_cost(10, 175)])
    assert COST_INCREASE_NOT_PASSED in _codes(intel)


def test_a_trend_claim_is_withheld_when_the_history_is_too_thin():
    """One order cannot support "cost has been rising". The single observed
    price it does support is still offered."""
    history = [_line(20, 100, 200, cost=175)]
    intel = _assess(qty=100, price=200, lines=history, costs=[_cost(10, 250)])
    assert COST_INCREASE_NOT_PASSED not in _codes(intel)
    assert THIN_HISTORY in _codes(intel)
    assert intel.data_sufficiency is EvidenceSufficiency.INSUFFICIENT
    # but the cost-based rule, which needs no history at all, still fires
    assert NEGATIVE_MARGIN in _codes(intel)


def test_a_first_time_item_says_so_instead_of_going_quiet():
    intel = _assess(qty=10, price=200, lines=[], costs=[_cost(10, 100)])
    assert NEW_RELATIONSHIP in _codes(intel)
    assert THIN_HISTORY not in _codes(intel)


def test_a_missing_cost_is_reported_not_treated_as_zero():
    intel = _assess(qty=10, price=200, lines=[_line(30, 10, 200)])
    assert NO_COST_BASIS in _codes(intel)
    assert intel.economics.margin is None, "a 100% margin would be a lie"
    assert NEGATIVE_MARGIN not in _codes(intel)
    assert BELOW_MIN_MARGIN not in _codes(intel)


def test_a_placeholder_zero_cost_is_treated_as_no_cost():
    intel = _assess(qty=10, price=200, costs=[_cost(10, 0)])
    assert NO_COST_BASIS in _codes(intel)
    assert intel.economics.unit_cost is None


def test_no_price_yet_asks_for_one_instead_of_flagging_a_phantom():
    intel = _assess(qty=10, price=None, lines=[_line(30, 10, 200)],
                    costs=[_cost(10, 100)])
    assert _codes(intel).count(NO_PRICE_SET) == 1
    assert BELOW_MIN_MARGIN not in _codes(intel)
    assert LAST_PRICE_PAID in by_code(intel.references)


def test_cost_is_resolved_as_of_the_quote_date_not_the_last_sale():
    """The quote's whole reason for existing: the item cost ₹100 when it last
    sold and ₹180 today, and the quote must be judged against ₹180."""
    history = [_line(d, 100, 200, cost=100) for d in (400, 300, 200, 150, 120, 100)]
    intel = _assess(qty=100, price=200, lines=history,
                    costs=[_cost(400, 100), _cost(5, 180)])
    assert intel.economics.unit_cost == Decimal("180")
    assert abs(intel.economics.margin - 0.10) < 1e-9
    assert BELOW_MIN_MARGIN in _codes(intel)


def test_a_future_cost_record_does_not_apply_to_todays_quote():
    intel = _assess(qty=100, price=200,
                    costs=[_cost(30, 100), _cost(-10, 190)])   # -10 = 10 days hence
    assert intel.economics.unit_cost == Decimal("100")


# ── peer comparison ─────────────────────────────────────────────────────────
def _benchmark(peer_prices, subject_price=None):
    from app.commercial.benchmark import compute_benchmark
    by_customer = {}
    for i, price in enumerate(peer_prices):
        by_customer[f"peer{i}"] = [_line(30, 10, price, cost=100, customer=f"peer{i}")]
    if subject_price is not None:
        by_customer["c1"] = [_line(30, 10, subject_price, cost=100)]
    return compute_benchmark("p1", "c1", by_customer, AS_OF, TH)


def test_a_two_customer_peer_group_is_not_a_market():
    bm = _benchmark([200, 210])
    intel = _assess(qty=100, price=120, benchmark=bm, costs=[_cost(10, 100)])
    assert PEER_MEDIAN_PRICE not in by_code(intel.references)


def test_a_price_below_a_reliable_peer_median_is_flagged():
    bm = _benchmark([200, 205, 210, 215])
    intel = _assess(qty=100, price=150, benchmark=bm)
    from app.commercial.quote_exceptions import BELOW_PEER_MEDIAN
    assert BELOW_PEER_MEDIAN in _codes(intel)


def test_the_engine_flags_being_too_expensive_as_well_as_too_cheap():
    """A margin cop that only ever says "you're too cheap" gets ignored. A
    price well above the rest of the book is a lost-order risk."""
    bm = _benchmark([200, 205, 210, 215])
    intel = _assess(qty=100, price=320, benchmark=bm)
    assert ABOVE_PEER_MEDIAN in _codes(intel)
    assert next(e for e in intel.exceptions
                if e.code == ABOVE_PEER_MEDIAN).severity == INFO


def test_the_peer_median_is_a_median_of_other_customers_not_of_everyone():
    bm = _benchmark([200, 200, 200, 200], subject_price=100)
    assert bm.median_price == Decimal("200"), "the subject must not pull its own benchmark"


# ── determinism and safety ──────────────────────────────────────────────────
def test_identical_inputs_give_a_byte_identical_assessment():
    history = [_line(d, 100, 200, cost=150) for d in (400, 300, 200, 100, 50)]
    args = dict(qty=100, price=170, lines=history, costs=[_cost(10, 160)])
    a, b = _assess(**args), _assess(**args)
    assert [e.to_dict() for e in a.exceptions] == [e.to_dict() for e in b.exceptions]
    assert [r.to_dict() for r in a.references] == [r.to_dict() for r in b.references]


def test_no_salesperson_facing_text_names_a_cost_or_a_margin():
    """The invariant that must never regress: an exception's ``detail`` is what
    a salesperson reads, and it must not carry a number that reveals cost."""
    history = [_line(d, 100, 200, cost=150) for d in (700, 600, 500, 400, 100, 50)]
    intel = _assess(qty=100, price=160, lines=history, costs=[_cost(10, 150)])
    assert intel.exceptions, "this case must actually fire something"
    for e in intel.exceptions:
        assert "150" not in e.detail, f"{e.code} leaks the unit cost"
        assert "%" not in e.detail, f"{e.code} leaks a margin percentage"


def test_manager_detail_is_where_the_numbers_live():
    intel = _assess(qty=100, price=112, costs=[_cost(10, 100)])
    fired = next(e for e in intel.exceptions if e.code == BELOW_MIN_MARGIN)
    assert fired.manager_detail and "%" in fired.manager_detail


def test_thresholds_version_travels_with_the_assessment():
    intel = _assess(qty=10, price=200, costs=[_cost(10, 100)])
    assert intel.thresholds_version == TH.version
    assert intel.thresholds_version.startswith("ci_")


def test_evaluate_is_pure_and_needs_no_metrics_at_all():
    """A brand-new customer for a brand-new item still gets an assessment."""
    found = evaluate(proposed_price=Decimal("90"), qty=Decimal("1"),
                     unit_cost=Decimal("100"), references=[], metrics=None,
                     benchmark=None, th=TH)
    assert {e.code for e in found} == {NEGATIVE_MARGIN, NEW_RELATIONSHIP}
