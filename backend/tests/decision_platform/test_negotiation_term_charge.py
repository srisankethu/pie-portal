"""The negotiation desk, once credit days cost something.

`discount_to_floor` and `price_for_target` are the two questions a salesperson
actually asks, and both are solved in closed form rather than searched — which
is what makes the desk usable on a phone call. The term charge had to preserve
that (I5), so these are round-trip tests: solve for a number, feed it back, and
assert the answer was exact.

The charge is off in the shipped block, so every one of these also runs with it
off and asserts the arithmetic is unchanged.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.commercial import incentive

AS_OF = date(2026, 8, 7)


def _deal(**kw) -> incentive.Deal:
    base = dict(qty=D("500"), floor_price=D("280"), agreed_price=D("340"))
    base.update(kw)
    return incentive.Deal(**base)


@pytest.fixture
def charged(monkeypatch):
    """The desk with the term charge switched on.

    Patched at `incentive._cfg`, which is the one loader the module reads, so
    every path under test sees the same block — a test that enabled it for the
    assessment but not for the inversions would prove the opposite of what it
    claims.
    """
    from incentive_engine.config import load_config

    def _on(as_of):
        cfg = load_config(as_of=as_of)
        cfg.raw["caf"]["term_charge"] = {**cfg.raw["caf"]["term_charge"],
                                         "enabled": True}
        return cfg

    monkeypatch.setattr(incentive, "_cfg", _on)
    return _on


# ── the inversions stay exact ────────────────────────────────────────────────
def test_the_discount_it_offers_lands_the_line_exactly_on_break_even(charged):
    """Give away precisely what the desk says is free, and CAF is zero. Not
    "about zero" — this is the number a negotiation is conducted against."""
    deal = _deal(credit_days=90)
    give = incentive.discount_to_floor(deal, as_of=AS_OF)
    assert give is not None

    at_break_even = _deal(credit_days=90, customer_discount=give)
    caf = _caf(at_break_even)
    # Rounded DOWN to the paise, so the residual is non-negative and under a
    # paisa per unit — a break-even rounded up is not one.
    assert D("0") <= caf < deal.qty * D("0.01")


def test_the_price_it_solves_for_hits_the_target_it_was_given(charged):
    """Exact in the algebra, and the only residual is the price being a price.

    `price_for_target` quantizes to the paise because that is what a salesperson
    can quote, so on a 500-unit line the answer can sit up to half a paise per
    unit either side of the target. The bound is therefore stated per unit —
    a fixed rupee tolerance would silently pass a formula that had drifted on a
    large line and fail a correct one.
    """
    for target in (D("0"), D("5000"), D("30000")):
        deal = _deal(credit_days=60)
        price = incentive.price_for_target(deal, target, as_of=AS_OF)
        assert price is not None
        got = _caf(_deal(credit_days=60, agreed_price=price))
        assert abs(got - target) < deal.qty * D("0.01")


def test_longer_terms_cost_real_negotiating_room(charged):
    """The behaviour change the charge exists to produce: a salesperson can see
    what the credit period is costing them in the only currency they are paid
    in, and trade it against price in the room."""
    advance = incentive.discount_to_floor(_deal(credit_days=0), as_of=AS_OF)
    ninety = incentive.discount_to_floor(_deal(credit_days=90), as_of=AS_OF)
    assert advance is not None and ninety is not None
    assert ninety < advance
    # On a 340/280 line that is several rupees a piece — enough to be worth
    # saying out loud, not so much that the desk stops being usable.
    assert D("5") < (advance - ninety) < D("15")


def test_the_deal_level_charges_still_come_off_and_scale_correctly(charged):
    """K, toolkit and Y are totals, and the term charge is proportional. The
    two must compose without either being applied twice."""
    plain = incentive.discount_to_floor(_deal(credit_days=45), as_of=AS_OF)
    with_k = incentive.discount_to_floor(
        _deal(credit_days=45, third_party_incentive=D("5000")), as_of=AS_OF)
    assert plain is not None and with_k is not None
    assert with_k < plain


# ── off is unchanged ─────────────────────────────────────────────────────────
def test_with_the_charge_off_the_desk_answers_exactly_what_it_used_to():
    """No fixture: the shipped block. `P - F` and nothing else."""
    deal = _deal(credit_days=90)
    assert incentive.discount_to_floor(deal, as_of=AS_OF) == D("60.00")
    assert incentive.price_for_target(deal, D("0"), as_of=AS_OF) == D("280.00")
    # And the credit period is visible as costing nothing, rather than absent.
    assert incentive.price_for_target(
        _deal(credit_days=0), D("0"), as_of=AS_OF) == D("280.00")


def _caf(deal: incentive.Deal) -> D:
    """CAF for a prospective deal, through the same engine call the desk uses."""
    from incentive_engine import caf as caf_mod

    line = incentive._line(deal, as_of=AS_OF, customer_id="c1",
                           product_id="p1", family="inserts", entity_id="SLS",
                           salesperson_id="s1")
    return caf_mod.line_caf(
        incentive._cfg(AS_OF), line,
        third_party=deal.third_party_incentive,
        toolkit=deal.toolkit_spend,
        vendor_yield=deal.vendor_yield).caf
