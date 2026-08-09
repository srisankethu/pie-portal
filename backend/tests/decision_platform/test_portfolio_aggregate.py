"""The one rule for "what is this book's margin", over persisted metric rows.

The sibling of ``test_commercial_economics``'s aggregate tests: the same rule,
applied to ``CustomerItemMetric`` rollups rather than to sale lines. It has its
own file because it had its own bug — the commercial-weather route summed gross
profit across every relationship while leaving *all* of their revenue in the
denominator, so relationships with no cost data contributed nothing to the
numerator and their full revenue to the divisor. The screen reported 7.8% and
banded it POOR against a 15% review floor; the figure over the relationships
that actually have cost was 19.7%, comfortably above it. A manager read "we are
pricing below our own floor" off what was really a cost-coverage gap.
"""
from __future__ import annotations

from decimal import Decimal

from app.commercial.portfolio import aggregate_margin
from app.domain import models


def _row(revenue, gross_profit=None) -> models.CustomerItemMetric:
    """A metric row carrying only what the aggregate reads."""
    return models.CustomerItemMetric(
        organization_id="org", customer_id="c1", product_id="p1",
        revenue_12m=Decimal(str(revenue)),
        gross_profit_12m=(Decimal(str(gross_profit))
                          if gross_profit is not None else None))


def test_uncosted_revenue_is_excluded_from_the_denominator():
    """The exact shape of the weather bug, in the numbers it was found with."""
    rows = [_row(74340, 14130), _row(56160, 11520),   # costed
            _row(150000), _row(49331)]                # no cost behind them
    agg = aggregate_margin(rows)

    assert agg.revenue == Decimal("329831"), "revenue counts every relationship"
    assert agg.costed_revenue == Decimal("130500")
    assert agg.gross_profit == Decimal("25650")
    assert abs(agg.margin - 0.1966) < 1e-4, "19.7%, not 7.8%"
    # The number the old route produced, so a regression is recognisable.
    assert abs(25650 / 329831 - 0.0778) < 1e-4
    assert agg.margin > 0.15, "above the review floor — the band flips on this"


def test_coverage_says_how_much_of_the_book_the_margin_speaks_for():
    agg = aggregate_margin([_row(100, 20), _row(300)])
    assert agg.revenue_coverage == 0.25


def test_no_cost_anywhere_is_no_margin_rather_than_zero():
    """"We cannot say" and "we made nothing" are different answers, and a zero
    banded against a margin floor is the more damaging of the two."""
    agg = aggregate_margin([_row(1000), _row(500)])
    assert agg.revenue == Decimal("1500")
    assert agg.gross_profit is None
    assert agg.margin is None
    assert agg.revenue_coverage == 0.0


def test_margin_is_not_a_mean_of_per_row_margins():
    """20% on ₹1,000 and 60% on ₹100 is 23.6% overall, never 40%."""
    agg = aggregate_margin([_row(1000, 200), _row(100, 60)])
    assert abs(agg.margin - (260 / 1100)) < 1e-9
    assert agg.margin < 0.24


def test_an_empty_book_divides_by_nothing():
    agg = aggregate_margin([])
    assert agg.revenue == Decimal("0")
    assert agg.margin is None
    assert agg.revenue_coverage is None


def test_a_row_with_revenue_but_zero_profit_still_counts_as_costed():
    """Zero profit is a measurement. Only a missing gross profit is unknown."""
    agg = aggregate_margin([_row(1000, 0)])
    assert agg.costed_revenue == Decimal("1000")
    assert agg.gross_profit == Decimal("0")
    assert agg.margin == 0.0
