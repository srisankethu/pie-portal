"""The pipe that fills the source-attribute columns, end to end.

``test_source_attributes.py`` pins the *shape* — that every entity has the
column, that the empty case is NULL, that a value is carried verbatim and
bounded, that ``trust`` reaches it. It proves none of that is reachable from a
real pull, and for a while none of it was: the columns existed, no repository
upsert wrote one, and ``zoho_client`` narrowed each record to a hand-listed
projection before ``normalize`` ever saw it. A column nothing fills is a column
nobody can tell from an ERP that has no custom fields.

So what is pinned here is the wiring, at each of the three seams and then
through all of them at once:

* the **client** reads both shapes Zoho publishes and produces the same bag
  from either, keyed on the api name;
* the **repositories** write it on all eight entities;
* and a real ``ZohoApiSource`` payload reaches a persisted row.

Nothing here asserts an interpretation, because there is none — no rule,
threshold or number reads a key out of the bag.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from app.domain import models, schemas
from app.domain.enums import CustomerStatus
from app.ingestion import normalize
from app.ingestion.sync import SyncService
from app.ingestion.zoho_client import (ZohoApiSource, ZohoCredentials,
                                       source_attributes)
from app.repositories import ReadModelRepository

ORG = "org_a"


# ── the client: two wire shapes, one bag ─────────────────────────────────────
#
# Both payloads below are the live SLS Engineers book (2026-09-18), trimmed to
# the custom fields. Which shape a record arrives in is a property of the
# endpoint: a list row flattens, a detail record does not flatten at all.

#: ``/bills`` — the list row. Flattened, with both derived twins.
BILL_LIST_ROW: dict[str, Any] = {
    "bill_id": "3452161000010581042",
    "cf_vendor_internal_doc_reference": "163",
    "cf_vendor_internal_doc_reference_formatted": "163",
    "cf_vendor_internal_doc_reference_unformatted": "163",
    "cf_vendor_internal_doc_date": "17/09/2026",
    "cf_vendor_internal_doc_date_formatted": "17/09/2026",
    "cf_vendor_internal_doc_date_unformatted": "2026-09-17",
}

#: ``/bills/{id}`` — the detail record for the *same bill*. No flat key at all:
#: the array and the hash are the only two places the fields appear.
BILL_DETAIL: dict[str, Any] = {
    "bill_id": "3452161000010581042",
    "custom_fields": [
        {"customfield_id": "3452161000003518158", "index": 1,
         "label": "Vendor Internal Doc Reference #",
         "api_name": "cf_vendor_internal_doc_reference",
         "data_type": "string", "value": "163", "value_formatted": "163"},
        {"customfield_id": "3452161000003518162", "index": 2,
         "label": "Vendor Internal Doc Date",
         "api_name": "cf_vendor_internal_doc_date",
         "data_type": "date", "value": "2026-09-17",
         "value_formatted": "17/09/2026"},
    ],
    "custom_field_hash": {
        "cf_vendor_internal_doc_reference": "163",
        "cf_vendor_internal_doc_reference_formatted": "163",
        "cf_vendor_internal_doc_reference_unformatted": "163",
        "cf_vendor_internal_doc_date": "17/09/2026",
        "cf_vendor_internal_doc_date_formatted": "17/09/2026",
        "cf_vendor_internal_doc_date_unformatted": "2026-09-17",
    },
}


def test_the_two_shapes_zoho_publishes_produce_the_same_bag():
    """The property the whole design turns on. One bill, read through its list
    row and through its detail record, is one bag — so a document fetched by
    one pull and resumed by another cannot store two different things."""
    assert source_attributes(BILL_LIST_ROW) == source_attributes(BILL_DETAIL)
    assert json.dumps(source_attributes(BILL_LIST_ROW), sort_keys=True) == \
        json.dumps(source_attributes(BILL_DETAIL), sort_keys=True)


def test_a_detail_record_carries_no_flat_key_and_is_read_anyway():
    """Measured, not assumed: ``/bills/{id}``, ``/estimates/{id}``,
    ``/items/{id}`` and ``/contacts`` all answer with the array and the hash
    and no ``cf_`` key on the record. A reader that only knew the flattened
    spelling would find nothing on any of them."""
    assert not [k for k in BILL_DETAIL if k.startswith("cf_")]
    assert source_attributes(BILL_DETAIL) == {
        "cf_vendor_internal_doc_reference": "163",
        "cf_vendor_internal_doc_date": "2026-09-17"}


def test_the_key_is_the_api_name_and_not_the_label():
    """A label is a caption an administrator renames from a settings screen —
    "Vendor Internal Doc Reference #" today, something else next quarter — and
    a key that moves when somebody edits a caption is not a key. The api name
    is also what Zoho itself uses for the flattened spelling, which is what
    lets the two shapes above agree."""
    bag = source_attributes(BILL_DETAIL)
    assert set(bag) == {"cf_vendor_internal_doc_reference",
                        "cf_vendor_internal_doc_date"}
    assert "Vendor Internal Doc Date" not in bag


def test_a_field_is_carried_once_and_holds_the_value_not_the_rendering():
    """Zoho publishes one configured field in up to three spellings. They are
    identical on a dropdown and differ on a date: ``17/09/2026`` is this book's
    locale rendering of ``2026-09-17``, the same class of field as
    ``total_formatted``, which no projection here has ever carried. The value
    is kept, so what is stored is what somebody entered."""
    bag = source_attributes(BILL_LIST_ROW)
    assert bag["cf_vendor_internal_doc_date"] == "2026-09-17"
    assert not [k for k in bag if k.endswith(("_formatted", "_unformatted"))]


def test_a_field_genuinely_named_like_a_twin_still_travels():
    """The twin rule is about a *pair*, not about a suffix. A field whose api
    name happens to end in ``_formatted`` with no base key beside it is
    somebody's real field and is carried as itself — the alternative is a
    reader that silently drops a field because of how it was spelled."""
    assert source_attributes({"cf_notes_formatted": "a real field"}) == {
        "cf_notes_formatted": "a real field"}


def test_a_blank_is_left_for_the_normalizer_to_drop():
    """"Absent stays absent" is one rule and ``normalize._source_attributes``
    owns it. This function translates a wire shape and decides nothing else —
    two owners of one rule is how they start disagreeing."""
    assert source_attributes({"cf_unset": "", "cf_set": "x"}) == {
        "cf_unset": "", "cf_set": "x"}
    assert normalize._source_attributes(
        {"source_attributes": source_attributes({"cf_unset": "", "cf_set": "x"})}
    ) == {"cf_set": "x"}


# ── the client: every projection carries it ──────────────────────────────────
class _FakeResponse:
    def __init__(self, body):
        self._body, self.status_code, self.headers = body, 200, {}

    def json(self):
        return self._body


class _FakeHttp:
    """Only the transport. Everything above it is the shipping code."""

    def __init__(self, routes):
        self.routes = routes

    def post(self, url, **kw):
        return _FakeResponse({"access_token": "tok", "expires_in": 3600})

    def get(self, url, params=None, **kw):
        for fragment, body in self.routes.items():
            if fragment in url:
                # A callable route reads the query string, which is how
                # ``/contacts`` can answer differently for `contact_type`
                # customer and vendor — the one endpoint this pull asks two
                # questions of.
                return _FakeResponse(body(dict(params or {}))
                                     if callable(body) else body)
        return _FakeResponse({"code": 0, "page_context": {"has_more_page": False}})


def _source(routes):
    return ZohoApiSource(http=_FakeHttp(routes), credentials=ZohoCredentials(
        organization_id="org-x", client_id="c", client_secret="s",
        refresh_token="r"))


_PAGE = {"page_context": {"has_more_page": False}}


def test_the_item_taxonomy_survives_the_by_id_fetch_after_all():
    """A defect this change removes rather than adds. ``_item_payload`` read
    ``cf_item_type`` off the record, which is right for the master list and
    reads ``None`` for every item ``get_item`` answers — so the master-read
    race that function exists to repair wrote a product with no taxonomy on it,
    and the test that promised the two paths agree passed because it fed the
    by-id route a *list-shaped* row."""
    detail = {"item_id": "7", "name": "x", "status": "active",
              "custom_fields": [
                  {"api_name": "cf_item_type", "label": "Item Type",
                   "value": "Insert", "value_formatted": "Insert"},
                  {"api_name": "cf_item_category", "label": "Item Category",
                   "value": "Turning", "value_formatted": "Turning"}],
              "custom_field_hash": {"cf_item_type": "Insert",
                                    "cf_item_type_unformatted": "Insert",
                                    "cf_item_category": "Turning",
                                    "cf_item_category_unformatted": "Turning"}}
    listed = {"item_id": "7", "name": "x", "status": "active",
              "cf_item_type": "Insert", "cf_item_type_formatted": "Insert",
              "cf_item_type_unformatted": "Insert",
              "cf_item_category": "Turning",
              "cf_item_category_formatted": "Turning",
              "cf_item_category_unformatted": "Turning"}
    src = _source({"/items/7": {"code": 0, "item": detail},
                   "/items": {"code": 0, "items": [listed], **_PAGE}})

    fetched = src.get_item("7")
    assert fetched["source_item_type"] == "Insert"
    assert fetched["source_item_category"] == "Turning"
    # The promise `_item_payload` exists to keep, now made against the shapes
    # the two endpoints really answer with rather than against one shape twice.
    assert fetched == list(src.list_items())[0]


def test_the_customer_lookup_on_an_item_is_still_not_copied_onto_the_product():
    """The one item field that does not travel, and the reason it is named
    rather than filtered by shape: ``cf_end_customer`` is a lookup onto a
    customer, and the list row most items arrive on carries no ``data_type`` to
    filter on. Everything else on the item travels, including a field
    configured tomorrow — the bag is not an allowlist with one entry missing,
    it is everything minus one documented exclusion."""
    row = {"item_id": "7", "name": "x", "status": "active",
           "cf_end_customer": "Some Customer Pvt Ltd",
           "cf_end_customer_unformatted": "Some Customer Pvt Ltd",
           "cf_bin_location": "A-14"}
    payload = list(_source({"/items": {"code": 0, "items": [row], **_PAGE}}
                           ).list_items())[0]

    assert "Some Customer" not in json.dumps(payload)
    assert payload["source_attributes"] == {"cf_bin_location": "A-14"}


def test_a_quote_field_nobody_listed_reaches_the_projection():
    """Three keys were hand-listed on this projection and a fourth configured
    on the book would have been dropped here with nothing recording that it had
    existed. That is the defect this whole line of work exists to end, and it
    lived one layer above the one the bag was added to."""
    row = {"estimate_id": "e1", "date": date.today().isoformat(),
           "status": "sent", "branch_id": "b1", "branch_name": "Head Office",
           "cf_quote_type": "REGULAR", "cf_quote_type_unformatted": "REGULAR",
           "cf_tender_reference": "GEM/2026/B/1234"}
    quote = list(_source({"/estimates": {"code": 0, "estimates": [row],
                                         **_PAGE}}).list_quotes())[0]

    assert quote["source_attributes"] == {
        "cf_quote_type": "REGULAR", "cf_tender_reference": "GEM/2026/B/1234"}
    # The branch is a standard source field with no typed column and is carried
    # by `normalize` under `also=`, not by the bag. It must keep reaching the
    # ERP quote screen.
    assert (quote["branch_id"], quote["branch_name"]) == ("b1", "Head Office")


@pytest.mark.parametrize("listing,records,key,call", [
    ("/contacts", [{"contact_id": "c1", "contact_name": "Acme",
                    "status": "active", "contact_type": "customer",
                    "custom_fields": [{"api_name": "cf_segment",
                                       "label": "Segment", "value": "PSU"}]}],
     "cf_segment", "list_contacts"),
    ("/contacts", [{"contact_id": "v1", "contact_name": "KMT",
                    "status": "active", "contact_type": "vendor",
                    "custom_fields": [{"api_name": "cf_msme",
                                       "label": "MSME", "value": "Y"}]}],
     "cf_msme", "list_vendors"),
    ("/salesorders", [{"salesorder_id": "s1", "date": date.today().isoformat(),
                       "status": "open", "cf_order_mode": "VERBAL",
                       "cf_order_mode_unformatted": "VERBAL"}],
     "cf_order_mode", "list_sales_orders"),
    ("/purchaseorders", [{"purchaseorder_id": "p1",
                          "date": date.today().isoformat(), "status": "open",
                          "cf_order_type": "As per Customer PO",
                          "cf_order_type_unformatted": "As per Customer PO"}],
     "cf_order_type", "list_purchase_orders"),
])
def test_every_list_projection_carries_the_source_s_own_fields(
        listing, records, key, call):
    """One parametrised pass over the projections a pull reads from a list
    call. A projection that forgot the bag is a column nothing ever fills, and
    it looks exactly like an ERP with no custom fields configured."""
    rows = list(getattr(_source({listing: {"code": 0,
                                           listing.strip("/"): records,
                                           **_PAGE}}), call)())
    assert rows and key in rows[0]["source_attributes"]


@pytest.mark.parametrize("path,plural,singular,record,key", [
    ("/invoices", "invoices", "invoice",
     {"invoice_id": "i1", "customer_id": "c1", "status": "paid",
      "custom_fields": [{"api_name": "cf_eway_bill", "label": "E-Way Bill",
                         "value": "123456789012"}]},
     "cf_eway_bill"),
    ("/bills", "bills", "bill",
     {"bill_id": "b1", "vendor_id": "v1", "status": "open",
      "custom_fields": [{"api_name": "cf_bill_entry_no",
                         "label": "Bill Entry No", "value": "163"}]},
     "cf_bill_entry_no"),
])
def test_every_detail_projection_carries_the_source_s_own_fields(
        path, plural, singular, record, key):
    """The two documents whose projection reads the *detail* payload, which is
    the shape with no flattened key on it. These are the ones a reader who
    assumed ``cf_`` keys would silently get nothing from."""
    record = {**record, "date": date.today().isoformat()}
    rows = list(getattr(_source({
        f"{path}/{record.get('invoice_id') or record.get('bill_id')}":
            {"code": 0, singular: record},
        path: {"code": 0, plural: [record], **_PAGE}}),
        f"list_{plural}")())
    assert rows and key in rows[0]["source_attributes"]


# ── the repositories: all eight write the column ─────────────────────────────
def _ref(record_type: str) -> schemas.SourceRef:
    return schemas.SourceRef(system="zoho", record_type=record_type,
                             record_id="x1")


#: One (upsert call, DTO, model) per entity, so a ninth is added here rather
#: than silently left unwritten.
def _upserts(bag):
    when = date(2026, 5, 1)
    return (
        ("upsert_customer", (schemas.CustomerIn(
            external_id="c1", name="Acme", status=CustomerStatus.ACTIVE,
            source_attributes=bag, source_ref=_ref("contact")),),
         models.Customer),
        ("upsert_product", (schemas.ProductIn(
            external_id="p1", name="CNMG 120408", source_attributes=bag,
            source_ref=_ref("item")),), models.Product),
        ("upsert_vendor", (schemas.VendorIn(
            external_id="v1", name="Kennametal India",
            status=CustomerStatus.ACTIVE, source_attributes=bag,
            source_ref=_ref("vendor")),), models.Vendor),
        ("upsert_sales_order", (None, schemas.SalesOrderIn(
            external_ref="so1", date=when, status="open",
            source_attributes=bag, source_ref=_ref("salesorder"))),
         models.SalesOrderDoc),
        ("upsert_purchase_order", (None, schemas.PurchaseOrderIn(
            external_ref="po1", date=when, status="open",
            source_attributes=bag, source_ref=_ref("purchaseorder"))),
         models.PurchaseOrderDoc),
        ("upsert_invoice", (None, schemas.InvoiceIn(
            external_ref="inv1", date=when, status="paid",
            source_attributes=bag, source_ref=_ref("invoice"))),
         models.InvoiceDoc),
        ("upsert_bill", (None, schemas.BillIn(
            external_ref="b1", date=when, status="open",
            source_attributes=bag, source_ref=_ref("bill"))),
         models.BillDoc),
        ("upsert_quote_document", (None, schemas.QuoteDocIn(
            external_ref="q1", date=when, source_status="sent",
            source_attributes=bag, source_ref=_ref("quote"))),
         models.QuoteDoc),
    )


_BAG = {"cf_segment": "PSU", "cf_credit_days": 0}


@pytest.mark.parametrize(
    "method,args,model", _upserts(_BAG),
    ids=[m.__tablename__ for _, _, m in _upserts(_BAG)])
def test_every_upsert_writes_the_source_s_own_fields(session, method, args,
                                                     model):
    """The half that was missing. Seven of these eight never assigned the
    column: the DTO carried the bag all the way to the repository and the
    repository dropped it on the floor, so the feature was eight columns
    nothing filled."""
    repo = ReadModelRepository(session, ORG)
    getattr(repo, method)(*args)
    session.commit()

    row = session.query(model).one()
    assert row.source_attributes == _BAG, model.__tablename__
    # A copy, not the DTO's own dict — a later mutation must not reach a row
    # that has already been written.
    assert row.source_attributes is not args[-1].source_attributes


@pytest.mark.parametrize(
    "method,args,model", _upserts(None),
    ids=[m.__tablename__ for _, _, m in _upserts(None)])
def test_an_entity_with_no_source_fields_is_written_null(session, method, args,
                                                         model):
    """NULL and ``{}`` are different claims and only one is available here.
    ``{}`` would assert *the source holds no custom fields on this record* —
    which nothing this side of a connector's projection can know."""
    repo = ReadModelRepository(session, ORG)
    getattr(repo, method)(*args)
    session.commit()

    assert session.query(model).one().source_attributes is None


