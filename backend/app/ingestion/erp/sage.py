"""Sage X3 and Sage 100, read through each product's SData service.

Two clients in one module because the two Sages share an auth story (HTTP
Basic as a dedicated integration user against an SData URL) and a market —
but not a wire format, so nothing else is shared:

* **Sage X3** serves JSON (``$resources`` arrays) from its Syracuse server.
  Listings are cheap; a document's lines need one ``$details`` call per
  record, so the sync's ``skip`` predicate is honoured exactly the way the
  Zoho client honours it — an unchanged document costs no detail call.
* **Sage 100** serves Atom XML from its Sage 100 SData provider
  (``MasApp/MasContract``). Records are parsed with the standard library —
  no new dependency — by local name, so the provider's namespace revisions
  do not break the read.

**What Sage 100 does not give this platform, said in the open:** its AP
invoice history records GL distributions, not item lines, so purchase costs
are not read and margin stays UNKNOWN for a Sage 100 book — the screens
already say so rather than estimate. Sales, customers, items and orders all
flow. X3 has purchase invoices with item lines, so an X3 book gets costs.

X3 line collections are *found*, not assumed: the ``$details`` payload nests
its grid under a block whose name varies by representation, so the translator
takes the first list-valued property whose rows name an item (``ITMREF``).
A representation with no such block yields a document with no lines, which
the sync reports per document instead of averaging around.
"""
from __future__ import annotations

import base64
from datetime import date
from typing import Any, Iterable, Iterator, Optional
import xml.etree.ElementTree as ET

from ..errors import SourceAuthError
from ..source import SkipPredicate
from .base import (ConnectorSpec, CredentialMaterial, DocumentTally, Field,
                   first, group_lines, in_window, iso_date, register)
from .transport import RestTransport

X3_SYSTEM = "sagex3"
SAGE100_SYSTEM = "sage100"

_PAGE = 200


def _basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _unwrap(value: Any) -> Any:
    """SData JSON sometimes dresses a scalar as ``{"$value": …}``."""
    if isinstance(value, dict) and "$value" in value:
        return value["$value"]
    return value


def _plain(record: dict[str, Any]) -> dict[str, Any]:
    return {k: _unwrap(v) for k, v in record.items()
            if not k.startswith("$")}


def field_of(record: dict[str, Any], *names: str) -> Any:
    """An X3 property under any of its dresses: bare, ``_0``-suffixed, or
    ``$value``-wrapped — Syracuse emits all three across versions."""
    for name in names:
        for key in (name, f"{name}_0"):
            if key in record:
                v = _unwrap(record[key])
                if v not in (None, ""):
                    return v
    return None


# ═════════════════════════════════════ Sage X3 ═══════════════════════════════
class SageX3Client(RestTransport):
    """Basic-auth SData JSON against one X3 folder (endpoint)."""

    system = "Sage X3"
    requests_per_minute = 60

    def __init__(self, *, base_url: str, username: str, password: str,
                 endpoint: str, http: Any = None) -> None:
        super().__init__(http=http)
        self._base = base_url.rstrip("/")
        self._endpoint = endpoint.strip().strip("/")
        self._headers = _basic(username, password)

    def _auth_headers(self) -> dict[str, str]:
        return dict(self._headers)

    def _url(self, entity: str, key: str = "") -> str:
        suffix = f"('{key}')" if key else ""
        return (f"{self._base}/sdata/x3/erp/{self._endpoint}/"
                f"{entity}{suffix}")

    def query(self, entity: str) -> Iterator[dict[str, Any]]:
        """Every record of one entity's ``$query`` representation."""
        start = 1
        while True:
            body = self.request(
                "GET", self._url(entity),
                params={"representation": f"{entity}.$query",
                        "count": _PAGE, "startIndex": start})
            rows = body.get("$resources") if isinstance(body, dict) else None
            rows = rows if isinstance(rows, list) else []
            yield from (_plain(r) for r in rows if isinstance(r, dict))
            if len(rows) < _PAGE:
                return
            start += _PAGE

    def details(self, entity: str, key: str) -> Optional[dict[str, Any]]:
        """One record in full — the call the skip predicate exists to spare."""
        body = self.request(
            "GET", self._url(entity, key),
            params={"representation": f"{entity}.$details"})
        return body if isinstance(body, dict) else None

    def ping(self) -> dict[str, Any]:
        next(self.query("BPCUSTOMER"), None)
        return {"authenticated": True, "organization_found": True,
                "organization_name": self._endpoint}


