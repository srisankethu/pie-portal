"""Retained profit against revenue growth, and every way it must refuse.

The interesting behaviour is the silence. Half of this reading is a figure the
platform cannot derive, so the tests that matter are the ones asserting it says
nothing — and says *what* is missing — rather than the one asserting the
arithmetic. A screen that showed zero retained profit, or quietly stood in gross
profit, would look identical to a healthy book on every field-level assertion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import selffunding
from app.commercial.policy import PolicyError, _coerce, validate

ENTITIES = {"cx_sls": "SLS Engineers", "cx_4u": "4U Precision"}
#: After FY2025-26 has closed, so that year is a candidate and FY2026-27 is not.
AFTER = date(2026, 8, 1)


@dataclass(frozen=True)
class Row:
    """The three fields ``TradeRow`` asks for, and nothing else."""

    customer_id: str
    date: date
    line_revenue: Decimal


def _book(previous: str, current: str) -> list[Row]:
    """One sale in FY2024-25 and one in FY2025-26."""
    return [
        Row("c1", date(2024, 9, 1), Decimal(previous)),
        Row("c1", date(2025, 9, 1), Decimal(current)),
    ]


def _th(**rows: str) -> CommercialThresholds:
    """Thresholds with a retained figure per entity for FY2025-26."""
    return CommercialThresholds(retained_pat=tuple(
        sorted((eid, "FY2025-26", amount) for eid, amount in rows.items())))


# ── the refusals ────────────────────────────────────────────────────────────
def test_nothing_is_asserted_before_any_figure_is_confirmed():
    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=CommercialThresholds())

    assert built["verdict"] == selffunding.UNKNOWN
    assert built["confirmed"] is False
    assert sorted(built["missing_entities"]) == ["4U Precision", "SLS Engineers"]


def test_the_refusal_never_reports_a_zero_or_a_gross_profit_stand_in():
    """The failure mode this module exists to avoid: an unconfirmed book must
    not carry a retained figure at all, however benign."""
    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=CommercialThresholds())

    assert "retained_pat" not in built
    assert "revenue_growth" not in built
    assert "gross" not in built["blocked_by"].lower().replace("gross profit", "")


def test_a_partly_confirmed_year_is_refused_and_names_who_is_missing():
    """Two entities' profit against three entities' revenue understates what was
    kept, which manufactures a shortfall rather than reporting one."""
    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="900000"))

    assert built["verdict"] == selffunding.UNKNOWN
    assert built["confirmed"] is False
    assert built["missing_entities"] == ["4U Precision"]
    assert built["financial_year"] == "FY2025-26"


def test_a_year_that_has_not_closed_is_not_read():
    """A figure entered against a running year is an estimate, not an account."""
    th = CommercialThresholds(retained_pat=(
        ("cx_sls", "FY2026-27", "900000"), ("cx_4u", "FY2026-27", "500000")))

    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER, th=th)

    assert built["verdict"] == selffunding.UNKNOWN
    assert "has not finished" in built["blocked_by"]


def test_no_prior_year_revenue_is_a_refusal_not_infinite_growth():
    rows = [Row("c1", date(2025, 9, 1), Decimal("14000000"))]

    built = selffunding.reading(rows, entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="900000", cx_4u="500000"))

    assert built["verdict"] == selffunding.UNKNOWN
    assert "comparable prior year" in built["blocked_by"]


def test_a_book_with_no_trading_entity_refuses_rather_than_summing_nothing():
    """Empty entities would make "everybody has confirmed" vacuously true."""
    built = selffunding.reading(_book("10000000", "14000000"), entities={},
                                as_of=AFTER, th=_th(cx_sls="900000"))

    assert built["verdict"] == selffunding.UNKNOWN
    assert "No connected company has traded" in built["blocked_by"]


# ── the reading ─────────────────────────────────────────────────────────────
def test_retained_profit_below_the_whole_increase_is_undetermined_not_a_shortfall():
    """Growth consumes working capital, a fraction of the increase, and that
    fraction is not measured here — so below the line is "cannot tell"."""
    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="900000", cx_4u="500000"))

    assert built["confirmed"] is True
    assert built["verdict"] == selffunding.UNDETERMINED
    assert built["retained_pat"] == 1_400_000.0
    assert built["revenue_growth"] == 4_000_000.0
    assert "undetermined rather than as a shortfall" in built["limit_note"]


def test_retained_profit_above_the_whole_increase_is_covered():
    built = selffunding.reading(_book("10000000", "11000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="900000", cx_4u="500000"))

    assert built["verdict"] == selffunding.COVERED


def test_a_book_that_did_not_grow_has_nothing_to_fund():
    built = selffunding.reading(_book("14000000", "13000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="900000", cx_4u="500000"))

    assert built["verdict"] == selffunding.NOT_GROWING
    # No growth to divide into: a very large ratio would read as a good year.
    assert built["retained_per_rupee_of_growth"] is None
    assert built["growth_ratio"] is not None


def test_a_loss_year_is_representable_and_is_the_reading_that_matters():
    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="-400000", cx_4u="-100000"))

    assert built["retained_pat"] == -500_000.0
    assert built["verdict"] == selffunding.UNDETERMINED
    assert built["funding_gap_pp"] == pytest.approx(-0.45)


def test_the_two_rates_share_a_base_so_the_gap_is_percentage_points():
    """Growth and retention are both over the *previous* year's revenue, which
    is the only thing that makes their difference meaningful."""
    built = selffunding.reading(_book("10000000", "14000000"),
                                entities=ENTITIES, as_of=AFTER,
                                th=_th(cx_sls="900000", cx_4u="500000"))

    assert built["growth_ratio"] == pytest.approx(0.4)
    assert built["retention_ratio"] == pytest.approx(0.14)
    assert built["funding_gap_pp"] == pytest.approx(0.14 - 0.4)


def test_money_survives_as_decimal_rather_than_through_a_float():
    """The figure is stored as the string it was typed as, so the paise the
    owner read off an audited account are the paise that come back."""
    built = selffunding.reading(
        [Row("c1", date(2024, 9, 1), Decimal("10000000.00")),
         Row("c1", date(2025, 9, 1), Decimal("10000000.10"))],
        entities=ENTITIES, as_of=AFTER,
        th=_th(cx_sls="0.07", cx_4u="0.03"))

    assert built["retained_pat"] == 0.10
    assert built["revenue_growth"] == 0.10
    assert built["verdict"] == selffunding.COVERED


def test_the_year_read_is_the_newest_one_every_entity_has_answered_for():
    th = CommercialThresholds(retained_pat=(
        ("cx_4u", "FY2024-25", "300000"),
        ("cx_sls", "FY2024-25", "700000"),
        ("cx_sls", "FY2025-26", "900000"),     # 4U has not closed FY2025-26
    ))
    rows = _book("10000000", "14000000") + [
        Row("c1", date(2023, 9, 1), Decimal("8000000"))]

    built = selffunding.reading(rows, entities=ENTITIES, as_of=AFTER, th=th)

    assert built["confirmed"] is True
    assert built["financial_year"] == "FY2024-25"
    assert built["previous_financial_year"] == "FY2023-24"


# ── the periods, against the one definition of a financial year ─────────────
def test_a_financial_year_period_is_closed_at_both_ends():
    period = selffunding.fy_period("FY2025-26")

    assert (period.start, period.end) == (date(2025, 4, 1), date(2026, 3, 31))
    assert period.contains(date(2026, 3, 31))
    assert not period.contains(date(2026, 4, 1))


def test_the_previous_financial_year_is_the_one_immediately_before():
    assert selffunding.previous_fy("FY2025-26") == "FY2024-25"


# ── the confirmed figure as policy ──────────────────────────────────────────
def test_an_indian_grouped_amount_is_accepted_as_typed():
    assert _coerce("retained_pat", [["cx_sls", "FY2025-26", "1,25,00,000"]]) == (
        ("cx_sls", "FY2025-26", "12500000"),)


def test_an_unreadable_amount_raises_valueerror_so_a_stored_row_degrades():
    """``InvalidOperation`` is an ``ArithmeticError``, which ``load_for_org``'s
    guard does not catch — one bad row would take the whole org's policy down."""
    with pytest.raises(ValueError):
        _coerce("retained_pat", [["cx_sls", "FY2025-26", "not a number"]])


def test_two_figures_for_one_entity_and_year_are_refused():
    th = CommercialThresholds(retained_pat=(
        ("cx_sls", "FY2025-26", "900000"), ("cx_sls", "FY2025-26", "800000")))

    with pytest.raises(PolicyError, match="closes one year once"):
        validate(th)


def test_a_figure_with_no_entity_is_refused():
    with pytest.raises(PolicyError, match="which entity"):
        validate(CommercialThresholds(retained_pat=(("", "FY2025-26", "900000"),)))


def test_a_malformed_financial_year_is_refused():
    with pytest.raises(PolicyError, match="not a financial year"):
        validate(CommercialThresholds(retained_pat=(("cx_sls", "2025", "900000"),)))


def test_a_loss_passes_validation_because_a_loss_year_is_a_real_year():
    validate(CommercialThresholds(retained_pat=(("cx_sls", "FY2025-26", "-400000"),)))


def test_confirming_a_figure_moves_the_thresholds_version():
    """The version hash is what lets a reading rendered before and after an
    owner's correction be told apart."""
    before = CommercialThresholds().version
    after = _th(cx_sls="900000").version

    assert before != after
