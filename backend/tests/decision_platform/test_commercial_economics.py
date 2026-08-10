"""Unit economics for one invoice line, and correct aggregation of many.

Everything in the Customer × Item layer is computed from these two functions,
so they are tested first and hardest. The two failures that would matter most:
silently costing an uncostable line at zero (reporting a 100% margin on the
platform's own ignorance), and averaging margin percentages instead of dividing
total profit by total revenue.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.commercial.economics import aggregate, in_window, line_economics
from app.signals.base import CostRow, SaleRow


def _sale(d="2026-06-01", qty=1, net_price=100, revenue=None, rate=None, discount=None,
          customer="c1", product="p1", invoice="INV1"):
    q = Decimal(str(qty))
    p = Decimal(str(net_price))
    return SaleRow(
        customer_id=customer, product_id=product, date=date.fromisoformat(d),
        qty=q, unit_price=p,
        line_revenue=Decimal(str(revenue)) if revenue is not None else q * p,
        source_ref={"system": "zoho", "record_type": "invoice", "record_id": invoice,
                    "line_id": "1"},
        external_ref=f"{invoice}:1",
        rate=Decimal(str(rate)) if rate is not None else None,
        discount_percent=Decimal(str(discount)) if discount is not None else None,
    )


def _cost(d="2026-01-01", unit_cost=60, product="p1", bill="BILL1"):
    return CostRow(product_id=product, date=date.fromisoformat(d), qty=Decimal("100"),
                   unit_cost=Decimal(str(unit_cost)),
                   source_ref={"system": "zoho", "record_type": "bill", "record_id": bill},
                   external_ref=f"{bill}:1")


# ── one line ────────────────────────────────────────────────────────────────
def test_no_discount_full_economics():
    e = line_economics(_sale(qty=10, net_price=100), [_cost(unit_cost=60)])
    assert e.net_unit_price == Decimal("100")
    assert e.revenue == Decimal("1000")
    assert e.effective_unit_cost == Decimal("60")
    assert e.cogs == Decimal("600")
    assert e.gross_profit == Decimal("400")
    assert e.gross_margin == 0.4


def test_a_discounted_line_earns_on_the_net_price_not_the_list_rate():
    """The bug this guards: unit_price used to hold the pre-discount rate while
    revenue was already post-discount, so margin was overstated on every
    discounted line."""
    # rate 200, 50% off -> net 100. Revenue must agree with the net price.
    e = line_economics(_sale(qty=10, net_price=100, rate=200, discount=50),
                       [_cost(unit_cost=60)])
    assert e.net_unit_price == Decimal("100")
    assert e.revenue == Decimal("1000")
    assert e.gross_profit == Decimal("400")
    assert e.gross_margin == 0.4
    # and the list rate survives for audit
    assert e.rate == Decimal("200") and e.discount_percent == Decimal("50")


def test_quantity_scales_totals_but_never_the_per_unit_figures():
    one = line_economics(_sale(qty=1, net_price=100), [_cost(unit_cost=60)])
    ten = line_economics(_sale(qty=10, net_price=100), [_cost(unit_cost=60)])
    assert ten.net_unit_price == one.net_unit_price == Decimal("100")
    assert ten.effective_unit_cost == one.effective_unit_cost == Decimal("60")
    assert ten.revenue == Decimal("1000") and ten.gross_profit == Decimal("400")
    assert ten.gross_margin == one.gross_margin, "margin is scale-free"


def test_missing_cost_withholds_margin_rather_than_inventing_one():
    e = line_economics(_sale(qty=10, net_price=100), [])
    assert e.revenue == Decimal("1000"), "revenue is still known"
    assert e.effective_unit_cost is None
    assert e.cogs is None and e.gross_profit is None and e.gross_margin is None
    assert e.has_cost is False


def test_zero_or_placeholder_cost_is_treated_as_missing():
    """A zero cost is a placeholder in the item master, not a free purchase —
    costing at it would report a 100% margin."""
    for placeholder in (0, -5):
        e = line_economics(_sale(), [_cost(unit_cost=placeholder)])
        assert e.effective_unit_cost is None, f"cost {placeholder} must not be trusted"
        assert e.gross_margin is None


def test_zero_revenue_yields_no_margin_not_a_division_error():
    e = line_economics(_sale(qty=5, net_price=0), [_cost(unit_cost=60)])
    assert e.revenue == Decimal("0")
    assert e.gross_margin is None
    assert e.gross_profit == Decimal("-300"), "a giveaway still costs money"


def test_zero_quantity_line_is_economically_empty():
    e = line_economics(_sale(qty=0, net_price=100, revenue=0), [_cost(unit_cost=60)])
    assert e.cogs == Decimal("0") and e.gross_profit == Decimal("0")
    assert e.gross_margin is None


def test_a_credit_note_style_negative_line_keeps_its_sign():
    """Returns arrive as negative quantity/revenue. They must reduce revenue and
    profit, not be dropped or absolutised."""
    e = line_economics(_sale(qty=-2, net_price=100, revenue=-200), [_cost(unit_cost=60)])
    assert e.revenue == Decimal("-200")
    assert e.cogs == Decimal("-120")
    assert e.gross_profit == Decimal("-80")


def test_cost_is_resolved_as_of_the_sale_date_not_today():
    """A line sold in March is costed at March's purchase price, even if a newer
    bill has landed since."""
    costs = [_cost("2026-01-01", 60), _cost("2026-05-01", 90, bill="BILL2")]
    march = line_economics(_sale("2026-03-01", qty=1, net_price=100), costs)
    june = line_economics(_sale("2026-06-01", qty=1, net_price=100), costs)
    assert march.effective_unit_cost == Decimal("60")
    assert june.effective_unit_cost == Decimal("90")


def test_a_cost_recorded_on_the_sale_date_itself_is_the_applicable_one():
    """"As of" includes the day. A bill booked the same day the line was sold is
    the cost that line was made at, and the alternative — skipping back to the
    previous purchase price — misprices exactly the lines where cost just moved,
    which are the ones anybody is looking at.
    """
    costs = [_cost("2026-01-01", 60), _cost("2026-06-01", 90, bill="BILL2")]
    same_day = line_economics(_sale("2026-06-01", qty=1, net_price=100), costs)
    assert same_day.effective_unit_cost == Decimal("90")
    assert same_day.cost_source_ref["record_id"] == "BILL2"

    # One day earlier still resolves to the older basis — the half of the
    # boundary that says this is a date comparison and not an off-by-one.
    day_before = line_economics(_sale("2026-05-31", qty=1, net_price=100), costs)
    assert day_before.effective_unit_cost == Decimal("60")


def test_both_cost_basis_implementations_answer_identically():
    """`commercial.economics` and `signals.aggregates` each define "the applicable
    cost at a date", and economics' docstring says that is deliberate: there must
    be exactly one answer to what an item cost us then, or two screens disagree.

    Nothing asserted that until this test. Both were free to drift — including at
    the same-day boundary above, which neither pinned.
    """
    from app.commercial.economics import cost_basis_asof as commercial_basis
    from app.signals.aggregates import cost_basis_asof as signals_basis

    costs = [_cost("2026-01-01", 60), _cost("2026-06-01", 90, bill="BILL2")]
    for d in ("2025-12-31", "2026-01-01", "2026-05-31", "2026-06-01", "2026-06-02"):
        as_of = date.fromisoformat(d)
        a, b = commercial_basis(costs, as_of), signals_basis(costs, as_of)
        assert a == b, f"the two cost bases disagree at {d}"
    assert commercial_basis(costs, date(2026, 6, 1)).unit_cost == Decimal("90")
    assert commercial_basis(costs, date(2025, 12, 31)) is None


def test_a_sale_before_any_cost_record_has_no_cost():
    e = line_economics(_sale("2025-01-01"), [_cost("2026-01-01", 60)])
    assert e.effective_unit_cost is None


def test_decimal_arithmetic_avoids_binary_float_noise():
    e = line_economics(_sale(qty=3, net_price="0.1", revenue="0.3"),
                       [_cost(unit_cost="0.07")])
    assert e.revenue == Decimal("0.3")
    assert e.cogs == Decimal("0.21")
    assert e.gross_profit == Decimal("0.09"), "0.3 - 0.21 exactly"


def test_the_cost_record_is_traceable():
    e = line_economics(_sale(), [_cost(bill="BILL-77")])
    assert e.cost_source_ref["record_id"] == "BILL-77"
    assert e.invoice_id == "INV1"


# ── aggregation ─────────────────────────────────────────────────────────────
def test_aggregated_margin_is_profit_over_revenue_not_a_mean_of_percentages():
    """The single most important rule in this layer. A large low-margin line and
    a tiny high-margin one must not average to something in the middle."""
    big = line_economics(_sale(qty=4000, net_price=100, invoice="A"),
                         [_cost(unit_cost=80)])          # ₹4,00,000 at 20%
    small = line_economics(_sale(qty=10, net_price=100, invoice="B"),
                           [_cost(unit_cost=40)])        # ₹1,000 at 60%
    agg = aggregate([big, small])

    assert agg.revenue == Decimal("401000")
    assert agg.gross_profit == Decimal("80600")          # 80,000 + 600
    assert abs(agg.margin - 0.201) < 1e-4
    assert agg.margin < 0.21, "a mean of 20% and 60% would be 40% — wrong"


def test_aggregate_excludes_uncosted_revenue_from_the_margin():
    """Counting an uncostable line's revenue with no COGS would drag the margin
    toward 100% and quietly report a profit the platform cannot stand behind."""
    costed = line_economics(_sale(qty=10, net_price=100), [_cost(unit_cost=60)])
    uncosted = line_economics(_sale(qty=10, net_price=100, invoice="B"), [])
    agg = aggregate([costed, uncosted])

    assert agg.revenue == Decimal("2000"), "revenue counts every line"
    assert agg.gross_profit == Decimal("400"), "profit counts only the costed one"
    assert agg.margin == 0.4, "not 0.7 — the uncosted revenue is excluded"
    assert (agg.txn_count, agg.costed_txn_count) == (2, 1)


def test_aggregate_with_no_costed_lines_reports_revenue_but_no_margin():
    agg = aggregate([line_economics(_sale(qty=10, net_price=100), [])])
    assert agg.revenue == Decimal("1000")
    assert agg.margin is None and agg.gross_profit is None
    assert agg.costed_txn_count == 0


def test_empty_aggregate_is_empty_not_zero_margin():
    agg = aggregate([])
    assert agg.is_empty and agg.revenue == Decimal("0")
    assert agg.margin is None


def test_aggregate_sums_quantity_across_lines():
    lines = [line_economics(_sale(qty=q, net_price=100, invoice=f"I{q}"),
                            [_cost(unit_cost=60)]) for q in (5, 10, 20)]
    assert aggregate(lines).qty == Decimal("35")


# ── windowing ───────────────────────────────────────────────────────────────
def test_windows_are_half_open_so_adjacent_periods_never_double_count():
    lines = [line_economics(_sale(d, invoice=d), [_cost(unit_cost=60)])
             for d in ("2026-03-31", "2026-04-01", "2026-06-30")]
    first = in_window(lines, date(2026, 1, 1), date(2026, 3, 31))
    second = in_window(lines, date(2026, 3, 31), date(2026, 6, 30))

    assert [ln.date.isoformat() for ln in first] == ["2026-03-31"]
    assert [ln.date.isoformat() for ln in second] == ["2026-04-01", "2026-06-30"]
    assert len(first) + len(second) == len(lines), "partitioned, not overlapping"


def test_open_ended_windows_are_supported():
    lines = [line_economics(_sale(d, invoice=d), []) for d in ("2025-01-01", "2026-06-01")]
    assert len(in_window(lines, None, date(2026, 12, 31))) == 2
    assert len(in_window(lines, date(2026, 1, 1), None)) == 1