def x3_lines(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The item-line grid of one ``$details`` payload, found by shape."""
    for value in record.values():
        if not (isinstance(value, list) and value
                and isinstance(value[0], dict)):
            continue
        rows = [_plain(r) for r in value if isinstance(r, dict)]
        if any(field_of(r, "ITMREF") for r in rows):
            return rows
    return []


def x3_translate_document(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """One X3 invoice (sales or purchase) → a canonical document."""
    doc = _plain(record)
    number = str(field_of(doc, "NUM") or "")
    out: dict[str, Any] = {
        f"{kind}_id": number,
        f"{kind}_number": number or None,
        ("customer_id" if kind == "invoice" else "vendor_id"):
            (str(field_of(doc, "BPR", "BPCINV", "BPCORD", "BPSINV"))
             if field_of(doc, "BPR", "BPCINV", "BPCORD", "BPSINV") else None),
        "date": iso_date(field_of(doc, "ACCDAT", "INVDAT", "BPRDAT")),
        "due_date": iso_date(field_of(doc, "DUDDAT")),
        "status": "open",
        "total": field_of(doc, "AMTNOT", "AMTNOTL"),
        "currency_code": (str(field_of(doc, "CUR")).upper()
                          if field_of(doc, "CUR") else None),
        "last_modified_time": str(iso_date(field_of(doc, "UPDDAT")) or ""),
        "line_items": [],
    }
    for i, ln in enumerate(x3_lines(record)):
        item = field_of(ln, "ITMREF")
        if not item:
            continue
        out["line_items"].append({
            "line_item_id": str(field_of(ln, "SIDLIN", "PIDLIN", "LIN") or i),
            "item_id": str(item),
            "quantity": field_of(ln, "QTY", "SQTY", "QTYSTU"),
            "rate": field_of(ln, "NETPRI", "GROPRI", "PRI"),
            "item_total": field_of(ln, "AMTNOTLIN", "LINAMT"),
            "name": str(field_of(ln, "ITMDES", "ITMDES1") or "") or None,
            "sku": str(item),
        })
    return out


class SageX3Source:
    """The read source for one X3 folder."""

    #: Entity → the canonical kind its documents map to.
    _SALES_INVOICE = "SINVOICE"
    _PURCHASE_INVOICE = "PINVOICE"

    def __init__(self, client: SageX3Client, *,
                 since: Optional[date] = None,
                 until: Optional[date] = None) -> None:
        self._client = client
        self._since = since
        self._until = until
        self._tally = DocumentTally()

    @property
    def listed(self) -> dict[str, set[str]]:
        return self._tally.listed

    @property
    def listing_complete(self) -> set[str]:
        return self._tally.listing_complete

    @property
    def documents_fetched(self) -> int:
        return self._tally.documents_fetched

    @property
    def documents_resumed(self) -> int:
        return self._tally.documents_resumed

    def ping(self) -> dict[str, Any]:
        return self._client.ping()

    def _documents(self, entity: str, kind: str,
                   skip: Optional[SkipPredicate]) -> Iterator[dict[str, Any]]:
        for summary in self._client.query(entity):
            number = str(field_of(summary, "NUM") or "")
            if not number:
                continue
            when = iso_date(field_of(summary, "ACCDAT", "INVDAT", "BPRDAT"))
            if not in_window(when, self._since, self._until):
                continue
            self._tally.saw(kind, number)
            modified = str(iso_date(field_of(summary, "UPDDAT")) or "")
            if skip is not None and skip(number, modified):
                self._tally.documents_resumed += 1
                continue
            record = self._client.details(entity, number)
            if record is None:
                continue
            self._tally.documents_fetched += 1
            yield x3_translate_document(record, kind=kind)
        self._tally.complete(kind)

    # ── the source protocol ──────────────────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        for row in self._client.query("BPCUSTOMER"):
            yield {
                "contact_id": str(field_of(row, "BPCNUM") or ""),
                "contact_name": str(field_of(row, "BPCNAM") or ""),
                "status": ("inactive"
                           if str(field_of(row, "BPCSTA") or "") == "2"
                           else "active"),
            }

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        for row in self._client.query("BPSUPPLIER"):
            yield {
                "contact_id": str(field_of(row, "BPSNUM") or ""),
                "contact_name": str(field_of(row, "BPSNAM") or ""),
                "status": "active",
            }

    def list_items(self) -> Iterable[dict[str, Any]]:
        for row in self._client.query("ITMMASTER"):
            ref = str(field_of(row, "ITMREF") or "")
            yield {
                "item_id": ref,
                "name": str(field_of(row, "ITMDES1", "ITMDES") or ref),
                "sku": ref or None,
                "unit": (str(field_of(row, "STU"))
                         if field_of(row, "STU") else None),
                "status": ("inactive"
                           if str(field_of(row, "ITMSTA") or "1") not in ("1", "2")
                           else "active"),
            }

    def list_invoices(self, skip: Optional[SkipPredicate] = None
                      ) -> Iterable[dict[str, Any]]:
        return self._documents(self._SALES_INVOICE, "invoice", skip)

    def list_bills(self, skip: Optional[SkipPredicate] = None
                   ) -> Iterable[dict[str, Any]]:
        return self._documents(self._PURCHASE_INVOICE, "bill", skip)

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        for row in self._client.query("SORDER"):
            when = iso_date(field_of(row, "ORDDAT"))
            if not in_window(when, self._since, self._until):
                continue
            number = str(field_of(row, "NUM") or "")
            yield {
                "salesorder_id": number,
                "salesorder_number": number or None,
                "customer_id": (str(field_of(row, "BPCORD"))
                                if field_of(row, "BPCORD") else None),
                "date": when,
                "shipment_date": iso_date(field_of(row, "SHIDAT", "DEMDLVDAT")),
                "status": str(field_of(row, "ORDSTA") or "open"),
                "total": field_of(row, "ORDNOT", "AMTNOT"),
            }

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        for row in self._client.query("PORDER"):
            when = iso_date(field_of(row, "ORDDAT"))
            if not in_window(when, self._since, self._until):
                continue
            number = str(field_of(row, "NUM") or "")
            yield {
                "purchaseorder_id": number,
                "purchaseorder_number": number or None,
                "vendor_id": (str(field_of(row, "BPSORD", "BPSNUM"))
                              if field_of(row, "BPSORD", "BPSNUM") else None),
                "date": when,
                "expected_delivery_date":
                    iso_date(field_of(row, "RCPDAT", "EXTRCPDAT")) or "",
                "status": str(field_of(row, "ORDSTA") or "open"),
                "receives": [],
            }


def _x3_build_source(material: CredentialMaterial,
                     since: Optional[date] = None) -> SageX3Source:
    client = SageX3Client(
        base_url=material.value("base_url"),
        username=material.value("username"),
        password=material.value("password"),
        endpoint=material.external_org_id or material.value("endpoint"),
    )
    return SageX3Source(client, since=since)


register(ConnectorSpec(
    key=X3_SYSTEM,
    label="Sage X3",
    company_term="folder",
    credential_fields=(
        Field("base_url", "Syracuse URL",
              placeholder="https://x3.example.com:8124",
              help="The Sage X3 web (Syracuse) server address."),
        Field("username", "API username",
              help="A dedicated integration user with SData/web-service "
                   "rights."),
        Field("password", "Password", secret=True),
    ),
    connection_fields=(
        Field("endpoint", "Folder (endpoint)",
              placeholder="PROD",
              help="The X3 folder this company's books live in — the "
                   "endpoint name in Syracuse."),
    ),
    external_id_field="endpoint",
    setup_note=(
        "Reads Sage X3 through its SData REST service as a dedicated "
        "integration user: customers, suppliers, the item master, sales and "
        "purchase invoices with lines, and orders. Unchanged documents are "
        "skipped on re-sync using X3's own update stamps."),
    build_source=_x3_build_source,
))


# ════════════════════════════════════ Sage 100 ═══════════════════════════════
class Sage100Client(RestTransport):
    """Basic-auth SData Atom feed against one Sage 100 company."""

    system = "Sage 100"
    requests_per_minute = 60

    def __init__(self, *, base_url: str, username: str, password: str,
                 company: str, http: Any = None) -> None:
        super().__init__(http=http)
        self._base = base_url.rstrip("/")
        self._company = company.strip()
        self._headers = _basic(username, password)

    def _auth_headers(self) -> dict[str, str]:
        return dict(self._headers)

    def resources(self, name: str) -> Iterator[dict[str, str]]:
        """Every record of one exposed resource, as flat localname→text."""
        start = 1
        while True:
            resp = self.request(
                "GET",
                f"{self._base}/sdata/MasApp/MasContract/{self._company}/{name}",
                params={"startIndex": start, "count": _PAGE},
                expect_json=False)
            entries = list(_atom_records(resp.text, resp.status_code, name))
            yield from entries
            if len(entries) < _PAGE:
                return
            start += _PAGE

    def ping(self) -> dict[str, Any]:
        next(self.resources("AR_Customer"), None)
        return {"authenticated": True, "organization_found": True,
                "organization_name": self._company}


def _atom_records(text: str, status: int, name: str) -> Iterator[dict[str, str]]:
    """Atom entries → flat records, namespace-blind.

    Each ``<entry>`` carries an ``<sdata:payload>`` whose first child element
    is the record; that element's children are the columns. Matching on local
    names keeps a provider's namespace revision from reading as an empty book.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise SourceAuthError(
            f"Sage 100 returned something other than an SData feed for "
            f"{name} (HTTP {status}) — usually a sign-in page, which means "
            "the URL, company code or credentials are wrong.")

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    for entry in root.iter():
        if local(entry.tag) != "entry":
            continue
        payload = next((el for el in entry.iter()
                        if local(el.tag) == "payload"), None)
        if payload is None or not len(payload):
            continue
        record = payload[0]
        yield {local(col.tag): (col.text or "").strip() for col in record}


