"""What a sync spends, and on what.

From a live run of a **one-day** window, timed by the run's own log:

    Reading customers      28s     13 calls
    Reading items        4m 31s    76 calls
    Reading suppliers      18s     13 calls
    the documents          30s     33 calls   ← what the sync was asked for
    Reading stock by location       608 calls (25 ids each, 15,200 items)

Six minutes of listing, then a stock stage that ran for the better part of half
an hour, to fetch thirty seconds of invoices. Every row of that work is keyed
and upserted **by day** — the masters are refreshed in place, the snapshots are
one row per item per day — so a second sync on the same day rewrites what the
first one wrote.

These pin the fix: the item catalogue and its shelf are a daily reading, a full
sync reads them regardless, and nothing a document names goes unresolved because
of it. Contacts stay on every pull — a customer has no by-id rescue, so
deferring them would skip a new customer's first invoice.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.domain import models
from app.ingestion import jobs
from app.ingestion.sync import SyncService

ORG = "org_cost"
CONN = "conn_sls"


@pytest.fixture()
def org(session):
    session.add(models.Organization(organization_id=ORG, name="SLS Engineers",
                                    currency="INR", config={}))
    session.flush()
    return ORG


class _CountingSource:
    """A source that records which listings were actually asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.item_location_ids: list[str] = []

    def list_contacts(self):
        self.calls.append("contacts")
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

    def list_items(self):
        self.calls.append("items")
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active", "stock_on_hand": 5, "track_inventory": True}]

    def list_vendors(self):
        self.calls.append("vendors")
        return []

    def list_users(self):
        return []

    def list_locations(self):
        self.calls.append("locations")
        return [{"location_id": "L1", "location_name": "Head office",
                 "is_location_active": True, "is_primary_location": True}]

    def list_item_locations(self, item_ids):
        self.calls.append("item_locations")
        self.item_location_ids.extend(item_ids)
        return []

    def list_invoices(self, skip=None):
        self.calls.append("invoices")
        return [{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                 "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                 "quantity": 10, "rate": 500,
                                 "item_total": 5000}]}]

    def list_bills(self, skip=None):
        self.calls.append("bills")
        return []


# ── what a pull reads ───────────────────────────────────────────────────────
def test_a_due_pull_reads_the_catalogue_and_the_shelf(session, org):
    source = _CountingSource()
    report = SyncService(session, source, ORG, catalogue_due=True).run()

    assert "items" in source.calls and "contacts" in source.calls
    assert "item_locations" in source.calls, "the shelf is part of the reading"
    assert report.catalogue_read is True


def test_a_pull_that_is_not_due_reads_only_the_documents(session, org):
    """The whole point: five minutes of listing and 152 stock calls, not spent
    to answer a question this morning already answered."""
    source = _CountingSource()
    report = SyncService(session, source, ORG, catalogue_due=False).run()

    assert "items" not in source.calls, "the item catalogue is the dominant cost"
    assert "item_locations" not in source.calls, "and the shelf is the rest of it"
    assert "contacts" in source.calls, (
        "contacts are NOT deferred: a customer has no by-id rescue, so a new "
        "one's first invoice would be skipped as UNKNOWN_CUSTOMER")
    assert "invoices" in source.calls, "the documents are what it was asked for"
    assert report.catalogue_read is False
    assert report.catalogue_skipped_reason, "and it says why, rather than looking idle"


def test_a_document_naming_an_unknown_item_still_resolves_it(session, org):
    """The property that makes deferring the master safe. An item created since
    this morning's reading is on today's invoice and not in the master — it is
    fetched by id, exactly as it is during the master-read race."""
    source = _CountingSource()
    fetched: list[str] = []

    def get_item(item_id):
        fetched.append(item_id)
        return {"item_id": item_id, "name": "Late arrival", "unit": "pcs",
                "status": "active"}

    source.get_item = get_item
    SyncService(session, source, ORG, catalogue_due=False).run()

    assert fetched == ["i1"], "the line's item was fetched rather than skipped"
    product = session.query(models.Product).one()
    assert product.name == "Late arrival"
    assert session.query(models.SalesTxn).count() == 1, "and the line landed"


# ── when it is due ──────────────────────────────────────────────────────────
def _run(session, *, connection_id=CONN, notes=None, status="OK",
         started=None, run_id="r1"):
    row = models.SyncRun(sync_run_id=run_id, organization_id=ORG, source="api",
                         connection_id=connection_id, status=status,
                         notes=notes or {})
    if started is not None:
        row.started_at = started
    session.add(row)
    session.flush()
    return row


def test_the_first_pull_of_the_day_is_due(session, org):
    assert jobs.catalogue_due(session, ORG, CONN, today=date(2026, 8, 18),
                              full=False, incremental=True) is True


def test_a_second_pull_the_same_day_is_not(session, org):
    _run(session, notes={jobs.CATALOGUE_READ_NOTE: {CONN: "2026-08-18"}})
    assert jobs.catalogue_due(session, ORG, CONN, today=date(2026, 8, 18),
                              full=False, incremental=True) is False


def test_the_next_day_is_due_again(session, org):
    _run(session, notes={jobs.CATALOGUE_READ_NOTE: {CONN: "2026-08-18"}})
    assert jobs.catalogue_due(session, ORG, CONN, today=date(2026, 8, 19),
                              full=False, incremental=True) is True


def test_one_company_being_read_says_nothing_about_another(session, org):
    """Three connected companies are three books."""
    _run(session, notes={jobs.CATALOGUE_READ_NOTE: {CONN: "2026-08-18"}})
    assert jobs.catalogue_due(session, ORG, "conn_4u", today=date(2026, 8, 18),
                              full=False, incremental=True) is True


def test_a_full_sync_reads_them_whatever_the_day(session, org):
    """`full` means re-read everything; that is the button's whole meaning."""
    _run(session, notes={jobs.CATALOGUE_READ_NOTE: {CONN: "2026-08-18"}})
    assert jobs.catalogue_due(session, ORG, CONN, today=date(2026, 8, 18),
                              full=True, incremental=True) is True


def test_the_reconciliation_pass_reads_them_too(session, org):
    """`incremental=False` is the periodic complete pass — deliberately not a
    cheap one."""
    _run(session, notes={jobs.CATALOGUE_READ_NOTE: {CONN: "2026-08-18"}})
    assert jobs.catalogue_due(session, ORG, CONN, today=date(2026, 8, 18),
                              full=False, incremental=False) is True


def test_a_failed_run_does_not_count_as_having_read_them(session, org):
    """A run that died before its own bookkeeping proves nothing about what it
    read — and the note is written from the report either way."""
    _run(session, status="FAILED",
         notes={jobs.CATALOGUE_READ_NOTE: {CONN: "2026-08-18"}})
    assert jobs.catalogue_due(session, ORG, CONN, today=date(2026, 8, 18),
                              full=False, incremental=True) is True
