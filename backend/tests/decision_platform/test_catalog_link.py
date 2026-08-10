"""The Product → PIE catalogue link: exact, derived, and honest when absent.

The link is the join key the platform did not have — ``Product`` is Zoho-derived
and carried no pointer at the decoded catalogue, so the two were disconnected
universes. These tests pin the three properties that make the pointer safe to
build on: it is exact, it is recomputed rather than remembered, and a missing
catalogue asserts nothing.

The engine is stubbed rather than loaded. What is under test is the *sync's*
decision-making, and driving it through the real 13 MB catalogue would make
these tests slow, dependent on a submodule, and — worse — silent about the
absent-catalogue branch, which is the one that cannot be reproduced when the
catalogue is present.
"""
from __future__ import annotations

import pytest

from app.domain import models
from app.ingestion.sync import SyncService


class _Source:
    def __init__(self, items):
        self._i = items

    def list_contacts(self): return []
    def list_users(self): return []
    def list_items(self): return list(self._i)
    def list_invoices(self, skip=None): return []
    def list_bills(self, skip=None): return []


class _Catalog:
    """Stands in for pie_service: a tiny catalogue, or none at all."""

    def __init__(self, records=None, available=True):
        self._records = records or {}
        self.catalog_available = available
        self.catalog_version = "ck_test_v1"

    def lookup_record(self, identifier):
        if not identifier or not self.catalog_available:
            return None
        return self._records.get(str(identifier).strip().upper())


@pytest.fixture
def catalog(monkeypatch):
    """Swap the module-level singleton the sync imports."""
    import app.pie_service as ps

    def _install(stub):
        monkeypatch.setattr(ps, "pie_service", stub)
        return stub
    return _install


ITEM = {"item_id": "i1", "name": "CNMG 120408", "unit": "pcs",
        "status": "active", "sku": "2001174"}
RECORD = {"record_id": "2001174", "iso_shape": "C",
          "product_family": "turning_insert"}


def _product(session):
    return session.query(models.Product).one()


def test_an_exact_sku_links_the_item_to_its_catalogue_record(session, catalog):
    catalog(_Catalog({"2001174": RECORD}))
    report = SyncService(session, _Source([ITEM]), "org_a").run()
    session.commit()

    p = _product(session)
    assert p.pie_record_id == "2001174"
    assert p.pie_link_method == "SKU_EXACT"
    # The stamp says which catalogue judged it, for the reason a computed row
    # carries a thresholds_version.
    assert p.pie_catalog_version == "ck_test_v1"
    assert report.catalog_links == 1


def test_an_unmatched_sku_stays_null_and_is_not_counted(session, catalog):
    catalog(_Catalog({"9999999": RECORD}))
    report = SyncService(session, _Source([ITEM]), "org_a").run()
    session.commit()

    p = _product(session)
    assert p.pie_record_id is None
    assert p.pie_link_method is None
    assert report.catalog_links == 0
    # The item itself still synced. An unlinked product is a normal product —
    # roughly nine in ten of them are.
    assert report.products == 1


def test_a_lost_match_clears_the_link_rather_than_leaving_it_stale(session, catalog):
    """Derived state is recomputed, and that has to cut both ways.

    A link that is only ever written and never cleared would keep asserting a
    record_id the current catalogue no longer supports, under a stamp claiming
    it does.
    """
    catalog(_Catalog({"2001174": RECORD}))
    SyncService(session, _Source([ITEM]), "org_a").run()
    session.commit()
    assert _product(session).pie_record_id == "2001174"

    # The catalogue is rebuilt and no longer carries this record.
    catalog(_Catalog({}))
    SyncService(session, _Source([ITEM]), "org_a").run()
    session.commit()

    p = _product(session)
    assert p.pie_record_id is None
    assert p.pie_link_method is None
    assert p.pie_catalog_version is None


def test_an_absent_catalogue_leaves_an_existing_link_alone(session, catalog):
    """"The pack does not cover this" and "nobody asked the pack" are different
    facts, and only the first is evidence about the item.

    Writing NULL on a sync that ran without a catalogue would erase every link
    in the organization on one bad deployment, and the erasure would be
    indistinguishable from a genuine loss of coverage.
    """
    catalog(_Catalog({"2001174": RECORD}))
    SyncService(session, _Source([ITEM]), "org_a").run()
    session.commit()

    catalog(_Catalog({}, available=False))
    report = SyncService(session, _Source([ITEM]), "org_a").run()
    session.commit()

    p = _product(session)
    assert p.pie_record_id == "2001174", "an absent catalogue must assert nothing"
    assert p.pie_link_method == "SKU_EXACT"
    assert report.catalog_links == 0
    assert report.products == 1, "the sync itself still ran"


def test_an_item_with_no_sku_links_to_nothing(session, catalog):
    catalog(_Catalog({"2001174": RECORD}))
    no_sku = {k: v for k, v in ITEM.items() if k != "sku"}
    SyncService(session, _Source([no_sku]), "org_a").run()
    session.commit()
    assert _product(session).pie_record_id is None