def _sage100_party_id(row: dict[str, str], division_key: str,
                      number_key: str) -> str:
    """Sage 100 keys parties on (division, number); the pair is the identity."""
    division = row.get(division_key) or "00"
    return f"{division}-{row.get(number_key) or ''}".strip("-")


def sage100_translate_invoice(header: dict[str, str],
                              lines: list[dict[str, str]]) -> dict[str, Any]:
    number = header.get("InvoiceNo") or ""
    # History carries one row per (InvoiceNo, HeaderSeqNo); the pair is the id.
    doc_id = f"{number}:{header.get('HeaderSeqNo') or '0'}"
    return {
        "invoice_id": doc_id,
        "invoice_number": number or None,
        "customer_id": _sage100_party_id(header, "ARDivisionNo", "CustomerNo")
                       or None,
        "customer_name": header.get("BillToName") or "",
        "date": iso_date(header.get("InvoiceDate")),
        "due_date": iso_date(header.get("InvoiceDueDate")),
        "status": "open",
        # Present on some history builds and absent on others; absent stays
        # absent rather than being summed up from tax components.
        "total": header.get("InvoiceTotal") or None,
        "balance": header.get("Balance") or None,
        "last_modified_time": str(
            iso_date(first(header, "DateUpdated", "TransactionDate")) or ""),
        "line_items": [
            {
                "line_item_id": ln.get("DetailSeqNo") or str(i),
                "item_id": ln.get("ItemCode") or "",
                "quantity": ln.get("QuantityShipped"),
                "rate": ln.get("UnitPrice"),
                "item_total": ln.get("ExtensionAmt"),
                "name": ln.get("ItemCodeDesc") or None,
                "sku": ln.get("ItemCode") or None,
            }
            for i, ln in enumerate(lines)
            if ln.get("ItemCode")
            and ln.get("QuantityShipped") not in (None, "")
        ],
    }