def test_a_record_that_loses_its_custom_fields_has_the_column_cleared(session):
    """Rewritten from the payload every pull, like every other column on these
    rows. A field deleted in the ERP must stop being on the row here, or the
    mirror disagrees with the source system it mirrors."""
    repo = ReadModelRepository(session, ORG)
    repo.upsert_customer(schemas.CustomerIn(
        external_id="c1", name="Acme", source_attributes={"cf_segment": "PSU"},
        source_ref=_ref("contact")))
    session.commit()
    repo.upsert_customer(schemas.CustomerIn(
        external_id="c1", name="Acme", source_ref=_ref("contact")))
    session.commit()

    assert session.query(models.Customer).one().source_attributes is None


# ── all three seams at once ──────────────────────────────────────────────────
def test_a_custom_field_travels_from_the_zoho_wire_to_a_persisted_row(session):
    """Client projection -> normalize -> SyncService -> a column, on the real
    code path and with no fake source in it.

    The seam test in ``test_sync_persistence`` makes the same argument about
    ``created_time``: a unit test at each boundary is blind to a field the real
    client never projects, which is exactly the state this change found the
    columns in.

    All eight entities, because a projection that forgot the bag is invisible
    from anywhere else — and the payloads are the shapes the live SLS Engineers
    book answers with, so each of the three spellings Zoho uses is exercised by
    the entities that really arrive in it: the contact and the vendor through
    the array only, the item and the two orders and the quote through the
    flattened list row, the bill and the invoice through the detail call.
    """
    today = date.today().isoformat()
    contact = {"contact_id": "c1", "contact_name": "Pitti Engineering",
               "status": "active",
               "custom_fields": [{"api_name": "cf_customer_segment",
                                  "label": "Customer Segment",
                                  "value": "OEM", "value_formatted": "OEM"}],
               "custom_field_hash": {"cf_customer_segment": "OEM",
                                     "cf_customer_segment_unformatted": "OEM"}}
    item = {"item_id": "i1", "name": "CNMG120408", "unit": "pcs",
            "status": "active", "cf_item_type": "Insert",
            "cf_item_type_formatted": "Insert",
            "cf_item_type_unformatted": "Insert",
            "cf_bin_location": "A-14", "cf_bin_location_unformatted": "A-14"}
    bill = {"bill_id": "b1", "bill_number": "163", "vendor_id": "v1",
            "date": today, "status": "open", "total": 38822, "balance": 38822,
            "line_items": [{"line_item_id": "L1", "item_id": "i1",
                            "quantity": 1, "rate": 32900,
                            "item_total": 32900}],
            "custom_fields": [
                {"api_name": "cf_vendor_internal_doc_date",
                 "label": "Vendor Internal Doc Date", "data_type": "date",
                 "value": "2026-09-17", "value_formatted": "17/09/2026"}],
            "custom_field_hash": {
                "cf_vendor_internal_doc_date": "17/09/2026",
                "cf_vendor_internal_doc_date_formatted": "17/09/2026",
                "cf_vendor_internal_doc_date_unformatted": "2026-09-17"}}
    estimate = {"estimate_id": "e1", "estimate_number": "SLS/QTN-334",
                "customer_id": "c1", "date": today, "status": "sent",
                "total": 21837, "branch_id": "br1",
                "branch_name": "Head Office",
                "cf_quote_type": "REGULAR",
                "cf_quote_type_formatted": "REGULAR",
                "cf_quote_type_unformatted": "REGULAR",
                "cf_tender_reference": "GEM/2026/B/1234"}
    vendor = {"contact_id": "v1", "contact_name": "SMART COMPUTERS",
              "contact_type": "vendor", "status": "active",
              "custom_fields": [{"api_name": "cf_msme_registered",
                                 "label": "MSME Registered", "value": "Yes"}]}
    invoice = {"invoice_id": "inv1", "invoice_number": "118/25-26",
               "customer_id": "c1", "date": today, "status": "paid",
               "total": 44560, "balance": 0,
               "line_items": [{"line_item_id": "L1", "item_id": "i1",
                               "quantity": 80, "rate": 557,
                               "item_total": 44560}],
               "custom_fields": [{"api_name": "cf_eway_bill",
                                  "label": "E-Way Bill",
                                  "value": "123456789012"}]}
    sales_order = {"salesorder_id": "so1", "salesorder_number": "SO-594",
                   "customer_id": "c1", "date": today, "status": "open",
                   "cf_order_mode": "VERBAL",
                   "cf_order_mode_formatted": "VERBAL",
                   "cf_order_mode_unformatted": "VERBAL"}
    purchase_order = {"purchaseorder_id": "po1",
                      "purchaseorder_number": "PO-344", "vendor_id": "v1",
                      "date": today, "status": "open",
                      "cf_order_type": "As per Customer PO",
                      "cf_order_type_formatted": "As per Customer PO",
                      "cf_order_type_unformatted": "As per Customer PO"}

    def contacts(params):
        wanted = params.get("contact_type")
        rows = [c for c in (contact, vendor)
                if c.get("contact_type", "customer") == wanted]
        return {"code": 0, "contacts": rows, **_PAGE}

    src = _source({
        "/contacts": contacts,
        "/items": {"code": 0, "items": [item], **_PAGE},
        "/bills/b1": {"code": 0, "bill": bill},
        "/bills": {"code": 0, "bills": [bill], **_PAGE},
        "/estimates/e1": {"code": 0, "estimate": {"estimate_id": "e1",
                                                  "line_items": []}},
        "/estimates": {"code": 0, "estimates": [estimate], **_PAGE},
        "/invoices/inv1": {"code": 0, "invoice": invoice},
        "/invoices": {"code": 0, "invoices": [invoice], **_PAGE},
        "/salesorders": {"code": 0, "salesorders": [sales_order], **_PAGE},
        "/purchaseorders": {"code": 0, "purchaseorders": [purchase_order],
                            **_PAGE},
    })
    SyncService(session, src, ORG).run()
    session.commit()

    # The contact: read out of the `custom_fields` array, which is the only
    # place a contact's fields ever appear.
    assert session.query(models.Customer).one().source_attributes == {
        "cf_customer_segment": "OEM"}
    # The vendor: the same endpoint, the other contact_type.
    assert session.query(models.Vendor).one().source_attributes == {
        "cf_msme_registered": "Yes"}
    # The item: flattened on the list row, twins folded, and the field the
    # platform promoted to a typed column is still in the bag as well.
    product = session.query(models.Product).one()
    assert product.source_attributes == {"cf_item_type": "Insert",
                                         "cf_bin_location": "A-14"}
    assert product.source_item_type == "Insert"
    # The bill: off the detail payload, and holding the value rather than this
    # book's rendering of it.
    assert session.query(models.BillDoc).one().source_attributes == {
        "cf_vendor_internal_doc_date": "2026-09-17"}
    # The quote: the three formerly hand-listed keys and the one that would
    # have been dropped, plus the branch `normalize` carries beside them.
    assert session.query(models.QuoteDoc).one().source_attributes == {
        "branch_id": "br1", "branch_name": "Head Office",
        "cf_quote_type": "REGULAR",
        "cf_tender_reference": "GEM/2026/B/1234"}
    # The receivable header, off its detail payload.
    assert session.query(models.InvoiceDoc).one().source_attributes == {
        "cf_eway_bill": "123456789012"}
    # The two header-grain orders, off their list rows — neither buys a detail
    # call, so the flattened spelling is the only one they ever see.
    assert session.query(models.SalesOrderDoc).one().source_attributes == {
        "cf_order_mode": "VERBAL"}
    assert session.query(models.PurchaseOrderDoc).one().source_attributes == {
        "cf_order_type": "As per Customer PO"}


