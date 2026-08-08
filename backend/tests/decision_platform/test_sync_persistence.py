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


# ── skips somebody can act on ───────────────────────────────────────────────
#
# "no product 3452161000001252021" is a true statement that nobody can do
# anything with: Zoho's own UI does not search on an item id, so the only route
# from that message to a fix is opening every bill by hand. These tests pin the
# context that turns the same skip into a task.

def _bill_for_missing_item() -> _Source:
    """A bill referencing an item the master does not have — what a retired
    item looked like before the pull started asking for inactive items."""
    return _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[],
        bills=[{"bill_id": "b1", "bill_number": "KTI/25-26/0043",
                "date": "2026-05-01", "vendor_name": "Kennametal India",
                "line_items": [{"line_item_id": "l1", "item_id": "itm-gone",
                                "name": "CNMG 120408-MP TN2000", "sku": "CN120408",
                                "quantity": 50, "rate": 372, "item_total": 18600}]}],
    )


def test_an_unresolvable_item_is_reported_with_enough_to_find_it(session):
    report = SyncService(session, _bill_for_missing_item(), "org_a").run()
    skip = next(s for s in report.skipped if s["code"] == "UNKNOWN_PRODUCT")
    ctx = skip["context"]
    # The name comes off the document line, because the master has no such
    # record — the line is the only place the name survives.
    assert ctx["label"] == "CNMG 120408-MP TN2000"
    assert ctx["sku"] == "CN120408"
    assert ctx["document"] == "KTI/25-26/0043"
    assert ctx["party"] == "Kennametal India"
    assert ctx["document_date"] == "2026-05-01"
    assert ctx["line_value"] == 18600
    assert "still counted" in ctx["fix"]


def test_a_missing_item_does_not_delete_the_trade(session):
    """The line is kept, against a placeholder.

    Dropping it is how real revenue went missing: an item deleted in Zoho still
    has documents pointing at it, and skipping those lines removed the money
    from revenue, from the customer's history and from margin — leaving a
    plausible wrong number rather than a visible gap. The sale happened; the
    item's *name* is the only thing the platform does not know.
    """
    from app.domain import models

    report = SyncService(session, _bill_for_missing_item(), "org_a").run()
    assert report.cost_records == 1, "the cost line was dropped"

    cost = session.query(models.CostRecord).one()
    assert float(cost.unit_cost) == 372

    prod = session.get(models.Product, cost.product_id)
    assert prod.external_id == "itm-gone"
    # Marked as standing in for a master entry rather than reflecting one, so a
    # screen can say so and the next pull that sees the item overwrites it in
    # place — same upsert key, no duplicate, no manual repair.
    assert prod.source_ref["provisional"] is True
    assert prod.active is False


def test_a_later_pull_that_finds_the_item_fills_the_placeholder_in(session):
    """No duplicate and no manual repair: the placeholder was written under the
    same key the real item upserts on."""
    from app.domain import models

    SyncService(session, _bill_for_missing_item(), "org_a").run()
    found = _bill_for_missing_item()
    found._i = [{"item_id": "itm-gone", "name": "CNMG 120408-MP TN2000",
                 "sku": "CN120408", "status": "inactive"}]
    SyncService(session, found, "org_a").run()

    rows = session.query(models.Product).filter_by(external_id="itm-gone").all()
    assert len(rows) == 1, "the real item was inserted beside the placeholder"
    assert rows[0].name == "CNMG 120408-MP TN2000"
    assert not (rows[0].source_ref or {}).get("provisional")


def test_one_missing_item_on_many_lines_is_one_problem_not_many(session):
    """A retired item on four hundred bill lines is one thing to fix. A list
    that shows it four hundred times — or the first twenty of them — describes
    the symptom and hides everything else."""
    src = _bill_for_missing_item()
    src._b = [
        {"bill_id": f"b{n}", "bill_number": f"KTI/25-26/{n:04d}",
         "date": f"2026-0{(n % 5) + 1}-01", "vendor_name": "Kennametal India",
         "line_items": [{"line_item_id": "l1", "item_id": "itm-gone",
                         "name": "CNMG 120408-MP TN2000", "sku": "CN120408",
                         "quantity": 50, "rate": 372, "item_total": 18600}]}
        for n in range(1, 13)
    ]
    report = SyncService(session, src, "org_a").run()

    assert len(report.skipped) == 12
    groups = report.unresolved()
    assert len(groups) == 1
    g = groups[0]
    assert g["missing_id"] == "itm-gone"
    assert g["label"] == "CNMG 120408-MP TN2000"
    assert g["lines"] == 12
    assert g["value"] == pytest.approx(12 * 18600)
    # Three examples is enough to recognise the pattern; the count carries the
    # rest. A worklist that pastes twelve identical rows is not a worklist.
    assert len(g["examples"]) == 3
    assert g["first_seen"] < g["last_seen"]


