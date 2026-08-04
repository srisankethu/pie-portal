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

from app.commercial.config import CommercialThresholds
from app.commercial.insight import (cadence, cohorts, composition, flow, landscape,
                                    periods, simulate, story, weather)
from app.domain import models
from app.signals.base import SaleRow
from app.signals.config import SignalThresholds

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


# ── landscape ───────────────────────────────────────────────────────────────
def _metric(session, customer: str, product: str, revenue: float, *,
            margin: float | None = None, profit: float | None = None,
            txns: int = 5, qty_recent: float = 0.0, qty_previous: float = 0.0,
            sufficiency: str = "SUFFICIENT"):
    from datetime import datetime

    row = models.CustomerItemMetric(
        customer_item_metric_id=f"{customer}:{product}",
        organization_id=ORG, customer_id=customer, product_id=product,
        transaction_count=txns, peer_count=0,
        revenue_12m=Decimal(str(revenue)),
        gross_profit_12m=None if profit is None else Decimal(str(profit)),
        current_margin=margin,
        qty_recent=Decimal(str(qty_recent)), qty_previous=Decimal(str(qty_previous)),
        volume_change_pct=(None if not qty_previous
                           else (qty_recent - qty_previous) / qty_previous),
        signals=[], data_sufficiency=sufficiency, sufficiency_reasons=[],
        cost_covered_txns=txns, cost_missing_txns=0,
        thresholds_version="v-test", computed_at=datetime(2026, 6, 30))
    session.add(row)
    return row


def test_the_vertical_split_is_the_policy_floor_not_a_statistic(session):
    """The whole point of the quadrants: a dot must not change corner because
    somebody filtered the page differently."""
    for i, margin in enumerate([0.05, 0.06, 0.07, 0.40]):
        _metric(session, f"c{i}", "p1", 100000 + i, margin=margin)
    session.flush()
    th = CommercialThresholds(margin_floor=0.18)

    built = landscape.build(session, ORG, th)
    assert built["y_split"] == pytest.approx(0.18)
    # Three of four sit below the floor. A median split would have put two above
    # it by construction, which is exactly the failure this guards.
    below = [p for p in built["points"] if p["y"] < built["y_split"]]
    assert len(below) == 3


def test_a_point_with_no_margin_is_unknown_not_poor(session):
    _metric(session, "c1", "p1", 500000, margin=None, profit=None)
    _metric(session, "c2", "p1", 100, margin=0.4, profit=40)
    session.flush()

    built = landscape.build(session, ORG, CommercialThresholds(margin_floor=0.2))
    point = next(p for p in built["points"] if p["customer_id"] == "c1")
    assert point["y"] is None
    # Large and unknown lands in REVIEW — worth a look — never in FIX_FIRST,
    # which would assert a pricing problem nobody has evidence for.
    assert point["quadrant"] == "REVIEW"


def test_product_margin_is_weighted_never_a_mean_of_margins(session):
    """Σ profit ÷ Σ revenue. The mean of 90% and 10% here is 50%; the truth is
    10.8%, because almost all of the revenue is the low-margin line."""
    _metric(session, "c1", "p1", 1000, margin=0.9, profit=900)
    _metric(session, "c2", "p1", 99000, margin=0.1, profit=9900)
    session.flush()

    built = landscape.build(session, ORG, CommercialThresholds(margin_floor=0.2),
                            subject="product")
    point = next(p for p in built["points"] if p["id"] == "p1")
    assert point["y"] == pytest.approx(0.108, abs=1e-4)


def test_a_product_margin_is_withheld_when_cost_is_missing(session):
    """Revenue with no cost behind it gets no vertical position, rather than a
    margin computed over the fraction that happens to have one."""
    _metric(session, "c1", "p1", 50000, margin=None, profit=None)
    session.flush()

    built = landscape.build(session, ORG, CommercialThresholds(margin_floor=0.2),
                            subject="product")
    assert next(p for p in built["points"] if p["id"] == "p1")["y"] is None


def test_a_rolled_up_product_takes_its_weakest_confidence(session):
    _metric(session, "c1", "p1", 50000, margin=0.3, profit=15000,
            sufficiency="SUFFICIENT")
    _metric(session, "c2", "p1", 40000, margin=0.3, profit=12000,
            sufficiency="INSUFFICIENT")
    session.flush()

    built = landscape.build(session, ORG, CommercialThresholds(margin_floor=0.2),
                            subject="product")
    assert next(p for p in built["points"]
                if p["id"] == "p1")["confidence"] == "INSUFFICIENT"


