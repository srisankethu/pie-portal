"""Every connector says, on the record, whether its system records its own
write time — and the ones that say yes carry it.

The incident this exists for: PIE's own Zoho client dropped ``created_time``
from all three document projections. Every row landed with
``source_recorded_at = NULL``, every line of every quote answered
INSUFFICIENT_EVIDENCE, and the sync reported success the whole time with every
number it did write correct. A connector written by somebody who has not read
``normalize._recorded_at`` makes that mistake by default, and nothing about the
result looks wrong.

So the declaration is the thing being tested, in three parts:

* It **cannot be omitted.** ``records_source_time`` and ``source_time_note``
  sit among ``ConnectorSpec``'s fields that have no default, so a connector
  module that forgets them fails at import rather than registering a spec that
  answers nothing. A default would have made silence a permitted answer, which
  is the state the whole mechanism exists to forbid.
* It **cannot be empty.** A ``False`` with a blank note tells the person
  holding the gap nothing at all — least of all whether the gap is the ERP's or
  ours, which is the only question the note is for.
* A ``True`` **is held to its word.** The three date clocks are distinct and
  only one of them answers "could the business have known this yet": the
  document's own date is when the commercial fact happened, the row's
  ``created_at`` is when this platform synced it, and neither substitutes. A
  connector that declares it has the third one and then hands over its document
  date has produced exactly the wrong answer confidently.

Deliberately *not* asserted here: that the carried stamp survives
``clock.utc_stamp``. Three of these systems state a creation stamp with no zone
on it — NetSuite in PST, X3 and Sage 100 as a bare date with the time in a
sibling column — so ``normalize._recorded_at`` drops them. That is a real gap
and each spec's note says so in its own words; it is not something a translator
can fix by inventing an offset, and a test that demanded one would be asking
for the guess.
"""
from __future__ import annotations

import pytest

from app.ingestion import erp
from app.ingestion.erp import acumatica, netsuite, prophet21, sage
from app.ingestion.erp.base import ConnectorSpec


# ── the declaration ─────────────────────────────────────────────────────────
def test_every_registered_connector_answers_the_question():
    """Both fields, on every spec, with no third state.

    ``bool`` rather than truthiness: a string note accidentally assigned to the
    flag would pass ``if spec.records_source_time`` and mean nothing.
    """
    catalog = erp.catalog()
    assert catalog, "an empty registry would let this whole file pass vacuously"
    for spec in catalog:
        assert isinstance(spec.records_source_time, bool), (
            f"{spec.key}: records_source_time must be True or False, not "
            f"{spec.records_source_time!r} — there is no third state")
        assert isinstance(spec.source_time_note, str), spec.key


def test_no_connector_can_leave_the_note_blank():
    """A declaration nobody can read is the documentation this replaces.

    Held on the live registry *and* on a fresh construction, because the two
    fail at different moments: the first catches a spec somebody edited down to
    an empty string, the second catches the next connector author.
    """
    for spec in erp.catalog():
        assert spec.source_time_note.strip(), (
            f"{spec.key}: source_time_note is empty — name the field this "
            f"system records its own write time in, or say why it has none")
        # Long enough to be a reason rather than a shrug. "No" and "n/a" are
        # the two notes this is here to refuse.
        assert len(spec.source_time_note.strip()) > 40, (
            f"{spec.key}: {spec.source_time_note!r} does not tell a reader "
            f"whether the gap is the ERP's or ours")

    with pytest.raises(ValueError, match="source_time_note cannot be empty"):
        _spec(source_time_note="   ")


def test_a_connector_that_forgets_to_declare_cannot_be_built_at_all():
    """Absence is not a permitted answer, and the type system says so.

    The point of putting these among the fields with no default: a connector
    module that never mentions them does not register a spec answering
    "unknown" — it fails to import. If this test ever starts failing because a
    default was added, the default is the defect.
    """
    with pytest.raises(TypeError) as missing:
        ConnectorSpec(
            key="forgetful", label="Forgetful ERP", company_term="company",
            credential_fields=(), connection_fields=(),
            external_id_field="company_id", setup_note="…",
            build_source=lambda *a, **k: None)
    assert "records_source_time" in str(missing.value)


