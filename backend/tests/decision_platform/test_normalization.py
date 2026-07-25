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
    assert costs[0].source_ref.record_type == "bill"


def test_decimal_parsing_is_exact():
    # Parsing through str avoids binary-float noise (determinism).
    txns = normalize_invoice({"invoice_id": "i", "customer_id": "c", "date": "2026-01-01",
                              "line_items": [{"item_id": "x", "quantity": 3, "rate": "0.1"}]})
    assert txns[0].line_revenue == Decimal("0.3")
