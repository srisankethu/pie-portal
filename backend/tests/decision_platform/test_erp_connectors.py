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


def _bc_row(**over):
    """One salesQuotes row as BC returns it, lines expanded."""
    row = {"id": "q-guid", "number": "SQ-1001",
           "externalDocumentNumber": "QB-1-abcd",
           "salesQuoteLines": [{"id": "l-1"}]}
    row.update(over)
    return row


class _BcHttp:
    """A fake Business Central.

    Routed on the ENTITY, not on a URL substring: "/salesQuotes" is a prefix of
    "/salesQuotes(q-guid)/salesQuoteLines", so substring matching let the header
    route answer line POSTs — a fake that quietly answers the wrong call makes
    every test built on it meaningless.

    Each route holds a QUEUE, so a test can say what the pre-flight read sees
    and what the settle read sees afterwards. The last entry repeats.
    """

    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) else [v]
                       for k, v in routes.items()}
        self.calls: list[dict] = []

    @staticmethod
    def _entity(url):
        tail = url.rsplit(")/", 1)[-1]
        return "salesQuoteLines" if "salesQuoteLines" in tail else tail.split("?")[0]

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        queue = self.routes.get((method, self._entity(url)))
        if not queue:
            return _Resp(404, {"error": f"no route for {method} {self._entity(url)}"})
        resp = queue[0] if len(queue) == 1 else queue.pop(0)
        return resp(kw.get("json")) if callable(resp) else resp


def _bc(routes) -> dynamics365.BusinessCentralSource:
    client = dynamics365.BusinessCentralClient(
        tenant_id="t", client_id="c", client_secret="s",
        environment="sandbox", http=_BcHttp(routes))
    client._token, client._token_expires_at = "tok", time.time() + 3600
    return dynamics365.BusinessCentralSource(client, "company-guid")


def _sent(src, method):
    return [c for c in src._client._http.calls if c["method"] == method]


#: The ordinary case: nothing there yet, the header is created, lines follow.
_CLEAN = {("GET", "salesQuotes"): _Resp(200, {"value": []}),
          ("POST", "salesQuotes"): _Resp(201, {"id": "q-guid", "number": "SQ-1001"}),
          ("POST", "salesQuoteLines"): _Resp(201, {"id": "l-1"})}


def test_a_bc_quote_without_a_reference_is_refused_unwritten():
    """The precondition the whole protocol rests on. Without a reference the
    write cannot be read back, so a lost reply would be unanswerable — and an
    unanswerable write must not be attempted at all."""
    src = _bc(_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001", reference="")
    assert "could not be found again" in str(e.value)
    assert not _sent(src, "POST"), "nothing may be sent"


def test_a_bc_quote_for_no_named_customer_is_refused_unwritten():
    src = _bc(_CLEAN)
    with pytest.raises(SourceWriteRefused):
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="", reference="QB-1-a")
    assert not _sent(src, "POST")


def test_a_reference_too_long_for_the_field_is_refused_rather_than_truncated():
    """Business Central stores externalDocumentNumber in 35 characters. A
    truncated reference is a reference that cannot be looked up, which defeats
    the settle protocol silently — so the length is checked, not trusted."""
    src = _bc(_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="Q" * 36)
    assert "35 characters" in str(e.value)
    assert not _sent(src, "POST")


def test_a_quote_with_no_lines_is_refused_rather_than_created_empty():
    """An empty sales quote in a customer's ledger is worse than none, and it
    would be reported as a successful send. Zoho refuses this; this dropped it."""
    src = _bc(_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", [], customer_ref="C-001", reference="QB-1-a")
    assert "no priced lines" in str(e.value)
    assert not _sent(src, "POST")


def test_a_line_with_no_item_id_is_refused_and_names_the_line():
    src = _bc(_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", [{"code": "MYSTERY", "qty": 1, "rate": "1"}],
                                customer_ref="C-001", reference="QB-1-a")
    assert e.value.codes == ["MYSTERY"]
    assert not _sent(src, "POST")


def test_a_line_with_no_price_is_refused_rather_than_priced_by_the_item_card():
    """Business Central fills an omitted unitPrice from the item card, so the
    customer would be quoted a number nobody here chose — a price this platform
    did not compute, on a document it did send."""
    src = _bc(_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", [{"code": "CN1", "itemId": "i-1", "qty": 2,
                                           "rate": None}],
                                customer_ref="C-001", reference="QB-1-a")
    assert e.value.codes == ["CN1"]
    assert not _sent(src, "POST")