def test_a_true_declaration_names_the_field_it_comes_from():
    """A person checking the claim against the vendor's docs needs the name.

    Not a style rule. ``True`` is the half of this declaration that obliges the
    connector, and a note that says "yes, it has one" cannot be checked by
    anybody — which makes it the same as no declaration with more words.
    """
    for spec in (s for s in erp.catalog() if s.records_source_time):
        assert "``" in spec.source_time_note, (
            f"{spec.key}: a True declaration must name the native field, so "
            f"the claim can be checked against that vendor's documentation")


# ── the connectors that said yes ────────────────────────────────────────────
# One payload each, in that system's own dress, asserting the two things that
# went wrong in the Zoho incident: the stamp is carried, and it is the system's
# stamp rather than the document's own date wearing its name.

def test_netsuite_carries_createddate_and_not_trandate():
    payload = netsuite.translate_document(
        {"id": "41", "tranid": "INV-41", "entity": "9",
         "trandate": "2026-01-04", "duedate": "2026-02-03", "status": "Open",
         "foreigntotal": "1200.00", "currency_code": "usd",
         "lastmodified": "2026-03-01T09:00:00",
         "createdtime": "2026-01-29T14:11:02"},
        [{"line_id": "1", "item": "7", "quantity": "2", "rate": "600.00",
          "netamount": "-1200.00"}],
        kind="invoice")
    assert payload["created_time"] == "2026-01-29T14:11:02"
    assert payload["created_time"] != payload["date"]


def test_a_netsuite_document_with_no_created_stamp_says_so():
    """Absent stays absent. ``None`` costs the row its place in the evidence
    and is counted; a document date silently promoted into this key would make
    a quote answerable from a bill nobody had yet seen."""
    payload = netsuite.translate_document(
        {"id": "41", "trandate": "2026-01-04", "status": "Open"}, [],
        kind="invoice")
    assert payload["created_time"] is None


def test_netsuite_asks_its_query_for_the_column():
    """The one connector whose translator could not have been fixed alone.

    NetSuite names its columns in the SELECT, so a translator reading
    ``createdtime`` off a row nobody asked for it on would carry ``None``
    forever and look correct doing it.
    """
    assert "createddate" in netsuite.NetSuiteSource._HEADER_COLUMNS
    assert "AS createdtime" in netsuite.NetSuiteSource._HEADER_COLUMNS


def test_acumatica_carries_createddatetime_through_the_value_dress():
    payload = acumatica.translate_invoice(acumatica._plain({
        "ReferenceNbr": {"value": "AR004121"},
        "CustomerID": {"value": "ACME"},
        "Date": {"value": "2026-01-04T00:00:00+00:00"},
        "Status": {"value": "Open"},
        "Amount": {"value": "1200.00"},
        "LastModifiedDateTime": {"value": "2026-03-01T09:00:00+00:00"},
        "CreatedDateTime": {"value": "2026-01-29T14:11:02+00:00"},
        "Details": [],
    }))
    assert payload["created_time"] == "2026-01-29T14:11:02+00:00"
    assert payload["created_time"] != payload["date"]


def test_acumatica_carries_it_on_a_bill_too():
    """Bills are the cost half of every margin on this platform, and a cost
    row with no recorded time is the one that silently stops being evidence."""
    payload = acumatica.translate_bill(acumatica._plain({
        "ReferenceNbr": {"value": "AP000901"},
        "Vendor": {"value": "KENNA"},
        "Date": {"value": "2026-01-02T00:00:00+00:00"},
        "Status": {"value": "Open"},
        "CreatedDateTime": {"value": "2026-01-24T08:02:00+00:00"},
        "Details": [],
    }))
    assert payload["created_time"] == "2026-01-24T08:02:00+00:00"


