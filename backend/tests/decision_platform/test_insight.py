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
from app.commercial.insight import stock as _stock
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


# ── the customer health timeline ────────────────────────────────────────────
def test_a_quiet_month_has_no_margin_rather_than_a_margin_of_zero():
    """The screen leaves the slot empty for a None. A zero would be drawn, and
    a month with no trade would read as a catastrophic month."""
    sales = [_sale("c1", date(2026, 6, 10), 1000, ref="a")]
    result = cohorts.health_timeline(sales, {"p1": 700.0}, date(2026, 6, 30), months=3)
    by_label = {p["label"]: p for p in result["series"]}
    june = by_label["Jun 2026"]
    quiet = [p for p in result["series"] if p["revenue"] == 0]

    assert june["margin"] is not None
    assert quiet and all(p["margin"] is None for p in quiet)
    assert all(p["orders"] == 0 for p in quiet)


def test_margin_needs_a_cost_behind_it_not_just_revenue():
    """Revenue with no cost on record yields no margin at all — never 100%,
    which is what (revenue - 0) / revenue would produce."""
    sales = [_sale("c1", date(2026, 6, 10), 1000, ref="a")]
    result = cohorts.health_timeline(sales, {}, date(2026, 6, 30), months=2)
    june = next(p for p in result["series"] if p["label"] == "Jun 2026")
    assert june["revenue"] == 1000.0
    assert june["margin"] is None


def test_cost_coverage_travels_with_the_margin():
    """The screen draws a partially-covered month hollow. It can only do that
    if the coverage arrives beside the number it qualifies."""
    sales = [_sale("c1", date(2026, 6, 5), 1000, product="p1", ref="a"),
             _sale("c1", date(2026, 6, 6), 1000, product="p2", ref="b")]
    result = cohorts.health_timeline(sales, {"p1": 600.0}, date(2026, 6, 30), months=2)
    june = next(p for p in result["series"] if p["label"] == "Jun 2026")
    assert june["cost_coverage"] == 0.5
    assert june["margin"] is not None


def test_orders_in_the_timeline_count_invoices_not_lines():
    sales = [_sale("c1", date(2026, 6, 10), 100, product=f"p{i}", ref="INV-1")
             for i in range(4)]
    result = cohorts.health_timeline(sales, {}, date(2026, 6, 30), months=2)
    assert next(p for p in result["series"] if p["label"] == "Jun 2026")["orders"] == 1


# ── Tier 3: the shelf, the suppliers and the cash ───────────────────────────
def _settled(customer: str, invoiced: date, paid: date, amount=1000.0,
             due: date | None = None):
    from app.commercial.insight.payments import Settlement
    return Settlement(customer_id=customer, invoice_ref=f"{customer}-{invoiced}",
                      invoice_number=None, invoice_date=invoiced, due_date=due,
                      paid_on=paid, amount=amount)


def test_days_to_pay_is_measured_per_invoice_not_per_payment():
    """One transfer settling three invoices is three observations. Averaging
    the payment instead flatters a customer who batches their remittances."""
    from datetime import timedelta

    from app.commercial.insight import payments

    base = date(2026, 4, 1)
    # Three invoices, 10/40/70 days old, all cleared by one transfer.
    settled = [_settled("c1", base + timedelta(days=d), date(2026, 7, 1))
               for d in (81, 51, 21)]
    built = payments.build(settled, {"c1": "Acme"}, date(2026, 7, 31))
    row = built["customers"][0]
    assert row["settlements"] == 3
    assert row["median_days_to_pay"] == pytest.approx(40.0)
    assert row["worst_days_to_pay"] == 70


def test_an_advance_is_not_a_zero_day_payment():
    """Money with no invoice behind it has no invoice date to subtract.
    Counting it as same-day would make every prepayer the fastest in the book —
    the opposite of what a prepayment means for risk."""
    from app.commercial.insight import payments

    built = payments.build([], {}, date(2026, 7, 31), advances=4,
                           unapplied_total=250000.0)
    assert built["customers"] == []
    assert built["advances"] == 4
    assert built["unapplied_total"] == 250000.0
    assert built["median_days_to_pay"] is None


