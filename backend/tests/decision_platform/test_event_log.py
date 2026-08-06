"""The event log, and the one test that makes it worth having.

An event log's only real claim is that state can be rebuilt from it. So the
central test here does not check that events were written — it wipes the read
model, replays, and asserts the rows come back **identical, column for column**.
If the events are lossy, that comparison is where it shows, and no amount of
downstream state engineering can recover what was never recorded.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete

from app.domain import models
from app.ingestion.sync import SyncService
from app.state import events as ev
from app.state.events import EventLog, Source
from app.state.replay import APPLIERS, replay


class _Source:
    """A source that offers every document kind the log records."""

    def __init__(self, **rows: Any) -> None:
        self._rows = rows

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"},
                {"contact_id": "c2", "contact_name": "Bharat Tools",
                 "status": "active"}]

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active", "stock_on_hand": 40, "available_stock": 35,
                 "purchase_rate": 400},
                {"item_id": "i2", "name": "End mill", "unit": "pcs",
                 "status": "active", "stock_on_hand": 12, "available_stock": 12,
                 "purchase_rate": 900}]

    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
                 "status": "active"}]

    def list_users(self):
        return []

    def _docs(self, key, id_field, skip):
        for row in self._rows.get(key, []):
            doc_id = str(row.get(id_field))
            if skip is not None and skip(doc_id, str(row.get("last_modified_time") or "")):
                continue
            yield row

    def list_invoices(self, skip=None):
        return list(self._docs("invoices", "invoice_id", skip))

    def list_bills(self, skip=None):
        return list(self._docs("bills", "bill_id", skip))

    def list_customer_payments(self, skip=None):
        return list(self._docs("payments", "payment_id", skip))

    def list_sales_orders(self):
        return list(self._rows.get("sales_orders", []))

    def list_purchase_orders(self):
        return list(self._rows.get("purchase_orders", []))

    def list_vendor_payments(self):
        return list(self._rows.get("vendor_payments", []))


def _everything() -> _Source:
    """One document of every kind, with two lines where lines exist."""
    return _Source(
        invoices=[{"invoice_id": "inv1", "invoice_number": "INV-1",
                   "customer_id": "c1", "date": "2026-06-01",
                   "line_items": [
                       {"line_item_id": "l1", "item_id": "i1", "quantity": 10,
                        "rate": "512.75", "item_total": "5127.50"},
                       {"line_item_id": "l2", "item_id": "i2", "quantity": 3,
                        "rate": "1200", "item_total": "3600"}]},
                  {"invoice_id": "inv2", "invoice_number": "INV-2",
                   "customer_id": "c2", "date": "2026-06-04",
                   "line_items": [
                       {"line_item_id": "l1", "item_id": "i1", "quantity": 4,
                        "rate": "500", "discount": "5%", "item_total": "1900"}]}],
        bills=[{"bill_id": "b1", "bill_number": "BILL-1", "vendor_id": "v1",
                "date": "2026-05-01", "due_date": "2026-05-31", "status": "open",
                "total": "48000", "balance": "48000",
                "line_items": [
                    {"line_item_id": "l1", "item_id": "i1", "quantity": 100,
                     "rate": "401.25"},
                    {"line_item_id": "l2", "item_id": "i2", "quantity": 20,
                     "rate": "880"}]}],
        payments=[{"payment_id": "p1", "customer_id": "c1", "date": "2026-06-20",
                   "amount": "5127.50", "payment_mode": "banktransfer",
                   "invoices": [{"invoice_id": "inv1", "invoice_number": "INV-1",
                                 "date": "2026-06-01", "due_date": "2026-07-01",
                                 "amount_applied": "5127.50"}]}],
        sales_orders=[{"salesorder_id": "so1", "salesorder_number": "SO-1",
                       "customer_id": "c1", "date": "2026-06-10",
                       "shipment_date": "2026-06-25", "status": "open",
                       "total": "125000.50"}],
        purchase_orders=[{"purchaseorder_id": "po1", "purchaseorder_number": "PO-1",
                          "vendor_id": "v1", "date": "2026-05-02",
                          "status": "issued", "total": "60000"}],
        vendor_payments=[{"payment_id": "vp1", "vendor_id": "v1",
                          "date": "2026-05-20", "amount": "48000",
                          "payment_mode": "banktransfer"}],
    )


#: Every table replay is expected to rebuild, and the column that identifies a
#: row within it. Masters are absent on purpose — replay resolves against them,
#: it does not recreate them.
_REBUILT = [
    (models.SalesTxn, "external_ref"),
    (models.CostRecord, "external_ref"),
    (models.BillDoc, "external_ref"),
    (models.PaymentReceipt, "external_ref"),
    (models.VendorPaymentDoc, "external_ref"),
    (models.SalesOrderDoc, "external_ref"),
    (models.PurchaseOrderDoc, "external_ref"),
    (models.StockSnapshot, "product_id"),
]

#: Columns that are honestly allowed to differ between the original write and
#: the rebuild: surrogate keys and write timestamps. Everything else must match.
_INCIDENTAL = {"created_at", "updated_at", "sales_txn_id", "cost_record_id",
               "bill_id", "payment_receipt_id", "vendor_payment_id",
               "sales_order_id", "purchase_order_id", "stock_snapshot_id"}


def _snapshot(session, org: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Every rebuildable row, keyed so two runs can be compared field by field."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for model, key in _REBUILT:
        rows = session.query(model).filter(model.organization_id == org).all()
        table: dict[str, dict[str, Any]] = {}
        for row in rows:
            values = {c.name: getattr(row, c.name)
                      for c in model.__table__.columns
                      if c.name not in _INCIDENTAL}
            # Stock is one row per item per day, so the day is part of its key.
            ident = str(values.get(key) or getattr(row, key))
            if model is models.StockSnapshot:
                ident = f"{ident}:{values['as_of']}"
            table[ident] = values
        out[model.__tablename__] = table
    return out


def _wipe(session, org: str) -> None:
    """Drop the read model, keeping the masters and the log.

    Deliberately *not* a full database wipe: replay's contract is that it can
    rebuild what it wrote, resolving against masters that are still there. A
    replay into an empty database would be testing a different, weaker claim.
    """
    for model, _ in _REBUILT:
        session.execute(delete(model).where(model.organization_id == org))
    session.execute(delete(models.PaymentApplication))
    session.flush()


# ── the test the log exists for ──────────────────────────────────────────────
def test_replaying_the_log_reproduces_the_read_model_exactly(session):
    """The honest test of completeness. Anything the events do not carry shows
    up here as a column that came back different — or a row that did not come
    back at all."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()
    original = _snapshot(session, "org_a")
    assert original["sales_txns"], "the fixture must actually write something"

    _wipe(session, "org_a")
    assert _snapshot(session, "org_a")["sales_txns"] == {}

    report = replay(session, "org_a")
    session.commit()

    assert report.unresolved == []
    rebuilt = _snapshot(session, "org_a")
    for table, rows in original.items():
        assert rebuilt[table] == rows, f"{table} did not come back identical"


