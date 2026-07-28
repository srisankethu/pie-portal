"""Sync + persistence: idempotency, provenance, malformed handling, isolation."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.domain import models
from app.ingestion.sync import SyncService
from app.repositories import ReadModelRepository


class _Source:
    """Configurable in-test Zoho source."""

    def __init__(self, contacts=None, items=None, invoices=None, bills=None, users=None):
        self._c, self._i, self._inv, self._b = contacts or [], items or [], invoices or [], bills or []
        self._u = users or []
        self.detail_calls: list[str] = []

    def list_contacts(self): return list(self._c)
    def list_items(self): return list(self._i)
    def list_users(self): return list(self._u)

    def _docs(self, rows, id_field, skip):
        for row in rows:
            doc_id = str(row.get(id_field))
            if skip is not None and skip(doc_id, str(row.get("last_modified_time") or "")):
                continue
            self.detail_calls.append(doc_id)
            yield row

    def list_invoices(self, skip=None): return list(self._docs(self._inv, "invoice_id", skip))
    def list_bills(self, skip=None): return list(self._docs(self._b, "bill_id", skip))


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


# ── resuming an interrupted pull ────────────────────────────────────────────
def test_a_second_run_does_not_re_fetch_documents_it_already_has(session):
    """Every invoice and bill costs its own detail call, so a re-run that
    re-fetches everything cannot finish inside a rate limit."""
    first = _good_source()
    SyncService(session, first, "org_a").run()
    session.commit()
    assert first.detail_calls == ["b1", "inv1"]

    second = _good_source()
    SyncService(session, second, "org_a").run()
    session.commit()
    assert second.detail_calls == [], "nothing changed in Zoho — nothing to re-read"


def test_a_document_edited_in_zoho_is_read_again(session):
    src = _good_source()
    SyncService(session, src, "org_a").run()
    session.commit()

    edited = _good_source()
    edited._inv[0]["last_modified_time"] = "2026-07-28T09:00:00+0530"
    edited._inv[0]["line_items"][0]["rate"] = 550
    edited._inv[0]["line_items"][0]["item_total"] = 5500
    SyncService(session, edited, "org_a").run()
    session.commit()

    assert edited.detail_calls == ["inv1"]
    assert session.query(models.SalesTxn).one().line_revenue == Decimal("5500")


def test_a_full_re_pull_forgets_what_it_held(session):
    """The escape hatch: when the read model looks wrong, read it all again."""
    SyncService(session, _good_source(), "org_a").run()
    session.commit()

    again = _good_source()
    SyncService(session, again, "org_a", resume=False).run()
    session.commit()
    assert again.detail_calls == ["b1", "inv1"]


def test_bills_are_pulled_before_invoices(session):
    """Bills are the only source of cost, and there are far fewer of them. They
    must not be hostage to a long invoice pull completing — without them margin
    and cost pass-through cannot be computed at all."""
    src = _good_source()
    SyncService(session, src, "org_a").run()
    assert src.detail_calls.index("b1") < src.detail_calls.index("inv1")


def test_progress_is_readable_after_the_pull_fails(session):
    """A run that wrote rows and then died wrote rows. The report has to say so
    — the caller records it, and a zero would contradict the database."""
    class Dies(_Source):
        def list_invoices(self, skip=None):
            raise RuntimeError("HTTP 429 (rate limited)")

    src = Dies(contacts=[{"contact_id": "c1", "contact_name": "Acme"}],
               items=[{"item_id": "i1", "name": "Insert"}])
    svc = SyncService(session, src, "org_a")
    try:
        svc.run()
    except RuntimeError:
        pass
    assert (svc.report.customers, svc.report.products) == (1, 1)
    assert svc.report.wrote_anything is True


# ── ownership ───────────────────────────────────────────────────────────────
def _with_salesperson(**kw):
    src = _good_source()
    src._inv[0].update(salesperson_id="zu-7", salesperson_name="R. Nair")
    src._u = kw.get("users", [{"user_id": "zu-7", "email": "R.Nair@Sanketh.in",
                               "name": "R. Nair"}])
    return src


def _user(session, org, email):
    u = models.User(organization_id=org, email=email, name="R. Nair", role="SALESPERSON")
    session.add(u)
    session.flush()
    return u


def test_zoho_s_salesperson_becomes_the_account_owner(session):
    """44 of 49 live customers had no owner, so every salesperson queue was
    empty regardless of what the detectors found."""
    user = _user(session, "org_a", "r.nair@sanketh.in")
    report = SyncService(session, _with_salesperson(), "org_a").run()
    session.commit()

    assert session.query(models.Customer).one().assigned_user_id == user.user_id
    assert report.assignments == 1


def test_an_unrecognised_salesperson_leaves_the_account_unassigned(session):
    """Assigning the wrong owner hides an account from the person who should act
    on it. Unassigned is visible to managers; wrongly assigned is invisible."""
    _user(session, "org_a", "someone.else@sanketh.in")
    report = SyncService(session, _with_salesperson(), "org_a").run()
    session.commit()

    assert session.query(models.Customer).one().assigned_user_id is None
    assert {s["code"] for s in report.skipped} == {"UNMAPPED_SALESPERSON"}


def test_ownership_failure_never_fails_the_pull(session):
    """Reading Zoho's user list needs its own scope. Missing it must cost the
    ownership mapping, not the commercial data."""
    class NoUsers(_Source):
        def list_users(self):
            raise RuntimeError("scope is not permitted")

    src = _with_salesperson()
    broken = NoUsers(contacts=src._c, items=src._i, invoices=src._inv, bills=src._b)
    report = SyncService(session, broken, "org_a").run()
    session.commit()

    assert report.sales_txns == 1, "the sales data still landed"
    assert "ASSIGNMENT_UNAVAILABLE" in {s["code"] for s in report.skipped}


def test_ownership_survives_a_resumed_pull(session):
    """The failure this guards: run 1 pulls the invoices and dies before mapping
    owners; run 2 skips those invoices, so recomputing ownership from *this
    run's* invoices alone would leave every account unowned forever."""
    class DiesAfterInvoices(_Source):
        def list_users(self):
            raise RuntimeError("scope is not permitted")

    src = _with_salesperson()
    first = DiesAfterInvoices(contacts=src._c, items=src._i, invoices=src._inv, bills=src._b)
    SyncService(session, first, "org_a").run()
    session.commit()
    assert session.query(models.Customer).one().assigned_user_id is None

    user = _user(session, "org_a", "r.nair@sanketh.in")
    second = _with_salesperson()                 # scope granted; nothing re-fetched
    report = SyncService(session, second, "org_a").run()
    session.commit()

    assert second.detail_calls == [], "the invoices are already held"
    assert session.query(models.Customer).one().assigned_user_id == user.user_id
    assert report.assignments == 1