def test_an_invoice_with_no_due_date_is_undatable_not_on_time():
    from app.commercial.insight import payments

    settled = [_settled("c1", date(2026, 5, 1), date(2026, 8, 1), due=None)]
    built = payments.build(settled, {"c1": "Acme"}, date(2026, 8, 31))
    assert built["late_share"] is None          # nothing could be judged late
    assert built["undatable_count"] == 1
    assert built["customers"][0]["late_share"] is None
    # 92 days of cash tied up is still answerable without a due date.
    assert built["customers"][0]["median_days_to_pay"] == pytest.approx(92.0)


def test_late_is_against_the_due_date_and_slow_is_against_the_invoice():
    from app.commercial.insight import payments

    settled = [_settled("c1", date(2026, 5, 1), date(2026, 7, 1),
                        due=date(2026, 6, 30))]
    built = payments.build(settled, {"c1": "Acme"}, date(2026, 7, 31))
    assert built["customers"][0]["median_days_to_pay"] == pytest.approx(61.0)
    assert built["late_share"] == 1.0          # one day past terms
    assert built["customers"][0]["late_count"] == 1


#: The carrying policy these stock tests are written against. Explicit rather
#: than the shipped default, so changing that default cannot silently move what
#: they assert — 18% a year is 1.5% a month, which keeps the arithmetic legible
#: in the assertions below.
CARRYING = _stock.Carrying(annual_pct=0.18, dead_days=365, slow_days=180)


def test_a_blank_reorder_level_is_never_read_as_zero():
    """An item with no reorder point cannot be below it. Reading blank as zero
    would flag the whole catalogue the day somebody leaves the field empty."""
    from app.commercial.insight import stock

    lines = [
        stock.StockLine("p1", "No policy", 0.0, 0.0, 0.0, None, date(2026, 7, 1), 5.0),
        stock.StockLine("p2", "Has policy", 2.0, 2.0, 2.0, 10.0, date(2026, 7, 1), 5.0),
    ]
    built = stock.build(lines, date(2026, 7, 31), CARRYING, with_cost=False)
    below = next(g for g in built["groups"] if g["key"] == "BELOW_REORDER")
    assert [i["product_id"] for i in below["items"]] == ["p2"]
    assert built["counts"]["no_reorder_point"] == 1
    # And the absence is named rather than silently excluded.
    assert any(u["series"] == "reorder_point" for u in built["unavailable"])


def test_committed_beyond_stock_is_zohos_arithmetic_not_ours():
    from app.commercial.insight import stock

    lines = [stock.StockLine("p1", "CNMG", 40.0, 40.0, -5.0, None,
                             date(2026, 7, 20), 12.0)]
    built = stock.build(lines, date(2026, 7, 31), CARRYING, with_cost=False)
    oversold = next(g for g in built["groups"] if g["key"] == "OVERSOLD")
    assert oversold["count"] == 1
    # Positive available with negative actual is exactly the case: sellable on
    # paper, already promised in reality.
    assert oversold["items"][0]["available"] == 40.0
    assert oversold["items"][0]["actual_available"] == -5.0


def test_weeks_of_cover_is_refused_rather_than_forecast():
    """The only forecast this data supports is "recent sales, repeated", which
    prints as a projection without being one."""
    from app.commercial.insight import stock

    built = stock.build([stock.StockLine("p1", "X", 5.0, 5.0, 5.0, None, None, 0.0)],
                        date(2026, 7, 31), CARRYING, with_cost=False)
    assert any(u["series"] == "weeks_of_cover" for u in built["unavailable"])


def test_stock_value_is_absent_without_cost_scope():
    from app.commercial.insight import stock

    lines = [stock.StockLine("p1", "X", 10.0, 10.0, 10.0, None, None, 0.0,
                             purchase_rate=400.0)]
    plain = stock.build(lines, date(2026, 7, 31), CARRYING, with_cost=False)
    priced = stock.build(lines, date(2026, 7, 31), CARRYING, with_cost=True)
    assert "stock_value" not in plain["counts"]
    assert priced["counts"]["stock_value"] == 4000.0
    for group in plain["groups"]:
        for item in group["items"]:
            assert "purchase_rate" not in item and "stock_value" not in item


