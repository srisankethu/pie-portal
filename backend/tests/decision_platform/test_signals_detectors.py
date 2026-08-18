"""Deterministic detector behaviour across realistic scenarios and edge cases."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.signals import cost_pass_through, decline, dormancy, margin
from app.signals.base import Anomaly, Withholding
from app.signals.config import SignalThresholds
from app.signals.quality import cost_anomalies, cost_is_reliable, sales_outliers

from .signal_fixtures import (
    AS_OF,
    cost,
    cost_pass_through_product,
    declining_customer,
    dormant_customer,
    margin_deterioration_product,
    sale,
    snap,
    steady_customer,
)

TH = SignalThresholds()


# ── CUSTOMER_DECLINE ─────────────────────────────────────────────────────────
def test_decline_triggers_on_real_drop():
    out = decline.detect(snap(sales=declining_customer()), TH, AS_OF)
    assert len(out) == 1
    d = out[0]
    assert d.subject_entity_id == "c_decline"
    assert d.metrics["pct_change"] < -0.30
    assert d.metrics["baseline_revenue"] == 30000.0
    assert d.metrics["recent_revenue"] == 12000.0
    assert d.metrics["top_declining_products"]          # populated
    assert d.evidence_refs and d.severity_base > 0
    assert "basis_period" in d.window and "comparison_period" in d.window


def test_decline_steady_customer_no_signal():
    assert decline.detect(snap(sales=steady_customer()), TH, AS_OF) == []


def test_decline_insufficient_history_no_signal():
    # a new customer: orders only in the last two months
    new = [sale("c_new", "p1", "2026-06-01", 10, 100, invoice="i1"),
           sale("c_new", "p1", "2026-06-15", 10, 100, invoice="i2"),
           sale("c_new", "p1", "2026-07-01", 2, 100, invoice="i3")]
    assert decline.detect(snap(sales=new), TH, AS_OF) == []


def test_decline_below_threshold_no_false_positive():
    # baseline 30000, recent ~21900 => -27% (< 30% threshold) => no signal
    s = [sale("c", "p1", "2025-07-01", 100, 100, invoice="h")]
    for i, d in enumerate(["2026-02-01", "2026-03-01", "2026-04-01"]):
        s.append(sale("c", "p1", d, 100, 100, invoice=f"b{i}"))
    for i, d in enumerate(["2026-05-01", "2026-06-01", "2026-07-01"]):
        s.append(sale("c", "p1", d, 73, 100, invoice=f"r{i}"))  # 7300*3 = 21900
    assert decline.detect(snap(sales=s), TH, AS_OF) == []


def test_decline_flags_outliers_in_sufficiency():
    s = declining_customer()
    s.append(sale("c_decline", "p1", "2026-06-15", -5, 100, invoice="credit"))  # negative
    out = decline.detect(snap(sales=s), TH, AS_OF)
    assert out and any(a["code"] == Anomaly.NEGATIVE_VALUE
                       for a in out[0].sufficiency.anomalies)


# ── CUSTOMER_DORMANCY ────────────────────────────────────────────────────────
def test_dormancy_triggers_when_overdue():
    out = dormancy.detect(snap(sales=dormant_customer()), TH, AS_OF)
    assert len(out) == 1
    d = out[0]
    assert d.subject_entity_id == "c_dormant"
    assert d.metrics["actual_gap_days"] > d.metrics["expected_interval_days"]
    assert d.metrics["order_count"] >= TH.dormancy_min_orders


def test_dormancy_active_customer_no_signal():
    # steady customer ordered on AS_OF → gap 0
    assert dormancy.detect(snap(sales=steady_customer()), TH, AS_OF) == []


def test_dormancy_sparse_customer_insufficient():
    sparse = [sale("c_sparse", "p1", "2025-08-01", 10, 100, invoice="i1"),
              sale("c_sparse", "p1", "2026-01-01", 10, 100, invoice="i2")]
    assert dormancy.detect(snap(sales=sparse), TH, AS_OF) == []


# ── MARGIN_DETERIORATION ─────────────────────────────────────────────────────
def test_margin_deterioration_triggers():
    sales, costs = margin_deterioration_product()
    out = margin.detect(snap(sales=sales, costs=costs), TH, AS_OF)
    assert len(out) == 1
    m = out[0].metrics
    assert round(m["baseline_margin_pct"], 3) == 0.3
    assert abs(m["current_margin_pct"] - 0.125) < 1e-6
    assert m["margin_drop_points"] > TH.margin_drop_points


def test_margin_missing_cost_withholds():
    sales, _ = margin_deterioration_product()
    assert margin.detect(snap(sales=sales, costs=[]), TH, AS_OF) == []


def test_margin_cost_gt_price_withholds():
    costs = [cost("p_bad", "2025-06-01", 1, 200, bill="b")]  # cost 200 > price 100
    sales = [sale("cA", "p_bad", d, 10, 100, invoice=f"i{i}")
             for i, d in enumerate(["2026-02-01", "2026-03-01", "2026-05-01", "2026-06-01"])]
    assert margin.detect(snap(sales=sales, costs=costs), TH, AS_OF) == []


def test_margin_placeholder_prior_cost_withholds():
    """A zero/placeholder PRIOR cost would inflate the baseline margin toward 100%
    and fabricate a huge false deterioration — it must be withheld, not asserted."""
    costs = [cost("p_ph", "2026-01-15", 100, 0, bill="b-ph"),   # prior basis: placeholder 0
             cost("p_ph", "2026-05-01", 100, 70, bill="b-ok")]  # recent basis: reliable
    sales = ([sale("cA", "p_ph", d, 10, 100, invoice=f"i-b{i}")
              for i, d in enumerate(["2026-02-01", "2026-03-01"])]
             + [sale("cA", "p_ph", d, 10, 80, invoice=f"i-r{i}")
                for i, d in enumerate(["2026-05-05", "2026-06-01"])])
    assert margin.detect(snap(sales=sales, costs=costs), TH, AS_OF) == []


def _margin_drop_of_one_sixteenth():
    """Price flat at 128, cost 64 → 72: margins 0.5 and 0.4375, a drop of exactly
    0.0625.

    Powers of two throughout, so every division is exact in binary and the
    comparison genuinely lands *on* the threshold rather than a hair either side
    of it. Round decimal numbers cannot do this — 0.40 − 0.35 is
    0.050000000000000044, which would silently test the wrong side.
    """
    costs = [cost("p_edge", "2025-06-01", 100, 64, bill="b-edge-lo"),
             cost("p_edge", "2026-05-01", 100, 72, bill="b-edge-hi")]
    sales = ([sale("cE", "p_edge", d, 10, 128, invoice=f"i-e-b{i}")
              for i, d in enumerate(["2026-02-01", "2026-03-01"])]
             + [sale("cE", "p_edge", d, 10, 128, invoice=f"i-e-r{i}")
                for i, d in enumerate(["2026-05-05", "2026-06-01"])])
    return sales, costs


def test_a_margin_drop_exactly_on_the_threshold_is_not_a_deterioration():
    """The threshold is the point at which a drop becomes worth routing to a
    human, and the rule is "more than", not "at least" — a drop of exactly the
    threshold is the largest movement the platform still considers normal.

    The second half matters as much as the first: the same data one notch under
    the threshold *does* fire, so what the first assertion pins is the boundary
    and not a fixture that was never going to produce a signal.
    """
    sales, costs = _margin_drop_of_one_sixteenth()
    at_the_line = replace(TH, margin_drop_points=0.0625)
    assert margin.detect(snap(sales=sales, costs=costs), at_the_line, AS_OF) == []

    just_under = replace(TH, margin_drop_points=0.06)
    out = margin.detect(snap(sales=sales, costs=costs), just_under, AS_OF)
    assert len(out) == 1
    assert out[0].metrics["margin_drop_points"] == 0.0625


def test_a_non_positive_price_withholds_a_margin_rather_than_dividing():
    """Zero is the case that matters: it is a real value a sales line can carry
    (a free replacement, a sample), and it is the one that divides. Withheld, not
    computed and not crashed."""
    assert margin._margin(Decimal("0"), Decimal("70")) is None
    assert margin._margin(Decimal("-5"), Decimal("70")) is None
    assert margin._margin(None, Decimal("70")) is None


def test_cost_anomaly_helpers():
    a = cost_anomalies(Decimal("200"), Decimal("100"), TH)
    assert {x["code"] for x in a} >= {Anomaly.COST_GT_PRICE, Anomaly.NEGATIVE_MARGIN}
    assert not cost_is_reliable(a)
    z = cost_anomalies(Decimal("0"), Decimal("100"), TH)
    assert any(x["code"] == Anomaly.ZERO_OR_PLACEHOLDER_COST for x in z)
    assert cost_is_reliable(cost_anomalies(Decimal("70"), Decimal("100"), TH))


# ── COST_PASS_THROUGH ────────────────────────────────────────────────────────
def test_cost_pass_through_triggers():
    sales, costs = cost_pass_through_product()
    out = cost_pass_through.detect(snap(sales=sales, costs=costs), TH, AS_OF)
    assert len(out) == 1
    m = out[0].metrics
    assert abs(m["cost_delta_pct"] - 0.25) < 1e-6
    assert m["price_change_pct"] < m["cost_delta_pct"]
    assert m["affected_customer_ids"] == ["cB"]


def test_cost_pass_through_single_cost_point_insufficient():
    sales, _ = cost_pass_through_product()
    costs = [cost("p_cost", "2026-05-01", 100, 500, bill="only")]  # single point
    assert cost_pass_through.detect(snap(sales=sales, costs=costs), TH, AS_OF) == []


def test_cost_pass_through_price_kept_pace_no_signal():
    costs = [cost("p_k", "2026-01-01", 100, 400, bill="c1"),
             cost("p_k", "2026-05-01", 100, 500, bill="c2")]  # +25%
    sales = [sale("cB", "p_k", d, 10, 530, invoice=f"b{i}")
             for i, d in enumerate(["2026-02-01", "2026-03-01", "2026-04-01"])]
    sales += [sale("cB", "p_k", d, 10, 662, invoice=f"r{i}")   # +24.9%, kept pace
              for i, d in enumerate(["2026-05-15", "2026-06-01", "2026-07-01"])]
    assert cost_pass_through.detect(snap(sales=sales, costs=costs), TH, AS_OF) == []


# ── outliers / negatives (data quality) ──────────────────────────────────────
def test_sales_outliers_flags_spike_and_negative():
    rows = [sale("c", "p", "2026-05-01", 10, 100, invoice="i1"),
            sale("c", "p", "2026-05-02", 10, 100, invoice="i2"),
            sale("c", "p", "2026-05-03", 10, 100, invoice="i3"),
            sale("c", "p", "2026-05-04", 900, 100, invoice="i4"),   # qty spike
            sale("c", "p", "2026-05-05", 10, -5, invoice="i5")]     # negative price
    codes = {a["code"] for a in sales_outliers(rows, TH)}
    assert Anomaly.QTY_SPIKE in codes
    assert Anomaly.NEGATIVE_VALUE in codes


def test_no_crash_on_empty_snapshot():
    s = snap()
    assert decline.detect(s, TH, AS_OF) == []
    assert dormancy.detect(s, TH, AS_OF) == []
    assert margin.detect(s, TH, AS_OF) == []
    assert cost_pass_through.detect(s, TH, AS_OF) == []


# ── coverage: the difference between "nothing wrong" and "could not look" ────
# Every detector used to `continue` silently on insufficient evidence, so a book
# whose customers all have four months of history produced exactly the same
# empty screen as a book whose customers are all healthy. `examine` keeps the
# denominator and the reasons; these pin that the two stay distinguishable.
def test_a_thin_book_and_a_healthy_book_do_not_look_alike():
    """The §1 failure this contract exists to close.

    Both runs raise zero signals. Only one of them examined anything.
    """
    new = [sale("c_new", "p1", "2026-06-01", 10, 100, invoice="i1"),
           sale("c_new", "p1", "2026-06-15", 10, 100, invoice="i2"),
           sale("c_new", "p1", "2026-07-01", 2, 100, invoice="i3")]
    thin = decline.examine(snap(sales=new), TH, AS_OF)
    healthy = decline.examine(snap(sales=steady_customer()), TH, AS_OF)

    assert thin.drafts == [] and healthy.drafts == []
    assert thin.clear == 0, "nothing in the thin book was actually judged"
    assert healthy.clear == 1, "the steady customer was judged, and was fine"
    assert thin.withheld_counts() == {Withholding.NOT_ENOUGH_HISTORY: 1}
    assert healthy.withheld_counts() == {}


def test_every_subject_lands_in_exactly_one_outcome():
    """found + clear + withheld = considered, with no subject counted twice.

    The partition is what makes the report addable. If a subject could be both
    withheld and clear the coverage figures would overlap and a reader summing
    them would get a denominator larger than the book.
    """
    sales = declining_customer() + steady_customer() + [
        sale("c_new", "p1", "2026-06-01", 10, 100, invoice="i9")]
    coverage = decline.examine(snap(sales=sales), TH, AS_OF)

    assert coverage.considered == 3
    assert len(coverage.drafts) + coverage.clear + len(coverage.withheld) == 3
    assert len(coverage.drafts) == 1 and coverage.clear == 1
    assert len(coverage.withheld) == 1


def test_a_book_with_no_purchase_costs_says_so_rather_than_going_quiet():
    """The most consequential withhold of the four.

    A book that has synced invoices but no bills produces no margin signal for
    any product. As a bare count that is a business with no margin problem.
    """
    sales, _ = margin_deterioration_product()
    coverage = margin.examine(snap(sales=sales, costs=[]), TH, AS_OF)

    assert coverage.drafts == []
    assert coverage.considered > 0
    assert coverage.clear == 0
    assert coverage.withheld_counts() == {
        Withholding.NO_COST_ON_RECORD: coverage.considered}


def test_a_result_below_the_threshold_is_clear_and_not_withheld():
    """The good news has to be real news.

    A margin that moved less than the threshold was examined on reliable cost
    and found acceptable. Filing that under "could not judge" would make an
    honest book unreadable in the other direction.
    """
    sales, costs = _margin_drop_of_one_sixteenth()
    coverage = margin.examine(snap(sales=sales, costs=costs),
                              replace(TH, margin_drop_points=0.20), AS_OF)

    assert coverage.drafts == []
    assert coverage.withheld == [], "cost was present and reliable — this was judged"
    assert coverage.clear == coverage.considered


def test_a_customer_with_too_few_orders_is_unjudgeable_not_on_time():
    """Dormancy's own version of the same trap.

    A customer with two orders ever has no established rhythm, so they are not
    late — but neither are they punctual, and counting them among the quiet
    majority is what made a sparse book look like a calm one.
    """
    sparse = [sale("c_sparse", "p1", "2026-01-10", 5, 100, invoice="s1"),
              sale("c_sparse", "p1", "2026-02-10", 5, 100, invoice="s2")]
    coverage = dormancy.examine(snap(sales=sparse),
                                replace(TH, dormancy_min_orders=4), AS_OF)

    assert coverage.drafts == []
    assert coverage.clear == 0
    assert coverage.withheld_counts() == {Withholding.CADENCE_NOT_ESTABLISHED: 1}


def test_every_withholding_reason_can_be_read_by_a_person():
    """A reason code with no sentence behind it reaches a screen as a code."""
    from app.signals.base import WITHHOLDING_REASONS

    codes = {v for k, v in vars(Withholding).items() if not k.startswith("_")
             and isinstance(v, str)}
    assert codes and codes <= set(WITHHOLDING_REASONS)
    assert all(WITHHOLDING_REASONS[c].strip() for c in codes)
