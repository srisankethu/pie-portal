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
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.domain import models
from app.domain.origin import CONNECTORS
from app.ingestion import erp
from app.ingestion import connections as conn
from app.ingestion.erp import acumatica, dynamics365, netsuite, prophet21, sage
from app.ingestion.erp.base import iso_date
from app.ingestion.errors import (SourceAuthError, SourceScopeError,
                                  SourceThrottleError)
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


# ── the connect service ─────────────────────────────────────────────────────
@pytest.fixture()
def db():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
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
    assert stored and "cs" not in stored and "ts" not in stored
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
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
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
    assert set(by_key) == {"netsuite", "dynamics365", "acumatica",
                           "prophet21", "sagex3", "sage100"}
    ns = by_key["netsuite"]
    secret_flags = {f["name"]: f["secret"] for f in ns["credential_fields"]}
    assert secret_flags["consumer_secret"] and secret_flags["token_secret"]
    assert by_key["dynamics365"]["can_discover"] is True


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