def _order(vendor, ordered, received=None, pending=0.0, total=1000.0):
    from app.commercial.insight.supply import SupplierOrder
    return SupplierOrder(vendor_id=vendor, vendor_label=vendor.upper(), number=None,
                         ordered_on=ordered, expected_on=None, received_on=received,
                         pending_qty=pending, ordered_qty=10.0, total=total,
                         status="open" if pending else "billed")


def test_lead_time_is_measured_only_where_a_receipt_was_logged():
    """An average over the orders somebody remembered to close is a statement
    about admin, not about suppliers."""
    from datetime import timedelta

    from app.commercial.insight import supply

    base = date(2026, 1, 1)
    orders = [_order("v1", base + timedelta(days=30 * i),
                     received=base + timedelta(days=30 * i + 14)) for i in range(3)]
    orders.append(_order("v1", base, received=None, pending=5.0))   # never received
    built = supply.build(orders, date(2026, 7, 31))
    assert built["median_lead_time_days"] == pytest.approx(14.0)
    assert built["counts"]["orders_with_a_receipt"] == 3
    assert built["suppliers"][0]["typical_lead_time_days"] == pytest.approx(14.0)


def test_too_few_receipts_means_no_typical_lead_time():
    from app.commercial.insight import supply

    orders = [_order("v1", date(2026, 1, 1), received=date(2026, 1, 15))]
    built = supply.build(orders, date(2026, 7, 31))
    assert built["suppliers"][0]["typical_lead_time_days"] is None
    assert built["suppliers"][0]["receipts_seen"] == 1
    assert built["median_lead_time_days"] is None


def test_a_book_with_no_promised_dates_says_so_rather_than_assuming_one():
    from app.commercial.insight import supply

    built = supply.build([_order("v1", date(2026, 5, 1), pending=5.0)],
                         date(2026, 7, 31))
    assert built["counts"]["orders_with_a_promised_date"] == 0
    assert any(u["series"] == "delivery_against_promise"
               for u in built["unavailable"])
    # Age is answerable and is what the open list ranks by instead.
    assert built["open_orders"][0]["age_days"] == 91


def test_the_supplier_tail_is_not_folded():
    """A supplier list is short and every name on it has a phone number. An
    "Other (14)" band would hide exactly the second sources this question
    exists to find."""
    from app.commercial.insight import supply

    orders = [_order(f"v{i}", date(2026, 5, 1), total=100.0 * (10 - i))
              for i in range(9)]
    built = supply.build(orders, date(2026, 7, 31))
    assert len(built["suppliers"]) == 9
    assert not any(s["vendor_id"] is None for s in built["suppliers"])


def test_the_timeline_stops_apologising_once_payments_are_synced():
    """The refusal was honest while the platform held no receipts. It holds
    them now, so it comes off — but only when a series is actually passed."""
    sales = [_sale("c1", date(2026, 6, 10), 1000, ref="a")]
    without = cohorts.health_timeline(sales, {}, date(2026, 6, 30), months=2)
    assert any(u["series"] == "payment_behaviour" for u in without["unavailable"])
    assert "median_days_to_pay" not in without["series"][0]

    with_payments = cohorts.health_timeline(
        sales, {}, date(2026, 6, 30), months=2,
        payment_series=[{**p, "median_days_to_pay": 31.0, "settled": 1}
                        for p in [x for x in without["series"]]])
    assert with_payments["unavailable"] == []
    assert with_payments["series"][-1]["median_days_to_pay"] == 31.0


def test_the_payment_series_is_keyed_by_invoice_month_not_payment_month():
    """Otherwise January's cash lands on the March row and the three timeline
    rows stop describing the same month."""
    from app.commercial.insight import payments

    windows = periods.months_back(date(2026, 6, 30), 3)
    # Invoiced in April, paid in June.
    settled = [_settled("c1", date(2026, 4, 10), date(2026, 6, 20))]
    series = payments.monthly_series(settled, windows)
    by_label = {p["label"]: p for p in series}
    assert by_label["Apr 2026"]["settled"] == 1
    assert by_label["Jun 2026"]["settled"] == 0


