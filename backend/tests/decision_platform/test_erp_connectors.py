"""Five US-market ERPs read through one connector registry.

What must hold, in rough order of how expensive each break would be:

* **Provenance tells the truth.** A NetSuite pull leaves no row anywhere
  claiming Zoho — not on the master rows, not in a ``source_ref``. This is
  the promise ``normalize.py`` makes about its ``system`` default, pinned
  here as ``test_multi_connector_sync``.
* **Two systems' id spaces never pool.** The same external id under two
  connectors is two records.
* **Secrets go in encrypted and never come out.** The credential's JSON
  document round-trips through the connect service; no response carries it.
* **The lifecycle is shared, not copied.** The multi-company plan gate and
  the duplicate-company update behave for a NetSuite company exactly as they
  do for a second Zoho company.
* **Each translator reads its ERP's dress correctly** — NetSuite's GL signs,
  Business Central's line types, Acumatica's ``{"value": …}`` wrapping,
  P21's Y/N flags and derived balance, X3's ``_0``-suffixed fields, Sage
  100's Atom feed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from decimal import Decimal
from urllib.parse import quote, unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import time

import dbsupport
from app.db import get_session
from app.domain import models
from app.domain.origin import CONNECTORS
from app.ingestion import erp
from app.ingestion import connections as conn
from app.ingestion.erp import acumatica, dynamics365, netsuite, prophet21, sage
from app.ingestion.erp.base import iso_date
from app.ingestion.errors import (IngestionError, SourceAuthError,
                                  SourceScopeError, SourceThrottleError,
                                  SourceWriteRefused, SourceWriteUncertain,
                                  SourceWriteUnknown)
from app.ingestion.erp.transport import RestTransport
from app.ingestion.normalize import normalize_bill, normalize_invoice
from app.ingestion.sync import SyncService
from app.routers import connections as connections_router, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"


# ── the registry ────────────────────────────────────────────────────────────
def test_every_registered_connector_has_a_display_identity():
    keys = {s.key for s in erp.catalog()}
    assert keys == {"netsuite", "dynamics365", "acumatica", "prophet21",
                    "sagex3", "sage100"}
    unnamed = keys - set(CONNECTORS)
    assert not unnamed, f"no origin badge for: {unnamed}"


def test_specs_keep_secrets_off_the_connection():
    for spec in erp.catalog():
        assert all(not f.secret for f in spec.connection_fields), spec.key
        assert spec.external_id_field in {f.name for f in spec.connection_fields}, spec.key
        # At least one secret per credential — a connector with none would
        # store its sign-in readable.
        assert any(f.secret for f in spec.credential_fields), spec.key


# The source class each spec's ``build_source`` produces. Named here rather
# than built, because building one needs a live credential — and what this
# checks is a class-level fact.
_SOURCE_OF = {
    "netsuite": netsuite.NetSuiteSource,
    "dynamics365": dynamics365.BusinessCentralSource,
    "acumatica": acumatica.AcumaticaSource,
    "prophet21": prophet21.Prophet21Source,
    "sagex3": sage.SageX3Source,
    "sage100": sage.Sage100Source,
}


def test_connector_permissions():
    """Every connector declares what its sign-in must be granted, and the
    declaration is held against what the connector actually reads.

    The screen used to publish Zoho's ten scope strings whichever system was
    selected, so a NetSuite connection was set up against a list of grants that
    do not exist in NetSuite. Per-connector lists fix that only while they stay
    true, and a list of permissions is exactly the kind of prose that rots
    silently — nothing fails when it is wrong, an owner simply grants the wrong
    things. So it is pinned in both directions, like ``REQUIRED_SCOPES`` is for
    Zoho: a stage the source reads and the list omits is a grant *nobody can
    ever have asked for*, and a stage the list names and the source never reads
    is access requested for no reason.
    """
    for spec in erp.catalog():
        assert spec.permissions, spec.key
        assert spec.permission_note, spec.key
        names = [p.name for p in spec.permissions]
        assert len(names) == len(set(names)), spec.key
        assert any(p.required for p in spec.permissions), spec.key

        source = _SOURCE_OF[spec.key]
        reads = {stage for p in spec.permissions for stage in p.reads}
        implemented = {stage for stage in erp.READ_STAGES
                       if hasattr(source, f"list_{stage}")}
        assert reads == implemented, (
            f"{spec.key}: reads but never asks for "
            f"{sorted(implemented - reads)}; asks for but never reads "
            f"{sorted(reads - implemented)}")


def test_connector_writes():
    """The same pin as ``test_connector_permissions``, in the direction that
    creates records — and it has to be armed *before* the first writer exists,
    which is why it is here while every spec still writes nothing.

    A capability list is prose until something holds it to the code. Declared
    but unimplemented, an owner is told to grant a permission for a write this
    connector cannot do, and a screen offers a button that fails at the ERP.
    Implemented but undeclared is worse: the platform can create a record in a
    system nobody was asked to grant a create permission in, and the first
    anyone hears of it is a refusal in the middle of sending a customer's
    quote. So the declaration equals what the source implements, both ways.
    """
    for spec in erp.catalog():
        source = _SOURCE_OF[spec.key]
        declared = set(spec.writes)
        implemented = {stage for stage in erp.WRITE_STAGES
                       if hasattr(source, f"create_{stage}")}
        assert declared == implemented, (
            f"{spec.key}: writes but never asks for "
            f"{sorted(implemented - declared)}; asks for but cannot write "
            f"{sorted(declared - implemented)}")


def test_a_permission_cannot_name_a_stage_that_is_not_a_sync_stage():
    """A typo in ``reads`` would make the pin above pass by describing a stage
    nothing runs. Refused where it is written instead."""
    with pytest.raises(ValueError) as e:
        erp.Permission("Lists → Customers", "why", reads=("custmoers",))
    assert "custmoers" in str(e.value)

    with pytest.raises(ValueError) as e:
        erp.Permission("Sales Quote → Create", "why", writes=("sales_qoutes",))
    assert "sales_qoutes" in str(e.value)

    # The two vocabularies are separate lists, not one list read twice: a pull
    # stage is not something the platform can create, and the one thing it can
    # create is not a stage the sync pulls. Either swap would make both pins
    # above pass while describing a capability that does not exist.
    with pytest.raises(ValueError):
        erp.Permission("Sales Quote → Create", "why", writes=("sales_orders",))
    with pytest.raises(ValueError):
        erp.Permission("Lists → Quotes", "why", reads=("sales_quotes",))


def test_split_inputs_refuses_missing_fields_by_label():
    with pytest.raises(ValueError) as e:
        erp.split_inputs(erp.get_spec("acumatica"), {"base_url": "https://x"})
    assert "Acumatica needs" in str(e.value)
    assert "Password" in str(e.value)


def test_split_inputs_refuses_values_the_spec_never_declared():
    spec = erp.get_spec("prophet21")
    with pytest.raises(ValueError) as e:
        erp.split_inputs(spec, {"base_url": "https://x", "username": "u",
                                "password": "p", "company_id": "C1",
                                "totally_unknown": "x"})
    assert "totally_unknown" in str(e.value)


# ── the shared date reader ──────────────────────────────────────────────────
def test_iso_date_reads_each_erps_dress_and_never_guesses():
    assert iso_date("2026-06-01") == "2026-06-01"
    assert iso_date("2026-06-01T14:22:09Z") == "2026-06-01"
    assert iso_date("6/1/2026") == "2026-06-01"          # NetSuite default
    assert iso_date("/Date(1750000000000)/") == "2025-06-15"
    assert iso_date("") is None
    assert iso_date("not a date") is None


# ── NetSuite ────────────────────────────────────────────────────────────────
def test_tba_signature_is_a_valid_oauth1_hmac_sha256():
    client = netsuite.NetSuiteClient(
        account_id="1234567_SB1", consumer_key="ck", consumer_secret="cs",
        token_id="ti", token_secret="ts", http=object())
    url = ("https://1234567-sb1.suitetalk.api.netsuite.com"
           "/services/rest/query/v1/suiteql")
    header = client._tba_header("POST", url, {"limit": 1000, "offset": 0})
    assert header.startswith('OAuth realm="1234567_SB1"')

    fields = dict(re.findall(r'(\w+)="([^"]*)"', header))
    assert fields["oauth_signature_method"] == "HMAC-SHA256"

    def rfc(v: str) -> str:
        return quote(v, safe="~-._")

    oauth = {k: unquote(v) for k, v in fields.items()
             if k.startswith("oauth_") and k != "oauth_signature"}
    both = {"limit": "1000", "offset": "0", **oauth}
    pairs = "&".join(f"{rfc(k)}={rfc(both[k])}" for k in sorted(both))
    base = f"POST&{rfc(url)}&{rfc(pairs)}"
    expected = base64.b64encode(
        hmac.new(b"cs&ts", base.encode(), hashlib.sha256).digest()).decode()
    assert unquote(fields["oauth_signature"]) == expected


def test_netsuite_invoice_undoes_the_gl_sign_and_a_bill_keeps_its_own():
    header = {"id": 101, "tranid": "INV-9", "entity": 55,
              "trandate": "2026-06-01", "duedate": "2026-07-01",
              "status": "Open", "foreigntotal": 250.0,
              "foreignamountunpaid": 100.0, "currency_code": "USD",
              "lastmodified": "2026-06-02T10:00:00"}
    lines = [{"tid": 101, "line_id": 1, "item": 7, "quantity": -10,
              "rate": 25.0, "netamount": -250.0}]
    payload = netsuite.translate_document(header, lines, kind="invoice")
    txns = normalize_invoice(payload, system="netsuite")
    assert len(txns) == 1
    assert txns[0].qty == Decimal("10")
    assert txns[0].line_revenue == Decimal("250.0")
    assert txns[0].source_ref.system == "netsuite"

    bill_header = {**header, "id": 202}
    bill_lines = [{"tid": 202, "line_id": 1, "item": 7, "quantity": 100,
                   "rate": 12.0, "netamount": 1200.0}]
    bill = netsuite.translate_document(bill_header, bill_lines, kind="bill")
    costs = normalize_bill(bill, system="netsuite")
    assert costs[0].qty == Decimal("100")
    assert costs[0].unit_cost == Decimal("12")


def test_netsuite_voided_documents_are_not_trade():
    header = {"id": 9, "status": "Voided", "trandate": "2026-06-01"}
    payload = netsuite.translate_document(header, [], kind="invoice")
    assert not netsuite._is_trade(payload)


# ── Business Central ────────────────────────────────────────────────────────
def test_bc_invoice_keeps_item_lines_and_drops_account_lines():
    row = {
        "id": "guid-1", "number": "SI-100", "customerId": "cust-guid",
        "customerName": "Acme", "invoiceDate": "2026-06-01",
        "dueDate": "2026-07-01", "status": "Open", "currencyCode": "USD",
        "totalAmountExcludingTax": 250, "remainingAmount": 250,
        "lastModifiedDateTime": "2026-06-01T12:00:00Z",
        "salesInvoiceLines": [
            {"id": "l1", "lineType": "Item", "itemId": "item-guid",
             "quantity": 10, "unitPrice": 25, "amountExcludingTax": 250,
             "description": "Insert"},
            {"id": "l2", "lineType": "Account", "itemId": None,
             "quantity": 1, "unitPrice": 40, "amountExcludingTax": 40},
        ],
    }
    payload = dynamics365.translate_sales_invoice(row)
    assert [ln["item_id"] for ln in payload["line_items"]] == ["item-guid"]
    txns = normalize_invoice(payload, system="dynamics365")
    assert txns[0].unit_price == Decimal("25")
    assert txns[0].source_ref.system == "dynamics365"


def test_bc_draft_documents_are_not_trade():
    assert not dynamics365._is_trade({"status": "Draft"})
    assert dynamics365._is_trade({"status": "Open"})


# ── Acumatica ───────────────────────────────────────────────────────────────
def test_acumatica_unwraps_the_value_dress_end_to_end():
    raw = {
        "ReferenceNbr": {"value": "AR-77"},
        "CustomerID": {"value": "ACME"},
        "Date": {"value": "2026-06-01T00:00:00+00:00"},
        "Status": {"value": "Open"},
        "Amount": {"value": 250.0},
        "Balance": {"value": 100.0},
        "CurrencyID": {"value": "USD"},
        "Details": [
            {"LineNbr": {"value": 1}, "InventoryID": {"value": "CNMG-1"},
             "Qty": {"value": 10.0}, "UnitPrice": {"value": 25.0},
             "Amount": {"value": 250.0}},
        ],
        "_links": {"self": "x"},
    }
    payload = acumatica.translate_invoice(acumatica._plain(raw))
    assert payload["invoice_id"] == "AR-77"
    txns = normalize_invoice(payload, system="acumatica")
    assert txns[0].line_revenue == Decimal("250.0")
    assert txns[0].source_ref.system == "acumatica"


# ── Prophet 21 ──────────────────────────────────────────────────────────────
def test_p21_groups_lines_and_derives_balance_from_stated_fields():
    header = {"invoice_no": "500123", "invoice_date": "2026-06-01T00:00:00",
              "customer_id": 88, "net_due_date": "2026-07-01T00:00:00",
              "total_amount": 250.0, "amount_paid": 150.0,
              "delete_flag": "N"}
    lines = [{"invoice_no": "500123", "line_no": 1, "inv_mast_uid": 4001,
              "item_id": "CNMG-1", "qty_shipped": 10, "unit_price": 25.0,
              "extended_price": 250.0}]
    payload = prophet21.translate_invoice(header, lines)
    assert payload["balance"] == pytest.approx(100.0)
    txns = normalize_invoice(payload, system="prophet21")
    assert txns[0].qty == Decimal("10")
    assert txns[0].source_ref.system == "prophet21"


def test_p21_balance_is_absent_when_either_side_is_unstated():
    payload = prophet21.translate_invoice(
        {"invoice_no": "1", "invoice_date": "2026-06-01",
         "total_amount": 250.0}, [])
    assert payload["balance"] is None


def test_p21_deleted_rows_read_as_void_and_inactive():
    assert prophet21.translate_invoice(
        {"invoice_no": "1", "delete_flag": "Y"}, [])["status"] == "void"
    assert prophet21.translate_customer(
        {"customer_id": 1, "customer_name": "X",
         "delete_flag": "Y"})["status"] == "inactive"


# ── Sage X3 ─────────────────────────────────────────────────────────────────
def test_x3_reads_fields_under_all_three_dresses_and_finds_the_line_grid():
    record = {
        "NUM_0": "SI-9",
        "BPR": {"$value": "C001"},
        "ACCDAT": "2026-06-01",
        "CUR_0": "USD",
        "AMTNOT_0": 250.0,
        "$uuid": "x",
        "SOMEBLOCK": [
            {"ITMREF_0": "CNMG-1", "QTY_0": 10, "NETPRI_0": 25.0,
             "AMTNOTLIN_0": 250.0},
        ],
    }
    payload = sage.x3_translate_document(record, kind="invoice")
    assert payload["invoice_id"] == "SI-9"
    assert payload["customer_id"] == "C001"
    assert payload["currency_code"] == "USD"
    txns = normalize_invoice(payload, system="sagex3")
    assert txns[0].line_revenue == Decimal("250.0")
    assert txns[0].source_ref.system == "sagex3"


# ── Sage 100 ────────────────────────────────────────────────────────────────
_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:sdata="http://schemas.sage.com/sdata/2008/1"
      xmlns:m="http://schemas.sage.com/mas90/contract">
  <entry>
    <sdata:payload>
      <m:AR_InvoiceHistoryHeader>
        <m:InvoiceNo>0100051</m:InvoiceNo>
        <m:HeaderSeqNo>000000</m:HeaderSeqNo>
        <m:ARDivisionNo>01</m:ARDivisionNo>
        <m:CustomerNo>ACME01</m:CustomerNo>
        <m:InvoiceDate>2026-06-01</m:InvoiceDate>
        <m:InvoiceDueDate>2026-07-01</m:InvoiceDueDate>
      </m:AR_InvoiceHistoryHeader>
    </sdata:payload>
  </entry>
</feed>"""


