"""Normalization: raw Zoho payloads → validated canonical DTOs, incl. malformed."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.normalize import (
    NormalizationError,
    normalize_bill,
    normalize_customer,
    normalize_invoice,
    normalize_product,
)


def test_normalize_customer_ok():
    c = normalize_customer({"contact_id": "cst-1", "contact_name": "Acme", "status": "active"})
    assert c.external_id == "cst-1"
    assert c.name == "Acme"
    assert c.source_ref.record_type == "contact"
    assert c.source_ref.record_id == "cst-1"


def test_normalize_customer_inactive_status():
    c = normalize_customer({"contact_id": "x", "contact_name": "Y", "status": "inactive"})
    assert c.status.value == "INACTIVE"


def test_normalize_customer_missing_name_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_customer({"contact_id": "cst-1"})
    assert e.value.code == "MISSING_FIELD"


def test_normalize_product_ok():
    p = normalize_product({"item_id": "itm-1", "name": "Insert", "unit": "pcs",
                           "hsn_or_sac": "8209", "status": "active"})
    assert p.external_id == "itm-1" and p.uom == "pcs" and p.hsn == "8209" and p.active


def test_normalize_invoice_line_grain_and_provenance():
    txns = normalize_invoice({
        "invoice_id": "inv-9", "customer_id": "cst-1", "date": "2026-06-10",
        "line_items": [
            {"line_item_id": "l1", "item_id": "itm-1", "quantity": 20, "rate": 530,
             "item_total": 10600},
            {"line_item_id": "l2", "item_id": "itm-2", "quantity": 2, "rate": 100},
        ],
    })
    assert len(txns) == 2
    assert txns[0].external_ref == "inv-9:l1"
    assert txns[0].line_revenue == Decimal("10600")
    assert txns[0].date == date(2026, 6, 10)
    assert txns[0].source_ref.record_id == "inv-9" and txns[0].source_ref.line_id == "l1"
    # revenue falls back to qty*rate deterministically when item_total absent
    assert txns[1].line_revenue == Decimal("200")


def test_normalize_invoice_bad_number_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_invoice({"invoice_id": "inv-1", "customer_id": "c", "date": "2026-01-01",
                           "line_items": [{"item_id": "i", "quantity": "NaNish", "rate": 5}]})
    assert e.value.code == "BAD_NUMBER"


def test_normalize_invoice_bad_date_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_invoice({"invoice_id": "inv-1", "customer_id": "c", "date": "not-a-date",
                           "line_items": [{"item_id": "i", "quantity": 1, "rate": 5}]})
    assert e.value.code == "BAD_DATE"


def test_normalize_invoice_no_lines_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_invoice({"invoice_id": "inv-1", "customer_id": "c", "date": "2026-01-01",
                           "line_items": []})
    assert e.value.code == "NO_LINES"


def test_normalize_bill_ok():
    costs = normalize_bill({"bill_id": "bill-1", "date": "2026-05-01",
                            "line_items": [{"line_item_id": "l1", "item_id": "itm-1",
                                            "quantity": 100, "rate": 405}]})
    assert costs[0].external_ref == "bill-1:l1"
    assert costs[0].unit_cost == Decimal("405")
    assert costs[0].rate == Decimal("405")
    assert costs[0].discount_percent == Decimal("0")
    assert costs[0].source_ref.record_type == "bill"


# ── bill line-item discount: the effective-unit-cost bug ───────────────────
def _bill_line(**overrides):
    line = {"line_item_id": "l1", "item_id": "itm-1", "quantity": 1, "rate": 3166}
    line.update(overrides)
    return {"bill_id": "bill-1", "date": "2026-05-01", "line_items": [line]}


def test_no_discount_cost_equals_rate():
    c = normalize_bill(_bill_line())[0]
    assert c.unit_cost == Decimal("3166")
    assert c.rate == Decimal("3166")
    assert c.discount_percent == Decimal("0")


def test_zero_percent_discount_is_the_same_as_no_discount():
    c = normalize_bill(_bill_line(discount=0))[0]
    assert c.unit_cost == Decimal("3166")
    assert c.discount_percent == Decimal("0")


def test_fifty_percent_discount_numeric():
    """The example from the bug report: Rate 3,166, discount 50% -> 1,583."""
    c = normalize_bill(_bill_line(discount=50))[0]
    assert c.unit_cost == Decimal("1583")
    assert c.rate == Decimal("3166"), "the original rate must not be overwritten"
    assert c.discount_percent == Decimal("50")


def test_fifty_percent_discount_as_percent_string():
    """Some Books payloads send discount as a "50%" string, not a bare number."""
    c = normalize_bill(_bill_line(discount="50%"))[0]
    assert c.unit_cost == Decimal("1583")


def test_quantity_does_not_multiply_into_unit_cost():
    """The exact failure mode this bug could reintroduce: a per-unit cost that
    is actually the line total because quantity leaked into the calculation."""
    c = normalize_bill(_bill_line(discount=50, quantity=10))[0]
    assert c.unit_cost == Decimal("1583"), "must stay per-unit, not become 15,830"
    assert c.qty == Decimal("10")
    assert c.unit_cost * c.qty == Decimal("15830"), "the merchandise value, separately"


def test_hundred_percent_discount_is_free_goods():
    c = normalize_bill(_bill_line(discount=100))[0]
    assert c.unit_cost == Decimal("0")
    assert c.discount_percent == Decimal("100")


def test_decimal_discount_percentage():
    c = normalize_bill(_bill_line(rate=1000, discount="12.5"))[0]
    assert c.unit_cost == Decimal("875.0")


def test_missing_discount_field_defaults_to_no_discount():
    c = normalize_bill(_bill_line())[0]  # no "discount" key at all
    assert c.unit_cost == c.rate == Decimal("3166")


def test_null_discount_defaults_to_no_discount():
    c = normalize_bill(_bill_line(discount=None))[0]
    assert c.unit_cost == Decimal("3166")


def test_missing_rate_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_bill({"bill_id": "b", "date": "2026-01-01",
                        "line_items": [{"item_id": "i", "quantity": 1}]})
    assert e.value.code == "MISSING_FIELD"


def test_zero_rate_with_no_discount_is_zero_cost():
    c = normalize_bill(_bill_line(rate=0))[0]
    assert c.unit_cost == Decimal("0")
    assert c.discount_percent is None, "cannot express a percent of a zero list rate"


def test_malformed_discount_raises_bad_discount():
    with pytest.raises(NormalizationError) as e:
        normalize_bill(_bill_line(discount="fifty percent off"))
    assert e.value.code == "BAD_DISCOUNT"


def test_discount_over_100_percent_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_bill(_bill_line(discount=150))
    assert e.value.code == "BAD_DISCOUNT"


def test_negative_discount_raises():
    with pytest.raises(NormalizationError) as e:
        normalize_bill(_bill_line(discount=-10))
    assert e.value.code == "BAD_DISCOUNT"


def test_rounding_uses_decimal_not_float():
    """A discount that does not divide evenly must not pick up binary-float
    noise (e.g. 1000 * (1 - 1/3) landing on 666.66666666...7)."""
    c = normalize_bill(_bill_line(rate="1000", discount="33.333"))[0]
    assert c.unit_cost == Decimal("1000") * (Decimal("1") - Decimal("33.333") / Decimal("100"))
    assert isinstance(c.unit_cost, Decimal)


# ── Zoho's own resolved values take priority over reapplying discount% ──────
def test_item_total_is_the_most_authoritative_source():
    """item_total is Zoho's own post-discount, pre-tax line total — it must win
    even if a (possibly stale or differently-shaped) discount field is also
    present, and it is qty-aware without needing quantity handled separately."""
    c = normalize_bill(_bill_line(discount=10, quantity=10, item_total="15830"))[0]
    assert c.unit_cost == Decimal("1583"), "15830 / 10, not derived from the 10% discount"


def test_discount_amount_is_preferred_over_a_percent_reapplication():
    """discount_amount is Zoho's own resolved monetary discount for the line —
    used directly rather than re-deriving a percentage from it."""
    c = normalize_bill(_bill_line(quantity=1, discount_amount="1583"))[0]
    assert c.unit_cost == Decimal("1583")
    assert c.discount_percent == Decimal("50"), "back-derived for audit, from rate vs cost"


def test_tax_fields_are_not_folded_into_unit_cost():
    """Cost has always been pre-tax here; this fix must not change that."""
    c = normalize_bill(_bill_line(discount=50, tax_amount="500", tax_percentage="18"))[0]
    assert c.unit_cost == Decimal("1583")


def test_downstream_margin_uses_effective_cost_not_the_raw_bill_rate():
    """End to end, through the real normalize_bill and the real margin formula:
    selling ₹2,000 against a ₹3,166 rate discounted 50% must show a 20.85% gross
    margin, not a large loss computed against the pre-discount rate."""
    from app.signals.margin import _margin

    cost = normalize_bill(_bill_line(rate=3166, discount=50))[0]
    assert cost.unit_cost == Decimal("1583")

    selling_price = Decimal("2000")
    gross_profit = selling_price - cost.unit_cost
    assert gross_profit == Decimal("417")

    gross_margin_pct = _margin(selling_price, cost.unit_cost)
    assert round(gross_margin_pct, 4) == round(0.2085, 4)
    assert round(gross_margin_pct * 100, 2) == 20.85


def test_decimal_parsing_is_exact():
    # Parsing through str avoids binary-float noise (determinism).
    txns = normalize_invoice({"invoice_id": "i", "customer_id": "c", "date": "2026-01-01",
                              "line_items": [{"item_id": "x", "quantity": 3, "rate": "0.1"}]})
    assert txns[0].line_revenue == Decimal("0.3")


def test_normalize_product_carries_the_sources_own_taxonomy():
    """The two fields exist because the column the platform already read is
    empty on the live master and these two are full. Raw in, raw out —
    ``normalize`` maps nothing here, for the reason ``category`` is unmapped."""
    p = normalize_product({"item_id": "itm-1", "name": "0.5x06x38x 2FL",
                           "status": "active", "source_item_type": "Endmill",
                           "source_item_category": "Milling"})
    assert p.source_item_type == "Endmill"
    assert p.source_item_category == "Milling"


def test_a_source_with_no_taxonomy_normalizes_to_nothing_said():
    """The connectors in ``ingestion/erp/`` map their own catalogue column onto
    ``category_name`` and keep no equivalent of these two. None is the correct
    value for them and must not become a guess from the item's name."""
    p = normalize_product({"item_id": "itm-1", "name": "CNMG 120408 INSERT",
                           "status": "active", "category_name": "Cutting Tools"})
    assert p.source_item_type is None
    assert p.source_item_category is None
    assert p.category == "Cutting Tools"


def test_a_blank_taxonomy_value_is_nothing_said_and_not_an_empty_string():
    """An empty custom field and an absent one mean the same thing — nobody
    said — and a consumer must not have to test for two."""
    p = normalize_product({"item_id": "itm-1", "name": "x", "status": "active",
                           "source_item_type": "", "source_item_category": ""})
    assert p.source_item_type is None and p.source_item_category is None