# ── the negotiation desk, in CAF ────────────────────────────────────────────
#
# The currency changed: the desk used to pay a share of price realisation
# against what a customer last paid, and now measures contribution above a
# published floor. These tests are written against the property that made the
# change worth making — a salesperson can compute every figure here themselves,
# from a price and a floor, with no cost anywhere in the payload.
ON = date(2026, 8, 5)


def _deal(**kw):
    from decimal import Decimal

    from app.commercial.incentive import Deal
    base = dict(qty=Decimal("100"), floor_price=Decimal("1220"),
                agreed_price=Decimal("1400"))
    base.update({k: Decimal(str(v)) for k, v in kw.items()})
    return Deal(**base)


def _assess(deal, **kw):
    from app.commercial import incentive
    args = dict(as_of=ON, customer_id="c1", product_id="p1", family="inserts",
                entity_id="SLS", salesperson_id="u1")
    args.update(kw)
    return incentive.assess(deal, **args)


def test_contribution_is_measured_against_the_floor_and_nothing_else():
    """q x (P - F). The arithmetic a salesperson can do on the phone, which is
    the entire reason the floor is disclosable and the cost is not."""
    result = _assess(_deal())
    assert result.contribution == Decimal("18000.00")
    assert result.caf == Decimal("18000.00")


def test_no_cost_no_margin_and_no_floor_margin_is_a_field_on_the_output():
    """Enforced by type, not by a filter. A projection that forgets to strip a
    field cannot leak what the object never carried."""
    from dataclasses import fields

    from app.commercial.incentive import Assessment

    names = {f.name for f in fields(Assessment)} | set(_assess(_deal()).to_dict())
    for forbidden in ("cost", "unit_cost", "margin", "m_floor", "markup"):
        assert not any(forbidden in n for n in names), forbidden


def test_a_discount_costs_exactly_its_own_rupees():
    """I5, linearity. ₹4 off a hundred units is ₹400 off the contribution — not
    a share of it, not a cap, not a fixed point. Anything else and the desk
    stops being arithmetic somebody can check."""
    plain = _assess(_deal()).caf
    discounted = _assess(_deal(customer_discount=4)).caf
    assert plain - discounted == Decimal("400.00")


def test_a_rupee_from_the_vendor_is_worth_a_rupee_held_on_price():
    """Incentive compatibility on the buy side: the salesperson is indifferent
    between the two, which is exactly the owner's own indifference."""
    from_price = _assess(_deal(agreed_price=1410)).caf
    from_vendor = _assess(_deal(vendor_yield=1000)).caf
    assert from_price == from_vendor


def test_a_third_party_payment_is_charged_in_full_and_a_toolkit_at_half():
    """The compliant lever is priced at half the non-compliant one, so it is
    reached for because it is cheaper rather than because anyone said so."""
    paid_across = _assess(_deal(third_party_incentive=2000))
    toolkit = _assess(_deal(toolkit_spend=2000))
    assert paid_across.third_party == Decimal("2000.00")
    assert toolkit.toolkit_charged == Decimal("1000.00")
    assert toolkit.caf - paid_across.caf == Decimal("1000.00")


def test_a_price_below_the_floor_takes_contribution_away_and_says_so():
    """Not floored silently at zero. A line under the floor is the thing the
    whole device exists to charge for."""
    result = _assess(_deal(agreed_price=1100))
    assert result.contribution < 0
    assert result.below_floor
    assert any("below the floor" in w for w in result.warnings)


def test_the_discount_to_the_floor_is_exactly_what_can_be_given_away():
    from app.commercial.incentive import discount_to_floor

    deal = _deal(third_party_incentive=5000, toolkit_spend=4000, vendor_yield=2000)
    per_unit = discount_to_floor(deal, as_of=ON)
    assert per_unit is not None
    at_limit = _assess(_deal(third_party_incentive=5000, toolkit_spend=4000,
                             vendor_yield=2000, customer_discount=per_unit))
    assert at_limit.caf == Decimal("0.00")