def test_every_registered_event_type_has_an_applier(session):
    """An unregistered applier replays as silence: the event is read, ordered,
    and quietly does nothing."""
    assert set(APPLIERS) == set(ev.EVENT_TYPES)


def test_a_full_pull_emits_one_event_per_thing_it_read(session):
    report = SyncService(session, _everything(), "org_a").run()
    session.commit()
    log = EventLog(session, "org_a")

    assert log.count(event_type=ev.SALE_LINE_RECORDED) == report.sales_txns == 3
    assert log.count(event_type=ev.COST_LINE_RECORDED) == report.cost_records == 2
    assert log.count(event_type=ev.PAYABLE_RECORDED) == 1
    assert log.count(event_type=ev.PAYMENT_RECEIVED) == report.payments == 1
    assert log.count(event_type=ev.PAYMENT_MADE) == report.vendor_payments == 1
    assert log.count(event_type=ev.SALES_ORDER_PLACED) == report.sales_orders == 1
    assert log.count(event_type=ev.PURCHASE_ORDER_PLACED) == report.purchase_orders == 1
    assert log.count(event_type=ev.STOCK_OBSERVED) == report.stock_snapshots == 2


# ── nothing mutates ─────────────────────────────────────────────────────────
def test_an_edited_document_supersedes_its_reading_rather_than_duplicating(session):
    SyncService(session, _everything(), "org_a").run()
    session.commit()

    edited = _everything()
    inv = edited._rows["invoices"][0]
    inv["last_modified_time"] = "2026-07-28T09:00:00+0530"
    inv["line_items"][0]["rate"] = "600"
    inv["line_items"][0]["item_total"] = "6000"
    SyncService(session, edited, "org_a").run()
    session.commit()

    log = EventLog(session, "org_a")
    # Still three live sale lines, not five: the first reading was retired.
    assert log.count(event_type=ev.SALE_LINE_RECORDED) == 3
    live = [e for e in log.live(types=[ev.SALE_LINE_RECORDED])
            if e.source_doc_id == "inv1"]
    assert {e.payload["line_revenue"] for e in live} == {"6000", "3600"}
    # And the old reading is kept, stamped, not deleted — the log records what
    # was believed and when it stopped being believed.
    retired = session.query(models.BusinessEvent).filter(
        models.BusinessEvent.superseded_at.isnot(None),
        models.BusinessEvent.event_type == ev.SALE_LINE_RECORDED,
        models.BusinessEvent.source_doc_id == "inv1").all()
    assert {e.payload["line_revenue"] for e in retired} == {"5127.50", "3600"}