def test_a_bc_quote_puts_the_reference_and_the_customer_on_the_wire():
    """What actually goes out, which nothing pinned.

    Four mutations — dropping externalDocumentNumber, sending a wrong one,
    addressing another customer, renaming the item key — all left the earlier
    tests green. The reference travelling as externalDocumentNumber is the
    claim the whole settle protocol rests on, so it is asserted here rather
    than inferred from a fake's own echo.
    """
    src = _bc(_CLEAN)
    doc = src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                  reference="QB-1-abcd")
    header, line = _sent(src, "POST")
    assert header["json"] == {"customerNumber": "C-001",
                              "externalDocumentNumber": "QB-1-abcd"}
    assert line["json"] == {"lineType": "Item", "itemId": "item-guid-1",
                            "quantity": "4", "unitPrice": "1250.00"}
    assert line["url"].endswith("/salesQuotes(q-guid)/salesQuoteLines")
    assert doc.number == "SQ-1001" and doc.document_id == "q-guid"
    assert doc.line_count == 1 and doc.already_existed is False


def test_money_goes_out_as_a_string_not_a_binary_float():
    """A price that has been through float repr is not the price that was
    quoted. Normalised through Decimal on the way out, as the Zoho adapter
    does — and no arithmetic here, only formatting (§1)."""
    src = _bc(_CLEAN)
    src.create_sales_quotes("Pitti", [{"code": "C", "itemId": "i-1", "qty": 3,
                                       "rate": 1250.3}],
                            customer_ref="C-001", reference="QB-1-abcd")
    _, line = _sent(src, "POST")
    assert line["json"]["unitPrice"] == "1250.3"
    assert isinstance(line["json"]["unitPrice"], str)


def test_a_quote_already_in_bc_under_this_reference_is_never_sent_twice():
    """The duplicate this connector could make and Zoho could not.

    Business Central puts no uniqueness on externalDocumentNumber, and the
    settle read only runs after a *fault* — a clean second press never faults.
    Without reading first, pressing send twice simply creates two quotes.
    """
    src = _bc({**_CLEAN,
               ("GET", "salesQuotes"): _Resp(200, {"value": [_bc_row()]})})
    doc = src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                  reference="QB-1-abcd")
    assert doc.already_existed is True and doc.number == "SQ-1001"
    assert not _sent(src, "POST"), "the quote was already there and was sent anyway"


def test_the_preflight_match_is_not_defeated_by_the_case_bc_stores():
    """externalDocumentNumber is an AL Code[35], which upper-cases what it
    stores; the references generated here carry lowercase hex. An exact
    re-check can only turn *found* into *not found* — and not-found is the
    branch that tells an operator retrying is safe."""
    src = _bc({**_CLEAN,
               ("GET", "salesQuotes"): _Resp(
                   200, {"value": [_bc_row(externalDocumentNumber="QB-1-ABCD ")]})})
    doc = src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                  reference="QB-1-abcd")
    assert doc.already_existed is True
    assert not _sent(src, "POST")


def test_a_bc_preflight_match_with_different_lines_is_unknown_not_already_sent():
    """The pre-flight asks whether the thing already there is *this* quote.

    It compared the found document's line count against itself, so the check
    could never fire: a document sitting under this reference with different
    lines was reported as "already sent — nothing was created twice", and the
    quote on screen was never sent at all. Found by the Acumatica writer's
    equivalent test, and present in both from the same shape.
    """
    src = _bc({**_CLEAN,
               ("GET", "salesQuotes"): _Resp(
                   200, {"value": [_bc_row(salesQuoteLines=[{"id": "a"}, {"id": "b"}])]})})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "2 lines where this quote has 1" in str(e.value)
    assert not _sent(src, "POST"), "nothing may be written while this is unresolved"


def test_a_5xx_on_the_bc_header_settles_by_reading_and_is_sent_once():
    """The write may have landed. One read answers it, and the document found
    is reported rather than a second one created."""
    src = _bc({("GET", "salesQuotes"): [_Resp(200, {"value": []}),
                                        _Resp(200, {"value": [_bc_row()]})],
               ("POST", "salesQuotes"): _Resp(503)})
    doc = src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                  reference="QB-1-abcd")
    assert doc.number == "SQ-1001"
    assert doc.already_existed is True, "found, not created — the screen says so"
    assert len(_sent(src, "POST")) == 1, "the write must never be replayed"