class Sage100Source:
    """The read source for one Sage 100 company.

    All window filtering is client-side: SData's ``where`` grammar exists but
    varies enough by provider build that a filter silently matching nothing
    would read as an empty book. Sage 100 histories are modest; the honest
    full listing costs minutes, not hours.
    """

    def __init__(self, client: Sage100Client, *,
                 since: Optional[date] = None,
                 until: Optional[date] = None) -> None:
        self._client = client
        self._since = since
        self._until = until
        self._tally = DocumentTally()

    @property
    def listed(self) -> dict[str, set[str]]:
        return self._tally.listed

    @property
    def listing_complete(self) -> set[str]:
        return self._tally.listing_complete

    @property
    def documents_fetched(self) -> int:
        return self._tally.documents_fetched

    def ping(self) -> dict[str, Any]:
        return self._client.ping()

    # ── the source protocol ──────────────────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        for row in self._client.resources("AR_Customer"):
            yield {
                "contact_id": _sage100_party_id(row, "ARDivisionNo",
                                                "CustomerNo"),
                "contact_name": row.get("CustomerName") or "",
                "status": ("inactive"
                           if (row.get("InactiveCustomer") or "N").upper() == "Y"
                           else "active"),
            }

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        for row in self._client.resources("AP_Vendor"):
            yield {
                "contact_id": _sage100_party_id(row, "APDivisionNo", "VendorNo"),
                "contact_name": row.get("VendorName") or "",
                "status": "active",
            }

    def list_items(self) -> Iterable[dict[str, Any]]:
        for row in self._client.resources("CI_Item"):
            code = row.get("ItemCode") or ""
            product_type = (row.get("ProductType") or "").upper()
            yield {
                "item_id": code,
                "name": row.get("ItemCodeDesc") or code,
                "sku": code or None,
                "unit": row.get("StandardUnitOfMeasure") or None,
                "category_name": row.get("ProductLine") or None,
                "status": ("inactive"
                           if (row.get("InactiveItem") or "N").upper() == "Y"
                           else "active"),
                # ProductType: 1/F finished goods … 4/D discontinued; only a
                # miscellaneous/comment item has no shelf.
                "item_type": ("service" if product_type in ("M", "K", "5")
                              else "inventory"),
            }

    def list_invoices(self, skip: Optional[SkipPredicate] = None
                      ) -> Iterable[dict[str, Any]]:
        headers = list(self._client.resources("AR_InvoiceHistoryHeader"))
        lines = group_lines(
            self._client.resources("AR_InvoiceHistoryDetail"), "InvoiceNo")
        for header in headers:
            payload = sage100_translate_invoice(
                header, lines.get(header.get("InvoiceNo") or "", []))
            if not in_window(payload.get("date"), self._since, self._until):
                continue
            self._tally.saw("invoice", payload["invoice_id"])
            self._tally.documents_fetched += 1
            yield payload
        self._tally.complete("invoice")

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        for row in self._client.resources("SO_SalesOrderHeader"):
            when = iso_date(row.get("OrderDate"))
            if not in_window(when, self._since, self._until):
                continue
            number = row.get("SalesOrderNo") or ""
            yield {
                "salesorder_id": number,
                "salesorder_number": number or None,
                "customer_id": _sage100_party_id(row, "ARDivisionNo",
                                                 "CustomerNo") or None,
                "date": when,
                "shipment_date": iso_date(row.get("ShipExpireDate")),
                "status": row.get("OrderStatus") or "open",
            }

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        for row in self._client.resources("PO_PurchaseOrderHeader"):
            when = iso_date(first(row, "PurchaseOrderDate", "OrderDate"))
            if not in_window(when, self._since, self._until):
                continue
            number = row.get("PurchaseOrderNo") or ""
            yield {
                "purchaseorder_id": number,
                "purchaseorder_number": number or None,
                "vendor_id": _sage100_party_id(row, "APDivisionNo", "VendorNo")
                             or None,
                "date": when,
                "expected_delivery_date":
                    iso_date(row.get("RequiredExpireDate")) or "",
                "status": row.get("OrderStatus") or "open",
                "receives": [],
            }