def test_every_point_lands_in_exactly_one_named_quadrant(session):
    for i in range(6):
        _metric(session, f"c{i}", "p1", (i + 1) * 10000, margin=i * 0.1)
    session.flush()

    built = landscape.build(session, ORG, CommercialThresholds(margin_floor=0.2))
    assert sum(built["counts"].values()) == len(built["points"])
    assert set(built["counts"]) <= set(landscape.QUADRANT_LABELS)
    # Every quadrant the screen can render must have words to render with it.
    for spec in built["quadrants"].values():
        assert spec["label"] and spec["meaning"]


def test_an_empty_landscape_still_carries_its_quadrant_vocabulary(session):
    built = landscape.build(session, ORG, CommercialThresholds())
    assert built["points"] == []
    assert set(built["quadrants"]) == set(landscape.QUADRANT_LABELS)


def test_the_quadrant_words_describe_the_measure_actually_on_the_axis(session):
    """The growth view reused the margin wording and told a reader that a
    shrinking item was "below the floor" and "only the price is wrong" — a
    sentence about pricing on a chart with no price in it."""
    _metric(session, "c1", "p1", 50000, margin=0.1, profit=5000,
            qty_recent=5, qty_previous=10)
    session.flush()
    th = CommercialThresholds(margin_floor=0.2)

    margin_words = landscape.build(session, ORG, th)["quadrants"]
    growth_words = landscape.build(session, ORG, th, measure="momentum")["quadrants"]

    assert "floor" in margin_words["FIX_FIRST"]["meaning"]
    assert "floor" not in growth_words["FIX_FIRST"]["meaning"]
    assert "shrinking" in growth_words["FIX_FIRST"]["meaning"]
    # The job names stay put — the shape of the chart is worth learning once.
    assert ({k: v["label"] for k, v in margin_words.items()}
            == {k: v["label"] for k, v in growth_words.items()})


# ── composition ─────────────────────────────────────────────────────────────
def test_the_tail_is_folded_into_one_named_band(session):
    as_of = date(2026, 6, 30)
    sales = [_sale(f"c{i}", date(2026, 6, 10), 1000 - i * 10, ref=f"r{i}")
             for i in range(10)]
    names = {f"c{i}": f"Customer {i}" for i in range(10)}

    built = composition.build(sales, names, as_of, top_n=6)
    assert built["folded_count"] == 4
    other = [s for s in built["series"] if s["key"] == composition.OTHER]
    assert len(other) == 1
    # Named and counted: a reader must be able to tell what was folded away.
    assert "4" in other[0]["label"]


def test_the_bands_are_ranked_once_over_the_whole_window(session):
    """A band whose identity changes month to month is a band whose position
    means nothing, so the ranking is over the window, not per period."""
    as_of = date(2026, 6, 30)
    sales = [
        _sale("small", date(2026, 5, 10), 10, ref="a"),
        _sale("small", date(2026, 6, 10), 900, ref="b"),   # wins June alone
        _sale("big", date(2026, 5, 10), 5000, ref="c"),    # wins the window
        _sale("big", date(2026, 6, 10), 10, ref="d"),
    ]
    built = composition.build(sales, {"small": "Small", "big": "Big"}, as_of,
                              months=3, top_n=1)
    assert [s["key"] for s in built["series"]][0] == "big"


def test_orders_count_invoices_not_lines():
    as_of = date(2026, 6, 30)
    # One invoice, three lines. That is one order.
    sales = [_sale("c1", date(2026, 6, 10), 100, product=f"p{i}", ref="INV-1")
             for i in range(3)]
    built = composition.build(sales, {"c1": "Acme"}, as_of, measure="orders")
    assert built["totals"][-1] == 1.0


def test_shares_and_amounts_are_both_returned():
    as_of = date(2026, 6, 30)
    sales = [_sale("c1", date(2026, 6, 10), 750, ref="a"),
             _sale("c2", date(2026, 6, 10), 250, ref="b")]
    built = composition.build(sales, {"c1": "A", "c2": "B"}, as_of)
    top = built["series"][0]
    assert top["values"][-1] == 750.0
    assert top["shares"][-1] == pytest.approx(0.75)