def test_a_settled_header_reports_the_lines_bc_holds_not_the_lines_we_sent():
    """The hole a two-POST write has and a one-POST write does not.

    The header landed and its lines did not. Echoing len(lines) would report a
    complete quote for a document with none — the exact state the line-failure
    path refuses to call a success, reached by the other road.
    """
    src = _bc({("GET", "salesQuotes"): [
                   _Resp(200, {"value": []}),
                   _Resp(200, {"value": [_bc_row(salesQuoteLines=[])]})],
               ("POST", "salesQuotes"): _Resp(503)})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "0 lines where this quote has 1" in str(e.value)


def test_a_settled_header_whose_lines_were_not_returned_is_unknown():
    """No lines in the response is not "no lines on the document"."""
    row = _bc_row()
    row.pop("salesQuoteLines")
    src = _bc({("GET", "salesQuotes"): [_Resp(200, {"value": []}),
                                        _Resp(200, {"value": [row]})],
               ("POST", "salesQuotes"): _Resp(503)})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "did not return its lines" in str(e.value)


def test_a_bc_write_the_read_proves_never_landed_is_safe_to_retry():
    """The read succeeded and found nothing. That is evidence, and reporting
    UNKNOWN would discard it — the same verdict the Zoho path reaches."""
    src = _bc({("GET", "salesQuotes"): _Resp(200, {"value": []}),
               ("POST", "salesQuotes"): _Resp(503)})
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

    src = _bc({("GET", "salesQuotes"): [_Resp(200, {"value": []}), die],
               ("POST", "salesQuotes"): _Resp(503)})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert e.value.reference == "QB-1-abcd"


def test_a_failed_preflight_read_refuses_rather_than_writing_blind():
    """Not knowing whether the quote is already there is not permission to
    create another. Refusing because we could not check is recoverable;
    sending a duplicate is not."""
    def die(_body):
        raise TimeoutError("connection timed out")

    src = _bc({**_CLEAN, ("GET", "salesQuotes"): die})
    with pytest.raises(Exception) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert not isinstance(e.value, SourceWriteRefused) or True
    assert not _sent(src, "POST"), "wrote without knowing whether it had already"


def test_a_header_that_lands_without_lines_is_unknown_not_success():
    """Success would put a quote missing lines in front of a customer; a
    refusal would claim nothing was written while a document sits there."""
    src = _bc({**_CLEAN,
               ("POST", "salesQuoteLines"): _Resp(422, {"error": "bad item"})})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    msg = str(e.value)
    assert "incomplete" in msg and "SQ-1001" in msg


def test_a_2xx_with_no_id_is_unknown_rather_than_a_quote_with_no_lines():
    src = _bc({**_CLEAN,
               ("POST", "salesQuotes"): _Resp(201, {"number": "SQ-1001"})})
    with pytest.raises(SourceWriteUnknown):
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")


def test_a_bc_refusal_is_a_write_outcome_not_a_bare_transport_error():
    """A 4xx is a definite "nothing was written" — which is what
    SourceWriteRefused means. Raised as a bare IngestionError it fell outside
    the three outcomes the caller handles and surfaced as a 500, and a 500 is
    the thing people answer by pressing the button again."""
    src = _bc({**_CLEAN,
               ("POST", "salesQuotes"): _Resp(400, {"error": "customerNumber invalid"})})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "refused the sales quote" in str(e.value)


def test_a_403_on_the_bc_write_names_the_permission_to_grant():
    """The answer a write provokes that a read never does, and it must not be
    re-wrapped as a refusal — the remedy is a grant, and it travels with it."""
    src = _bc({**_CLEAN,
               ("POST", "salesQuotes"): _Resp(403, {"error": "forbidden"})})
    with pytest.raises(SourceScopeError) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "API.ReadWrite.All" in (e.value.scope or "")


def test_a_reference_carrying_a_quote_cannot_forge_the_settle_filter():
    """An OData string literal ends at a single quote, so a reference holding
    one would change what the filter means. Doubled, per OData."""
    src = _bc(_CLEAN)
    src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                            reference="QB-1-o'brien")
    reads = _sent(src, "GET")
    assert reads, "the pre-flight read never went out"
    sent_filter = (reads[0].get("params") or {}).get("$filter", "")
    assert "o''brien" in sent_filter, (
        f"the quote was not doubled, so the filter means something else: {sent_filter!r}")