def test_sage100_parses_the_atom_feed_by_local_name():
    rows = list(sage._atom_records(_ATOM, 200, "AR_InvoiceHistoryHeader"))
    assert rows == [{
        "InvoiceNo": "0100051", "HeaderSeqNo": "000000",
        "ARDivisionNo": "01", "CustomerNo": "ACME01",
        "InvoiceDate": "2026-06-01", "InvoiceDueDate": "2026-07-01"}]
    payload = sage.sage100_translate_invoice(rows[0], [
        {"InvoiceNo": "0100051", "DetailSeqNo": "1", "ItemCode": "CNMG-1",
         "QuantityShipped": "10", "UnitPrice": "25.00",
         "ExtensionAmt": "250.00"}])
    assert payload["customer_id"] == "01-ACME01"
    txns = normalize_invoice(payload, system="sage100")
    assert txns[0].unit_price == Decimal("25.00")


def test_sage100_a_sign_in_page_reads_as_an_auth_failure_not_an_empty_book():
    with pytest.raises(SourceAuthError):
        list(sage._atom_records("<html>login</html>...", 200, "AR_Customer"))


def test_sage100_refuses_an_entity_expansion_bomb():
    # A hostile source that puts a DTD in front of the feed is aiming an
    # entity-expansion bomb at ElementTree; the parser must refuse the DTD
    # rather than expand a 1 KB body into gigabytes of resident text.
    bomb = ('<?xml version="1.0"?><!DOCTYPE r ['
            '<!ENTITY a "AAAAAAAAAA">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            ']><r>&c;</r>')
    with pytest.raises(SourceAuthError):
        list(sage._atom_records(bomb, 200, "AR_Customer"))


