"""The term charge: credit days, priced.

CAF had no term for payment terms, so credit days were given away free on every
deal — and `c(d)` did not catch it, because c(d) reads days LATE against the DUE
date and the due date already moves with whatever terms were agreed.

The claims worth pinning, in the order they would break:

* **off by default, and off means identical.** The shipped parameter block
  reproduces the mechanism as it was, to the paise, on every path;
* **no recorded term is not zero days.** Zero reads as "against delivery", the
  best term in the book, and defaulting to it would make *not recording* a term
  the profitable choice;
* **linearity survives** (I5). The charge changes which floor, not the shape of
  the arithmetic, so `discount_to_floor` stays a subtraction and both
  inversions stay exact rather than searched;
* **nothing new is disclosed.** Every input is a price, a quantity, a day count
  or a published rate.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from incentive_engine import caf, floor, vendor
from incentive_engine.config import ConfigError, load_config
from incentive_engine.models import InvoiceLine, ValidationError

AS_OF = date(2026, 8, 7)


def _line(**kw) -> InvoiceLine:
    base = dict(entity_id="SLS", invoice_id="I-1", invoice_date=AS_OF,
                customer_id="c1", customer_group_id="g1", item_id="CNMG",
                item_family="inserts", brand="KM", qty=D("500"),
                unit_price_net=D("340"), floor_price=D("280"),
                salesperson_id="s1")
    base.update(kw)
    return InvoiceLine(**base)


def _cfg_on(monkeypatch=None, *, enabled=True, **over):
    """The shipped block with the charge switched on, for the tests that need it.

    The parameter block is loaded, not hand-built, so these exercise the real
    published rate rather than a number invented in the test.
    """
    cfg = load_config(as_of=AS_OF)
    cfg.raw["caf"]["term_charge"] = {**cfg.raw["caf"]["term_charge"],
                                     "enabled": enabled, **over}
    return cfg


# ── off by default, and off is bit-identical ─────────────────────────────────
def test_the_shipped_block_ships_the_charge_off(cfg):
    """Invariant 6. A term that changes what people are paid does not arrive
    switched on with a deploy."""
    assert cfg.get("caf", "term_charge", "enabled") is False


def test_disabled_reproduces_the_mechanism_exactly(cfg):
    """Not "approximately" and not "within rounding" — the same Decimal."""
    for days in (None, 0, 30, 90, 365):
        got = caf.line_caf(cfg, _line(credit_days=days))
        assert got.term_charge == D("0")
        assert got.caf == D("500") * (D("340") - D("280")) == D("30000")


def test_disabled_leaves_both_inversions_where_they_were(cfg):
    """The floor a disabled charge produces is the published floor itself."""
    assert floor.terms_adjusted(D("280"), D("0")) == D("280")
    rate, days, assumed = caf.term_charge_rate(cfg, None)
    assert rate == D("0") and assumed is False and days is None


# ── an unrecorded term is not free credit ────────────────────────────────────
def test_no_recorded_term_is_charged_at_the_standard_one_not_at_zero():
    """Zero days is "against delivery" — the most valuable term in the book.
    Defaulting to it would hand every unrecorded line the best treatment and
    make not recording a term the profitable choice."""
    cfg = _cfg_on()
    unknown = caf.line_caf(cfg, _line(credit_days=None))
    assert unknown.credit_days_assumed is True
    assert unknown.credit_days == cfg.int_("caf", "term_charge",
                                           "assumed_days_when_unknown")
    assert unknown.term_charge > D("0")

    advance = caf.line_caf(cfg, _line(credit_days=0))
    assert advance.term_charge == D("0")
    assert advance.credit_days_assumed is False
    assert advance.caf > unknown.caf, "advance must beat an unstated term"


def test_the_charge_rises_with_the_term_and_never_falls():
    cfg = _cfg_on()
    charges = [caf.line_caf(cfg, _line(credit_days=d)).term_charge
               for d in (0, 30, 45, 60, 90, 120)]
    assert charges == sorted(charges)
    assert charges[0] == D("0")


def test_negative_credit_days_cannot_be_constructed():
    """An advance is zero days of credit. A negative term would pay a bonus
    through the charge rather than removing it."""
    with pytest.raises(ValidationError, match="negative credit days"):
        _line(credit_days=-30)


# ── linearity (I5) ───────────────────────────────────────────────────────────
def test_the_charge_is_exactly_a_floor_that_rose():
    """`CAF = q.P(1-k) - q.F` factors to `(1-k).q.(P - F/(1-k))`. If that
    identity ever stops holding, the desk stops agreeing with the payslip."""
    cfg = _cfg_on()
    line = _line(credit_days=90)
    got = caf.line_caf(cfg, line)

    k, _d, _a = caf.term_charge_rate(cfg, 90)
    f_adj = floor.terms_adjusted(line.floor_price, k)
    identity = (D("1") - k) * line.qty * (line.unit_price_net - f_adj)
    assert abs(got.caf - identity) < D("0.0000001")


def test_break_even_is_where_the_adjusted_floor_says_it_is():
    """Price the line exactly at `F/(1-k)` and CAF is zero — which is what
    "floor" has always meant and must keep meaning."""
    cfg = _cfg_on()
    k, _d, _a = caf.term_charge_rate(cfg, 90)
    at_floor = floor.terms_adjusted(D("280"), k)
    got = caf.line_caf(cfg, _line(credit_days=90, unit_price_net=at_floor))
    assert abs(got.caf) < D("0.0000001")


def test_a_charge_that_would_exceed_the_invoice_is_refused_not_inverted():
    """At `k >= 1` the credit period costs more than the line sells for, and a
    floor expressed from it would be negative or infinite — either of which
    silently inverts the price discipline."""
    with pytest.raises(ValueError, match="cannot be expressed as a floor"):
        floor.terms_adjusted(D("280"), D("1"))


# ── the two clocks stay separate ─────────────────────────────────────────────
def test_the_agreed_term_and_the_measured_lateness_are_different_clocks():
    """Agreed days price the decision the salesperson made; measured lateness
    prices what the customer then did. Charging measured days here as well
    would be double jeopardy and would reintroduce the defect that killed the
    realisation currency — paying on the customer's history rather than on the
    line.

    The guard that matters is compositional: the term charge lands inside CAF,
    and `c(d)` multiplies the result. A future change that fed measured days
    into `credit_days` would make these two move together, and they must not.
    """
    from incentive_engine import collection

    cfg = _cfg_on()
    charged = caf.line_caf(cfg, _line(credit_days=90))

    # c(d) does not take the term as an input and cannot: same lateness, same
    # factor, whatever was agreed.
    assert collection.factor(cfg, 0) == D("1.00")
    on_time = charged.caf * collection.factor(cfg, 0)
    late = charged.caf * collection.factor(cfg, 45)

    # Both clocks bite, and they bite in sequence rather than instead of each
    # other: the term is already gone from `caf`, and lateness then scales it.
    assert charged.term_charge > D("0")
    assert late < on_time < D("30000")


# ── one rate, both directions ────────────────────────────────────────────────
def test_the_buy_side_and_the_sell_side_read_the_same_cost_of_capital(cfg):
    """The mechanism used to pay for extracting a credit day from a vendor and
    charge nothing for giving one to a customer. Two copies of "what money
    costs" is one copy that gets re-cut and one that does not."""
    assert cfg.dec("cost_of_capital", "annual") > D("0")
    # 100 days of credit on 1,00,000 at 12% is a shade under 3,300.
    assert abs(vendor.value_extended_credit(cfg, D("100000"), 100)
               - D("3287.67")) < D("0.01")
    with pytest.raises(ConfigError):
        cfg.get("vendor", "valuation", "cost_of_capital_annual")


def test_the_published_sell_side_rate_carries_the_gst_gross_up(cfg):
    """The receivable actually funded is the tax-inclusive invoice, while
    `unit_price_net` is tax-exclusive. Charging the bare cost of capital on a
    tax-exclusive base under-recovers by the GST share."""
    money = cfg.dec("cost_of_capital", "annual")
    charged = cfg.dec("caf", "term_charge", "rate_annual")
    assert charged > money
    assert abs(charged / money - D("1.18")) < D("0.005")