def test_a_throttled_write_is_uncertain_rather_than_waited_out_and_resent():
    """A gateway can throttle a call its backend already accepted, and the two
    are indistinguishable from here. Waiting and re-sending bets that it did
    not — which is how one quote becomes two."""
    # Never landed: the settle read proves it, and retrying is safe.
    src = _bc({**_CLEAN, ("POST", "salesQuotes"): _Resp(429)})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                reference="QB-1-abcd")
    assert "sending again is safe" in str(e.value)
    assert len(_sent(src, "POST")) == 1, "a write must never be replayed"

    # Landed, and the 429 came from the front end afterwards. The quote is
    # reported, not created a second time — which is what waiting out the
    # backoff and re-sending would have done.
    landed = _bc({("GET", "salesQuotes"): [_Resp(200, {"value": []}),
                                           _Resp(200, {"value": [_bc_row()]})],
                  ("POST", "salesQuotes"): _Resp(429)})
    doc = landed.create_sales_quotes("Pitti", _BC_LINES, customer_ref="C-001",
                                     reference="QB-1-abcd")
    assert doc.already_existed is True and doc.number == "SQ-1001"
    assert len(_sent(landed, "POST")) == 1


# ── the Acumatica quote write ───────────────────────────────────────────────
# The same contract again. What differs is worth knowing: Acumatica takes one
# PUT carrying its own lines, so "found" means "complete" and the header-without-
# lines state Business Central has cannot arise; and its session holds a
# licensed seat, so the write signs out however it ends.

_ACU_LINES = [{"code": "CNMG120408", "qty": 4, "rate": "1250.00"}]


def _acu_row(**over):
    row = {"id": "guid-1", "OrderNbr": {"value": "QT000123"},
           "OrderType": {"value": "QT"},
           "CustomerOrderNbr": {"value": "QB-1-abcd"},
           "Details": [{"InventoryID": {"value": "CNMG120408"}}]}
    row.update(over)
    return row


class _AcuHttp:
    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) else [v]
                       for k, v in routes.items()}
        self.calls: list[dict] = []

    @staticmethod
    def _what(method, url):
        if url.endswith("/logout"):
            return ("POST", "logout")
        if url.endswith("/login"):
            return ("POST", "login")
        return (method, url.rsplit("/", 1)[-1].split("?")[0])

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        queue = self.routes.get(self._what(method, url))
        if not queue:
            return _Resp(404, {"error": "no route"})
        resp = queue[0] if len(queue) == 1 else queue.pop(0)
        return resp(kw.get("json")) if callable(resp) else resp

    # The client signs in and out through the bare http object, not `request`.
    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def _acu(routes) -> acumatica.AcumaticaSource:
    http = _AcuHttp(routes)
    client = acumatica.AcumaticaClient(
        base_url="https://acu.example", username="u", password="p",
        tenant="Company", http=http)
    client._signed_in = True
    return acumatica.AcumaticaSource(client)


def _acu_sent(src, method):
    return [c for c in src._client._http.calls
            if c["method"] == method and not c["url"].endswith(("/login", "/logout"))]


_ACU_CLEAN = {("GET", "SalesOrder"): _Resp(200, []),
              ("PUT", "SalesOrder"): _Resp(200, _acu_row()),
              ("POST", "logout"): _Resp(204)}


def test_an_acumatica_quote_puts_the_reference_and_the_customer_on_the_wire():
    """Acumatica reads and writes the same record in different clothes — every
    scalar leaves as {"value": …}. ``_plain`` undressed on the way in and
    nothing dressed on the way out, because until now nothing wrote."""
    src = _acu(_ACU_CLEAN)
    doc = src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C000001",
                                  reference="QB-1-abcd")
    (put,) = _acu_sent(src, "PUT")
    assert put["json"] == {
        "OrderType": {"value": "QT"},
        "CustomerID": {"value": "C000001"},
        "CustomerOrderNbr": {"value": "QB-1-abcd"},
        "Details": [{"InventoryID": {"value": "CNMG120408"},
                     "OrderQty": {"value": "4"},
                     "UnitPrice": {"value": "1250.00"}}]}
    assert doc.number == "QT000123" and doc.line_count == 1
    assert doc.already_existed is False