def test_the_biggest_mover_is_named_rather_than_left_to_the_reader():
    as_of = date(2026, 6, 30)
    sales = [
        _sale("grower", date(2026, 5, 10), 100, ref="a"),
        _sale("grower", date(2026, 6, 10), 900, ref="b"),
        _sale("steady", date(2026, 5, 10), 900, ref="c"),
        _sale("steady", date(2026, 6, 10), 100, ref="d"),
    ]
    built = composition.build(sales, {"grower": "Grower", "steady": "Steady"},
                              as_of, months=2)
    mover = built["movement"]["biggest_mover"]
    assert mover["key"] in {"grower", "steady"}
    assert mover["change"] != 0


def test_the_headline_compares_halves_not_a_single_thin_month():
    """The endpoint version reported a customer trading in one quiet month as
    having "gone from 68% to 0%" — the denominator had moved, not the customer.
    Here `spike` owns one early month outright and is otherwise a rounding
    error; a half-window share must not call that a collapse from 100%."""
    as_of = date(2026, 6, 30)
    sales = [_sale("spike", date(2026, 1, 10), 100, ref="s1")]
    # `steady` trades every month from February at a much larger scale.
    for month in range(2, 7):
        sales.append(_sale("steady", date(2026, month, 10), 10000, ref=f"t{month}"))

    built = composition.build(sales, {"spike": "Spike", "steady": "Steady"},
                              as_of, months=6)
    spike = next(m for m in ([built["movement"]["biggest_mover"]]
                             + built["movement"]["others"])
                 if m["key"] == "spike")
    # First half is Jan–Mar: 100 of 20,100. Nowhere near 100%.
    assert spike["from"] < 0.02
    assert built["movement"]["basis"].startswith("share of the first half")


def test_a_half_with_no_trade_at_all_is_skipped_rather_than_called_a_collapse():
    as_of = date(2026, 6, 30)
    # Everything happened in one month, so one half has a zero denominator.
    sales = [_sale("c1", date(2026, 6, 10), 100, ref="a")]
    built = composition.build(sales, {"c1": "Acme"}, as_of, months=2)
    movement = built["movement"]
    # Two periods, the earlier one empty: there is no comparison to report.
    assert movement is None or movement["biggest_mover"]["from"] is not None


def test_an_empty_window_does_not_divide_by_zero():
    built = composition.build([], {}, date(2026, 6, 30))
    assert built["series"] == []


# ── cadence ─────────────────────────────────────────────────────────────────
def _regular(customer: str, start: date, every: int, times: int, amount=1000):
    from datetime import timedelta
    return [_sale(customer, start + timedelta(days=every * i), amount,
                  ref=f"{customer}-{i}") for i in range(times)]


def test_overdue_is_measured_against_the_customer_s_own_rhythm():
    """A quarterly buyer is not late in month two. A weekly buyer is late in
    month one. One company-wide interval could not say both."""
    as_of = date(2026, 6, 30)
    weekly = _regular("weekly", date(2026, 1, 5), 7, 12)
    quarterly = _regular("quarterly", date(2025, 1, 5), 90, 6)
    built = cadence.build(weekly + quarterly,
                          {"weekly": "Weekly", "quarterly": "Quarterly"}, as_of)
    by_id = {c["customer_id"]: c for c in built["customers"]}
    assert by_id["weekly"]["overdue"] is True
    assert by_id["quarterly"]["overdue"] is False


def test_too_few_orders_means_no_rhythm_rather_than_an_assumed_one():
    as_of = date(2026, 6, 30)
    sig = SignalThresholds()
    thin = _regular("thin", date(2020, 1, 1), 30, sig.dormancy_min_orders - 1)
    built = cadence.build(thin, {"thin": "Thin"}, as_of)
    row = built["customers"][0]
    assert row["typical_interval_days"] is None
    # Six years silent, and still not called overdue — there is no baseline to
    # be overdue against.
    assert row["overdue"] is False
    assert built["unestimable_count"] == 1


def test_the_median_gap_survives_one_emergency_order():
    as_of = date(2026, 6, 30)
    from datetime import timedelta
    base = date(2026, 1, 1)
    days = [0, 30, 60, 61, 90, 120]      # one order the day after another
    sales = [_sale("c1", base + timedelta(days=d), 100, ref=f"r{d}") for d in days]
    built = cadence.build(sales, {"c1": "Acme"}, as_of)
    # The mean of these gaps is 24; the median is 30, which is the real rhythm.
    assert built["customers"][0]["typical_interval_days"] == pytest.approx(30.0)