def test_there_is_nothing_to_give_away_on_a_line_at_the_floor():
    """An honest "nothing", not zero dressed up as an answer."""
    from app.commercial.incentive import discount_to_floor

    assert discount_to_floor(_deal(agreed_price=1220), as_of=ON) is None
    assert discount_to_floor(_deal(agreed_price=1100), as_of=ON) is None


def test_the_price_that_holds_a_target_is_solved_not_searched():
    from app.commercial.incentive import price_for_target

    deal = _deal(third_party_incentive=5000, toolkit_spend=4000, vendor_yield=2000)
    price = price_for_target(deal, Decimal("9000"), as_of=ON)
    assert price is not None
    check = _assess(_deal(agreed_price=price, third_party_incentive=5000,
                          toolkit_spend=4000, vendor_yield=2000))
    assert check.caf == Decimal("9000.00")


def test_contribution_is_banked_on_invoice_and_earned_on_receipt():
    """Payment timing is a lever the salesperson holds, which is why there is
    no separate collections target anywhere in this product."""
    advance = _assess(_deal(), expected_days_late=-1)
    on_time = _assess(_deal(), expected_days_late=0)
    late = _assess(_deal(), expected_days_late=75)
    assert advance.collected_caf > on_time.collected_caf > late.collected_caf
    assert on_time.collected_caf == on_time.caf


def test_a_third_party_payment_is_unsaveable_on_an_unclassified_account():
    """I2 fails closed. An account nobody has classified is treated exactly
    like a PSU, because the two mistakes do not cost the same."""
    from app.commercial import incentive

    assert incentive.may_pay_third_party("PRIVATE")
    assert not incentive.may_pay_third_party(None)
    assert not incentive.may_pay_third_party("RESTRICTED")

    for eligibility in (None, "RESTRICTED"):
        with pytest.raises(incentive.IncentiveBlocked) as caught:
            incentive.check_third_party(Decimal("1000"), customer_id="c1",
                                        eligibility=eligibility, as_of=ON,
                                        entity_id="SLS")
        assert "blacklisting" in str(caught.value)


def test_the_desk_and_the_payout_run_the_same_calculator():
    """Not "the same formula" — the same function. Two implementations of CAF
    is how a screen and a payslip end up disagreeing about a rupee."""
    import inspect

    from app.commercial import incentive

    source = inspect.getsource(incentive.assess)
    assert "incentive_engine" in source and "line_caf" in source


def test_the_floor_markup_varies_by_family_which_is_what_makes_f_safe():
    """The premise the whole device rests on. With one global markup, a
    salesperson holding one line and one floor recovers the cost by division —
    so if these ever collapse to a single value, F stops being disclosable and
    this test is the thing that says so."""
    from app.commercial.floor import _m_floor

    families = ("inserts", "solid_carbide", "holders_toolsystems", "metrology",
                "chemicals", "machines", None)
    markups = {_m_floor(f, ON) for f in families}
    assert len(markups) > 1, "a single markup makes every floor invertible"


def test_the_operations_floor_and_the_owner_reconciliation_are_two_types():
    """Not one type filtered on the way out. Section 6 of the brief asks for
    the separation to be structural, and this is where it is structural."""
    from dataclasses import fields

    from app.commercial.floor import FloorReconciliation, ResolvedFloor

    ops = {f.name for f in fields(ResolvedFloor)}
    owner = {f.name for f in fields(FloorReconciliation)}
    assert "unit_cost" not in ops and "m_floor" not in ops
    assert "unit_cost" in owner and "m_floor" in owner


def test_incentive_rates_have_exactly_one_home_and_it_is_not_the_thresholds():
    """Compensation policy in two places is the copy that gets edited never
    being the copy that pays."""
    from dataclasses import fields

    from app.commercial import policy
    from app.commercial.config import CommercialThresholds

    assert not [f.name for f in fields(CommercialThresholds)
                if "incentive" in f.name]
    assert not [f for f in policy.EDITABLE if "incentive" in f]