def test_a_line_deleted_upstream_stops_being_believed(session):
    """Appending alone could never express this: the second reading has fewer
    lines than the first, and only supersession makes the missing one go away."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()

    shortened = _everything()
    inv = shortened._rows["invoices"][0]
    inv["last_modified_time"] = "2026-07-28T09:00:00+0530"
    inv["line_items"] = inv["line_items"][:1]
    SyncService(session, shortened, "org_a").run()
    session.commit()

    log = EventLog(session, "org_a")
    live = [e for e in log.live(types=[ev.SALE_LINE_RECORDED])
            if e.source_doc_id == "inv1"]
    assert [e.source_line_id for e in live] == ["l1"]


def test_a_re_read_that_changed_nothing_leaves_one_live_reading(session):
    """A resumed pull skips documents it already holds, so nothing is
    re-recorded — and even a forced re-read must leave one live event per line,
    never two."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()
    SyncService(session, _everything(), "org_a", resume=False).run()
    session.commit()

    assert EventLog(session, "org_a").count(event_type=ev.SALE_LINE_RECORDED) == 3


# ── ordering and provenance ─────────────────────────────────────────────────
def test_events_are_ordered_by_when_they_were_read_not_by_their_date(session):
    """A correction entered today for a March invoice must win over the March
    reading. Ordering on the business date would apply them the other way
    round."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()

    corrected = _everything()
    inv = corrected._rows["invoices"][1]           # dated after inv1
    inv["last_modified_time"] = "2026-07-28T09:00:00+0530"
    inv["date"] = "2026-05-01"                     # back-dated on correction
    inv["line_items"][0]["item_total"] = "1000"
    SyncService(session, corrected, "org_a", resume=True).run()
    session.commit()

    live = list(EventLog(session, "org_a").live(types=[ev.SALE_LINE_RECORDED]))
    assert [e.seq for e in live] == sorted(e.seq for e in live)
    # The correction is last in sequence despite being first by date.
    assert live[-1].source_doc_id == "inv2"
    assert live[-1].occurred_on == date(2026, 5, 1)


def test_an_event_records_which_company_it_was_read_from(session):
    """An event with no source cannot be re-derived, and a log that cannot be
    re-derived is not replayable."""
    SyncService(session, _everything(), "org_a", connector="zoho",
                connection_id="conn-1").run()
    session.commit()

    for e in EventLog(session, "org_a").live():
        assert (e.connector, e.connection_id) == ("zoho", "conn-1")


def test_two_organizations_never_see_each_other_s_events(session):
    SyncService(session, _everything(), "org_a").run()
    SyncService(session, _everything(), "org_b").run()
    session.commit()

    a = list(EventLog(session, "org_a").live())
    b = list(EventLog(session, "org_b").live())
    assert a and b
    assert {e.seq for e in a}.isdisjoint({e.seq for e in b})


# ── what the log refuses to do ──────────────────────────────────────────────
def test_an_unregistered_event_type_is_refused_loudly(session):
    """Written and ordered correctly and then silently never reaching a reducer
    is the kind of failure that takes a quarter to notice."""
    log = EventLog(session, "org_a")
    with pytest.raises(ev.UnknownEventType):
        log.record("MARGIN_IMPROVED", date(2026, 6, 1),
                   Source("invoice", "inv1"), {})


def test_a_decimal_survives_the_round_trip_exactly(session):
    """Payloads are JSON. A float round-trip would put binary noise into a
    figure the whole platform is meant to be able to reproduce."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()

    sale = next(e for e in EventLog(session, "org_a").live(
        types=[ev.SALE_LINE_RECORDED]) if e.source_doc_id == "inv1")
    assert sale.payload["unit_price"] == "512.75"          # a string, not 512.75
    assert Decimal(sale.payload["line_revenue"]) == Decimal("5127.50")


def test_no_master_record_is_an_event(session):
    """A customer is not something that happened; it is what an event refers
    to. Replaying names would turn every re-import into a rename history."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()

    kinds = {e.source_doc_type for e in EventLog(session, "org_a").live()}
    assert kinds.isdisjoint({"contact", "customer", "item", "product", "vendor"})


def test_an_unresolvable_reference_is_reported_by_replay_not_swallowed(session):
    """A replay that silently drops a line and reports success is worse than no
    replay: it makes a lossy log look complete."""
    SyncService(session, _everything(), "org_a").run()
    session.commit()
    _wipe(session, "org_a")
    session.execute(delete(models.Product).where(
        models.Product.external_id == "i2"))
    session.flush()

    report = replay(session, "org_a")
    session.commit()

    assert report.unresolved, "a missing product must be reported"
    assert {r["missing"] for r in report.unresolved} == {"product"}
    assert all(r["reference"] == "i2" for r in report.unresolved)
