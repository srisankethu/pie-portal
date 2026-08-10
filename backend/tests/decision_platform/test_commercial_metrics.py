"""Customer × Item metrics: margin history, cost-vs-price, volume, gaps.

Analyses 1, 2, 4 and 5. The cases that matter are the ones where a naive
implementation produces a confident, wrong answer: averaging percentages,
annualizing six weeks of trading, or calling a successful volume trade
"leakage".
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.economics import line_economics
from app.commercial.metrics import (
    COST_DRIVEN,
    MIXED,
    NONE,
    PRICE_DRIVEN,
    _sufficiency,
    classify_erosion,
    compute_relationship,
)
from app.domain.enums import EvidenceSufficiency
from app.signals.base import CostRow, SaleRow

AS_OF = date(2026, 7, 1)
TH = CommercialThresholds()


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _line(days_ago, qty, price, cost=None, invoice=None):
    q, p = Decimal(str(qty)), Decimal(str(price))
    when = _d(days_ago)
    ref = invoice or f"INV-{days_ago}"
    sale = SaleRow(customer_id="c1", product_id="p1", date=when, qty=q, unit_price=p,
                   line_revenue=q * p,
                   source_ref={"record_type": "invoice", "record_id": ref},
                   external_ref=f"{ref}:1")
    costs = ([CostRow(product_id="p1", date=when - timedelta(days=1), qty=Decimal("1"),
                      unit_cost=Decimal(str(cost)), source_ref={"record_id": "B"},
                      external_ref="B:1")]
             if cost is not None else [])
    return line_economics(sale, costs)


def _relationship(lines):
    return compute_relationship("c1", "p1", lines, AS_OF, TH)


# ── Analysis 1: margin history ──────────────────────────────────────────────
def test_aggregated_period_margins_use_profit_over_revenue():
    """Not a mean of transaction percentages — the single rule that decides
    whether a portfolio number is trustworthy."""
    lines = [
        _line(30, 4000, 100, cost=80),      # ₹4,00,000 at 20%, recent
        _line(20, 10, 100, cost=40),        # ₹1,000 at 60%, recent
    ]
    m = _relationship(lines)
    assert abs(m.current_margin - 0.201) < 1e-3, "a 20/60 mean would be 40%"


def test_margin_change_is_in_percentage_points_not_percent():
    lines = [_line(d, 100, 135, cost=100) for d in (600, 500, 400, 300)]   # 25.9%
    lines += [_line(d, 100, 139, cost=124) for d in (80, 50, 20)]          # 10.8%
    m = _relationship(lines)

    assert abs(m.historical_margin - 0.2593) < 1e-3
    assert abs(m.current_margin - 0.1079) < 1e-3
    # 25.93% -> 10.79% is -15.1 POINTS, not -58% (which is the percent change)
    assert abs(m.margin_change_pp - (-0.1513)) < 1e-3


def test_historical_baseline_excludes_the_recent_window():
    """Including recent in its own baseline would dilute exactly the move being
    measured, and understate every real deterioration."""
    lines = [_line(d, 100, 200, cost=100) for d in (500, 400, 300)]  # 50% baseline
    lines += [_line(d, 100, 110, cost=100) for d in (60, 30)]        # ~9% recent
    m = _relationship(lines)
    assert abs(m.historical_margin - 0.5) < 1e-6, "recent must not be in the baseline"


def test_period_margins_are_reported_for_3_6_and_12_months():
    lines = [_line(d, 10, 200, cost=100) for d in (350, 200, 100, 40, 10)]
    m = _relationship(lines)
    assert m.margin_3m is not None and m.margin_6m is not None and m.margin_12m is not None


def test_a_relationship_with_no_cost_reports_revenue_but_no_margin():
    m = _relationship([_line(d, 10, 200) for d in (300, 200, 100, 30)])
    assert m.revenue_recent > 0
    assert m.current_margin is None and m.historical_margin is None
    assert m.cost_missing_txns == 4 and m.cost_covered_txns == 0
    assert m.data_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert any("cost known for only" in r for r in m.sufficiency_reasons)


def test_empty_relationship_is_insufficient_not_zero():
    m = _relationship([])
    assert m.transaction_count == 0
    assert m.current_margin is None
    assert m.data_sufficiency is EvidenceSufficiency.INSUFFICIENT


# ── Analysis 2: cost vs price classification ────────────────────────────────
@pytest.mark.parametrize("cost_change,price_change,expected", [
    (0.20, 0.00, COST_DRIVEN),     # cost up, price flat
    (0.20, 0.20, NONE),            # cost up, price kept pace exactly
    (0.20, 0.25, NONE),            # price outran cost
    (0.20, 0.05, COST_DRIVEN),     # cost up, price up insufficiently
    (0.00, -0.10, PRICE_DRIVEN),   # cost flat, price down
    (-0.10, 0.00, NONE),           # cost down, price flat — not erosion
    (0.20, -0.10, MIXED),          # cost up AND price down
    (0.01, 0.00, NONE),            # neither moved materially
    # Exactly on the thresholds. "Material" is defined as at-or-above, so the
    # boundary value itself counts. Without these three the comparisons could be
    # loosened to a strict > and nothing in the suite would object.
    (0.05, 0.00, COST_DRIVEN),     # cost rise exactly at meaningful_cost_increase_pct
    (0.00, -0.02, PRICE_DRIVEN),   # price fall exactly at meaningful_price_change_pct
    (0.05, -0.02, MIXED),          # both exactly at their thresholds
])
def test_erosion_classification_is_deterministic(cost_change, price_change, expected):
    assert classify_erosion(cost_change, price_change, TH) == expected


def test_erosion_classification_needs_both_measurements():
    assert classify_erosion(None, 0.1, TH) == NONE
    assert classify_erosion(0.1, None, TH) == NONE


# ── Evidence sufficiency, exactly on each threshold ──────────────────────────
#
# Every number in this ladder is a policy decision about when the platform is
# allowed to sound certain, and each is written as "at or above". Every case
# below sits precisely on a threshold, because that is the one value a test
# supplying round numbers never reaches — and a rung quietly loosened from >= to
# > changes what the whole platform is willing to assert while every other test
# stays green.
def test_a_relationship_exactly_on_the_floor_thresholds_is_not_insufficient():
    """3 transactions, 3.0 months, 60% costed — each exactly the stated minimum.

    "Below this: nothing is asserted" means below, not at.
    """
    verdict, reasons = _sufficiency(3, 3.0, 0.6, TH)
    assert verdict is not EvidenceSufficiency.INSUFFICIENT
    assert not any("need" in r for r in reasons)


def test_a_relationship_exactly_on_the_strong_thresholds_is_sufficient():
    """6 transactions and 6.0 months is the definition of enough — with no
    caveat attached, because a caveat here reads as doubt the data does not
    warrant."""
    verdict, reasons = _sufficiency(6, 6.0, 0.6, TH)
    assert verdict is EvidenceSufficiency.SUFFICIENT
    assert reasons == []


def test_history_exactly_at_the_strong_span_is_not_given_as_a_reason():
    """Short of the transaction count but with the full span: the span is not
    what is lacking, so it must not appear in the reasons."""
    verdict, reasons = _sufficiency(5, 6.0, 0.6, TH)
    assert verdict is EvidenceSufficiency.PARTIAL
    assert len(reasons) == 1
    assert "transactions" in reasons[0]
    assert not any("months" in r for r in reasons)


def test_transactions_exactly_at_the_strong_count_are_not_given_as_a_reason():
    """The mirror of the case above — enough trades, not enough span."""
    verdict, reasons = _sufficiency(6, 5.0, 0.6, TH)
    assert verdict is EvidenceSufficiency.PARTIAL
    assert len(reasons) == 1
    assert "months" in reasons[0]


def test_cost_driven_erosion_is_detected_end_to_end():
    lines = [_line(d, 100, 135, cost=100) for d in (600, 500, 400, 300)]
    lines += [_line(d, 100, 139, cost=124) for d in (80, 50, 20)]
    m = _relationship(lines)
    assert m.erosion_kind == COST_DRIVEN
    assert abs(m.cost_change_pct - 0.24) < 1e-6
    assert abs(m.price_change_pct - 0.0296) < 1e-3


def test_price_driven_erosion_is_detected_end_to_end():
    lines = [_line(d, 100, 200, cost=100) for d in (600, 500, 400, 300)]
    lines += [_line(d, 100, 160, cost=100) for d in (80, 50, 20)]
    m = _relationship(lines)
    assert m.erosion_kind == PRICE_DRIVEN
    assert abs(m.cost_change_pct) < 1e-9
    assert abs(m.price_change_pct - (-0.2)) < 1e-6


# ── Analysis 5: volume ──────────────────────────────────────────────────────
def test_volume_growth_is_measured_against_the_previous_equal_window():
    lines = [_line(d, 100, 200, cost=150) for d in (170, 140, 100)]   # previous
    lines += [_line(d, 310, 180, cost=150) for d in (80, 50, 20)]     # recent
    m = _relationship(lines)
    assert m.qty_previous == Decimal("300") and m.qty_recent == Decimal("930")
    assert abs(m.volume_change_pct - 2.1) < 1e-6


def test_flat_volume_is_reported_as_flat_not_growth():
    lines = [_line(d, 100, 200, cost=150) for d in (170, 140, 100)]
    lines += [_line(d, 105, 180, cost=150) for d in (80, 50, 20)]
    m = _relationship(lines)
    assert abs(m.volume_change_pct - 0.05) < 1e-6
    assert m.volume_change_pct < TH.meaningful_volume_change_pct


def test_volume_change_is_none_without_a_comparable_previous_period():
    """No previous-window trading means there is nothing to compare against —
    which is different from "volume did not change"."""
    m = _relationship([_line(d, 100, 200, cost=150) for d in (80, 50, 20)])
    assert m.volume_change_pct is None


# ── Analysis 4: margin gap and annualization ────────────────────────────────
def test_historical_margin_gap_is_recent_revenue_times_the_lost_points():
    lines = [_line(d, 100, 200, cost=100) for d in (600, 500, 400, 300)]  # 50%
    lines += [_line(d, 100, 200, cost=140) for d in (80, 50, 20)]         # 30%
    m = _relationship(lines)

    assert abs(m.margin_change_pp - (-0.2)) < 1e-6
    # recent revenue 3 x 100 x 200 = 60,000; 20 points of it = 12,000
    assert abs(float(m.historical_margin_gap) - 12_000) < 1.0


def test_no_gap_when_margin_improved():
    lines = [_line(d, 100, 200, cost=150) for d in (600, 500, 400, 300)]
    lines += [_line(d, 100, 200, cost=100) for d in (80, 50, 20)]
    m = _relationship(lines)
    assert m.margin_change_pp > 0
    assert m.historical_margin_gap is None


def test_annualization_is_withheld_without_enough_history():
    """A yearly figure extrapolated from a few weeks is a guess wearing a suit."""
    th = CommercialThresholds(annualize_min_history_months=6.0,
                              annualize_min_transactions=4)
    lines = [_line(d, 100, 200, cost=100) for d in (80, 70)]
    lines += [_line(d, 100, 200, cost=180) for d in (30, 10)]
    m = compute_relationship("c1", "p1", lines, AS_OF, th)
    assert m.history_months < 6.0
    assert m.historical_margin_gap is None or m.annualized_historical_margin_gap is None


def test_annualization_scales_the_window_gap_to_a_year():
    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 200, cost=140) for d in (80, 50, 20)]
    m = _relationship(lines)
    ratio = float(m.annualized_historical_margin_gap) / float(m.historical_margin_gap)
    assert abs(ratio - 365 / 90) < 0.01, "a 90-day window scales by 365/90"


# ── data sufficiency ────────────────────────────────────────────────────────
def test_a_single_transaction_never_reaches_a_confident_level():
    m = _relationship([_line(30, 10, 200, cost=100)])
    assert m.data_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert any("transaction" in r for r in m.sufficiency_reasons)


def test_a_short_but_real_history_is_partial_not_sufficient():
    lines = [_line(d, 10, 200, cost=100) for d in (150, 100, 60, 30)]
    m = _relationship(lines)
    assert 3.0 <= m.history_months < 6.0
    assert m.data_sufficiency is EvidenceSufficiency.PARTIAL
    assert m.sufficiency_reasons, "PARTIAL must say why it is not SUFFICIENT"


def test_long_dense_history_with_cost_is_sufficient():
    lines = [_line(d, 10, 200, cost=100)
             for d in (600, 500, 400, 300, 200, 100, 50, 20)]
    m = _relationship(lines)
    assert m.data_sufficiency is EvidenceSufficiency.SUFFICIENT
    assert m.sufficiency_reasons == []


def test_partial_cost_coverage_below_the_floor_is_insufficient():
    """Margin computed from a third of the transactions is not a margin for the
    relationship."""
    lines = [_line(d, 10, 200, cost=100) for d in (600, 500)]
    lines += [_line(d, 10, 200) for d in (400, 300, 200, 100)]     # no cost
    m = _relationship(lines)
    assert m.cost_covered_txns == 2 and m.cost_missing_txns == 4
    assert m.data_sufficiency is EvidenceSufficiency.INSUFFICIENT


# ── the diagnosis prose ─────────────────────────────────────────────────────
def test_diagnosis_never_describes_a_zero_move_as_a_decrease():
    """Caught in the real UI: a flat cost read "decreased 0.0%". The verb and
    the number it quotes have to agree."""
    from app.commercial.diagnosis import diagnose

    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 182, cost=100) for d in (80, 50, 20)]   # cost flat
    text = " ".join(diagnose(_relationship(lines), None, TH))

    assert "was unchanged" in text
    assert "decreased 0.0%" not in text and "increased 0.0%" not in text


def test_diagnosis_says_no_peers_rather_than_only_zero_peers():
    """"Only 0 other customers" is technically true and reads as a glitch."""
    from app.commercial.benchmark import compute_benchmark
    from app.commercial.diagnosis import diagnose

    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 160, cost=100) for d in (80, 50, 20)]
    bm = compute_benchmark("p1", "c1", {"c1": lines}, AS_OF, TH)
    text = " ".join(diagnose(_relationship(lines), bm, TH))

    assert bm.peer_count == 0
    assert "No other customer bought this item" in text
    assert "Only 0" not in text


def test_diagnosis_frames_the_gap_as_an_estimate_and_peers_as_a_benchmark():
    """The two claims most likely to be over-read if stated flatly."""
    from app.commercial.diagnosis import diagnose

    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 200, cost=140) for d in (80, 50, 20)]
    text = " ".join(diagnose(_relationship(lines), None, TH))
    assert "estimated gap, not recoverable profit" in text


def test_diagnosis_on_insufficient_data_refuses_to_conclude():
    from app.commercial.diagnosis import diagnose

    text = diagnose(_relationship([_line(20, 10, 200, cost=100)]), None, TH)
    assert len(text) == 1
    assert "Not enough data" in text[0]