def test_the_worklist_puts_the_biggest_blockage_first(session):
    src = _bill_for_missing_item()
    src._b = (
        [{"bill_id": f"a{n}", "bill_number": f"A{n}", "date": "2026-05-01",
          "line_items": [{"line_item_id": "l1", "item_id": "itm-common",
                          "name": "Common", "quantity": 1, "rate": 10,
                          "item_total": 10}]} for n in range(5)]
        + [{"bill_id": "z1", "bill_number": "Z1", "date": "2026-05-01",
            "line_items": [{"line_item_id": "l1", "item_id": "itm-rare",
                            "name": "Rare", "quantity": 1, "rate": 900000,
                            "item_total": 900000}]}]
    )
    groups = SyncService(session, src, "org_a").run().unresolved()
    assert [g["missing_id"] for g in groups] == ["itm-common", "itm-rare"]


# ── one supply stage must not take the others down ──────────────────────────
def test_a_refused_payment_scope_does_not_stop_suppliers_or_orders(session):
    """The shape of the reported problem. Payments and purchase orders need
    scopes bills and invoices do not, so a connection authorised before those
    scopes existed 401s on exactly those endpoints — and that used to abort the
    whole supply stage after suppliers had been read, leaving purchase orders
    unattempted and the error blaming the credentials."""
    from app.ingestion.zoho_client import ZohoScopeError

    class HalfGranted(_Source):
        def list_vendors(self):
            return [{"contact_id": "v1", "contact_name": "Kennametal India",
                     "status": "active"}]

        def list_customer_payments(self, skip=None):
            raise ZohoScopeError(
                "Zoho refused customerpayments: this connection was not granted "
                "ZohoBooks.customerpayments.READ.",
                path="customerpayments", scope="ZohoBooks.customerpayments.READ")

        def list_purchase_orders(self):
            return [{"purchaseorder_id": "po1", "purchaseorder_number": "PO-1",
                     "date": "2026-05-01", "vendor_id": "v1", "status": "issued",
                     "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                     "quantity": 10, "rate": 400}]}]

    src = HalfGranted(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}])
    report = SyncService(session, src, "org_a").run()
    session.commit()

    # The two stages that *could* run, did.
    assert report.vendors == 1
    assert report.purchase_orders == 1
    assert report.payments == 0
    # And the one that could not says which permission is missing.
    refusal = next(s for s in report.skipped if s["code"] == "SCOPE_NOT_GRANTED")
    assert refusal["context"]["scope"] == "ZohoBooks.customerpayments.READ"
    assert "Reconnect" in refusal["context"]["fix"]