def test_the_wheel_reports_how_often_each_day_exists():
    """Days 29–31 do not occur in every month. Without the opportunity count a
    screen would draw a cliff that is the calendar, not the customers."""
    built = cadence.build([], {}, date(2026, 6, 30))
    slots = {d["day"]: d["occurs_in_months"] for d in built["wheel"]}
    assert len(built["wheel"]) == 31
    assert slots[15] == 12
    assert slots[31] < slots[29] < slots[15]


def test_the_wheel_counts_invoices_not_lines():
    as_of = date(2026, 6, 30)
    sales = [_sale("c1", date(2026, 6, 15), 100, product=f"p{i}", ref="INV-1")
             for i in range(4)]
    built = cadence.build(sales, {"c1": "Acme"}, as_of)
    assert next(d for d in built["wheel"] if d["day"] == 15)["orders"] == 1
    assert next(d for d in built["wheel"] if d["day"] == 15)["revenue"] == 400.0


def test_a_tuned_dormancy_threshold_moves_both_the_queue_and_the_screen():
    """Two screens disagreeing about who is dormant is worse than either being
    wrong, because a person cannot tell which one to believe. The multiplier is
    tunable, so this asserts the screen *follows* it rather than that it happens
    to equal the default today."""
    # Five orders 30 days apart, last on 2026-06-21, read on 2026-07-31: a gap
    # of 40 days. Overdue against ×1.0 (40 > 30), not against ×1.5 (40 < 45).
    # One customer, two settings, two different answers — both correct.
    sales = _regular("c1", date(2026, 2, 21), 30, 5)
    as_of = date(2026, 7, 31)

    lenient = cadence.build(sales, {"c1": "Acme"}, as_of,
                            thresholds=SignalThresholds(dormancy_interval_multiplier=1.5))
    strict = cadence.build(sales, {"c1": "Acme"}, as_of,
                           thresholds=SignalThresholds(dormancy_interval_multiplier=1.0))
    assert lenient["customers"][0]["days_since_last"] == 40
    assert lenient["customers"][0]["overdue"] is False
    assert strict["customers"][0]["overdue"] is True
    assert lenient["overdue_multiplier"] == 1.5
    assert strict["overdue_multiplier"] == 1.0


def test_the_screen_and_the_detector_read_the_same_two_settings():
    """Not equal-by-copy: the same object. A copy agrees until somebody sets
    SIG_DORMANCY_MULT."""
    import inspect

    source = inspect.getsource(cadence.build)
    assert "th.dormancy_min_orders" in source
    assert "th.dormancy_interval_multiplier" in source


# ── the one cadence implementation ──────────────────────────────────────────
def test_same_day_orders_are_not_a_rhythm_anyone_can_be_late_against():
    """The quote assembler used to accept two orders and divide by a zero
    interval, so a customer whose orders all landed on one day was reported
    overdue on every quote from then on. One shared guard, one answer."""
    from app.signals import aggregates as agg

    same_day = [_sale("c1", date(2026, 1, 5), 100, product=f"p{i}", ref=f"r{i}")
                for i in range(4)]
    c = agg.cadence_of(same_day, date(2026, 6, 30), min_orders=2, multiplier=1.5)
    assert c.estimable is False
    assert c.overdue is False
    assert c.typical_interval_days is None
    # The gap is still known and still reported — only the rhythm is missing.
    assert c.days_since_last == 176


def test_the_three_cadence_readers_share_one_implementation():
    """Not "agree today" — the same function. dormancy raises the signal,
    quote_context publishes the facts, the rhythm screen shows the population."""
    import inspect

    from app.commercial.insight import cadence as screen
    from app.signals import dormancy, quote_context

    for module in (dormancy.detect, quote_context.assemble, screen.build):
        assert "cadence_of" in inspect.getsource(module), module.__qualname__


def test_the_eligibility_bar_is_a_parameter_not_a_constant():
    from app.signals import aggregates as agg

    sales = _regular("c1", date(2026, 1, 1), 30, 3)
    as_of = date(2026, 6, 30)
    # Three orders: enough to describe on a quote, not enough to raise a signal.
    assert agg.cadence_of(sales, as_of, min_orders=2, multiplier=1.5).estimable
    assert not agg.cadence_of(sales, as_of, min_orders=4, multiplier=1.5).estimable