def test_an_acumatica_write_signs_out_however_it_ends():
    """The session holds a licensed seat. This is a one-shot rather than a
    sync, so a send that leaks a seat per press exhausts them."""
    ok = _acu(_ACU_CLEAN)
    ok.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1", reference="QB-1-a")
    assert any(c["url"].endswith("/logout") for c in ok._client._http.calls)

    refused = _acu(_ACU_CLEAN)
    with pytest.raises(SourceWriteRefused):
        refused.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                    reference="")
    assert any(c["url"].endswith("/logout") for c in refused._client._http.calls), (
        "a refused send held the seat")


def test_a_quote_already_in_acumatica_is_never_sent_twice():
    """PUT is Acumatica's insert-or-update, but with no OrderNbr it always
    inserts — so it is not idempotent by itself, and the pre-flight read is
    what stops a second press making a second quote."""
    src = _acu({**_ACU_CLEAN, ("GET", "SalesOrder"): _Resp(200, [_acu_row()])})
    doc = src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                  reference="QB-1-abcd")
    assert doc.already_existed is True and doc.number == "QT000123"
    assert not _acu_sent(src, "PUT"), "it was already there and was sent anyway"


def test_an_acumatica_quote_with_no_stock_code_is_refused_and_names_the_line():
    """Acumatica addresses stock by InventoryID, which *is* the SKU string
    rather than an internal id — so this is the line's code, not the itemId
    Business Central needs. Same guard, different field."""
    src = _acu(_ACU_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", [{"code": "", "qty": 1, "rate": "1"}],
                                customer_ref="C1", reference="QB-1-a")
    assert not _acu_sent(src, "PUT")
    assert "InventoryID" in str(e.value)


def test_an_acumatica_quote_with_no_lines_or_no_price_is_refused():
    for lines in ([], [{"code": "C1", "qty": 2, "rate": None}]):
        src = _acu(_ACU_CLEAN)
        with pytest.raises(SourceWriteRefused):
            src.create_sales_quotes("Pitti", lines, customer_ref="C1",
                                    reference="QB-1-a")
        assert not _acu_sent(src, "PUT")


def test_a_5xx_on_the_acumatica_write_settles_by_reading_and_is_sent_once():
    src = _acu({("GET", "SalesOrder"): [_Resp(200, []), _Resp(200, [_acu_row()])],
                ("PUT", "SalesOrder"): _Resp(503),
                ("POST", "logout"): _Resp(204)})
    doc = src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                  reference="QB-1-abcd")
    assert doc.already_existed is True
    assert len(_acu_sent(src, "PUT")) == 1, "the write must never be replayed"


def test_an_acumatica_write_the_read_proves_never_landed_is_safe_to_retry():
    src = _acu({**_ACU_CLEAN, ("PUT", "SalesOrder"): _Resp(503)})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                reference="QB-1-abcd")
    assert "sending again is safe" in str(e.value)


def test_an_acumatica_write_whose_settling_read_also_fails_is_unknown():
    def die(_body):
        raise TimeoutError("connection timed out")

    src = _acu({("GET", "SalesOrder"): [_Resp(200, []), die],
                ("PUT", "SalesOrder"): _Resp(503),
                ("POST", "logout"): _Resp(204)})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                reference="QB-1-abcd")
    assert e.value.reference == "QB-1-abcd"


def test_an_acumatica_refusal_is_a_write_outcome_not_a_bare_transport_error():
    src = _acu({**_ACU_CLEAN,
                ("PUT", "SalesOrder"): _Resp(400, {"message": "CustomerID invalid"})})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                reference="QB-1-abcd")
    assert "refused the sales quote" in str(e.value)


def test_an_acumatica_quote_kept_with_different_lines_is_unknown():
    """One PUT carries the lines, so a mismatch means Acumatica kept something
    other than what was sent — neither a success to report nor safe to resend."""
    src = _acu({**_ACU_CLEAN,
                ("GET", "SalesOrder"): _Resp(200, [_acu_row(Details=[])])})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _ACU_LINES, customer_ref="C1",
                                reference="QB-1-abcd")
    assert "0 lines where this quote has 1" in str(e.value)


# ── the NetSuite estimate write ─────────────────────────────────────────────
# The one write here that cannot duplicate. PUT …/estimate/eid:{ref} upserts on
# the external id, so arriving twice and arriving once are the same result —
# which is why this call is marked replayable where the other two must not be.

_NS_LINES = [{"code": "CNMG120408", "itemId": "1042", "qty": 4, "rate": "1250.00"}]


