"""The three root causes behind a real run's skipped-rows export, pinned.

An 18-row export from a live three-book pull decomposed into exactly three
defects, and each test here is named for the row it would have prevented:

* ``UNKNOWN_PRODUCT`` on items that exist and are active in Zoho — created
  minutes before the invoice that named them, inside the same run, after the
  master had already been listed. The master is read once; the documents take
  minutes; the gap between is a live book where people are working.
* ``SUPPLY_STAGE_FAILED: Resource does not exist`` on all three companies at
  once — the per-location stock ask was built from an organization-wide id map,
  so each book was asked about the other two books' items and Zoho answered the
  first foreign id with a 404 for the whole batch.
* ``UNMAPPED_SALESPERSON`` naming ids from other companies' books — the
  assignment pass walked the organization's pooled customers, looking each
  book's salespeople up in whichever book's user list was running.

Plus the defect that made the export itself unactionable: the client projection
stripped ``name``/``sku``/``description`` from document lines and
``customer_name`` from invoices, so every skip row promised "the item as
written on the document" and delivered a blank.
"""
from __future__ import annotations

import pytest

from app.domain import models
from app.ingestion.sync import SyncService
from app.ingestion.zoho_client import ZohoThrottleError
from app.repositories import ReadModelRepository


class _Source:
    """A configurable book, with an optional by-id item fetch."""

    def __init__(self, contacts=None, items=None, invoices=None, bills=None,
                 users=None, items_by_id=None):
        self._c, self._i = contacts or [], items or []
        self._inv, self._b, self._u = invoices or [], bills or [], users or []
        #: What `get_item` can answer beyond the listing. None values mean
        #: "Zoho no longer holds this id".
        self._by_id = items_by_id
        self.item_fetches: list[str] = []

    def list_contacts(self): return list(self._c)
    def list_items(self): return list(self._i)
    def list_users(self): return list(self._u)
    def list_invoices(self, skip=None): return list(self._inv)
    def list_bills(self, skip=None): return list(self._b)

    def get_item(self, item_id):
        self.item_fetches.append(item_id)
        answer = (self._by_id or {}).get(item_id)
        if isinstance(answer, Exception):
            raise answer
        return answer


CUSTOMER = {"contact_id": "c1", "contact_name": "Sandvik Mining", "status": "active"}

#: An invoice whose item the master listing never returned — the racing case.
RACED_INVOICE = {
    "invoice_id": "inv9", "customer_id": "c1", "customer_name": "Sandvik Mining",
    "date": "2026-08-11", "invoice_number": "HYD/FY27/INV-751",
    "line_items": [
        {"line_item_id": "l1", "item_id": "i-new", "name": "REGRIND RECOAT D8",
         "sku": "5183438", "quantity": 4, "rate": 3480, "item_total": 13920},
        {"line_item_id": "l2", "item_id": "i-new", "name": "REGRIND RECOAT D8",
         "sku": "5183438", "quantity": 6, "rate": 4060, "item_total": 24360},
    ],
}

RACED_ITEM = {"item_id": "i-new", "name": "REGRIND RECOAT D8", "sku": "5183438",
              "unit": "Nos", "status": "active"}


def test_an_item_created_mid_run_is_fetched_by_id_not_placeholdered(session):
    """The racing case: created 12:48, invoiced 13:14, one pull. The item
    demonstrably exists; concluding UNKNOWN_PRODUCT from a listing that ran
    before it did was answering a question about the past."""
    src = _Source(contacts=[CUSTOMER], invoices=[RACED_INVOICE],
                  items_by_id={"i-new": RACED_ITEM})
    report = SyncService(session, src, "org_a").run()
    session.commit()

    assert [s for s in report.skipped if s["code"] == "UNKNOWN_PRODUCT"] == []
    prod = session.query(models.Product).one()
    assert prod.name == "REGRIND RECOAT D8", "the real record, not a placeholder"
    assert not (prod.source_ref or {}).get("provisional")
    assert report.sales_txns == 2, "both lines landed against the real product"


def test_one_unknown_item_costs_one_fetch_not_one_per_line(session):
    """Forty lines of one unknown item must not be forty detail calls — the
    rate limit is the scarcest thing a pull spends."""
    src = _Source(contacts=[CUSTOMER], invoices=[RACED_INVOICE],
                  items_by_id={"i-new": RACED_ITEM})
    SyncService(session, src, "org_a").run()

    assert src.item_fetches == ["i-new"], "second line reused the answer"