# ── payment patterns ────────────────────────────────────────────────────────
def _pay(customer, invoiced, paid, due=None, ref=None):
    from app.commercial.insight.payments import Settlement
    return Settlement(customer, ref or f"{customer}-{invoiced}", None, invoiced,
                      due, paid, 1000.0)


def test_a_consistent_payer_is_prompt_and_a_wild_one_is_erratic():
    """Slow and unpredictable are different problems. A customer who always
    takes 45 days can be planned around; one who takes 5 or 95 cannot."""
    from datetime import timedelta

    from app.commercial.insight import payments

    base = date(2026, 1, 1)
    steady = [_pay("a", base + timedelta(days=30 * i),
                   base + timedelta(days=30 * i + 28),
                   base + timedelta(days=30 * i + 30)) for i in range(5)]
    wild = [_pay("c", base + timedelta(days=30 * i),
                 base + timedelta(days=30 * i + d),
                 base + timedelta(days=30 * i + 30))
            for i, d in enumerate([5, 80, 12, 95, 30])]

    assert payments.classify(steady)["pattern"] == "PROMPT"
    assert payments.classify(wild)["pattern"] == "ERRATIC"


def test_consistently_late_is_a_terms_problem_not_a_collections_problem():
    from datetime import timedelta

    from app.commercial.insight import payments

    base = date(2026, 1, 1)
    late = [_pay("b", base + timedelta(days=30 * i),
                 base + timedelta(days=30 * i + 40),
                 base + timedelta(days=30 * i + 30)) for i in range(5)]
    got = payments.classify(late)
    assert got["pattern"] == "PREDICTABLY_LATE"
    assert got["median_days_late"] == pytest.approx(10.0)
    assert "terms problem" in payments.PATTERNS["PREDICTABLY_LATE"]["meaning"]


def test_no_pattern_is_asserted_below_the_floor():
    from app.commercial.insight import payments

    thin = [_pay("d", date(2026, 1, 1), date(2026, 2, 1))]
    assert payments.classify(thin)["pattern"] == "TOO_FEW"


def test_the_spread_survives_one_catastrophic_invoice():
    """Median absolute deviation, not standard deviation: one invoice settled
    nine months late is a story about that invoice, not about the customer."""
    from datetime import timedelta

    from app.commercial.insight import payments

    base = date(2026, 1, 1)
    rows = [_pay("e", base + timedelta(days=30 * i),
                 base + timedelta(days=30 * i + 30)) for i in range(6)]
    rows.append(_pay("e", base, base + timedelta(days=280), ref="e-outlier"))
    got = payments.classify(rows)
    assert got["spread_days"] <= payments.ERRATIC_SPREAD_DAYS
    assert got["pattern"] != "ERRATIC"


def test_the_trend_compares_halves_by_invoice_date():
    from datetime import timedelta

    from app.commercial.insight import payments

    base = date(2026, 1, 1)
    # Older invoices took 60 days, newer ones take 20.
    rows = [_pay("f", base + timedelta(days=15 * i),
                 base + timedelta(days=15 * i + (60 if i < 3 else 20)))
            for i in range(6)]
    assert payments.classify(rows)["trend"] == "IMPROVING"


def test_batching_and_part_payment_are_counted_because_they_change_the_call():
    from app.commercial.insight import payments

    # One transfer clearing three invoices, and one invoice paid in two parts.
    rows = [_pay("g", date(2026, 1, i + 1), date(2026, 3, 1), ref=f"g-{i}")
            for i in range(3)]
    rows.append(_pay("g", date(2026, 1, 1), date(2026, 4, 1), ref="g-0-part2"))
    rows[-1] = payments.Settlement("g", "g-inv-0", None, date(2026, 1, 1), None,
                                   date(2026, 4, 1), 500.0)
    rows.append(payments.Settlement("g", "g-inv-0", None, date(2026, 1, 1), None,
                                    date(2026, 5, 1), 500.0))
    got = payments.classify(rows)
    assert got["largest_batch"] == 3
    assert got["part_paid_invoices"] == 1