def test_a_broken_supply_stage_keeps_everything_already_written(session):
    """A supplier list that 500s must not discard an invoice pull that took
    twenty minutes."""
    class BadVendors(_Source):
        def list_vendors(self):
            raise RuntimeError("upstream exploded")

    src = BadVendors(contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}], items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}], invoices=[{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                      "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                      "quantity": 10, "rate": 500, "item_total": 5000}]}])
    report = SyncService(session, src, "org_a").run()
    session.commit()

    assert report.sales_txns == 1
    assert session.query(models.SalesTxn).count() == 1
    failed = next(s for s in report.skipped if s["code"] == "SUPPLY_STAGE_FAILED")
    assert "upstream exploded" in failed["detail"]


# ── one record, one source ──────────────────────────────────────────────────
#
# The read model keyed customers and items on (organization, external_id).
# That is unique only by accident: Zoho issues globally unique contact ids, so
# two connected Zoho companies never collided. The first connector that numbers
# records per company breaks it, and breaks it silently.

def _company(contact_id="1", name="ABC Industries", item_id="10"):
    return _Source(
        contacts=[{"contact_id": contact_id, "contact_name": name, "status": "active"}],
        items=[{"item_id": item_id, "name": "Insert", "unit": "pcs", "status": "active"}])


def test_the_same_id_in_two_connected_companies_is_two_records(session):
    """Tally numbers ledgers from 1 in every company. Without the source in the
    key, company B's customer 1 overwrites company A's — one of two real
    customers silently disappears, and nothing reports it."""
    SyncService(session, _company("1", "ABC Industries (Chennai)"), "org_a",
                connector="tally", connection_id="conn-a").run()
    SyncService(session, _company("1", "ABC Industries (Pune)"), "org_a",
                connector="tally", connection_id="conn-b").run()
    session.commit()

    rows = session.query(models.Customer).order_by(models.Customer.name).all()
    assert [r.name for r in rows] == ["ABC Industries (Chennai)",
                                      "ABC Industries (Pune)"]
    assert {r.connection_id for r in rows} == {"conn-a", "conn-b"}
    # Both kept their own id; neither was rewritten by the other.
    assert {r.external_id for r in rows} == {"1"}


def test_the_same_name_in_two_companies_stays_two_customers(session):
    """"ABC Industries" in two connected companies is two customers who happen
    to share a name. Names are never identity — the platform links records
    through the identity layer, on evidence, with a person deciding."""
    SyncService(session, _company("100", "ABC Industries"), "org_a",
                connector="zoho", connection_id="conn-a").run()
    SyncService(session, _company("200", "ABC Industries"), "org_a",
                connector="zoho", connection_id="conn-b").run()
    session.commit()
    assert session.query(models.Customer).count() == 2


def test_a_re_sync_of_the_same_company_updates_rather_than_duplicates(session):
    """The property the old key existed to give, kept: source identity is
    stable across runs, so a second pull is an update."""
    for name in ("ABC Industries", "ABC Industries Pvt Ltd"):
        SyncService(session, _company("1", name), "org_a",
                    connector="zoho", connection_id="conn-a").run()
    session.commit()
    rows = session.query(models.Customer).all()
    assert len(rows) == 1 and rows[0].name == "ABC Industries Pvt Ltd"


def test_a_pull_resolves_documents_against_its_own_company(session):
    """An invoice from company B naming item 10 must resolve to B's item 10,
    not to A's. This is the same defect as the collision above, seen from the
    document side — and the one that would put A's cost on B's margin."""
    a = _Source(
        contacts=[{"contact_id": "1", "contact_name": "A Ltd", "status": "active"}],
        items=[{"item_id": "10", "name": "A's insert", "unit": "pcs", "status": "active"}])
    b = _Source(
        contacts=[{"contact_id": "1", "contact_name": "B Ltd", "status": "active"}],
        items=[{"item_id": "10", "name": "B's insert", "unit": "pcs", "status": "active"}],
        invoices=[{"invoice_id": "B-1", "customer_id": "1", "date": "2026-06-01",
                   "line_items": [{"line_item_id": "l1", "item_id": "10",
                                   "quantity": 2, "rate": 100, "item_total": 200}]}])
    SyncService(session, a, "org_a", connector="zoho", connection_id="conn-a").run()
    SyncService(session, b, "org_a", connector="zoho", connection_id="conn-b").run()
    session.commit()

    txn = session.query(models.SalesTxn).one()
    product = session.get(models.Product, txn.product_id)
    customer = session.get(models.Customer, txn.customer_id)
    assert product.name == "B's insert" and product.connection_id == "conn-b"
    assert customer.name == "B Ltd" and customer.connection_id == "conn-b"


def test_rows_imported_before_provenance_existed_still_resolve(session):
    """Nothing is backfilled — a row written before this existed cannot be
    attributed after the fact. It must still resolve, or the next pull orphans
    every document it wrote."""
    SyncService(session, _company("1", "Legacy Ltd"), "org_a").run()
    session.commit()
    assert session.query(models.Customer).one().connection_id is None

    # A later pull that *does* know its company finds the unattributed row
    # rather than creating a second one beside it.
    repo = ReadModelRepository(session, "org_a", connector="zoho",
                               connection_id="conn-a")
    assert repo.get_customer_by_external("1").name == "Legacy Ltd"


# ── the shared origin projection ────────────────────────────────────────────
def test_one_origin_shape_for_every_imported_entity(session):
    """Customers, items and vendors are unrelated tables that share one
    property — they came from somewhere. One projection, so a customer picker
    and an item picker cannot describe their source two different ways."""
    from app.domain.origin import Companies

    session.add(models.Organization(organization_id="org_a", name="Sanketh"))
    session.add(models.ZohoConnection(
        connection_id="conn-a", organization_id="org_a",
        zoho_organization_id="60036630626", label="4U Precision",
        accounts_base="https://accounts.zoho.in",
        api_base="https://www.zohoapis.in/books/v3"))
    session.flush()
    SyncService(session, _good_source(), "org_a",
                connector="zoho", connection_id="conn-a").run()
    session.commit()

    companies = Companies(session, "org_a")
    for row in (session.query(models.Customer).one(),
                session.query(models.Product).one()):
        o = companies.of(row).to_dict()
        assert o["company"] == "4U Precision"
        assert o["connector_short"] == "Zoho"
        assert o["unknown"] is False
        assert o["external_id"]


def test_an_unattributed_row_says_so_rather_than_being_guessed_at(session):
    """The organization has exactly one connection, so guessing would be right
    today — and wrong the moment a second company connects, silently, on rows
    nobody would re-check."""
    from app.domain.origin import Companies

    session.add(models.Organization(organization_id="org_a", name="Sanketh"))
    session.add(models.ZohoConnection(
        connection_id="conn-a", organization_id="org_a",
        zoho_organization_id="60036630626", label="4U Precision",
        accounts_base="https://accounts.zoho.in",
        api_base="https://www.zohoapis.in/books/v3"))
    session.flush()
    SyncService(session, _good_source(), "org_a").run()   # no connection named
    session.commit()

    o = Companies(session, "org_a").of(session.query(models.Customer).one())
    assert o.company == ""
    assert o.connection_id is None


def test_source_badges_are_information_only_when_there_is_more_than_one(session):
    """With a single connected company every badge says the same thing, and a
    column of identical badges is decoration that costs width on every screen."""
    from app.domain.origin import Companies

    session.add(models.Organization(organization_id="org_a", name="Sanketh"))
    session.flush()
    assert Companies(session, "org_a").count == 0

    for n, label in ((1, "4U Precision"), (2, "SLS Engineers")):
        session.add(models.ZohoConnection(
            connection_id=f"conn-{n}", organization_id="org_a",
            zoho_organization_id=f"6003663062{n}", label=label,
            accounts_base="https://accounts.zoho.in",
            api_base="https://www.zohoapis.in/books/v3"))
    session.flush()
    assert Companies(session, "org_a").count == 2


def test_nothing_in_the_origin_layer_branches_on_a_connector_name():
    """A connector contributes a row to a registry and nothing else. A rule
    that needed `if connector == "zoho"` would belong in that connector's own
    package, not in the projection every entity goes through."""
    import inspect


    import ast

    from app.domain import origin as _origin

    tree = ast.parse(inspect.getsource(_origin))
    names = set(_origin.CONNECTORS)
    # Branching is the thing that does not scale: a comparison against a
    # connector name is a rule that has to be repeated for every future one.
    # Naming a connector in a *registry* is data and is fine — that is how a
    # connector declares itself.
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Constant) and side.value in names:
                    raise AssertionError(
                        f"origin.py branches on connector {side.value!r} at "
                        f"line {node.lineno}")


# ── commitments and money out ───────────────────────────────────────────────
#
# Sales orders and vendor payments arrived together because they answer the
# same class of question: what is the business exposed to *before* an
# accounting entry exists. An open order is a promise nobody has invoiced; a
# payment out is the half of cash that receipts alone cannot show.

class _Commitments(_Source):
    """A source that offers both new stages, on top of the reference pulls."""

    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
                 "status": "active"}]

    def list_sales_orders(self):
        return [{"salesorder_id": "so1", "salesorder_number": "SO-1",
                 "customer_id": "c1", "date": "2026-06-10",
                 "shipment_date": "2026-06-25", "status": "open",
                 "invoiced_status": "not_invoiced", "shipped_status": "pending",
                 "total": "125000.50", "salesperson_id": "u9"}]

    def list_vendor_payments(self):
        return [{"payment_id": "vp1", "vendor_id": "v1", "date": "2026-06-12",
                 "amount": "80000.25", "payment_mode": "banktransfer",
                 "reference_number": "NEFT-8891"}]