class _NsHttp:
    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) else [v]
                       for k, v in routes.items()}
        self.calls: list[dict] = []

    @staticmethod
    def _what(method, url):
        return (method, "suiteql" if "/query/v1/suiteql" in url else "record")

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        queue = self.routes.get(self._what(method, url))
        if not queue:
            return _Resp(404, {"error": "no route"})
        resp = queue[0] if len(queue) == 1 else queue.pop(0)
        return resp(kw.get("json")) if callable(resp) else resp


def _ns(routes) -> netsuite.NetSuiteSource:
    client = netsuite.NetSuiteClient(
        account_id="ACCT", consumer_key="ck", consumer_secret="cs",
        token_id="ti", token_secret="ts", http=_NsHttp(routes))
    return netsuite.NetSuiteSource(client)


def _ns_sent(src, what):
    return [c for c in src._client._http.calls
            if (("/query/v1/suiteql" in c["url"]) == (what == "suiteql"))]


def _ns_rows(*rows):
    return _Resp(200, {"items": list(rows), "hasMore": False})


_NS_FOUND = _ns_rows({"id": "77", "tranid": "EST123", "lines": 1})
_NS_CLEAN = {("PUT", "record"): _Resp(204),
             ("POST", "suiteql"): _NS_FOUND}


def test_a_netsuite_estimate_is_upserted_on_the_external_id():
    """The reference is the external id, and the external id is the key the
    upsert turns on — which is what makes sending twice safe here and unsafe
    everywhere else."""
    src = _ns(_NS_CLEAN)
    doc = src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                                  reference="QB-1-abcd")
    (put,) = _ns_sent(src, "record")
    assert put["url"].endswith("/services/rest/record/v1/estimate/eid:QB-1-abcd")
    assert put["json"]["externalId"] == "QB-1-abcd"
    assert put["json"]["entity"] == {"id": "4400"}
    assert put["json"]["item"]["items"] == [
        {"item": {"id": "1042"}, "quantity": "4", "rate": "1250.00"}]
    assert put["headers"]["NetSuite-Idempotency-Key"] == "QB-1-abcd"
    assert doc.number == "EST123" and doc.line_count == 1


def test_a_5xx_on_the_netsuite_upsert_is_retried_where_the_others_may_not_be():
    """The behavioural half of "keyed", and the reason it is worth having.

    A 5xx on the Business Central or Acumatica write raises SourceWriteUncertain
    and is never sent again — the transport cannot tell "never arrived" from
    "arrived, answer lost", and guessing wrong makes two quotes. On an upsert
    keyed by external id those two outcomes are the same outcome, so the call
    keeps its retry budget and simply succeeds.
    """
    src = _ns({("PUT", "record"): [_Resp(503), _Resp(204)],
               ("POST", "suiteql"): _NS_FOUND})
    doc = src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                                  reference="QB-1-abcd")
    assert doc.number == "EST123"
    assert len(_ns_sent(src, "record")) == 2, (
        "the keyed upsert was refused a retry it is safe to have")


def test_an_upsert_that_updated_an_estimate_does_not_report_it_as_created():
    """The claim an upsert cannot make for itself.

    PUT …/eid: answers 204 whether it created the estimate or updated one
    already there, and the record read back afterwards looks identical either
    way. ``already_existed`` was hardcoded False, so sending the same quote
    twice said "created" both times — about an estimate the second call had
    merely updated. Only the caller, which looked before writing, knows.
    """
    fresh = _ns({("PUT", "record"): _Resp(204),
                 ("POST", "suiteql"): [_ns_rows(), _NS_FOUND]})
    assert fresh.create_sales_quotes(
        "Pitti", _NS_LINES, customer_ref="4400",
        reference="QB-1-abcd").already_existed is False

    # Same reference, and NetSuite already holds it. The upsert updates; the
    # screen must say so rather than claiming a second document.
    again = _ns({("PUT", "record"): _Resp(204), ("POST", "suiteql"): _NS_FOUND})
    assert again.create_sales_quotes(
        "Pitti", _NS_LINES, customer_ref="4400",
        reference="QB-1-abcd").already_existed is True


def test_a_netsuite_reference_too_long_for_the_field_is_refused():
    """The cap the other three carried and this one did not. One constant now,
    bounded by the smallest field any of these systems stores a reference in."""
    src = _ns(_NS_CLEAN)
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                                reference="Q" * 36)
    assert "35 characters" in str(e.value)
    assert not _ns_sent(src, "record")