# ── the business day ────────────────────────────────────────────────────────
#
# Storage is UTC and stays UTC. These pin the *other* question — which day is it
# for this business — which the code used to answer with `date.today()`: the
# server's zone, and UTC in every container this runs in.

def test_the_business_day_is_not_the_servers_day():
    """Between 18:30 and 24:00 UTC it is already tomorrow in India. A pull that
    runs then stamped yesterday onto today's shelf count, and the sync screen
    said a run that had just finished happened the day before."""
    from datetime import datetime, timezone as tz

    from app import clock

    instant = datetime(2026, 8, 5, 19, 30, tzinfo=tz.utc)   # 01:00 IST on the 6th
    assert instant.date() == date(2026, 8, 5)
    assert clock.to_local(instant, "Asia/Kolkata").date() == date(2026, 8, 6)


def test_an_unknown_timezone_falls_back_rather_than_raising():
    """A bad string in one tenant's row must not take that organization's
    screens down; the fallback is the zone the deployment already runs on."""
    from app import clock

    assert str(clock.zone("Not/AZone")) == clock.DEFAULT_ZONE
    assert str(clock.zone(None)) == clock.DEFAULT_ZONE
    assert str(clock.zone("Asia/Dubai")) == "Asia/Dubai"


def test_the_timezone_is_inside_the_thresholds_version():
    """Two zones put a month boundary in two different places, so rows computed
    under one are not comparable to rows computed under the other."""
    from app.commercial.config import CommercialThresholds

    assert (CommercialThresholds(timezone="Asia/Kolkata").version
            != CommercialThresholds(timezone="Asia/Dubai").version)


# ── what the shelf costs to keep ────────────────────────────────────────────
def _shelf(**kw):
    base = dict(product_id="p1", label="CNMG 120408", on_hand=100.0,
                available=100.0, actual_available=100.0, reorder_level=None,
                last_sold=date(2026, 7, 1), sold_qty_window=5.0,
                purchase_rate=400.0)
    base.update(kw)
    return _stock.StockLine(**base)


def test_the_monthly_drain_is_value_times_the_rate_over_twelve():
    """100 pieces at ₹400 is ₹40,000 on the shelf; at 18% a year that is ₹600 a
    month. Asserted as a number rather than recomputed from the same expression
    the implementation uses — a test that repeats the formula verifies nothing."""
    line = _shelf()
    assert line.inventory_value() == 40_000.0
    assert line.monthly_holding_cost(CARRYING) == 600.0
    assert line.annual_holding_cost(CARRYING) == 7_200.0


def test_an_item_with_no_purchase_cost_has_no_drain_rather_than_zero():
    """Zero would sort it to the bottom of a worst-first list, which is exactly
    where a line nobody has costed must not quietly sit."""
    line = _shelf(purchase_rate=None)
    assert line.inventory_value() is None
    assert line.monthly_holding_cost(CARRYING) is None
    assert line.priority(date(2026, 8, 5), CARRYING) is None


def test_health_is_age_alone_because_value_does_not_make_stock_fresh():
    as_of = date(2026, 8, 5)
    assert _shelf(last_sold=date(2026, 7, 1)).health(as_of, CARRYING) == _stock.HEALTHY
    assert _shelf(last_sold=date(2026, 1, 1)).health(as_of, CARRYING) == _stock.SLOW
    assert _shelf(last_sold=date(2024, 1, 1)).health(as_of, CARRYING) == _stock.DEAD
    # Never sold and on the shelf is the strongest version of dead, not a
    # reason to leave the row out.
    assert _shelf(last_sold=None).health(as_of, CARRYING) == _stock.DEAD
    # Nothing on the shelf cannot be dead stock — there is no stock.
    assert _shelf(on_hand=0.0, last_sold=None).health(as_of, CARRYING) == _stock.HEALTHY


