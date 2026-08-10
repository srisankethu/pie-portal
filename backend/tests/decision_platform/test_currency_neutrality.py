"""The platform must not assume it is being run in India.

Not a style check. Each of these covers a place where a rupee assumption was
baked into a *computed* number or into a version hash, which is the difference
between a cosmetic label and a wrong answer:

  * a threshold set had no currency, so a 10,000 floor in rupees and a 10,000
    floor in dollars stamped the same ``version`` onto rows that cannot be
    compared — the version hash is the platform's whole reproducibility claim;
  * ``recommend_price`` rounded to a literal 5, which is a sensible tick on a
    ₹2,000 insert and a 5% distortion on a $100 one;
  * the quote summary applied a literal 0.18.

The identity layer is covered separately (``test_identity.py``) — GSTIN is
already a registered strategy there rather than a hardcoded rule.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.commercial import policy
from app.commercial.config import CommercialThresholds
from app.commercial.money import money
from app.domain import models


# ── the version hash has to see the currency ────────────────────────────────
def test_same_numbers_in_two_currencies_are_two_versions():
    inr = CommercialThresholds(currency="INR", min_material_gap=10_000.0)
    usd = replace(inr, currency="USD")
    assert inr.version != usd.version, (
        "A 10,000 floor in rupees and a 10,000 floor in dollars are different "
        "policies. Identical version hashes would let a metric row stamped "
        "under one be read as reproducible under the other.")


def test_version_is_still_stable_for_identical_policy():
    a = CommercialThresholds(currency="AED")
    b = CommercialThresholds(currency="AED")
    assert a.version == b.version


# ── the tenant's currency, not the deployment's ─────────────────────────────
def _org(session, org_id="org_x", currency="INR"):
    session.add(models.Organization(organization_id=org_id, name="X",
                                    erp="zoho", currency=currency, config={}))
    session.flush()
    return org_id


def test_thresholds_take_the_organizations_currency(session):
    org_id = _org(session, currency="AED")
    th = policy.load_for_org(session, org_id)
    assert th.currency == "AED"


def test_two_organizations_on_one_deployment_keep_their_own(session):
    india = _org(session, "org_in", "INR")
    gulf = _org(session, "org_ae", "AED")
    assert policy.load_for_org(session, india).currency == "INR"
    assert policy.load_for_org(session, gulf).currency == "AED"


def test_currency_reaches_the_settings_screen(session):
    org_id = _org(session, currency="USD")
    assert policy.describe(session, org_id)["currency"] == "USD"


def test_money_fields_are_not_labelled_as_ratios(session):
    """A money field rendered with the ratio unit shows 1000000% on screen."""
    org_id = _org(session)
    kinds = {f["field"]: f["kind"] for f in policy.describe(session, org_id)["fields"]}
    assert kinds["min_material_gap"] == "money"
    assert kinds["min_quote_exception_impact"] == "money"
    assert kinds["price_rounding_increment"] == "money"
    assert kinds["target_margin_default"] == "ratio"


def test_a_day_count_is_not_labelled_as_a_ratio(session):
    """The same trap as money, sprung again: ``_kind`` falls through to "ratio",
    the screen multiplies a ratio by 100, and a 365-day threshold rendered as
    "36500 %"."""
    org_id = _org(session)
    kinds = {f["field"]: f["kind"] for f in policy.describe(session, org_id)["fields"]}
    assert kinds["dead_stock_days"] == "days"
    assert kinds["slow_stock_days"] == "days"
    assert kinds["carrying_rate_is_published"] == "flag"
    # The rate itself stays a ratio — only the switch that publishes it is a flag.
    assert kinds["carrying_cost_annual_pct"] == "ratio"


def test_every_editable_field_declares_the_kind_it_is_parsed_as(session):
    """The screen renders on ``kind`` and the server parses on ``_coerce``. When
    those two disagree the field is either displayed wrong or rejected on save,
    so this asserts they are derived from the same lists rather than trusting
    that whoever adds the next field updates both."""
    org_id = _org(session)
    expected = {
        "flag": bool,
        "days": int,
        "money": float,
        "ratio": float,
        # A ratio an owner is allowed to leave undecided. It parses to a float
        # like any other ratio; the separate kind exists so the screen renders
        # an empty field rather than "0 %" for a rate nobody has set.
        "optional_ratio": float,
    }
    for f in policy.describe(session, org_id)["fields"]:
        kind = f["kind"]
        if kind not in expected:      # family_margins / band_edges are containers
            continue
        coerced = policy._coerce(f["field"], f["value"])
        if kind == "optional_ratio" and coerced is None:
            # Unset is the whole point of the kind, and it must survive a round
            # trip as None rather than arriving back as 0.0.
            assert f["value"] is None
            continue
        assert type(coerced) is expected[kind], (
            f"{f['field']} is rendered as {kind!r} but parses to "
            f"{type(coerced).__name__}")


# ── the rounding increment is policy, not a constant ────────────────────────
def test_recommended_price_uses_the_configured_increment():
    from app import pricing

    th = CommercialThresholds(target_margin_default=0.20,
                              price_rounding_increment=5.0)
    # cost 96 at a 20% target -> 120 exactly; no half-way case in either tick,
    # so this pins the increment rather than the tie-break rule.
    assert pricing.recommend_price(96.0, None, th) == 120.0

    coarse = replace(th, price_rounding_increment=50.0)
    assert pricing.recommend_price(96.0, None, coarse) == 100.0


def test_half_way_prices_keep_the_rounding_they_always_had():
    """Pinned, not chosen here: ``round`` is banker's rounding, and this
    function has always used it. Worth a test so a later change to the
    increment does not quietly change the tie-break as well."""
    from app import pricing

    th = CommercialThresholds(target_margin_default=0.20,
                              price_rounding_increment=50.0)
    # 125 / 50 = 2.5 -> to even -> 2 -> 100.
    assert pricing.recommend_price(100.0, None, th) == 100.0


def test_zero_increment_disables_rounding():
    from app import pricing

    th = CommercialThresholds(target_margin_default=0.20,
                              price_rounding_increment=0.0)
    got = pricing.recommend_price(99.0, None, th)
    assert got == pytest.approx(123.75), (
        "An increment of 0 must return the unrounded figure rather than "
        "dividing by zero or silently falling back to 5.")


def test_a_small_price_is_not_destroyed_by_a_rupee_scale_tick():
    """The bug this guards: a $100-scale item quoted on a ₹5 tick."""
    from app import pricing

    th = CommercialThresholds(target_margin_default=0.20,
                              price_rounding_increment=0.01)
    assert pricing.recommend_price(80.0, None, th) == pytest.approx(100.0)


# ── the tax rate is configuration ───────────────────────────────────────────
def test_quote_summary_reports_the_rate_it_used(monkeypatch):
    from app import store

    monkeypatch.setattr(store, "sales_tax_rate", lambda: 0.05)
    monkeypatch.setattr(store, "sales_tax_label", lambda: "VAT")
    q = store.Quote(id="q1", customer="C", number="Q-1")
    summary = q.to_dict(mgmt=False)["summary"]

    assert summary["taxRate"] == 0.05
    assert summary["taxLabel"] == "VAT"
    assert "gst" not in summary, (
        "A field named 'gst' carrying a VAT amount is a lie the screen cannot "
        "detect.")


# ── formatting follows the currency ─────────────────────────────────────────
@pytest.mark.parametrize("currency,amount,expected", [
    ("INR", 400000, "₹4,00,000"),   # lakh grouping
    ("USD", 400000, "$400,000"),
    ("EUR", 1234, "€1,234"),
    ("MXN", 1234, "MXN 1,234"),     # unknown symbol -> ISO code, never a guess
    ("INR", -1500, "₹-1,500"),
])
def test_money_formatting(currency, amount, expected):
    assert money(amount, currency) == expected


def test_money_renders_none_as_unknown():
    assert money(None, "INR") == "unknown"


def test_thresholds_can_spell_their_own_amounts():
    th = CommercialThresholds(currency="USD")
    assert th.money(2500) == "$2,500"