def test_an_item_zoho_no_longer_holds_still_placeholders_and_reports(session):
    """The rescue must never become a new failure mode: when the by-id fetch
    answers nothing, the outcome is exactly the old one — placeholder, skip,
    line kept — and the fetch is not repeated per line."""
    src = _Source(contacts=[CUSTOMER], invoices=[RACED_INVOICE],
                  items_by_id={})                      # get_item answers None
    report = SyncService(session, src, "org_a").run()
    session.commit()

    skips = [s for s in report.skipped if s["code"] == "UNKNOWN_PRODUCT"]
    assert len(skips) == 2, "reported per affected line, as before"
    assert src.item_fetches == ["i-new"], "asked once, remembered"
    prod = session.query(models.Product).one()
    assert (prod.source_ref or {}).get("provisional")
    assert report.sales_txns == 2, "the revenue is still counted"

    # The export-actionability half: the skip names what the document named.
    ctx = skips[0]["context"]
    assert ctx["label"] == "REGRIND RECOAT D8"
    assert ctx["sku"] == "5183438"
    assert ctx["party"] == "Sandvik Mining"
    assert ctx["document"] == "HYD/FY27/INV-751"


def test_a_throttle_during_the_rescue_stops_the_pull(session):
    """A rate limit is a reason to stop and resume — reporting it as a missing
    item would be the 'absence of evidence' mistake with a retry budget."""
    src = _Source(contacts=[CUSTOMER], invoices=[RACED_INVOICE],
                  items_by_id={"i-new": ZohoThrottleError("429")})
    with pytest.raises(ZohoThrottleError):
        SyncService(session, src, "org_a").run()


def test_a_source_without_get_item_behaves_exactly_as_before(session):
    """The fixture source and any older adapter simply lack the method; the
    placeholder path must be exactly the old behaviour for them."""

    class _NoFetch(_Source):
        get_item = None                 # the probe is hasattr-and-callable

    src = _NoFetch(contacts=[CUSTOMER], invoices=[RACED_INVOICE])
    report = SyncService(session, src, "org_a").run()

    skips = [s for s in report.skipped if s["code"] == "UNKNOWN_PRODUCT"]
    assert len(skips) == 2 and report.sales_txns == 2


# ── the stock ask: each book asked only about its own items ──────────────────

def test_the_stock_ask_names_only_this_books_items(session):
    """The 404 that failed per-location stock on all three companies: an
    organization-wide id map sent each book the other books' ids, and Zoho
    answers a batch containing a foreign id with one 404 for the whole call."""
    ReadModelRepository(session, "org_a", connector="zoho",
                        connection_id="conn_sls")
    for conn, ext in (("conn_sls", "sls-item"), ("conn_4up", "4up-item")):
        session.add(models.Product(organization_id="org_a", external_id=ext,
                                   connector="zoho", connection_id=conn,
                                   name=ext, active=True))
    # A placeholder: absent from the master by definition, so asking Zoho
    # about it by id is asking for the 404 the batch containment would then
    # spend calls isolating.
    session.add(models.Product(organization_id="org_a", external_id="ghost",
                               connector="zoho", connection_id="conn_sls",
                               name="", active=False,
                               source_ref={"provisional": True}))
    session.flush()

    repo = ReadModelRepository(session, "org_a", connector="zoho",
                               connection_id="conn_sls")
    assert set(repo.product_ids_by_external()) == {"sls-item"}

    # A repository with no source of its own keeps the organization-wide view —
    # scoping is a property of pulls, not of every reader.
    unscoped = ReadModelRepository(session, "org_a")
    assert set(unscoped.product_ids_by_external()) == {"sls-item", "4up-item"}


# ── assignments: each book maps its own salespeople ──────────────────────────

def _customer(org, conn, ext, owner):
    return models.Customer(organization_id=org, external_id=ext,
                           connector="zoho", connection_id=conn,
                           name=ext, source_owner_id=owner)


def test_assignments_stay_inside_the_book_that_named_the_salesperson(session):
    """Eight of ten UNMAPPED_SALESPERSON rows on a real run were other
    companies' salespeople, reported by whichever book ran last. A salesperson
    id means nothing outside the book that issued it."""
    session.add(_customer("org_a", "conn_sls", "c-sls", "sp-sls"))
    session.add(_customer("org_a", "conn_4up", "c-4up", "sp-4up"))
    session.flush()

    src = _Source(users=[{"user_id": "sp-4up", "email": "", "name": "Ravi"}])
    svc = SyncService(session, src, "org_a", connection_id="conn_4up")
    svc._sync_assignments()

    refs = [s["ref"] for s in svc.report.skipped
            if s["code"] == "UNMAPPED_SALESPERSON"]
    assert refs == ["sp-4up"], "the other book's salesperson is not ours to report"
    # And the report now says who, so somebody can act without an id lookup.
    detail = svc.report.skipped[0]["detail"]
    assert "Ravi" in detail


def test_a_salesperson_the_user_list_does_not_know_is_named_as_such(session):
    """'No email in Zoho' was the wrong diagnosis for an id the user list had
    never heard of — the fix lives in a different screen for the two cases."""
    session.add(_customer("org_a", "conn_4up", "c-4up", "sp-foreign"))
    session.flush()

    src = _Source(users=[{"user_id": "u1", "email": "someone@4up.in",
                          "name": "Someone"}])
    svc = SyncService(session, src, "org_a", connection_id="conn_4up")
    svc._sync_assignments()

    [skip] = [s for s in svc.report.skipped
              if s["code"] == "UNMAPPED_SALESPERSON"]
    assert "not among its users" in skip["detail"]
