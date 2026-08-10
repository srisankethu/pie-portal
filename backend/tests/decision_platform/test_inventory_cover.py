"""Days of cover: one calculation, two callers, and no cost in it.

The arithmetic lived privately inside ``ExcessCoverDetector`` and the stock
screen refused to show it at all, so the queue was printing a figure the screen
called unknowable. It is now ``commercial/offtake.py``, and these are the three
things that have to stay true about that move:

1. **The answer did not move with it.** The detector's months of cover and the
   screen's days of cover are the same division, so they must agree on the same
   shelf. This is the test that makes the shared copy worth having — two copies
   would pass every test either one was written against and still disagree.
2. **A line that has never sold has no cover.** Not zero, not an infinity.
   ``CLAUDE.md`` §1: absence of evidence is not a pass, and it is not a failure
   either.
3. **Cover carries no cost.** The detector sizes its impact in money and is
   cost-gated for it; the column is not, so the figure must be invariant under
   the purchase rate. Swept rather than asserted field-by-field, because §1
   records that every field-level assertion in the quote suite passed while the
   endpoint gave up cost.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.commercial import offtake
from app.commercial.insight import stock
from app.state.opportunities.base import DecisionPolicy
from app.state.opportunities.inventory import ExcessCoverDetector
from app.state.reducers.inventory import INVENTORY

AS_OF = date(2026, 8, 10)
CARRYING = stock.Carrying(annual_pct=0.18, dead_days=365, slow_days=180)

POLICY = DecisionPolicy(
    dead_days=365, slow_days=180,
    carrying_annual_pct=Decimal("0.18"),
    excess_cover_months=Decimal("3"),
    rupees_per_point=Decimal("1000"),
    min_impact=Decimal("1000"),
    version="ci_test")


def _line(**over) -> stock.StockLine:
    """One shelf line. 100 on hand, 200 sold over the 200 days since it first
    sold — a daily rate of 1.0, so the cover is 100 days exactly and the
    assertions below read as arithmetic rather than as fixture archaeology."""
    fields = dict(
        product_id="p1", label="CNMG 120408", on_hand=100.0, available=100.0,
        actual_available=100.0, reorder_level=None,
        last_sold=date(2026, 8, 1), sold_qty_window=200.0,
        purchase_rate=401.25, first_sold=AS_OF - timedelta(days=200))
    fields.update(over)
    return stock.StockLine(**fields)


# ── the measurement itself ──────────────────────────────────────────────────
def test_cover_is_the_shelf_over_the_rate_it_has_actually_moved_at():
    assert _line().daily_offtake(AS_OF) == pytest.approx(1.0)
    assert _line().days_of_cover(AS_OF) == 100.0
    # Twice the shelf at the same rate is twice the cover. Stated because it is
    # the property the column claims, and a ratio that failed it would still
    # pass a single-value assertion.
    assert _line(on_hand=200.0).days_of_cover(AS_OF) == 200.0


def test_the_window_runs_from_the_first_sale_not_the_last_movement():
    """The one modelling choice in the module, pinned so it cannot be
    "improved" into a trailing window.

    Same shelf, same units sold, a last sale nine days ago. Measured from the
    first sale the rate is 1.0/day; measured from the last movement it would be
    200 units over 9 days — 22/day — and the cover would collapse from 100 days
    to 4. That is the failure the detector's comment has always warned about.
    """
    row = _line(last_sold=AS_OF - timedelta(days=9))
    assert row.days_of_cover(AS_OF) == 100.0


def test_a_line_that_has_never_sold_has_unknown_cover_not_zero_and_not_infinity():
    row = _line(sold_qty_window=0.0, last_sold=None, first_sold=None)
    assert row.daily_offtake(AS_OF) is None
    assert row.days_of_cover(AS_OF) is None
    # And through the projection, where a zero would be the damaging default: a
    # reader sorting on this column must not find never-sold stock at either end
    # of it by accident.
    assert row.to_dict(AS_OF, CARRYING, with_cost=False)["days_of_cover"] is None


def test_a_first_sale_today_is_a_rate_over_one_day_rather_than_a_division_by_zero():
    row = _line(sold_qty_window=4.0, first_sold=AS_OF, last_sold=AS_OF)
    assert row.daily_offtake(AS_OF) == pytest.approx(4.0)
    assert row.days_of_cover(AS_OF) == 25.0


def test_an_empty_shelf_that_does_sell_covers_zero_days():
    """Zero here is a measurement, not a missing one — the item moves and there
    is none of it. The distinction from the case above is the whole point of
    returning ``None`` there."""
    assert _line(on_hand=0.0).days_of_cover(AS_OF) == 0.0


# ── the shared copy ─────────────────────────────────────────────────────────
def _state(on_hand: str, sold: str, first_sold: date, last_sold: date) -> dict:
    return {INVENTORY: {"p1": {
        "tracked": True, "on_hand": on_hand, "units_sold": sold,
        "revenue": "400000", "purchase_rate": "401.25",
        "first_sold_on": first_sold.isoformat(),
        "last_sold_on": last_sold.isoformat(),
        "first_observed_on": (first_sold).isoformat(),
    }}}


def test_the_detector_and_the_screen_agree_about_how_much_is_held():
    """The reason the calculation is shared rather than copied.

    ``ExcessCoverDetector`` reports months and the column reports days, so the
    comparison is made in one unit. If these ever drift, a decision card tells
    an owner they hold nine months of something the screen beside it calls four.
    """
    first_sold, last_sold = AS_OF - timedelta(days=200), AS_OF - timedelta(days=5)
    states = _state("1000", "200", first_sold, last_sold)

    drafts = list(ExcessCoverDetector().detect(states, POLICY, AS_OF))
    assert drafts, "the fixture must raise a card to compare against"
    card_months = Decimal(drafts[0].impact.operational["months_of_cover"])

    row = stock.lines_from_state(
        states[INVENTORY], labels={"p1": "CNMG"}, buyers={}, with_cost=True)[0]
    screen_days = row.days_of_cover(AS_OF)

    # The card quantizes to 0.1 months and the column rounds to whole days, so
    # they are compared at the coarser of the two resolutions rather than for
    # bit equality — the claim is that one division produced both, not that two
    # different roundings of it are identical.
    assert screen_days is not None
    assert (Decimal(str(screen_days)) / offtake.DAYS_PER_MONTH) == pytest.approx(
        card_months, abs=0.1)


def test_the_screen_reads_the_offtake_window_the_fold_recorded():
    """``first_sold`` comes off the fold, not from a second derivation. The
    detector divides by a rate measured over exactly this window."""
    first_sold = AS_OF - timedelta(days=200)
    row = stock.lines_from_state(
        _state("100", "200", first_sold, AS_OF - timedelta(days=5))[INVENTORY],
        labels={"p1": "CNMG"}, buyers={}, with_cost=False)[0]
    assert row.first_sold == first_sold


# ── no cost in it ───────────────────────────────────────────────────────────
def test_cover_does_not_move_with_the_purchase_rate():
    """Swept, not asserted per field. Cover is quantity x days / quantity, so a
    reader who can see it learns nothing about what the stock cost — which is
    what makes it safe on the screen that withholds ``inventory_value``."""
    seen = set()
    for rate in (None, 0.01, 1.0, 401.25, 99_999.0):
        row = _line(purchase_rate=rate)
        seen.add(row.days_of_cover(AS_OF))
        # And the projected row: the field a browser actually receives.
        seen.add(row.to_dict(AS_OF, CARRYING, with_cost=False)["days_of_cover"])
    assert seen == {100.0}, (
        "days_of_cover changed with the purchase rate, which makes it a "
        "predicate a caller can walk to recover cost — CLAUDE.md §1.")


def test_the_column_reaches_a_reader_who_cannot_see_cost():
    """The point of the change. Withholding it from operations would leave the
    screen unable to answer "how much of this do we hold", which is the question
    it was asked for."""
    out = _line().to_dict(AS_OF, CARRYING, with_cost=False)
    assert out["days_of_cover"] == 100.0
    assert out["first_sold"] == (AS_OF - timedelta(days=200)).isoformat()
    for withheld in ("purchase_rate", "inventory_value", "annual_holding_cost"):
        assert withheld not in out


# ── the refusal that had to be resolved with it ─────────────────────────────
def test_projected_cover_is_still_refused_and_says_which_cover_it_refuses():
    """Restated, never deleted. Shipping the column while the old blanket
    refusal stood would have left the screen contradicting itself — a refusal a
    reader can disprove from the grid beside it teaches them to ignore the rest.
    """
    built = stock.build([_line()], AS_OF, CARRYING, with_cost=False)
    entry = next(u for u in built["unavailable"] if u["series"] == "weeks_of_cover")
    assert entry["kind"] == "PERMANENT"
    reason = entry["reason"].lower()
    # It must refuse the projection and say the measurement is shown, rather
    # than refusing cover as an idea.
    assert "projected cover" in reason
    assert "forecast" in reason
    assert "measured" in reason
    assert "actually moved" in reason


def test_lines_with_no_offtake_are_named_rather_than_left_as_silent_blanks():
    sold = _line()
    never = _line(product_id="p2", sold_qty_window=0.0, last_sold=None,
                  first_sold=None)
    built = stock.build([sold, never], AS_OF, CARRYING, with_cost=False)
    entry = next(u for u in built["unavailable"]
                 if u["series"] == "days_of_cover_for_lines_that_have_never_sold")
    # TRANSIENT: a sale makes it computable, and there is nothing for anybody to
    # go and record in the meantime.
    assert entry["kind"] == "TRANSIENT"
    assert "1 line(s)" in entry["reason"]


def test_no_such_entry_when_every_line_on_the_shelf_has_a_rate():
    built = stock.build([_line()], AS_OF, CARRYING, with_cost=False)
    assert not any(u["series"] == "days_of_cover_for_lines_that_have_never_sold"
                   for u in built["unavailable"])