def test_the_recommended_action_is_a_band_not_a_judgement():
    as_of = date(2026, 8, 5)
    assert _shelf(last_sold=date(2026, 7, 1)).recommended_action(as_of, CARRYING) == "HOLD"
    assert _shelf(last_sold=date(2026, 1, 1)).recommended_action(as_of, CARRYING) == "PUSH"
    # Dead, and somebody has bought it before: there is a call to make.
    assert _shelf(last_sold=date(2025, 1, 1),
                  buyers=("Brakes India",)).recommended_action(as_of, CARRYING) == "DISCOUNT"
    # Dead with nobody who has ever bought it: bundling is the remaining lever.
    assert _shelf(last_sold=date(2025, 1, 1)).recommended_action(as_of, CARRYING) == "BUNDLE"
    # Never sold once. Nothing here evidences a discount; it is a question for
    # whoever bought it, so the proposal is a write-off rather than a price.
    assert _shelf(last_sold=None).recommended_action(as_of, CARRYING) == "WRITE_OFF"


def test_priority_is_the_drain_itself_not_an_invented_score():
    """A weighted 0–100 blend of age, quantity and value is a number nobody can
    check and whose meaning moves whenever a weight does. Rupees per month is
    already the right ranking, and it is arithmetic anyone can redo."""
    line = _shelf()
    assert line.priority(date(2026, 8, 5), CARRYING) == line.monthly_holding_cost(CARRYING)


def test_a_salesperson_gets_the_drain_and_not_the_value_behind_it():
    """The uncomfortable line on this screen, pinned. The drain is what makes
    dead stock actionable, so it stays; everything it is computed *from* goes."""
    row = _shelf().to_dict(date(2026, 8, 5), CARRYING, with_cost=False)
    assert row["monthly_holding_cost"] == 600.0
    for banned in ("purchase_rate", "inventory_value", "stock_value",
                   "annual_holding_cost", "last_purchased"):
        assert banned not in row, banned

    owner = _shelf().to_dict(date(2026, 8, 5), CARRYING, with_cost=True)
    assert owner["inventory_value"] == 40_000.0
    assert owner["annual_holding_cost"] == 7_200.0


def test_the_carrying_rate_is_never_in_any_payload():
    """The one number that makes the drain invertible back into cost. It is
    owner policy, set in Settings — it must not ride out on the stock response
    for any role, including an owner's, because the response is what a browser
    keeps."""
    built = _stock.build([_shelf()], date(2026, 8, 5), CARRYING, with_cost=True)

    def walk(node):
        """Every key and every scalar in the response, flattened."""
        if isinstance(node, dict):
            for k, v in node.items():
                yield ("key", k)
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
        else:
            yield ("value", node)

    for kind, item in walk(built):
        if kind == "key":
            assert "carrying" not in item and "annual_pct" not in item, item
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            # The rate itself, and the monthly rate it divides into. Prose may
            # mention that a rate exists; the number must not appear.
            assert item != pytest.approx(CARRYING.annual_pct), item
            assert item != pytest.approx(CARRYING.monthly_pct), item


def test_the_grid_ranks_by_what_each_line_costs_to_keep():
    lines = [
        _shelf(product_id="cheap", on_hand=10.0, purchase_rate=100.0),
        _shelf(product_id="dear", on_hand=500.0, purchase_rate=900.0),
        _shelf(product_id="unpriced", purchase_rate=None),
    ]
    built = _stock.build(lines, date(2026, 8, 5), CARRYING, with_cost=True)
    # Costliest first; the line nobody has costed sorts last, as unknown.
    assert [i["product_id"] for i in built["items"]] == ["dear", "cheap", "unpriced"]


def test_every_kpi_carries_the_filter_that_produced_it():
    """A headline figure somebody cannot drill into is a figure they have to
    take on trust, which is the opposite of what this product is for."""
    built = _stock.build([_shelf(last_sold=date(2024, 1, 1))], date(2026, 8, 5),
                         CARRYING, with_cost=True)
    keys = {f["key"] for f in built["filters"]}
    for card in built["kpis"]:
        assert card["filter"] is None or card["filter"] in keys, card


def test_a_forecast_is_refused_rather_than_dressed_as_a_probability():
    built = _stock.build([_shelf()], date(2026, 8, 5), CARRYING, with_cost=True)
    refused = {u["series"] for u in built["unavailable"]}
    assert {"recovery_probability", "expected_recovery_value",
            "branch", "supplier_and_brand"} <= refused