def _committed(cls=_Commitments) -> "_Commitments":
    """The reference rows the commitment stages resolve against."""
    return cls(contacts=[{"contact_id": "c1", "contact_name": "Acme",
                          "status": "active"}],
               items=[{"item_id": "i1", "name": "Insert", "unit": "pcs",
                       "status": "active"}])


def test_an_open_order_is_stored_with_the_customer_it_was_promised_to(session):
    report = SyncService(session, _committed(), "org_a").run()
    session.commit()

    assert report.sales_orders == 1
    so = session.query(models.SalesOrderDoc).one()
    customer = session.query(models.Customer).one()
    assert so.customer_id == customer.customer_id
    assert so.external_ref == "so1"
    assert so.total == Decimal("125000.50")
    # Both fulfilment facts kept apart: an order can be invoiced and unshipped.
    assert (so.invoiced_status, so.shipped_status) == ("not_invoiced", "pending")
    assert so.expected_ship_date == date(2026, 6, 25)


def test_money_out_is_stored_against_the_supplier_it_was_paid_to(session):
    report = SyncService(session, _committed(), "org_a").run()
    session.commit()

    assert report.vendor_payments == 1
    vp = session.query(models.VendorPaymentDoc).one()
    vendor = session.query(models.Vendor).one()
    assert vp.vendor_id == vendor.vendor_id
    # Parsed through str, so the paise survive intact into a figure cash will
    # add up.
    assert vp.amount == Decimal("80000.25")
    assert vp.mode == "banktransfer"