# ── the shared transport ────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""
        self.url = "https://erp.example/x"

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _Http:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, method, url, **kw):
        self.calls += 1
        return self.responses.pop(0)


class _Quiet(RestTransport):
    system = "TestERP"
    max_retries = 3

    def __init__(self, http):
        super().__init__(http=http)
        self.slept: list[float] = []

    def _sleep(self, seconds):
        if seconds > 0:
            self.slept.append(seconds)


# ── the Business Central quote write ────────────────────────────────────────
# The same contract the Zoho write is held to, case for case. Two adapters that
# answer differently on the same evidence is the defect this repo already found
# once, between Zoho's own two write paths.

_BC_LINES = [{"code": "CNMG120408", "itemId": "item-guid-1", "qty": 4, "rate": "1250.00"}]


class _BcHttp:
    """A fake Business Central. Routes are (METHOD, path-substring) -> response
    or callable; a callable may raise to model a fault this side of the answer."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.sent: list[tuple[str, str]] = []
        #: Every call's kwargs, so a test can assert on what actually went out
        #: — a $filter travels in ``params``, not in the URL.
        self.calls: list[dict] = []

    def request(self, method, url, **kw):
        self.sent.append((method, url))
        self.calls.append({"method": method, "url": url, **kw})
        for (verb, part), resp in self.routes.items():
            if verb == method and part in url:
                if callable(resp):
                    return resp(kw.get("json"))
                return resp
        return _Resp(200, {"value": []})


def _bc(routes) -> dynamics365.BusinessCentralSource:
    client = dynamics365.BusinessCentralClient(
        tenant_id="t", client_id="c", client_secret="s",
        environment="sandbox", http=_BcHttp(routes))
    client._token, client._token_expires_at = "tok", time.time() + 3600
    return dynamics365.BusinessCentralSource(client, "company-guid")


def _posts(source) -> list[tuple[str, str]]:
    return [c for c in source._client._http.sent if c[0] == "POST"]


def test_a_bc_quote_without_a_reference_is_refused_unwritten():
    """The precondition the whole protocol rests on. Without a reference the
    write cannot be read back, so a lost reply would be unanswerable — and an
    unanswerable write must not be attempted at all."""
    src = _bc({})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001", reference="")
    assert "could not be found again" in str(e.value)
    assert not _posts(src), "nothing may be sent"


def test_a_bc_quote_for_no_named_customer_is_refused_unwritten():
    src = _bc({})
    with pytest.raises(SourceWriteRefused):
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="", reference="QB-1-a")
    assert not _posts(src)


def test_a_reference_too_long_for_the_field_is_refused_rather_than_truncated():
    """Business Central stores externalDocumentNumber in 35 characters. A
    truncated reference is a reference that cannot be looked up, which defeats
    the settle protocol silently — so the length is checked, not trusted."""
    src = _bc({})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="Q" * 36)
    assert "35 characters" in str(e.value)
    assert not _posts(src)


def test_a_line_with_no_item_id_is_refused_and_names_the_line():
    """A quote line must name an item that already exists in Business Central.
    The refusal carries the codes so a screen can point at the lines."""
    src = _bc({})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", [{"code": "MYSTERY", "qty": 1, "rate": "1"}],
                                customer_ref="C-001", reference="QB-1-a")
    assert e.value.codes == ["MYSTERY"]
    assert not _posts(src)


def test_a_bc_quote_is_created_header_then_lines():
    src = _bc({("POST", "/salesQuotes"): _Resp(201, {"id": "q-guid", "number": "SQ-1001"})})
    doc = src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                  reference="QB-1-abcd")
    assert doc.number == "SQ-1001" and doc.document_id == "q-guid"
    assert doc.line_count == 1 and doc.already_existed is False
    assert len(_posts(src)) == 2, "one header, one line"


def test_a_5xx_on_the_bc_header_settles_by_reading_and_is_sent_once():
    """The write may have landed. One read answers it, and the document found
    is reported rather than a second one created."""
    landed = {"id": "q-guid", "number": "SQ-1001",
              "externalDocumentNumber": "QB-1-abcd"}
    src = _bc({("POST", "/salesQuotes"): _Resp(503),
               ("GET", "/salesQuotes"): _Resp(200, {"value": [landed]})})
    doc = src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                  reference="QB-1-abcd")
    assert doc.number == "SQ-1001"
    assert doc.already_existed is True, "found, not created — the screen says so"
    assert len(_posts(src)) == 1, "the write must never be replayed"


def test_a_bc_write_the_read_proves_never_landed_is_safe_to_retry():
    """The read succeeded and found nothing. That is evidence, and reporting
    UNKNOWN would discard it — the same verdict the Zoho path reaches."""
    src = _bc({("POST", "/salesQuotes"): _Resp(503),
               ("GET", "/salesQuotes"): _Resp(200, {"value": []})})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "sending again is safe" in str(e.value)


def test_a_bc_write_whose_settling_read_also_fails_is_unknown():
    """The realistic shape: the fault that lost the write has not healed by the
    time the read goes out. Neither verdict is available, and the reference to
    look up travels with the answer."""
    def die(_body):
        raise TimeoutError("connection timed out")

    src = _bc({("POST", "/salesQuotes"): _Resp(503),
               ("GET", "/salesQuotes"): die})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert e.value.reference == "QB-1-abcd"
    assert "QB-1-abcd" in str(e.value)


def test_a_header_that_lands_without_lines_is_unknown_not_success():
    """The failure mode a two-POST write has and a one-POST write does not.

    The header exists and the quote there is incomplete. Success would put a
    quote missing lines in front of a customer; a refusal would claim nothing
    was written while a document sits in the ledger. So it says exactly that and
    names what to go and look at.
    """
    src = _bc({("POST", "/salesQuotes("): _Resp(422, {"error": "bad item"}),
               ("POST", "/salesQuotes"): _Resp(201, {"id": "q-guid", "number": "SQ-1001"})})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    msg = str(e.value)
    assert "incomplete" in msg and "SQ-1001" in msg


def test_a_2xx_with_no_id_is_unknown_rather_than_a_quote_with_no_lines():
    """Accepted, but nothing to attach lines to and nothing to look up later.
    Reporting success here would be the fabricated one this file exists to stop."""
    src = _bc({("POST", "/salesQuotes"): _Resp(201, {"number": "SQ-1001"})})
    with pytest.raises(SourceWriteUnknown):
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")


def test_a_403_on_the_bc_write_names_the_permission_to_grant():
    """The answer a write provokes that a read never does: the app registration
    authenticates and was never granted write access."""
    src = _bc({("POST", "/salesQuotes"): _Resp(403, {"error": "forbidden"})})
    with pytest.raises(SourceScopeError) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "API.ReadWrite.All" in (e.value.scope or "")


def test_a_reference_carrying_a_quote_cannot_forge_the_settle_filter():
    """An OData string literal ends at a single quote, so a reference holding
    one would change what the filter means. Doubled, per OData."""
    src = _bc({("POST", "/salesQuotes"): _Resp(503),
               ("GET", "/salesQuotes"): _Resp(200, {"value": []})})
    with pytest.raises(SourceWriteRefused):
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-o'brien")
    reads = [c for c in src._client._http.calls if c["method"] == "GET"]
    assert reads, "the settle read never went out"
    sent_filter = (reads[0].get("params") or {}).get("$filter", "")
    assert "o''brien" in sent_filter, (
        f"the quote was not doubled, so the filter means something else: {sent_filter!r}")


def test_transport_backs_off_a_429_and_gives_up_as_a_throttle():
    t = _Quiet(_Http([_Resp(429), _Resp(429), _Resp(429)]))
    with pytest.raises(SourceThrottleError):
        t.get("https://erp.example/x")
    assert t.calls == 3
    assert len(t.slept) >= 2          # backed off between attempts


def test_transport_refreshes_auth_once_then_names_the_credentials():
    class _Auth(_Quiet):
        def __init__(self, http):
            super().__init__(http)
            self.invalidated = 0

        def _invalidate_auth(self):
            self.invalidated += 1

    t = _Auth(_Http([_Resp(401), _Resp(401)]))
    with pytest.raises(SourceAuthError):
        t.get("https://erp.example/x")
    assert t.invalidated == 1


def test_transport_reports_a_named_scope_refusal_over_a_generic_auth_error():
    class _Scoped(_Quiet):
        def _scope_refusal(self, resp):
            if resp.status_code == 403:
                return SourceScopeError("no permission", path="x", scope="read")
            return None

    with pytest.raises(SourceScopeError):
        _Scoped(_Http([_Resp(403)])).get("https://erp.example/x")


def test_a_5xx_on_an_unmarked_write_is_uncertain_and_is_sent_exactly_once():
    """The distinction the whole settle-by-read protocol is built on.

    A 5xx on a non-idempotent call cannot be told apart from a success whose
    answer was lost, so it is not replayed — that is how one quote becomes two
    orders. But "not replayed" is only half of it: raised as a plain
    ``IngestionError`` it reads identically to "the source answered and said
    no", and a caller that cannot tell those apart either sends again or
    reports a failure that may well have landed.
    """
    http = _Http([_Resp(500), _Resp(500), _Resp(500)])
    t = _Quiet(http)
    with pytest.raises(SourceWriteUncertain):
        t.request("POST", "https://erp.example/quotes", json={"line": 1})
    assert http.calls == 1                # never sent a second time
    assert t.calls == 1


def test_a_refusal_is_a_plain_failure_not_an_uncertain_write():
    """The other side of that pin: the source answered, so nothing was
    written and there is nothing to go and look up."""
    t = _Quiet(_Http([_Resp(400, {"message": "customer is required"})]))
    with pytest.raises(IngestionError) as e:
        t.request("POST", "https://erp.example/quotes", json={"line": 1})
    assert not isinstance(e.value, SourceWriteUncertain)


def test_a_post_the_caller_marks_replayable_still_keeps_its_retry_budget():
    """A POST that is really a read — NetSuite's SuiteQL, a token sign-in —
    is idempotent, and the 5xx backoff it has always had is not a write."""
    http = _Http([_Resp(500), _Resp(500), _Resp(200, {"items": [{"id": 1}]})])
    t = _Quiet(http)
    body = t.request("POST", "https://erp.example/suiteql",
                     json={"q": "SELECT 1"}, replayable=True)
    assert body == {"items": [{"id": 1}]}
    assert http.calls == 3


def test_a_dropped_connection_on_a_write_is_uncertain_and_on_a_read_is_not():
    """A fault this side of the answer is the same unknown as a 5xx: the
    request may well have been received. A read that never landed is only a
    failed read, and dressing it as an uncertain write would send an owner
    looking for a record nothing ever tried to create."""
    class _Broken:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, **kw):
            self.calls += 1
            raise ConnectionError("connection reset by peer")

    write = _Broken()
    with pytest.raises(SourceWriteUncertain):
        _Quiet(write).request("POST", "https://erp.example/quotes",
                              json={"line": 1})
    assert write.calls == 1

    read = _Broken()
    with pytest.raises(ConnectionError):
        _Quiet(read).get("https://erp.example/x")


# ── the connect service ─────────────────────────────────────────────────────
@pytest.fixture()
def db():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    try:
        yield s
    finally:
        s.close()


_NS_VALUES = {"consumer_key": "ck", "consumer_secret": "cs",
              "token_id": "ti", "token_secret": "ts",
              "company_id": "1234567"}


def test_secrets_are_encrypted_at_rest_and_config_stays_readable(db):
    row = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES,
                           label="US Books")
    cred = row.credential
    assert cred.connector == "netsuite"
    stored = cred.secrets_encrypted
    # The quoted-JSON forms, not bare "cs"/"ts": those are two base64
    # characters and appear somewhere in a ~120-char Fernet token by pure
    # chance in roughly one run in twenty. A double quote can never occur in
    # base64url, so cleartext JSON — the thing this test exists to forbid —
    # is detected deterministically.
    assert stored and '"cs"' not in stored and '"ts"' not in stored
    material = conn.credential_material(db, row)
    assert material.secrets == {"consumer_secret": "cs", "token_secret": "ts"}
    assert cred.config == {"consumer_key": "ck", "token_id": "ti"}
    # The Zoho-typed columns stay empty: no half-shaped credential exists.
    assert cred.client_id is None


def test_the_same_secrets_attach_to_the_credential_already_on_file(db):
    a = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    b = conn.connect_erp(db, ORG, connector="netsuite",
                         values={**_NS_VALUES, "company_id": "1234567:3"})
    assert a.credential_id == b.credential_id
    assert a.connection_id != b.connection_id


def test_reconnecting_the_same_company_updates_rather_than_duplicates(db):
    a = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    b = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES,
                         label="Renamed")
    assert a.connection_id == b.connection_id
    assert b.label == "Renamed"


def test_the_same_external_id_under_two_connectors_is_two_companies(db):
    ns = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    p21 = conn.connect_erp(db, ORG, connector="prophet21", values={
        "base_url": "https://p21.example", "username": "api",
        "password": "pw", "company_id": "1234567"})
    assert ns.zoho_organization_id == p21.zoho_organization_id == "1234567"
    assert ns.connection_id != p21.connection_id


def test_a_second_company_on_a_free_plan_is_refused_whichever_system_it_lives_in(db):
    from app import entitlements

    org = db.get(models.Organization, ORG)
    org.plan = "free"
    db.flush()
    conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    with pytest.raises(entitlements.PlanRefused):
        conn.connect_erp(db, ORG, connector="prophet21", values={
            "base_url": "https://p21.example", "username": "api",
            "password": "pw", "company_id": "OTHER"})


def test_the_trial_key_carries_the_connector_so_id_collisions_cannot_spend_it(db):
    conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    trials = db.scalars(select(models.IntelligenceTrial)).all()
    assert [t.zoho_organization_id for t in trials] == ["netsuite:1234567"]


def test_rotation_replaces_the_whole_secret_document(db):
    row = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    conn.rotate_erp_credential(db, ORG, row.credential_id, values={
        "consumer_key": "ck", "consumer_secret": "cs2",
        "token_id": "ti2", "token_secret": "ts2"})
    material = conn.credential_material(db, row)
    assert material.secrets == {"consumer_secret": "cs2", "token_secret": "ts2"}


def test_the_zoho_typed_resolver_refuses_another_connectors_row(db):
    row = conn.connect_erp(db, ORG, connector="netsuite", values=_NS_VALUES)
    with pytest.raises(ValueError):
        conn.credentials_for(db, row)
    # And the org-level Zoho fallback never hands it back either.
    assert conn.get_zoho_credentials(db, ORG) is None


# ── a full pull under a non-Zoho connector ──────────────────────────────────
class _CanonicalStub:
    """A source already speaking the canonical shape, as every ERP source does
    after its translator."""

    def __init__(self, prefix="NS"):
        self.p = prefix

    def list_contacts(self):
        return [{"contact_id": f"{self.p}-C1", "contact_name": "Acme Industrial",
                 "status": "active"}]

    def list_vendors(self):
        return [{"contact_id": f"{self.p}-V1", "contact_name": "Kennametal Inc",
                 "status": "active"}]

    def list_items(self):
        return [{"item_id": f"{self.p}-I1", "name": "CNMG 120408",
                 "sku": "CNMG-1", "status": "active"}]

    def list_invoices(self, skip=None):
        return [{"invoice_id": f"{self.p}-INV1", "customer_id": f"{self.p}-C1",
                 "date": "2026-06-01", "currency_code": "USD",
                 "total": 250, "balance": 250,
                 "line_items": [{"line_item_id": "1", "item_id": f"{self.p}-I1",
                                 "quantity": 10, "rate": 25,
                                 "item_total": 250}]}]

    def list_bills(self, skip=None):
        return [{"bill_id": f"{self.p}-B1", "vendor_id": f"{self.p}-V1",
                 "date": "2026-05-20", "currency_code": "USD", "total": 1200,
                 "line_items": [{"line_item_id": "1", "item_id": f"{self.p}-I1",
                                 "quantity": 100, "rate": 12,
                                 "item_total": 1200}]}]

    def list_users(self):
        return []


@pytest.fixture()
def us_org(db):
    db.add(models.Organization(organization_id="org_us", name="US Client",
                               currency="USD"))
    db.flush()
    return "org_us"


def test_multi_connector_sync(db, us_org):
    """A NetSuite pull leaves no row anywhere claiming Zoho."""
    report = SyncService(db, _CanonicalStub(), us_org,
                         connector="netsuite", connection_id="conn-ns").run()
    assert report.customers == 1 and report.products == 1
    assert report.sales_txns == 1 and report.cost_records == 1
    assert not report.skipped, report.skipped

    for model in (models.Customer, models.Product, models.Vendor):
        rows = db.scalars(select(model).where(
            model.organization_id == us_org)).all()
        assert rows and all(r.connector == "netsuite" for r in rows), model

    stamped = []
    for model in (models.Customer, models.Product, models.Vendor,
                  models.SalesTxn, models.CostRecord):
        for row in db.scalars(select(model).where(
                model.organization_id == us_org)):
            ref = getattr(row, "source_ref", None) or {}
            if ref.get("system"):
                stamped.append(ref["system"])
    assert stamped and set(stamped) == {"netsuite"}, stamped


def test_two_connectors_sharing_external_ids_stay_two_sets_of_records(db, us_org):
    SyncService(db, _CanonicalStub(), us_org,
                connector="netsuite", connection_id="conn-ns").run()
    SyncService(db, _CanonicalStub(), us_org,
                connector="prophet21", connection_id="conn-p21").run()
    customers = db.scalars(select(models.Customer).where(
        models.Customer.organization_id == us_org)).all()
    assert len(customers) == 2
    assert {c.connector for c in customers} == {"netsuite", "prophet21"}


def test_a_foreign_currency_document_is_refused_not_pooled(db, us_org):
    class _Mixed(_CanonicalStub):
        def list_invoices(self, skip=None):
            rows = super().list_invoices(skip)
            rows[0]["currency_code"] = "CAD"
            return rows

    report = SyncService(db, _Mixed(), us_org,
                         connector="netsuite", connection_id="conn-ns").run()
    assert report.sales_txns == 0
    assert any(s["code"] == "FOREIGN_CURRENCY" for s in report.skipped)


# ── the HTTP surface ────────────────────────────────────────────────────────
@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    for r in (platform_auth.router, connections_router.router):
        app.include_router(r)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_catalog_declares_every_form_a_client_can_render(client):
    r = client.get("/api/v1/connections/catalog", headers=_hdr(client, OWNER))
    assert r.status_code == 200, r.text
    by_key = {c["key"]: c for c in r.json()["connectors"]}
    # Zoho leads the same list rather than being written out on the screen —
    # the tab strip and the access list below it come from one place, which is
    # what stops them describing different systems.
    assert set(by_key) == {"zoho", "netsuite", "dynamics365", "acumatica",
                           "prophet21", "sagex3", "sage100"}
    assert r.json()["connectors"][0]["key"] == "zoho"
    ns = by_key["netsuite"]
    secret_flags = {f["name"]: f["secret"] for f in ns["credential_fields"]}
    assert secret_flags["consumer_secret"] and secret_flags["token_secret"]
    assert by_key["dynamics365"]["can_discover"] is True

    # Every row carries the write capability, and every row's answer is true.
    # The one that matters is Zoho's: it is the only connector that can create
    # a record and the only one outside the erp registry, so it is the row a
    # hand-written default would have got wrong — and the both-ways pin that
    # guards the others iterates the registry and cannot see it.
    assert all("writes" in c and "can_write_quotes" in c
               for c in by_key.values()), "a client cannot render what is not served"
    assert by_key["zoho"]["can_write_quotes"] is True
    assert by_key["zoho"]["writes"] == ["sales_quotes"]
    # Asserted against the capability function the quote router routes on, not
    # against a hardcoded list of which connectors write. The first version of
    # this said "only Zoho", which was true when it was written and false the
    # moment Business Central gained a writer — a test that has to be edited
    # every time the answer changes teaches people to edit it without reading.
    for key, row in by_key.items():
        assert row["can_write_quotes"] is conn.can_write_quotes(key), (
            f"{key}: the screen and the write path disagree about whether a "
            f"quote can be created there")
        assert row["writes"] == list(conn.writes_for(key))


def test_connecting_through_the_api_never_echoes_a_secret(client):
    r = client.post("/api/v1/connections/erp", headers=_hdr(client, OWNER),
                    json={"connector": "netsuite", "values": _NS_VALUES,
                          "label": "US Books"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["connector"] == "netsuite"
    assert body["connector_label"] == "Oracle NetSuite"
    assert body["zoho_organization_id"] == "1234567"
    text = r.text
    for secret in ("cs", "ts"):
        assert f'"{secret}"' not in text
    # Listed afterwards, with the connector named.
    listed = client.get("/api/v1/connections", headers=_hdr(client, OWNER)).json()
    assert listed["connections"][0]["connector"] == "netsuite"
    assert listed["credentials"][0]["connector"] == "netsuite"


def test_a_missing_field_is_refused_with_its_label_not_stored(client):
    r = client.post("/api/v1/connections/erp", headers=_hdr(client, OWNER),
                    json={"connector": "acumatica",
                          "values": {"base_url": "https://erp.example"}})
    assert r.status_code == 400
    assert "Password" in r.json()["detail"]


def test_an_unknown_connector_is_a_404_naming_the_known_ones(client):
    r = client.post("/api/v1/connections/erp", headers=_hdr(client, OWNER),
                    json={"connector": "quicken", "values": {}})
    assert r.status_code == 404
    assert "netsuite" in r.json()["detail"]


def test_connecting_an_erp_needs_the_owner(client):
    r = client.post("/api/v1/connections/erp", headers=_hdr(client, SALES),
                    json={"connector": "netsuite", "values": _NS_VALUES})
    assert r.status_code == 403


def test_discover_refuses_a_connector_whose_signin_is_already_scoped(client):
    r = client.post("/api/v1/connections/erp/discover",
                    headers=_hdr(client, OWNER),
                    json={"connector": "acumatica",
                          "values": {"base_url": "https://x", "username": "u",
                                     "password": "p"}})
    assert r.status_code == 400
    assert "tenant" in r.json()["detail"]


def test_zoho_rotation_and_erp_rotation_refuse_each_others_rows(client):
    hdr = _hdr(client, OWNER)
    r = client.post("/api/v1/connections/erp", headers=hdr,
                    json={"connector": "netsuite", "values": _NS_VALUES})
    connection_id = r.json()["connection_id"]
    wrong = client.post(f"/api/v1/connections/{connection_id}/rotate",
                        headers=hdr, json={"refresh_token": "tok"})
    assert wrong.status_code == 400
    assert "rotate-erp" in wrong.json()["detail"]

    right = client.post(f"/api/v1/connections/{connection_id}/rotate-erp",
                        headers=hdr,
                        json={"values": {"consumer_key": "ck",
                                         "consumer_secret": "c2",
                                         "token_id": "ti",
                                         "token_secret": "t2"}})
    assert right.status_code == 200, right.text
    assert right.json()["rotated"] is True