# ── bill discount: the effective-unit-cost bug ─────────────────────────────
def test_sync_stores_effective_cost_and_preserves_the_audit_trail(session):
    """The bug this guards: unit_cost must be the post-discount cost, and the
    original rate must still be recoverable for audit."""
    src = _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme"}],
        items=[{"item_id": "i1", "name": "Insert"}],
        bills=[{"bill_id": "b1", "date": "2026-05-01",
                "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                "quantity": 1, "rate": 3166, "discount": 50}]}],
    )
    SyncService(session, src, "org_a").run()
    session.commit()
    cost = session.query(models.CostRecord).one()
    assert cost.unit_cost == Decimal("1583")
    assert cost.rate == Decimal("3166")
    assert cost.discount_percent == Decimal("50")


def test_re_sync_backfills_a_historical_cost_record_in_place(session):
    """A row written before the fix (rate == unit_cost, no discount known)
    must be corrected once the bill is re-fetched with the discount fields —
    the same re-sync path used to recover from a rate-limited pull."""
    legacy = _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme"}],
        items=[{"item_id": "i1", "name": "Insert"}],
        bills=[{"bill_id": "b1", "date": "2026-05-01",
                "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                "quantity": 1, "rate": 3166}]}],  # no discount info
    )
    SyncService(session, legacy, "org_a").run()
    session.commit()
    before = session.query(models.CostRecord).one()
    assert before.unit_cost == Decimal("3166")

    corrected = _Source(
        contacts=legacy._c, items=legacy._i,
        bills=[{"bill_id": "b1", "date": "2026-05-01",
                "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                "quantity": 1, "rate": 3166, "discount": 50}]}],
    )
    SyncService(session, corrected, "org_a", resume=False).run()
    session.commit()

    after = session.query(models.CostRecord).one()
    assert after.cost_record_id == before.cost_record_id, "same row, corrected in place"
    assert after.unit_cost == Decimal("1583")
    assert after.rate == Decimal("3166") and after.discount_percent == Decimal("50")


def test_pending_backfill_count_reflects_legacy_rows(session):
    """Visibility for the operator: how many cost records still need a re-sync
    before their unit_cost can be trusted."""
    from app.repositories import ReadModelRepository

    repo = ReadModelRepository(session, "org_a")
    session.add(models.Product(organization_id="org_a", product_id="p1", external_id="i1",
                               name="Insert"))
    session.flush()
    # a legacy row with no rate/discount recorded (as if written before the fix)
    session.add(models.CostRecord(organization_id="org_a", external_ref="legacy-bill:l1",
                                  product_id="p1", date=date(2026, 5, 1),
                                  qty=Decimal("1"), unit_cost=Decimal("3166")))
    session.commit()
    assert repo.count_cost_records_pending_discount_backfill() == 1

    SyncService(session, _good_source(), "org_a").run()   # a fresh, correctly-shaped row
    session.commit()
    assert repo.count_cost_records_pending_discount_backfill() == 1, \
        "the new row has rate set and must not count as pending"


# ── get_source: multi-tenant credential resolution ─────────────────────────
def test_get_source_is_the_fixture_regardless_of_org_when_source_is_not_api(session, monkeypatch):
    from app.config import settings
    from app.ingestion.mock_source import FixtureZohoSource
    from app.ingestion.sync import get_source

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    assert isinstance(get_source(session, "org_a"), FixtureZohoSource)


def test_get_source_raises_clearly_when_the_org_has_no_connection(session, monkeypatch):
    from app.config import settings
    from app.ingestion.sync import ZohoNotConfiguredError, get_source

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    session.add(models.Organization(organization_id="org_unconfigured", name="Unconfigured"))
    session.flush()

    with pytest.raises(ZohoNotConfiguredError) as e:
        get_source(session, "org_unconfigured")
    assert "org_unconfigured" in str(e.value)


def test_get_source_uses_this_org_s_own_stored_connection(session, monkeypatch):
    from app.config import settings
    from app.ingestion.connections import set_zoho_credentials
    from app.ingestion.sync import get_source
    from app.ingestion.zoho_client import ZohoApiSource

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    session.add(models.Organization(organization_id="org_a", name="Org A"))
    session.flush()
    set_zoho_credentials(session, "org_a", zoho_organization_id="12345", client_id="cid",
                         client_secret="s", refresh_token="r")

    src = get_source(session, "org_a")
    assert isinstance(src, ZohoApiSource)
    assert src._org == "12345"


def test_two_orgs_sync_from_their_own_credentials_never_the_others(session, monkeypatch):
    from app.config import settings
    from app.ingestion.connections import set_zoho_credentials
    from app.ingestion.sync import get_source

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    session.add(models.Organization(organization_id="org_a", name="Org A"))
    session.add(models.Organization(organization_id="org_b", name="Org B"))
    session.flush()
    set_zoho_credentials(session, "org_a", zoho_organization_id="AAA", client_id="cid-a",
                         client_secret="sa", refresh_token="ra")
    set_zoho_credentials(session, "org_b", zoho_organization_id="BBB", client_id="cid-b",
                         client_secret="sb", refresh_token="rb")

    assert get_source(session, "org_a")._org == "AAA"
    assert get_source(session, "org_b")._org == "BBB"
