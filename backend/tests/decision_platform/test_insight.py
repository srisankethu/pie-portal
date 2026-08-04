"""The visualization layer's arithmetic, and the honesty rules around it.

Two classes of test, and the second is the one that matters more.

The first is ordinary correctness: periods, the waterfall reconciling, break-even
arithmetic. The second pins the *refusals* — that an empty radar explains itself
rather than being fixed by lowering the floor, that the simulator does not
invent a behavioural response, that a cause it cannot name is reported as
unexplained. Those are the properties a later change is most likely to erode,
because eroding them makes screens look fuller.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.commercial.insight import cohorts, flow, periods, simulate, story, weather
from app.signals.base import SaleRow

ORG = "org_viz"


def _sale(customer: str, day: date, amount: float, product: str = "p1",
          ref: str = "") -> SaleRow:
    return SaleRow(customer_id=customer, product_id=product, date=day,
                   qty=Decimal("1"), unit_price=Decimal(str(amount)),
                   line_revenue=Decimal(str(amount)),
                   source_ref={"record_id": ref or f"{customer}{day}"},
                   external_ref=ref or f"{customer}{day}")


# ── periods ─────────────────────────────────────────────────────────────────
def test_month_end_handles_leap_years():
    assert periods.month_end(date(2024, 2, 10)) == date(2024, 2, 29)
    assert periods.month_end(date(2025, 2, 10)) == date(2025, 2, 28)
    assert periods.month_end(date(2025, 12, 3)) == date(2025, 12, 31)


def test_the_comparison_windows_are_equal_length_and_adjacent():
    c = periods.comparison(date(2026, 8, 4), months=3)
    assert c.current.start == date(2026, 6, 1)
    assert c.current.end == date(2026, 8, 31)
    assert c.previous.start == date(2026, 3, 1)
    assert c.previous.end == date(2026, 5, 31)
    # Adjacent with no gap and no overlap — otherwise a month is double-counted
    # or silently dropped from the comparison.
    assert (c.current.start.toordinal() - c.previous.end.toordinal()) == 1


def test_the_current_month_is_included():
    """Dropping it would show a business as having stopped trading for weeks."""
    c = periods.comparison(date(2026, 8, 4), months=1)
    assert c.current.contains(date(2026, 8, 4))


# ── the waterfall must reconcile ────────────────────────────────────────────
def test_the_decomposition_sums_to_the_movement():
    """A waterfall whose bars do not add up teaches people to distrust the page."""
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [
        _sale("lost", date(2026, 5, 10), 1000),          # gone this period
        _sale("grown", date(2026, 5, 10), 500),
        _sale("grown", date(2026, 6, 10), 900),
        _sale("shrunk", date(2026, 5, 10), 800),
        _sale("shrunk", date(2026, 6, 10), 300),
        _sale("new", date(2026, 6, 10), 400),
    ]
    result = flow.compute(sales, {}, c)
    assert result.reconciles(), (
        f"buckets {result.buckets} sum to {sum(result.buckets.values())}, "
        f"movement is {result.delta}")


def test_every_customer_lands_in_exactly_one_bucket():
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [_sale(f"c{i}", date(2026, 5, 10), 100 * (i + 1)) for i in range(5)]
    sales += [_sale(f"c{i}", date(2026, 6, 10), 90 * (i + 1)) for i in range(3)]
    result = flow.compute(sales, {}, c)
    assert len({m.customer_id for m in result.moves}) == len(result.moves)


def test_a_returning_customer_is_not_counted_as_new():
    """They mean opposite things: one is a win-back, the other is acquisition."""
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [
        _sale("back", date(2025, 1, 10), 500),      # traded long ago
        _sale("back", date(2026, 6, 10), 500),      # nothing in May, returns in June
        _sale("fresh", date(2026, 6, 10), 500),     # never seen before
    ]
    kinds = {m.customer_id: m.kind for m in flow.compute(sales, {}, c).moves}
    assert kinds["back"] == flow.RECOVERED
    assert kinds["fresh"] == flow.NEW


def test_small_movement_is_stable_not_growth():
    """A customer who orders monthly is always a few percent either way."""
    assert flow.classify(1000, 1020, False) == flow.STABLE
    assert flow.classify(1000, 1200, False) == flow.GROWN
    assert flow.classify(1000, 800, False) == flow.SHRUNK


def test_zero_to_zero_is_not_a_division_by_zero():
    assert flow.classify(0, 0, False) == flow.STABLE
    assert flow.classify(0, 0, True) == flow.STABLE


# ── cohorts ─────────────────────────────────────────────────────────────────
def test_band_edges_come_from_the_data_not_from_fixed_amounts():
    """Fixed rupee edges are meaningless in another currency."""
    edges = cohorts._quantile_edges([10.0, 20.0, 30.0, 40.0, 100.0, 500.0])
    assert edges and edges == sorted(edges)


def test_migration_uses_one_set_of_edges_for_both_periods():
    """Banding each period separately would let a customer 'move up' while
    spending less, purely because the cohort around them shrank."""
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = []
    for i in range(6):
        sales.append(_sale(f"c{i}", date(2026, 5, 10), 100 * (i + 1)))
        sales.append(_sale(f"c{i}", date(2026, 6, 10), 100 * (i + 1)))
    result = cohorts.migration(sales, {}, c)
    # Nobody moved: every cell is on the diagonal.
    assert all(cell["from"] == cell["to"] for cell in result["cells"]), result["cells"]


def test_dormancy_is_an_observation_not_a_prediction():
    sales = [_sale("quiet", date(2025, 1, 10), 5000),
             _sale("active", date(2026, 6, 10), 100)]
    result = cohorts.dormancy(sales, {}, date(2026, 6, 30))
    ids = {c["customer_id"] for c in result["customers"]}
    assert ids == {"quiet"}
    assert result["customers"][0]["months_quiet"] == 17


def test_an_unnameable_cause_is_reported_as_unexplained():
    """Padding it with a plausible cause is how a tool stops being trusted."""
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [_sale("c1", date(2026, 5, 10), 1000),
             _sale("c1", date(2026, 6, 10), 400)]
    result = cohorts.lost_revenue(sales, {}, c, {})   # no metric rows at all
    assert [b["cause"] for b in result["causes"]] == ["UNEXPLAINED"]


def test_payment_behaviour_is_declared_missing_rather_than_faked():
    result = cohorts.health_timeline([], {}, date(2026, 6, 30), months=6)
    missing = {u["series"] for u in result["unavailable"]}
    assert "payment_behaviour" in missing


# ── the simulator ───────────────────────────────────────────────────────────
def test_break_even_needs_no_behavioural_assumption():
    """At a 20% margin, a 5% price rise tolerates a 20% volume loss."""
    got = simulate.break_even_volume_change(0.05, 0.20)
    assert got == pytest.approx(-0.20, abs=1e-9)


def test_break_even_is_undefined_when_contribution_goes_negative():
    """No volume can compensate a price cut that takes contribution below zero."""
    assert simulate.break_even_volume_change(-0.30, 0.20) is None


def test_a_price_rise_with_no_volume_response_always_gains():
    line = simulate.Line(customer_id="c", product_id="p", customer_label="C",
                         product_label="P", revenue_12m=1000.0, qty_recent=10.0,
                         unit_price=100.0, unit_cost=80.0, current_margin=0.2)
    result = simulate.price_change([line], pct=0.10, assumed_volume_change=0.0)
    assert result["delta"]["gross_profit"] == pytest.approx(100.0)
    assert result["projected"]["revenue"] == pytest.approx(1100.0)


def test_the_simulator_says_the_volume_response_is_an_assumption():
    line = simulate.Line("c", "p", "C", "P", 1000.0, 10.0, 100.0, 80.0, 0.2)
    result = simulate.price_change([line], pct=0.05, assumed_volume_change=-0.1)
    assert "assumption" in result["assumption_note"].lower()
    assert result["inputs"]["assumed_volume_change"] == -0.1


def test_unpriceable_lines_are_excluded_rather_than_assumed():
    priced = simulate.Line("c", "p", "C", "P", 1000.0, 10.0, 100.0, 80.0, 0.2)
    blind = simulate.Line("c2", "p2", "C2", "P2", 500.0, 5.0, None, None, None)
    result = simulate.price_change([priced, blind], pct=0.05)
    assert result["line_count"] == 1
    assert result["skipped_lines"] == 1
    assert result["skipped_reason"]


def test_scenarios_without_data_are_named_not_silently_dropped():
    """The spec asked for supplier-delay and inventory scenarios. Neither has a
    data source, so they are declared blocked rather than quietly omitted."""
    names = {u["scenario"] for u in simulate.UNAVAILABLE}
    assert names == {"SUPPLIER_DELAY", "INVENTORY_CHANGE"}
    assert all(u["needs"] and u["why"] for u in simulate.UNAVAILABLE)


def test_the_simulator_is_deterministic():
    line = simulate.Line("c", "p", "C", "P", 1000.0, 10.0, 100.0, 80.0, 0.2)
    a = simulate.price_change([line], pct=0.07, assumed_volume_change=-0.05)
    b = simulate.price_change([line], pct=0.07, assumed_volume_change=-0.05)
    assert a == b


# ── the weather roll-up ─────────────────────────────────────────────────────
def test_missing_evidence_is_unknown_not_healthy():
    """An executive view that grades missing data as fine is worse than a gap."""
    result = weather.build(
        flow_summary={"pct": None, "current_total": 0, "previous_total": 0},
        radar_totals={}, margin_now=None, margin_prev=None, margin_floor=0.15,
        dormant_count=0, active_customers=0, coverage=None)
    bands = {f["key"]: f["band"] for f in result["fronts"]}
    assert bands["margin"] == weather.UNKNOWN
    assert bands["evidence"] == weather.UNKNOWN


def test_the_worst_front_sets_the_overall_band():
    result = weather.build(
        flow_summary={"pct": -0.30, "current_total": 100, "previous_total": 200},
        radar_totals={"confident_impact": 0, "count": 0},
        margin_now=0.30, margin_prev=0.30, margin_floor=0.15,
        dormant_count=0, active_customers=10, coverage=0.9)
    assert result["overall"] == weather.POOR
    assert result["fronts"][0]["band"] == weather.POOR, "worst must sort first"


def test_every_front_offers_somewhere_to_go():
    """A dimension with nowhere to click generates meetings, not actions."""
    result = weather.build(
        flow_summary={"pct": 0.05, "current_total": 200, "previous_total": 190},
        radar_totals={"confident_impact": 5000, "count": 3},
        margin_now=0.25, margin_prev=0.24, margin_floor=0.15,
        dormant_count=1, active_customers=10, coverage=0.8)
    assert all(f["drill_to"] for f in result["fronts"])


# ── the storyboard's contract ───────────────────────────────────────────────
def test_every_beat_carries_all_three_answers():
    """The rule the whole product rests on: what changed, why, what next."""
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [_sale("c1", date(2026, 5, 10), 100000),
             _sale("c1", date(2026, 6, 10), 40000)]
    movement = flow.compute(sales, {"c1": "Acme"}, c)
    lost = cohorts.lost_revenue(sales, {"c1": "Acme"}, c, {})
    built = story.build(
        flow=movement.to_dict(), lost=lost, radar=[],
        radar_totals={"confident_impact": 0, "count": 0},
        dormant={"count": 0, "customers": []},
        concentration=story.concentration_of(
            [m.to_dict() for m in movement.moves], {"c1": "Acme"}),
        currency="INR")

    assert built["beats"], "expected at least one beat from a 60% revenue drop"
    for beat in built["beats"]:
        assert beat["headline"]
        assert beat["change"], f"{beat['kind']} has no 'what changed'"
        assert beat["cause"], f"{beat['kind']} has no 'why'"
        assert beat["action"].get("route"), f"{beat['kind']} has no 'what next'"


def test_an_empty_storyboard_explains_itself():
    built = story.build(flow={}, lost={"total_lost": 0, "causes": []}, radar=[],
                        radar_totals={}, dormant={"count": 0},
                        concentration={"top_customer_share": None},
                        currency="INR")
    assert built["beats"] == []
    assert built["empty_reason"]


def test_concentration_is_measured_not_asserted():
    moves = [{"customer_id": "big", "current": 800.0},
             {"customer_id": "small", "current": 200.0}]
    result = story.concentration_of(moves, {"big": "Big", "small": "Small"})
    assert result["top_customer_share"] == pytest.approx(0.8)
    assert result["top_customer_label"] == "Big"


def test_headlines_carry_their_currency():
    """A number with no unit on the first screen of a commercial product.

    Headlines were built with a bare ``:,.0f`` and rendered "241,141 of revenue
    stopped" — caught by screenshotting the real page, which is the only way
    this class of defect shows up.
    """
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [_sale("c1", date(2026, 5, 10), 100000),
             _sale("c1", date(2026, 6, 10), 40000)]
    movement = flow.compute(sales, {"c1": "Acme"}, c)
    built = story.build(
        flow=movement.to_dict(),
        lost=cohorts.lost_revenue(sales, {"c1": "Acme"}, c, {}),
        radar=[], radar_totals={"confident_impact": 0, "count": 0},
        dormant={"count": 0, "customers": []},
        concentration=story.concentration_of(
            [m.to_dict() for m in movement.moves], {"c1": "Acme"}),
        currency="INR")

    money_beats = [b for b in built["beats"] if "revenue stopped" in b["headline"]]
    assert money_beats, "expected a lost-revenue beat"
    assert "₹" in money_beats[0]["headline"], money_beats[0]["headline"]


def test_a_dollar_tenant_gets_a_dollar_headline():
    c = periods.comparison(date(2026, 6, 30), months=1)
    sales = [_sale("c1", date(2026, 5, 10), 100000),
             _sale("c1", date(2026, 6, 10), 40000)]
    movement = flow.compute(sales, {"c1": "Acme"}, c)
    built = story.build(
        flow=movement.to_dict(),
        lost=cohorts.lost_revenue(sales, {"c1": "Acme"}, c, {}),
        radar=[], radar_totals={}, dormant={"count": 0, "customers": []},
        concentration=story.concentration_of(
            [m.to_dict() for m in movement.moves], {"c1": "Acme"}),
        currency="USD")
    headline = next(b["headline"] for b in built["beats"] if "stopped" in b["headline"])
    assert "$" in headline and "₹" not in headline