def _sage100_build_source(material: CredentialMaterial,
                          since: Optional[date] = None) -> Sage100Source:
    client = Sage100Client(
        base_url=material.value("base_url"),
        username=material.value("username"),
        password=material.value("password"),
        company=material.external_org_id or material.value("company"),
    )
    return Sage100Source(client, since=since)


register(ConnectorSpec(
    key=SAGE100_SYSTEM,
    label="Sage 100",
    company_term="company",
    credential_fields=(
        Field("base_url", "SData URL",
              placeholder="https://sage100.example.com:5493",
              help="The Sage 100 SData provider address (the server the "
                   "eBusiness/SData service runs on)."),
        Field("username", "Username",
              help="A Sage 100 user with SData access to the modules being "
                   "read."),
        Field("password", "Password", secret=True),
    ),
    connection_fields=(
        Field("company", "Company code", placeholder="ABC",
              help="The three-character Sage 100 company code."),
    ),
    external_id_field="company",
    setup_note=(
        "Reads Sage 100 through its SData feed: customers, vendors, the item "
        "master, invoice history with lines, and open orders. Sage 100's AP "
        "history carries GL distributions rather than item lines, so "
        "purchase costs are not read — margin stays unknown for this book "
        "and the screens say so rather than estimate."),
    build_source=_sage100_build_source,
))