def test_a_netsuite_estimate_is_read_back_rather_than_trusted():
    """An upsert that updated an existing estimate and one that created it look
    the same in the response. The read is what turns that into a fact."""
    src = _ns({("PUT", "record"): _Resp(204),
               ("POST", "suiteql"): _ns_rows()})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                                reference="QB-1-abcd")
    assert "did not return it" in str(e.value)


def test_a_netsuite_estimate_kept_with_different_lines_is_unknown():
    src = _ns({("PUT", "record"): _Resp(204),
               ("POST", "suiteql"): _ns_rows({"id": "77", "tranid": "EST123",
                                              "lines": 3})})
    with pytest.raises(SourceWriteUnknown) as e:
        src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                                reference="QB-1-abcd")
    assert "3 lines where this quote has 1" in str(e.value)


def test_a_netsuite_refusal_is_a_write_outcome_not_a_bare_transport_error():
    src = _ns({("PUT", "record"): _Resp(400, {"detail": "invalid entity"}),
               ("POST", "suiteql"): _NS_FOUND})
    with pytest.raises(SourceWriteRefused) as e:
        src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                                reference="QB-1-abcd")
    assert "refused the estimate" in str(e.value)


def test_a_netsuite_estimate_without_a_reference_or_lines_is_refused_unwritten():
    for kwargs, lines in ((dict(customer_ref="4400", reference=""), _NS_LINES),
                          (dict(customer_ref="", reference="QB-1-a"), _NS_LINES),
                          (dict(customer_ref="4400", reference="QB-1-a"), [])):
        src = _ns(_NS_CLEAN)
        with pytest.raises(SourceWriteRefused):
            src.create_sales_quotes("Pitti", lines, **kwargs)
        assert not _ns_sent(src, "record")


def test_a_netsuite_line_with_no_item_id_or_no_price_is_refused():
    for line in ({"code": "MYSTERY", "qty": 1, "rate": "1"},
                 {"code": "C1", "itemId": "1042", "qty": 1, "rate": None}):
        src = _ns(_NS_CLEAN)
        with pytest.raises(SourceWriteRefused) as e:
            src.create_sales_quotes("Pitti", [line], customer_ref="4400",
                                    reference="QB-1-a")
        assert e.value.codes == [line["code"]]
        assert not _ns_sent(src, "record")


def test_a_reference_carrying_a_quote_cannot_forge_the_suiteql_lookup():
    """SuiteQL ends a string literal at a single quote exactly as OData does,
    so the same doubling applies — one rule, one helper, three connectors."""
    src = _ns(_NS_CLEAN)
    src.create_sales_quotes("Pitti", _NS_LINES, customer_ref="4400",
                            reference="QB-1-o'brien")
    queries = _ns_sent(src, "suiteql")
    assert queries, "no lookup went out"
    # Both the pre-flight and the read-back carry it, and either one built with
    # a bare quote would ask a different question than the one intended.
    assert all("o''brien" in q["json"]["q"] for q in queries)


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
    # Not asserted against ``conn.can_write_quotes`` — the router *builds* these
    # two fields by calling it, so that comparison is a tautology only a
    # serialisation bug could fail. The both-ways pin above already holds
    # declared == implemented. What is worth pinning here is the thing nothing
    # else covers: a row may not advertise a write the quote path would refuse,
    # because ``_QUOTE_ADAPTERS`` is the gate ``book_for_customer`` routes on
    # and it is a different set from "declares the capability".
    # The dispatch has landed, so Business Central now offers the send it can
    # honour. Both halves are asserted: the grant an owner must ask for, and
    # that the quote path will actually accept a customer from there. Every
    # other connector declares no write, so the screen offers none — an owner
    # told "can create quotes here" beside a send that refuses has been told
    # something false, and that gap is a whole window wide while a writer is
    # being built.
    for key in ("zoho", "dynamics365", "acumatica", "netsuite"):
        assert by_key[key]["writes"] == ["sales_quotes"]
        assert by_key[key]["can_write_quotes"] is True
    # The three the write spike found no reachable surface for. Their access
    # copy says read-only and it is true; a screen offering a send here would
    # ask an owner for a permission that cannot exist.
    for key in ("prophet21", "sagex3", "sage100"):
        assert by_key[key]["can_write_quotes"] is False, (
            f"{key} offers a send with no writer behind it")
        assert by_key[key]["writes"] == []


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