def test_p21_carries_date_created_in_its_own_right():
    """P21 read this column before this field existed — as a *fallback for the
    document date*, which is the substitution the whole declaration forbids.
    Both keys are filled now, and they are not the same value."""
    payload = prophet21.translate_invoice(
        {"invoice_no": "9912", "customer_id": "4400",
         "invoice_date": "2026-01-04", "date_created": "2026-01-29T14:11:02Z",
         "total_amount": "1200.00", "amount_paid": "0",
         "date_last_modified": "2026-03-01"},
        [])
    assert payload["created_time"] == "2026-01-29T14:11:02Z"
    assert payload["date"] == "2026-01-04"


def test_p21_still_falls_back_to_date_created_for_an_undated_document():
    """The pre-existing fallback is left alone deliberately. It answers a
    different question — "when did this happen", for a row P21 states no
    invoice date on — and removing it would drop those documents entirely."""
    payload = prophet21.translate_invoice(
        {"invoice_no": "9913", "date_created": "2026-01-29T14:11:02Z"}, [])
    assert payload["date"] == "2026-01-29"
    assert payload["created_time"] == "2026-01-29T14:11:02Z"


def test_x3_carries_credat_under_every_dress_field_of_reads():
    """``_0``-suffixed, because that is the dress Syracuse most often sends and
    the one a translator reading ``record["CREDAT"]`` would silently miss."""
    payload = sage.x3_translate_document(
        {"NUM_0": "SIN0041", "BPR_0": "C0012", "ACCDAT_0": "2026-01-04",
         "AMTNOT_0": "1200.00", "UPDDAT_0": "2026-03-01",
         "CREDAT_0": "2026-01-29"},
        kind="invoice")
    assert payload["created_time"] == "2026-01-29"
    assert payload["created_time"] != payload["date"]


def test_x3_keeps_a_full_timestamp_when_the_representation_states_one():
    """Carried raw rather than through ``iso_date``: the date is all X3's
    ``CREDAT`` column usually holds, but a representation that states the whole
    stamp is the only shape ``normalize`` can actually place on the UTC line,
    and truncating it here would throw that away for good."""
    payload = sage.x3_translate_document(
        {"NUM": "SIN0041", "ACCDAT": "2026-01-04",
         "CREDAT": "2026-01-29T14:11:02Z"},
        kind="invoice")
    assert payload["created_time"] == "2026-01-29T14:11:02Z"


def test_sage100_carries_datecreated():
    payload = sage.sage100_translate_invoice(
        {"InvoiceNo": "0041121", "HeaderSeqNo": "000",
         "ARDivisionNo": "01", "CustomerNo": "ABF",
         "InvoiceDate": "2026-01-04", "DateUpdated": "2026-03-01",
         "DateCreated": "2026-01-29"},
        [])
    assert payload["created_time"] == "2026-01-29"
    assert payload["created_time"] != payload["date"]


# ── the connector that said no ──────────────────────────────────────────────
def test_business_central_declares_the_gap_rather_than_carrying_a_stand_in():
    """The declaration a person can check, and the one worth having.

    Business Central's API v2.0 invoice resources publish ``lastModifiedDateTime``
    and no creation counterpart. The valuable part of ``False`` is that it is
    distinguishable from "we forgot" — so what is asserted is the absence of a
    ``created_time`` key filled from something else, not merely a missing value.
    """
    from app.ingestion.erp import dynamics365
    spec = erp.get_spec("dynamics365")
    assert spec.records_source_time is False
    payload = dynamics365.translate_sales_invoice({
        "id": "8f1", "number": "S-INV1041", "customerId": "c1",
        "invoiceDate": "2026-01-04", "status": "Open",
        "totalAmountExcludingTax": "1200.00",
        "lastModifiedDateTime": "2026-03-01T09:00:00Z",
    })
    assert payload.get("created_time") is None


def _spec(**over) -> ConnectorSpec:
    return ConnectorSpec(**{
        "key": "probe", "label": "Probe", "company_term": "company",
        "credential_fields": (), "connection_fields": (),
        "external_id_field": "company_id", "setup_note": "…",
        "build_source": lambda *a, **k: None,
        "records_source_time": False,
        "source_time_note": "A probe spec used only to exercise the refusal.",
        **over})
