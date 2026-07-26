"""Sync + persistence: idempotency, provenance, malformed handling, isolation."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain import models
from app.ingestion.sync import SyncService
from app.repositories import ReadModelRepository


class _Source:
    """Configurable in-test Zoho source."""

    def __init__(self, contacts=None, items=None, invoices=None, bills=None):
        self._c, self._i, self._inv, self._b = contacts or [], items or [], invoices or [], bills or []

    def list_contacts(self): return list(self._c)
    def list_items(self): return list(self._i)
    def list_invoices(self): return list(self._inv)
    def list_bills(self): return list(self._b)


def _good_source() -> _Source:
    return _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
        invoices=[{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                   "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                   "quantity": 10, "rate": 500, "item_total": 5000}]}],
        bills=[{"bill_id": "b1", "date": "2026-05-01",
                "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                "quantity": 100, "rate": 400}]}],
    )


def test_sync_writes_read_model(session):
    report = SyncService(session, _good_source(), "org_a").run()
    session.commit()
    assert (report.customers, report.products, report.sales_txns, report.cost_records) == (1, 1, 1, 1)
    assert report.skipped == []
    repo = ReadModelRepository(session, "org_a")
    assert repo.count(models.Customer) == 1
    txn = session.query(models.SalesTxn).one()
    assert txn.line_revenue == Decimal("5000")


def test_sync_is_idempotent(session):
    SyncService(session, _good_source(), "org_a").run()
    session.commit()
    SyncService(session, _good_source(), "org_a").run()  # re-run
    session.commit()
    # No duplicates: still one of each.
    assert session.query(models.Customer).count() == 1
    assert session.query(models.SalesTxn).count() == 1
    assert session.query(models.CostRecord).count() == 1


def test_provenance_source_refs_present(session):
    SyncService(session, _good_source(), "org_a").run()
    session.commit()
    txn = session.query(models.SalesTxn).one()
    assert txn.source_ref == {"system": "zoho", "record_type": "invoice",
                              "record_id": "inv1", "line_id": "l1"}
    cust = session.query(models.Customer).one()
    assert cust.source_ref["record_id"] == "c1"
    cost = session.query(models.CostRecord).one()
    assert cost.source_ref["record_type"] == "bill"


def test_malformed_rows_are_skipped_not_dropped_silently(session):
    src = _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme"},        # ok
                  {"contact_id": "c2"}],                               # missing name
        items=[{"item_id": "i1", "name": "Insert"}],
        invoices=[{"invoice_id": "inv1", "customer_id": "c1", "date": "bad-date",
                   "line_items": [{"item_id": "i1", "quantity": 1, "rate": 1}]},  # bad date
                  {"invoice_id": "inv2", "customer_id": "cX", "date": "2026-01-01",
                   "line_items": [{"item_id": "i1", "quantity": 1, "rate": 1}]}],  # unknown customer
    )
    report = SyncService(session, src, "org_a").run()
    session.commit()
    assert report.customers == 1                       # only the valid contact
    codes = {s["code"] for s in report.skipped}
    assert "MISSING_FIELD" in codes                    # c2
    assert "BAD_DATE" in codes                         # inv1
    assert "UNKNOWN_CUSTOMER" in codes                 # inv2
    # nothing partial written for the bad invoice
    assert session.query(models.SalesTxn).count() == 0


def test_organization_isolation(session):
    SyncService(session, _good_source(), "org_a").run()
    SyncService(session, _good_source(), "org_b").run()
    session.commit()
    repo_a = ReadModelRepository(session, "org_a")
    repo_b = ReadModelRepository(session, "org_b")
    assert repo_a.count(models.Customer) == 1
    assert repo_b.count(models.Customer) == 1
    # each org sees only its own rows; the customer_ids differ
    a_ids = {c.customer_id for c in repo_a.list_customers()}
    b_ids = {c.customer_id for c in repo_b.list_customers()}
    assert a_ids.isdisjoint(b_ids)
    # a repo scoped to org_a cannot see org_b's external record
    assert repo_a.get_customer_by_external("c1").organization_id == "org_a"


def test_source_data_preserved_external_ids(session):
    SyncService(session, _good_source(), "org_a").run()
    session.commit()
    # external ids from Zoho are retained verbatim (not renumbered)
    assert session.query(models.Customer).one().external_id == "c1"
    assert session.query(models.Product).one().external_id == "i1"
    assert session.query(models.SalesTxn).one().external_ref == "inv1:l1"
