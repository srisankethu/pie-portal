"""Every source's own fields, on every entity that can have them.

The defect this closes is a shape rather than a field. ``erp_quotes`` had a
JSON bag and was filled from a list of five key names, so a sixth custom field
configured on a Zoho quote was dropped here with nothing anywhere recording
that it had existed — and the other seven entities a source puts custom fields
on had nowhere to put them at all. What is pinned below is the replacement:

* a key nobody has heard of travels, which is the property the allowlist did
  not have;
* an empty read is ``None`` rather than ``{}``, because ``{}`` is a claim about
  the *source* that nothing downstream of a connector's projection can make;
* the bag is bounded, and where a bound bites the key still travels carrying a
  statement that its value was omitted — never a shortened value a reader would
  take for the whole one;
* the same input produces the same bytes;
* and ``trust.erasure`` reaches it, in both directions: the export carries it
  and the receipt names it among what key destruction does *not* unread.

Nothing here asserts an interpretation, because there is none. No rule,
threshold or computed number reads a key out of the bag.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.domain import models, schemas
from app.ingestion import normalize
from app.trust import erasure

ORG = "org_pie"

#: The eight (DTO, model) pairs this change covers, so a ninth entity added
#: later is added here rather than silently left out.
ENTITIES: tuple[tuple[type, type], ...] = (
    (schemas.CustomerIn, models.Customer),
    (schemas.ProductIn, models.Product),
    (schemas.VendorIn, models.Vendor),
    (schemas.SalesOrderIn, models.SalesOrderDoc),
    (schemas.PurchaseOrderIn, models.PurchaseOrderDoc),
    (schemas.InvoiceIn, models.InvoiceDoc),
    (schemas.BillIn, models.BillDoc),
    (schemas.QuoteDocIn, models.QuoteDoc),
)


@pytest.fixture()
def maker():
    engine = dbsupport.fresh_engine()
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


# ── the shape, on every entity ───────────────────────────────────────────────
@pytest.mark.parametrize("dto,model", ENTITIES,
                         ids=[m.__tablename__ for _, m in ENTITIES])
def test_every_entity_carries_the_source_s_own_fields(dto, model):
    field = dto.model_fields["source_attributes"]
    assert field.default is None, (
        f"{dto.__name__}.source_attributes must default to None, not to a bag: "
        "an empty bag asserts the source holds no custom fields")

    column = model.__table__.c["source_attributes"]
    assert column.nullable, f"{model.__tablename__}.source_attributes must be nullable"
    assert column.default is None and column.server_default is None, (
        f"{model.__tablename__}.source_attributes must have no default — a row "
        "written before it existed is one nobody looked at, not one with none")


def test_nothing_is_still_called_attributes():
    """One name for one concept. ``attributes`` is the decoded technical fact
    about a product (``product_attribute_values``); this is the ERP's own
    fields. The quote column carried the short name while it was the only one
    of its kind and gives it up here."""
    for _, model in ENTITIES:
        assert "attributes" not in model.__table__.c, (
            f"{model.__tablename__} still has an `attributes` column")


# ── what is carried ──────────────────────────────────────────────────────────
def test_a_custom_field_nobody_listed_travels():
    """The whole point. ``_QUOTE_ATTRIBUTE_KEYS`` named five keys and dropped
    the sixth silently; a field configured yesterday now travels without a code
    change."""
    quote = normalize.normalize_quote_document({
        "estimate_id": "e1", "date": "2026-05-01", "status": "sent",
        "cf_quote_type": "Tender",
        "cf_a_field_nobody_has_heard_of": "yes",
    })
    assert quote.source_attributes == {
        "cf_a_field_nobody_has_heard_of": "yes", "cf_quote_type": "Tender"}


def test_the_quote_s_branch_still_travels_beside_its_custom_fields():
    """A regression guard, not a new property. The branch is a standard source
    field with no typed column, and the screen has shown it since the bag
    existed."""
    quote = normalize.normalize_quote_document({
        "estimate_id": "e1", "date": "2026-05-01", "status": "sent",
        "branch_id": "b1", "branch_name": "Head Office",
    })
    assert quote.source_attributes == {"branch_id": "b1",
                                       "branch_name": "Head Office"}


def test_an_adapter_may_name_its_own_fields_in_an_envelope():
    """The connector-blind half. Only an adapter knows its ERP spells a custom
    field ``custentity…``, so it puts what it found under one agreed key rather
    than this module guessing at a prefix it has never seen."""
    customer = normalize.normalize_customer({
        "contact_id": "c1", "contact_name": "Acme Engineering",
        "source_attributes": {"Customer Segment": "PSU", "Territory": "South"},
    })
    assert customer.source_attributes == {"Customer Segment": "PSU",
                                          "Territory": "South"}


def test_an_envelope_that_is_not_a_mapping_is_carried_rather_than_ignored():
    """The envelope's contract is a mapping. An adapter that passes its ERP's
    own array through unflattened has broken it — and the answer is not to drop
    the payload, which is the defect shape this reader was rewritten to end.
    The value arrives whole, under the key it came on, where its author will
    see it."""
    customer = normalize.normalize_customer({
        "contact_id": "c1", "contact_name": "Acme",
        "source_attributes": [{"label": "Segment", "value": "PSU"}],
    })
    assert customer.source_attributes == {
        "source_attributes": [{"label": "Segment", "value": "PSU"}]}


def test_values_are_carried_as_the_source_wrote_them():
    """Verbatim means the source's own types, not a stringified copy. Nothing
    computes from these, so nothing needs them coerced — and a number turned
    into text here would be a value the source never wrote."""
    product = normalize.normalize_product({
        "item_id": "i1", "name": "CNMG 120408",
        "cf_shelf_life_days": 365, "cf_is_consumable": True,
        "cf_tags": ["turning", "insert"],
    })
    assert product.source_attributes == {
        "cf_is_consumable": True, "cf_shelf_life_days": 365,
        "cf_tags": ["turning", "insert"]}


@pytest.mark.parametrize("blank", [None, ""])
def test_a_field_nobody_set_stays_absent(blank):
    """"Not set" is not a category. A quote with no ``cf_quote_type`` is a
    quote nobody classified, which is a different fact from every unclassified
    quote sharing a bucket called "other"."""
    quote = normalize.normalize_quote_document({
        "estimate_id": "e1", "date": "2026-05-01", "status": "sent",
        "cf_quote_type": blank, "cf_pricing_type": "List",
    })
    assert quote.source_attributes == {"cf_pricing_type": "List"}


def test_a_field_set_to_zero_or_false_is_not_absent():
    """The sibling of the rule above, and the one it is easy to break: falsy is
    not blank. ``0`` is a value somebody entered."""
    vendor = normalize.normalize_vendor({
        "contact_id": "v1", "contact_name": "Kennametal India",
        "cf_credit_days": 0, "cf_on_hold": False,
    })
    assert vendor.source_attributes == {"cf_credit_days": 0, "cf_on_hold": False}


# ── the empty case is None, and that is a claim about what we know ───────────
@pytest.mark.parametrize("call,payload", [
    (normalize.normalize_customer, {"contact_id": "c1", "contact_name": "Acme"}),
    (normalize.normalize_product, {"item_id": "i1", "name": "CNMG 120408"}),
    (normalize.normalize_vendor, {"contact_id": "v1", "contact_name": "KMT"}),
    (normalize.normalize_sales_order,
     {"salesorder_id": "s1", "date": "2026-05-01"}),
    (normalize.normalize_purchase_order,
     {"purchaseorder_id": "p1", "date": "2026-05-01"}),
    (normalize.normalize_invoice_terms,
     {"invoice_id": "i1", "date": "2026-05-01"}),
    (normalize.normalize_bill_terms, {"bill_id": "b1", "date": "2026-05-01"}),
    (normalize.normalize_quote_document,
     {"estimate_id": "e1", "date": "2026-05-01", "status": "sent"}),
])
def test_a_record_with_no_source_fields_says_none_rather_than_empty(call, payload):
    """``{}`` would assert *this source holds no custom fields on this record*.
    Normalisation cannot know that: the payload arrives already projected by a
    connector, so an adapter that never read them is indistinguishable here
    from an ERP that has none. ``None`` says only what is true."""
    assert call(payload).source_attributes is None


# ── bounded, and never silently ──────────────────────────────────────────────
def test_an_oversized_value_is_described_rather_than_truncated():
    """A ceiling has to exist — a source value is bounded by nothing this
    platform controls, and these columns sit on the largest tables and travel
    whole in the trust export. It must not be a truncation: the key still
    travels, saying the field is set and that its value is not here, rather
    than a shortened value a reader would take for the whole one."""
    huge = "x" * (normalize._MAX_SOURCE_ATTRIBUTE_CHARS + 1)
    customer = normalize.normalize_customer({
        "contact_id": "c1", "contact_name": "Acme",
        "cf_notes": huge, "cf_segment": "PSU",
    })
    bag = customer.source_attributes
    assert bag is not None
    # The neighbour is untouched: one pathological field does not cost the rest.
    assert bag["cf_segment"] == "PSU"
    assert bag["cf_notes"]["omitted"]
    assert bag["cf_notes"]["chars"] > normalize._MAX_SOURCE_ATTRIBUTE_CHARS
    assert huge not in json.dumps(bag)


def test_a_value_the_column_could_not_store_is_described_rather_than_carried():
    """A value the JSON column cannot serialise would fail at commit, a long
    way from the record that caused it. It is described here instead — and
    described rather than dropped, because a silently missing key is the defect
    this whole reader exists to end."""
    bag = normalize._source_attributes({"cf_when": datetime(2026, 5, 1,
                                                            tzinfo=timezone.utc)})
    assert bag == {"cf_when": {"omitted": "value is not JSON-serialisable",
                               "type": "datetime"}}


# ── determinism ──────────────────────────────────────────────────────────────
def test_the_same_record_produces_the_same_bytes_whatever_order_it_arrives_in():
    """Identical input, identical bytes. An adapter that happens to build its
    dict in a different order must not make two runs of the same sync differ in
    the database."""
    fields = {"cf_zone": "West", "cf_alpha": "1", "branch_id": "b1",
              "cf_mid": "m", "branch_name": "HO"}
    base = {"estimate_id": "e1", "date": "2026-05-01", "status": "sent"}
    forward = normalize.normalize_quote_document({**base, **fields})
    reversed_ = normalize.normalize_quote_document(
        {**base, **dict(reversed(list(fields.items())))})
    assert (json.dumps(forward.source_attributes)
            == json.dumps(reversed_.source_attributes))
    assert list(forward.source_attributes) == sorted(fields)


# ── trust: the export reaches it, and the receipt names it ───────────────────
def test_the_trust_export_carries_the_bag_on_every_entity(maker):
    """An unbounded verbatim bag the tenant cannot take with them is an export
    that promises everything and hands back less. ``_rows`` is column-blind, so
    this is really a check that all eight tables are in ``EXPORTED`` — which is
    the half a new column can silently miss."""
    s = maker()
    s.add(models.Organization(organization_id=ORG, name="PIE"))
    s.add(models.Customer(organization_id=ORG, customer_id="c1", external_id="1",
                          name="Acme", source_attributes={"cf_segment": "PSU"}))
    s.add(models.Product(organization_id=ORG, product_id="p1", external_id="2",
                         name="CNMG", source_attributes={"cf_bin": "A1"}))
    s.add(models.Vendor(organization_id=ORG, vendor_id="v1", external_id="3",
                        name="KMT", source_attributes={"cf_msme": "Y"}))
    s.add(models.SalesOrderDoc(organization_id=ORG, external_ref="so1",
                               date=date(2026, 5, 1),
                               source_attributes={"cf_project": "P-9"}))
    s.add(models.PurchaseOrderDoc(organization_id=ORG, external_ref="po1",
                                  date=date(2026, 5, 1),
                                  source_attributes={"cf_buyer": "RN"}))
    s.add(models.InvoiceDoc(organization_id=ORG, external_ref="inv1",
                            date=date(2026, 5, 1),
                            source_attributes={"cf_eway": "123"}))
    s.add(models.BillDoc(organization_id=ORG, external_ref="b1",
                         date=date(2026, 5, 1),
                         source_attributes={"cf_grn": "G-7"}))
    s.add(models.QuoteDoc(organization_id=ORG, external_ref="q1",
                          date=date(2026, 5, 1), total=Decimal("100"),
                          source_attributes={"cf_quote_type": "Tender"}))
    s.commit()

    data = erasure.export(s, ORG)["data"]
    s.close()

    for _, model in ENTITIES:
        rows = data[model.__tablename__]
        assert len(rows) == 1, model.__tablename__
        assert rows[0]["source_attributes"], (
            f"{model.__tablename__}.source_attributes did not reach the export")


def test_the_erasure_receipt_says_the_bag_survives_in_plaintext():
    """Key destruction does not reach it — these columns were never encrypted.
    A custom field can hold a person's name, so a receipt that did not name it
    would overstate what the erasure destroyed, which is the one thing a signed
    document must not do."""
    named = [e for e in erasure.SURVIVES_PLAINTEXT
             if e["column"] == "source_attributes"]
    assert len(named) == 1
    for _, model in ENTITIES:
        assert model.__tablename__ in named[0]["table"]
