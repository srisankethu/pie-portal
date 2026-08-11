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
from datetime import date, timedelta

import pytest

from app.commercial import policy
from app.commercial.config import CommercialThresholds
from app.commercial.money import money
from app.domain import models
from app.ingestion.sync import SyncService


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


# ── a foreign document never becomes a local number ─────────────────────────
# The gap these close is not cosmetic and was reachable through the ordinary
# flow. One organization holds several connected companies and rolls revenue and
# margin up across them; `ping` has always read each company's currency and the
# screen has always shown it; it was never stored and never compared. No money
# row in this schema carries a currency, so a EUR bill read into a book that
# reports in INR is subtracted from INR revenue by `economics.line_economics`
# and reports a margin near 100% with a clean version hash on it. There is no
# later point at which that is still visible, which is why the refusal is at the
# seam rather than in the arithmetic.
class _CurrencySource:
    """A source reporting one invoice and one bill, in whatever currency."""

    def __init__(self, currency=None):
        self._currency = currency
        self.listed: dict[str, set[str]] = {}
        self.listing_complete: set[str] = set()
        self._since = date.today() - timedelta(days=365)
        self._until = None

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active", "purchase_rate": "100"}]

    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Supplier", "status": "active"}]

    def list_users(self):
        return []

    def list_sales_orders(self):
        return []

    def list_purchase_orders(self):
        return []

    def _money(self, doc):
        # Only set the key when a currency was given: the absent case is a
        # distinct behaviour and a None here would not exercise it.
        if self._currency is not None:
            doc["currency_code"] = self._currency
            doc["exchange_rate"] = "90.0"
        return doc

    def list_invoices(self, skip=None):
        day = (date.today() - timedelta(days=10)).isoformat()
        yield self._money({
            "invoice_id": "INV1", "invoice_number": "INV-1", "customer_id": "c1",
            "date": day, "status": "sent", "total": "5000", "balance": "5000",
            "due_date": day, "last_modified_time": "stamp-1",
            "line_items": [{"line_item_id": "l1", "item_id": "i1",
                            "quantity": 10, "rate": "500", "item_total": "5000"}]})

    def list_bills(self, skip=None):
        day = (date.today() - timedelta(days=10)).isoformat()
        yield self._money({
            "bill_id": "BILL1", "bill_number": "B-1", "vendor_id": "v1",
            "date": day, "status": "open", "total": "1000", "balance": "1000",
            "due_date": day, "last_modified_time": "stamp-b1",
            "line_items": [{"line_item_id": "bl1", "item_id": "i1",
                            "quantity": 10, "rate": "100", "item_total": "1000"}]})


def _run(session, org_id, source, connection_id=None):
    svc = SyncService(session, source, org_id, connector="zoho",
                      connection_id=connection_id)
    svc.run()
    session.commit()
    return svc


def test_a_foreign_currency_invoice_is_refused_rather_than_counted(session):
    org_id = _org(session, "org_fx", currency="INR")
    svc = _run(session, org_id, _CurrencySource("EUR"))

    assert session.query(models.SalesTxn).filter_by(
        organization_id=org_id).count() == 0
    refused = [s for s in svc.report.skipped if s["code"] == "FOREIGN_CURRENCY"]
    assert {s["kind"] for s in refused} == {"invoice", "bill"}
    # The refusal has to be actionable: which currency, against which book, and
    # enough to find the document in Zoho.
    ctx = refused[0]["context"]
    assert ctx["document_currency"] == "EUR" and ctx["book_currency"] == "INR"
    assert ctx["exchange_rate"] == "90.0"


def test_a_document_in_the_books_own_currency_is_read(session):
    org_id = _org(session, "org_same", currency="EUR")
    svc = _run(session, org_id, _CurrencySource("EUR"))

    assert session.query(models.SalesTxn).filter_by(
        organization_id=org_id).count() == 1
    assert [s for s in svc.report.skipped if s["code"] == "FOREIGN_CURRENCY"] == []


def test_a_document_naming_no_currency_is_read_but_counted(session):
    """Silence is not a statement that a document is foreign.

    Refusing on an absent field would empty the read model against any source
    that does not report it. The gap is counted instead of passed over, so a
    number that stays high says the guard is not seeing currencies rather than
    that it is finding none to object to.
    """
    org_id = _org(session, "org_silent", currency="INR")
    svc = _run(session, org_id, _CurrencySource(None))

    assert session.query(models.SalesTxn).filter_by(
        organization_id=org_id).count() == 1
    assert [s for s in svc.report.skipped if s["code"] == "FOREIGN_CURRENCY"] == []
    assert svc.report.foreign_currency_unknown == 2      # the invoice and the bill


def test_a_whole_connection_in_another_currency_is_refused_not_merely_consistent(session):
    """Per-connection consistency is not the property that matters.

    An AED company under an INR organization keeps internally consistent books,
    so a guard comparing each document against *its own connection* passes every
    one of them — and `ZohoConnection` rolls revenue and margin up across
    connections, so the AED numbers land in the INR totals anyway. The defect
    arrives one level up and looks like nothing went wrong.

    So the standard is the organization's currency, and `base_currency` is the
    diagnostic that says which connections will be refused wholesale.
    """
    org_id = _org(session, "org_multi", currency="INR")
    session.add(models.ZohoConnection(
        connection_id="conn_ae", organization_id=org_id,
        zoho_organization_id="z-ae", label="Gulf", base_currency="AED"))
    session.flush()

    svc = _run(session, org_id, _CurrencySource("AED"), connection_id="conn_ae")

    assert session.query(models.SalesTxn).filter_by(
        organization_id=org_id).count() == 0
    refused = [s for s in svc.report.skipped if s["code"] == "FOREIGN_CURRENCY"]
    assert {s["kind"] for s in refused} == {"invoice", "bill"}
    assert refused[0]["context"]["book_currency"] == "INR"
