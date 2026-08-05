"""CAF — the currency. 100% coverage required by the brief.

The tests that matter here are not the arithmetic ones. They are the ones that
pin the *coefficients*, because the coefficients are the mechanism: K at 1:1
makes the salesperson's optimum equal the owner's, Toolkit at 0.5 prices the
compliant weapon below the non-compliant one, Y at 1:1 makes the salesperson
indifferent to the source of a rupee.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from incentive_engine import caf
from incentive_engine.models import (
    ThirdPartyIncentive, ToolkitSpend,
)


# ── the worked baseline from the brief, reproduced exactly ──────────────────
def test_s1_playing_the_mechanism_reproduces_the_brief(cfg, line):
    """500 inserts, F 280, P 340, K 0, Toolkit 4,000 at 50%, Y 9,000."""
    got = caf.line_caf(cfg, line(), third_party=D("0"),
                       toolkit=D("4000"), vendor_yield=D("9000"))
    assert got.price_contribution == D("30000")
    assert got.toolkit_charged == D("2000")
    assert got.caf == D("37000")


def test_s2_the_easy_path_reproduces_the_brief(cfg, line):
    """5% discount and a 3,400 kickback, no vendor call."""
    got = caf.line_caf(cfg, line(unit_price_net=D("323")), third_party=D("3400"))
    assert got.caf == D("18100")


def test_playing_the_mechanism_beats_the_easy_path_by_more_than_two(cfg, line):
    good = caf.line_caf(cfg, line(), toolkit=D("4000"), vendor_yield=D("9000")).caf
    easy = caf.line_caf(cfg, line(unit_price_net=D("323")),
                        third_party=D("3400")).caf
    assert good / easy >= 2


# ── the coefficients ARE the mechanism ──────────────────────────────────────
def test_the_toolkit_is_priced_at_half_the_kickback(cfg, line):
    """S reaches for the compliant instrument first because it is cheaper —
    not because anyone told them to. Break this ratio and the whole S<->C
    sub-game inverts."""
    with_toolkit = caf.line_caf(cfg, line(), toolkit=D("10000")).caf
    with_cash = caf.line_caf(cfg, line(), third_party=D("10000")).caf
    assert with_toolkit - with_cash == D("5000")


def test_a_rupee_from_the_vendor_equals_a_rupee_held_on_price(cfg, line):
    """Y at 1:1 is what makes S indifferent to the source of a rupee, exactly
    as the owner is. That indifference is the incentive compatibility."""
    from_price = caf.line_caf(cfg, line(unit_price_net=D("342"))).caf
    from_vendor = caf.line_caf(cfg, line(), vendor_yield=D("1000")).caf
    assert from_price == from_vendor


def test_routing_the_kickback_as_a_discount_costs_exactly_the_same(cfg, line):
    """Exploit 26. P is NET realised, so a discount hits CAF identically to a
    declared K. There is no cheaper route, which is why declaring is weakly
    dominant."""
    declared = caf.line_caf(cfg, line(), third_party=D("3400")).caf
    discounted = caf.line_caf(cfg, line(unit_price_net=D("340") - D("3400") / D("500"))).caf
    assert declared == discounted


def test_a_line_below_the_floor_takes_points_away(cfg, line):
    """No "just this once". The arithmetic charges for it whether or not
    anyone was watching."""
    assert caf.line_caf(cfg, line(unit_price_net=D("250"))).caf == D("-15000")


# ── deal-level terms spread across lines ────────────────────────────────────
def test_deal_terms_apportion_by_contribution_not_by_value(cfg, line):
    """Apportioning by value would let S park the whole kickback on the one
    line furthest above the floor and leave the rest untouched."""
    lines = [line(item_id="A", qty=D("100"), unit_price_net=D("400")),
             line(item_id="B", qty=D("100"), unit_price_net=D("290"))]
    k = ThirdPartyIncentive("SLS", "d1", "INV-1", "c1", D("1300"), "cash",
                            date(2026, 6, 1))
    got = caf.compute(cfg, lines, incentives=[k])
    by_item = {g.line.item_id: g for g in got}
    # A contributes 12,000 and B contributes 1,000, so A carries 12/13 of it.
    assert by_item["A"].third_party == pytest.approx(D("1200"), abs=D("0.01"))
    assert by_item["B"].third_party == pytest.approx(D("100"), abs=D("0.01"))
    assert sum(g.third_party for g in got) == D("1300")


def test_a_charge_never_disappears_when_no_line_is_above_the_floor(cfg, line):
    """Every line under water still has to carry the kickback somewhere."""
    lines = [line(item_id="A", unit_price_net=D("270")),
             line(item_id="B", unit_price_net=D("260"))]
    k = ThirdPartyIncentive("SLS", "d1", "INV-1", "c1", D("500"), "cash",
                            date(2026, 6, 1))
    got = caf.compute(cfg, lines, incentives=[k])
    assert sum(g.third_party for g in got) == D("500")


def test_toolkit_lands_once_per_customer_not_once_per_invoice(cfg, line):
    """Ten invoices to one customer must not be charged the toolkit ten times."""
    lines = [line(invoice_id="INV-1"), line(invoice_id="INV-2")]
    spend = ToolkitSpend("SLS", "c1", "s1", D("4000"), "trial", "R-1",
                         date(2026, 6, 1))
    got = caf.compute(cfg, lines, toolkit=[spend])
    assert sum(g.toolkit_charged for g in got) == D("2000")


def test_aged_lines_are_excluded_from_caf_by_default(cfg, line):
    """Q1(a). Below some age the aged floor sits under cost, so a cash-losing
    clearance would produce positive CAF and corrupt the margin signal."""
    normal = line(invoice_id="N")
    aged = line(invoice_id="A", is_aged_stock=True, stock_age_days=400,
                floor_price=D("196"))
    got = caf.compute(cfg, [normal, aged])
    assert caf.total(got) == D("30000")
    assert caf.total(got, include_aged=True) > D("30000")


def test_vendor_yield_credit_rate_is_read_from_config_not_hardcoded(cfg, line):
    cfg.raw["caf"]["vendor_yield_credit"] = "1.50"
    got = caf.line_caf(cfg, line(), vendor_yield=D("1000"))
    assert got.vendor_yield == D("1500.0")


def test_every_line_explains_itself(cfg, line):
    """A payout nobody can trace is a payout nobody will believe."""
    got = caf.line_caf(cfg, line(), toolkit=D("4000"), vendor_yield=D("9000"))
    explained = got.explain()
    assert explained["caf"] == "37000.00"
    assert explained["floor"] == "280.00"
    assert "cost" not in explained and "margin" not in explained
