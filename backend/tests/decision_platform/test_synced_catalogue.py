"""The catalogue a registry connector's quote reads from: the synced master.

Business Central, Acumatica, NetSuite, Prophet 21 and Sage have no live
item-lookup call here, so every line on one of their quotes read BOOKS OFFLINE
— honest about live prices and blind to everything the sync already held:
whether the item exists in that book, its id there, the stock the last pull
saw. ``SyncedCatalogue`` answers those from ``item_connector_records`` and
refuses the rest, and every answer says when it was last true.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.domain import models
from app.ingestion.synced_catalogue import SyncedCatalogue
from app.zoho import ZohoWriteRefused

ORG = "org_pie"
BC = "cx_bc"
SYNCED = datetime(2026, 9, 12, 6, 30, tzinfo=timezone.utc)


def _record(s, *, rid: str, connection_id: str, external_id: str, sku: str | None,
            product_id: str | None, synced_at: datetime | None = SYNCED,
            description: str = "") -> None:
    s.add(models.ItemIdentity(identity_id=f"id_{rid}", organization_id=ORG))
    s.add(models.ItemConnectorRecord(
        record_id=rid, organization_id=ORG, identity_id=f"id_{rid}",
        connector="dynamics365", connection_id=connection_id,
        external_id=external_id, sku=sku, description=description,
        product_id=product_id, last_synced_at=synced_at))


@pytest.fixture()
def session():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    # The insert, as the sync left it: a product, its connector record keyed
    # on the normalised SKU, and two stock snapshots of which the newer answers.
    s.add(models.Product(product_id="p_cnmg", organization_id=ORG,
                         connector="dynamics365", connection_id=BC,
                         external_id="ITEM-900", name="CNMG 120408-MP"))
    _record(s, rid="rec_cnmg", connection_id=BC, external_id="ITEM-900",
            sku="CNMG120408MP", product_id="p_cnmg",
            description="CNMG 120408-MP insert")
    s.add(models.StockSnapshot(
        stock_snapshot_id="ss_old", organization_id=ORG, product_id="p_cnmg",
        as_of=date(2026, 9, 1), on_hand=Decimal("50"), available=Decimal("12"),
        purchase_rate=Decimal("300"), tracked=True, source_ref={}))
    s.add(models.StockSnapshot(
        stock_snapshot_id="ss_new", organization_id=ORG, product_id="p_cnmg",
        as_of=date(2026, 9, 11), on_hand=Decimal("60"), available=Decimal("42"),
        purchase_rate=Decimal("371"), tracked=True, source_ref={}))
    # A service item: no SKU on the record, matched by the product's name,
    # and never counted — so no snapshot, and no stock is not stock of nought.
    s.add(models.Product(product_id="p_freight", organization_id=ORG,
                         connector="dynamics365", connection_id=BC,
                         external_id="SVC-1", name="Freight & handling"))
    _record(s, rid="rec_freight", connection_id=BC, external_id="SVC-1",
            sku=None, product_id="p_freight", synced_at=None)
    # Another company of the same system holds a different item. Its master is
    # not this company's, whatever the code.
    _record(s, rid="rec_other", connection_id="cx_other", external_id="ITEM-1",
            sku="KCMT090304LF", product_id=None)
    s.commit()
    yield s
    s.close()


def _catalogue(s, connection_id: str = BC) -> SyncedCatalogue:
    return SyncedCatalogue(s, ORG, connection_id=connection_id,
                           connector="dynamics365", label="Business Central")


def test_an_item_the_sync_holds_is_in_the_books_with_its_id_and_its_stock(session):
    item = _catalogue(session).get_item("cnmg 120408-mp")
    assert item is not None and item.in_books is True
    assert item.item_id == "ITEM-900", "the source's own id, which the writer puts on the line"
    assert item.name == "CNMG 120408-MP"
    assert item.stock == 42, "`available` from the newest snapshot — never on_hand"
    assert item.cost == 371.0
    assert item.list_price is None, "the master carries no selling price"
    assert item.synthetic is False
    assert item.as_of == "2026-09-12"


def test_a_code_the_sync_does_not_hold_is_not_in_the_books_as_of_the_sync(session):
    item = _catalogue(session).get_item("KCMT 090304 LF")   # the other company's
    assert item is not None and item.in_books is False
    assert item.list_price is None and item.stock is None and item.cost is None
    assert item.item_id is None
    # Absent *as of the last pull*: an item created in the book since is not
    # known here, and the line says how old that answer is.
    assert item.as_of == "2026-09-12"


def test_the_products_name_is_tried_after_the_sku(session):
    item = _catalogue(session).get_item("Freight & handling")
    assert item is not None and item.in_books is True and item.item_id == "SVC-1"
    assert item.stock is None and item.cost is None, "no snapshot is not nought"
    # No stamp on the record and no snapshot to fall back on: unknown, not today.
    assert item.as_of is None


def test_the_stamp_falls_back_to_the_snapshot_when_the_record_carries_none(session):
    with session.begin_nested():
        session.get(models.ItemConnectorRecord, "rec_cnmg").last_synced_at = None
        session.flush()
        assert _catalogue(session).get_item("CNMG120408MP").as_of == "2026-09-11"
        session.rollback()


def test_a_company_that_has_never_synced_is_not_available(session):
    assert _catalogue(session).available is True
    assert _catalogue(session, "cx_never").available is False


def test_creating_an_item_is_refused_and_names_the_book(session):
    with pytest.raises(ZohoWriteRefused) as e:
        _catalogue(session).create_item("NEW-1", "New item", 100.0)
    assert "Business Central" in str(e.value) and "sync" in str(e.value)


def test_an_empty_code_answers_nothing(session):
    assert _catalogue(session).get_item("") is None