def test_re_reading_the_same_order_updates_it_rather_than_duplicating(session):
    """Zoho's status moves; the order is still one order. Keyed on
    (organization, external_ref), so a second pull must overwrite."""
    SyncService(session, _committed(), "org_a").run()
    session.commit()

    class Progressed(_Commitments):
        def list_sales_orders(self):
            row = dict(super().list_sales_orders()[0])
            row["shipped_status"] = "shipped"
            return [row]

    SyncService(session, _committed(Progressed), "org_a").run()
    session.commit()

    so = session.query(models.SalesOrderDoc).one()   # one, not two
    assert so.shipped_status == "shipped"
    assert session.query(models.VendorPaymentDoc).count() == 1


def test_an_order_for_an_unknown_customer_is_kept_not_dropped(session):
    """A promise to somebody the contact pull did not return is still a
    promise. Dropping it would understate committed demand; keeping it with a
    null customer only costs the ability to group it."""
    class Orphan(_Commitments):
        def list_sales_orders(self):
            row = dict(super().list_sales_orders()[0])
            row["customer_id"] = "c-unknown"
            return [row]

    report = SyncService(session, _committed(Orphan), "org_a").run()
    session.commit()

    assert report.sales_orders == 1
    assert session.query(models.SalesOrderDoc).one().customer_id is None


def test_a_payment_out_with_no_amount_is_reported_never_treated_as_zero(session):
    """Defaulting it would understate cash out by exactly what the row was
    worth, silently, and in the direction that flatters liquidity."""
    class Malformed(_Commitments):
        def list_vendor_payments(self):
            return [{"payment_id": "vp1", "vendor_id": "v1", "date": "2026-06-12"}]

    report = SyncService(session, _committed(Malformed), "org_a").run()
    session.commit()

    assert report.vendor_payments == 0
    assert session.query(models.VendorPaymentDoc).count() == 0
    assert any(s["kind"] == "vendor_payment" and s["ref"] == "vp1"
               for s in report.skipped)


def test_a_refused_sales_order_scope_still_reads_payments_out(session):
    """The two new stages need two new scopes. A connection authorised before
    either existed must degrade to a named skip per endpoint, not abort the
    pull — the same guarantee the older supply stages already have."""
    from app.ingestion.zoho_client import ZohoScopeError

    class HalfGranted(_Commitments):
        def list_sales_orders(self):
            raise ZohoScopeError(
                "Zoho refused salesorders: this connection was not granted "
                "ZohoBooks.salesorders.READ.",
                path="salesorders", scope="ZohoBooks.salesorders.READ")

    report = SyncService(session, _committed(HalfGranted), "org_a").run()
    session.commit()

    assert report.sales_orders == 0
    assert report.vendor_payments == 1          # the stage after it still ran
    refusal = next(s for s in report.skipped
                   if s["code"] == "SCOPE_NOT_GRANTED" and s["kind"] == "sales_order")
    assert refusal["context"]["scope"] == "ZohoBooks.salesorders.READ"