def test_two_syncs_of_one_book_store_identical_bytes(session):
    """Determinism, over the whole pipe rather than over the normalizer alone.
    ``_source_attributes`` sorts its keys; a client that built the envelope
    from an unordered read would undo that, and a re-sync would rewrite every
    row with the same facts in a different order."""
    today = date.today().isoformat()
    estimate = {"estimate_id": "e1", "customer_id": "c1", "date": today,
                "status": "sent", "cf_zone": "West", "cf_alpha": "1",
                "branch_id": "b1", "cf_mid": "m", "branch_name": "HO"}
    routes = {
        "/estimates/e1": {"code": 0, "estimate": {"estimate_id": "e1",
                                                  "line_items": []}},
        "/estimates": {"code": 0, "estimates": [estimate], **_PAGE},
        "/contacts": lambda params: {
            "code": 0, **_PAGE,
            "contacts": ([{"contact_id": "c1", "contact_name": "Acme",
                           "status": "active"}]
                         if params.get("contact_type") == "customer" else [])},
    }
    SyncService(session, _source(routes), ORG).run()
    session.commit()
    first = json.dumps(session.query(models.QuoteDoc).one().source_attributes)

    reversed_row = dict(reversed(list(estimate.items())))
    routes["/estimates"] = {"code": 0, "estimates": [reversed_row], **_PAGE}
    SyncService(session, _source(routes), ORG).run()
    session.commit()
    session.expire_all()

    assert json.dumps(
        session.query(models.QuoteDoc).one().source_attributes) == first
    assert list(json.loads(first)) == sorted(json.loads(first))