def test_a_source_that_offers_neither_stage_produces_no_rows_and_no_error(session):
    """A fixture written before these existed, or a Zoho plan without them.
    Probed, never assumed."""
    report = SyncService(session, _good_source(), "org_a").run()
    session.commit()

    assert (report.sales_orders, report.vendor_payments) == (0, 0)
    assert [s for s in report.skipped if s["kind"] in ("sales_order", "vendor_payment")] == []


# ── what a bill still owes ──────────────────────────────────────────────────
#
# Bills were read from the first sync, but only for what the stock cost: each
# was flattened into cost records at line grain and the header discarded. So
# the platform knew what it had paid per insert and nothing about what it
# still owed.

def _billed(bill: dict[str, Any], **kw) -> _Source:
    return _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
        bills=[bill], **kw)


def _payable(**over) -> dict[str, Any]:
    row = {"bill_id": "b1", "bill_number": "BILL-1", "vendor_id": "v1",
           "date": "2026-05-01", "due_date": "2026-05-31", "status": "open",
           "total": "50000", "balance": "50000",
           "line_items": [{"line_item_id": "l1", "item_id": "i1",
                           "quantity": 100, "rate": 400}]}
    row.update(over)
    return row


class _WithVendor(_Source):
    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
                 "status": "active"}]


def test_a_bill_records_what_is_owed_alongside_what_it_cost(session):
    src = _WithVendor(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
        bills=[_payable()])
    report = SyncService(session, src, "org_a").run()
    session.commit()

    assert report.cost_records == 1              # the cost side is unchanged
    bill = session.query(models.BillDoc).one()
    assert bill.due_date == date(2026, 5, 31)
    assert (bill.total, bill.balance) == (Decimal("50000"), Decimal("50000"))
    assert bill.status == "open"
    # Resolved to the supplier on the first pull, not the second: suppliers are
    # masters and are read before documents for exactly this reason.
    assert bill.vendor_id == session.query(models.Vendor).one().vendor_id


def test_a_settled_bill_stops_reading_as_outstanding_on_the_next_pull(session):
    """``balance`` moves as a bill is paid. A row written once and never
    revisited would report every settled bill as still owed."""
    SyncService(session, _billed(_payable()), "org_a").run()
    session.commit()

    paid = _payable(status="paid", balance="0", last_modified_time="2026-06-02T10:00:00+0530")
    SyncService(session, _billed(paid), "org_a").run()
    session.commit()

    bill = session.query(models.BillDoc).one()   # one, not two
    assert (bill.status, bill.balance) == ("paid", Decimal("0"))


def test_a_bill_with_no_usable_lines_is_still_money_owed(session):
    """The cost side rejects it — there is nothing to cost. Dropping the
    payable too would understate what is owed by exactly the bills that are
    hardest to see."""
    report = SyncService(session, _billed(_payable(line_items=[])), "org_a").run()
    session.commit()

    assert report.cost_records == 0
    assert any(s["kind"] == "bill" and s["code"] == "NO_LINES" for s in report.skipped)
    assert session.query(models.BillDoc).one().balance == Decimal("50000")


def test_a_bill_with_no_terms_is_left_unageable_not_assumed_due(session):
    """Defaulting the due date to the bill date would report every untermed
    bill as overdue from the day it was raised."""
    SyncService(session, _billed(_payable(due_date=None)), "org_a").run()
    session.commit()

    assert session.query(models.BillDoc).one().due_date is None


def test_the_balance_is_zoho_s_never_total_minus_what_we_have_seen_paid(session):
    """A credit note applied to the bill makes that subtraction wrong, and
    wrong in the direction that overstates what is owed."""
    SyncService(session, _billed(_payable(total="50000", balance="12000")),
                "org_a").run()
    session.commit()

    assert session.query(models.BillDoc).one().balance == Decimal("12000")


def test_the_payable_is_not_counted_as_a_second_thing_read(session):
    """A payable is the header of a bill ``cost_records`` already counts. A
    second counter over the same documents would make the pull look like it did
    twice the work."""
    report = SyncService(session, _billed(_payable()), "org_a").run()
    session.commit()

    assert report.cost_records == 1
    assert session.query(models.BillDoc).count() == 1
    assert report.to_dict()["cost_records"] == 1
    assert "payables" not in report.to_dict()
