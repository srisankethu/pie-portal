"""Epicor Prophet 21, read through its OData service.

P21 is *the* distribution ERP in the US cutting-tool world, which makes it the
connector a US client list most needs. Its API surface is the middleware's
token sign-in (``/api/security/token`` with the API user's name and password)
plus an OData service that exposes P21's own tables as views — ``customer``,
``supplier``, ``inv_mast``, ``invoice_hdr``/``invoice_line``, ``oe_hdr``,
``po_hdr``, ``apinv_hdr``/``apinv_line`` — under names whose *prefix and root
path vary by version and site configuration*. Both are therefore connection
configuration with the common defaults, not constants: a site whose views live
elsewhere enters its paths instead of waiting for a code change.

Two honesty notes, in the open rather than in a footnote:

* **Receivable balance is derived**, as ``total_amount − amount_paid`` — the
  same formula P21's own aging reports use — because ``invoice_hdr`` states
  those two and not a balance. Stated fields only; absent either, the balance
  is absent.
* **AP invoice lines are read where the site records them.** P21 commonly
  books AP against PO receipts rather than item lines, so ``apinv_line`` rows
  without an item are payable detail this platform cannot cost — such bills
  land as payables with no cost lines, reported per bill on the sync screen
  rather than silently averaged around.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Iterable, Iterator, Optional

from ..errors import SourceAuthError
from ..source import SkipPredicate
from .base import (ConnectorSpec, CredentialMaterial, DocumentTally, Field,
                   Permission, first, group_lines, in_window, iso_date,
                   register)
from .transport import RestTransport

SYSTEM = "prophet21"

_PAGE = 500


def _flag(value: Any) -> bool:
    """P21 spells booleans 'Y'/'N'."""
    return str(value or "").strip().upper() == "Y"


class Prophet21Client(RestTransport):
    """Token-bearing OData against one P21 middleware."""

    system = "Prophet 21"
    requests_per_minute = 60

    def __init__(self, *, base_url: str, username: str, password: str,
                 odata_root: str = "odataservice/odata/view",
                 view_prefix: str = "p21_view_",
                 http: Any = None) -> None:
        super().__init__(http=http)
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._root = odata_root.strip("/")
        self._prefix = view_prefix.strip()
        self._token: Optional[str] = None
        self._token_born = 0.0

    def _auth_headers(self) -> dict[str, str]:
        # P21 tokens default to a multi-hour life; re-mint hourly to be safe.
        if not self._token or time.time() - self._token_born > 3300:
            resp = self._client().post(
                f"{self._base}/api/security/token/",
                headers={"username": self._username,
                         "password": self._password})
            try:
                body = resp.json() if resp.status_code == 200 else {}
            except ValueError:
                body = {}
            token = body.get("AccessToken") or body.get("access_token")
            if not token:
                raise SourceAuthError(
                    "Prophet 21 refused the sign-in (HTTP "
                    f"{resp.status_code}). The username and password must "
                    "belong to an API-enabled P21 user, and the URL must be "
                    "the middleware server's address.")
            self._token = str(token)
            self._token_born = time.time()
        return {"Authorization": f"Bearer {self._token}"}

    def _invalidate_auth(self) -> None:
        self._token = None

    def view(self, name: str, *, select: str = "",
             filter_: str = "") -> Iterator[dict[str, Any]]:
        """Every row of one exposed view, paged with $top/$skip."""
        url = f"{self._base}/{self._root}/{self._prefix}{name}"
        skip = 0
        while True:
            params: dict[str, Any] = {"$top": _PAGE, "$skip": skip or None}
            if select:
                params["$select"] = select
            if filter_:
                params["$filter"] = filter_
            body = self.request("GET", url, params=params)
            rows = body.get("value") if isinstance(body, dict) else None
            rows = rows if isinstance(rows, list) else []
            yield from rows
            if len(rows) < _PAGE:
                return
            skip += _PAGE

    def ping(self) -> dict[str, Any]:
        next(self.view("customer"), None)
        return {"authenticated": True, "organization_found": True}


# ── translators: P21 view rows → canonical payloads ──────────────────────────
def translate_customer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact_id": str(first(row, "customer_id", "customer_no") or ""),
        "contact_name": str(row.get("customer_name") or ""),
        "status": "inactive" if _flag(row.get("delete_flag")) else "active",
    }


def translate_supplier(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact_id": str(first(row, "supplier_id", "supplier_no") or ""),
        "contact_name": str(row.get("supplier_name") or ""),
        "status": "inactive" if _flag(row.get("delete_flag")) else "active",
    }


def translate_item(row: dict[str, Any]) -> dict[str, Any]:
    inactive = _flag(row.get("delete_flag")) or _flag(row.get("inactive"))
    return {
        # inv_mast_uid is the stable key document lines join on; item_id is
        # the SKU a person recognises.
        "item_id": str(first(row, "inv_mast_uid", "item_id") or ""),
        "name": str(first(row, "item_desc", "item_id") or ""),
        "sku": str(row.get("item_id") or "") or None,
        "status": "inactive" if inactive else "active",
        "item_type": "inventory",
    }


def _balance(row: dict[str, Any]) -> Any:
    """``total_amount − amount_paid`` from stated fields, else absent."""
    total, paid = row.get("total_amount"), row.get("amount_paid")
    if total in (None, "") or paid in (None, ""):
        return None
    try:
        return float(total) - float(paid)
    except (TypeError, ValueError):
        return None


def translate_invoice(header: dict[str, Any],
                      lines: list[dict[str, Any]]) -> dict[str, Any]:
    number = str(first(header, "invoice_no", "invoice_number") or "")
    return {
        "invoice_id": number,
        "invoice_number": number or None,
        "customer_id": (str(header["customer_id"])
                        if header.get("customer_id") not in (None, "") else None),
        "date": iso_date(first(header, "invoice_date", "date_created")),
        "due_date": iso_date(header.get("net_due_date")),
        "status": "void" if _flag(header.get("delete_flag")) else "open",
        "total": header.get("total_amount"),
        "balance": _balance(header),
        "last_modified_time": str(
            iso_date(header.get("date_last_modified")) or ""),
        "line_items": [
            {
                "line_item_id": str(first(ln, "line_no", "line_number") or i),
                "item_id": str(first(ln, "inv_mast_uid", "item_id") or ""),
                "quantity": first(ln, "qty_shipped", "unit_quantity",
                                  "qty_requested"),
                "rate": ln.get("unit_price"),
                "item_total": ln.get("extended_price"),
                "name": str(first(ln, "item_desc", "item_id") or "") or None,
                "sku": str(ln.get("item_id") or "") or None,
            }
            for i, ln in enumerate(lines)
            if first(ln, "inv_mast_uid", "item_id")
        ],
    }


def translate_bill(header: dict[str, Any],
                   lines: list[dict[str, Any]]) -> dict[str, Any]:
    uid = str(first(header, "apinv_hdr_uid", "invoice_no") or "")
    supplier = header.get("supplier_id")
    return {
        "bill_id": uid,
        "bill_number": str(header.get("invoice_no") or "") or None,
        "vendor_id": (str(supplier) if supplier not in (None, "") else None),
        "date": iso_date(header.get("invoice_date")),
        "due_date": iso_date(header.get("net_due_date")),
        "status": "void" if _flag(header.get("delete_flag")) else "open",
        "total": first(header, "invoice_amount", "total_amount"),
        "last_modified_time": str(
            iso_date(header.get("date_last_modified")) or ""),
        "line_items": [
            {
                "line_item_id": str(first(ln, "line_no", "apinv_line_uid") or i),
                "item_id": str(first(ln, "inv_mast_uid", "item_id") or ""),
                "quantity": first(ln, "qty_vouchered", "unit_quantity", "qty"),
                "rate": first(ln, "unit_price", "unit_cost"),
                "item_total": first(ln, "extended_price", "extended_cost"),
                "sku": str(ln.get("item_id") or "") or None,
            }
            for i, ln in enumerate(lines)
            if first(ln, "inv_mast_uid", "item_id")
            and first(ln, "qty_vouchered", "unit_quantity", "qty") not in (None, "")
        ],
    }


def translate_sales_order(row: dict[str, Any]) -> dict[str, Any]:
    completed = _flag(row.get("completed"))
    return {
        "salesorder_id": str(row.get("order_no") or ""),
        "salesorder_number": str(row.get("order_no") or "") or None,
        "customer_id": (str(row["customer_id"])
                        if row.get("customer_id") not in (None, "") else None),
        "date": iso_date(row.get("order_date")),
        "shipment_date": iso_date(row.get("requested_date")),
        "status": ("cancelled" if _flag(row.get("cancel_flag"))
                   else "closed" if completed else "open"),
    }


def translate_purchase_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "purchaseorder_id": str(row.get("po_no") or ""),
        "purchaseorder_number": str(row.get("po_no") or "") or None,
        "vendor_id": (str(row["supplier_id"])
                      if row.get("supplier_id") not in (None, "") else None),
        "date": iso_date(row.get("order_date")),
        "expected_delivery_date": "",
        "status": "closed" if _flag(row.get("complete")) else "open",
        "received_status": ("received" if _flag(row.get("complete"))
                            else "to_be_received"),
        "receives": [],
    }


class Prophet21Source:
    """The read source for one P21 database."""

    def __init__(self, client: Prophet21Client, *,
                 since: Optional[date] = None,
                 until: Optional[date] = None,
                 server_filter: bool = True) -> None:
        self._client = client
        self._since = since
        self._until = until
        # The escape hatch for a site whose OData rejects the date grammar:
        # connection config {"filter": "client"} lists in full and windows
        # here instead. Slower, never wrong.
        self._server_filter = server_filter
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

    def _date_filter(self, column: str) -> str:
        if not self._server_filter:
            return ""
        parts = []
        if self._since is not None:
            parts.append(f"{column} ge {self._since.isoformat()}T00:00:00Z")
        if self._until is not None:
            parts.append(f"{column} le {self._until.isoformat()}T23:59:59Z")
        return " and ".join(parts)

    # ── the source protocol ──────────────────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return (translate_customer(r) for r in self._client.view("customer"))

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        return (translate_supplier(r) for r in self._client.view("supplier"))

    def list_items(self) -> Iterable[dict[str, Any]]:
        return (translate_item(r) for r in self._client.view("inv_mast"))

    def list_invoices(self, skip: Optional[SkipPredicate] = None
                      ) -> Iterable[dict[str, Any]]:
        filter_ = self._date_filter("invoice_date")
        headers = list(self._client.view("invoice_hdr", filter_=filter_))
        lines = group_lines(
            self._client.view("invoice_line", filter_=filter_), "invoice_no")
        for header in headers:
            payload = translate_invoice(
                header, lines.get(str(header.get("invoice_no") or ""), []))
            if payload["status"] == "void":
                continue
            if not in_window(payload.get("date"), self._since, self._until):
                continue
            self._tally.saw("invoice", payload["invoice_id"])
            self._tally.documents_fetched += 1
            yield payload
        self._tally.complete("invoice")

    def list_bills(self, skip: Optional[SkipPredicate] = None
                   ) -> Iterable[dict[str, Any]]:
        filter_ = self._date_filter("invoice_date")
        headers = list(self._client.view("apinv_hdr", filter_=filter_))
        lines = group_lines(
            self._client.view("apinv_line", filter_=filter_), "invoice_no")
        for header in headers:
            payload = translate_bill(
                header, lines.get(str(header.get("invoice_no") or ""), []))
            if payload["status"] == "void":
                continue
            if not in_window(payload.get("date"), self._since, self._until):
                continue
            self._tally.saw("bill", payload["bill_id"])
            self._tally.documents_fetched += 1
            yield payload
        self._tally.complete("bill")

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        filter_ = self._date_filter("order_date")
        return (p for p in (translate_sales_order(r) for r in
                            self._client.view("oe_hdr", filter_=filter_))
                if p["status"] != "cancelled"
                and in_window(p.get("date"), self._since, self._until))

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        filter_ = self._date_filter("order_date")
        return (p for p in (translate_purchase_order(r) for r in
                            self._client.view("po_hdr", filter_=filter_))
                if in_window(p.get("date"), self._since, self._until))


def _build_source(material: CredentialMaterial,
                  since: Optional[date] = None) -> Prophet21Source:
    client = Prophet21Client(
        base_url=material.value("base_url"),
        username=material.value("username"),
        password=material.value("password"),
        odata_root=material.value("odata_root", "odataservice/odata/view"),
        view_prefix=material.value("view_prefix", "p21_view_"),
    )
    return Prophet21Source(
        client, since=since,
        server_filter=material.value("filter", "server") != "client")


SPEC = register(ConnectorSpec(
    key=SYSTEM,
    label="Epicor Prophet 21",
    company_term="company",
    credential_fields=(
        Field("base_url", "Middleware URL",
              placeholder="https://p21.example.com",
              help="The address of the P21 API/middleware server."),
        Field("username", "API username",
              help="A P21 user enabled for API access — a dedicated "
                   "integration user, not a person's login."),
        Field("password", "Password", secret=True),
        Field("odata_root", "OData path", required=False,
              placeholder="odataservice/odata/view",
              help="Where your middleware serves OData views; leave blank "
                   "for the default."),
        Field("view_prefix", "View prefix", required=False,
              placeholder="p21_view_",
              help="The prefix your site's exposed views carry; leave blank "
                   "for the default."),
    ),
    connection_fields=(
        Field("company_id", "Company code",
              help="Your P21 company identifier — used to tell this "
                   "connection apart, and shown on every imported row."),
    ),
    external_id_field="company_id",
    setup_note=(
        "Reads the Prophet 21 middleware's OData views — customers, "
        "suppliers, the item master, invoices with lines, AP invoices, "
        "orders — with the token sign-in of a dedicated API user. AP "
        "invoices booked against receipts rather than item lines import as "
        "payables without cost lines, and the sync report names each one."),
    permission_note=(
        "Two halves: the API user is enabled for API access in P21 (Maintain "
        "Users → API), and each OData view below is exposed on the middleware "
        "and readable by that user. A view that is not exposed answers 404, "
        "which reads like a wrong URL rather than a missing grant."),
    permissions=(
        Permission("API access on the user",
                   "The token sign-in itself. Without it nothing below is "
                   "reachable."),
        Permission("customer",
                   "Customers — who was sold to.", reads=("contacts",)),
        Permission("inv_mast",
                   "The item master.", reads=("items",)),
        Permission("invoice_hdr and invoice_line",
                   "Invoices with their lines — what was sold, and for how "
                   "much. Both views: headers alone import totals with nothing "
                   "under them.",
                   reads=("invoices",)),
        Permission("apinv_hdr and apinv_line",
                   "AP invoices with their lines — what it cost. Without these "
                   "there is no margin anywhere in the platform, only revenue.",
                   reads=("bills",)),
        Permission("supplier",
                   "Suppliers. Optional: AP invoices still land without it, "
                   "with the supplier known only by its id.",
                   required=False, reads=("vendors",)),
        Permission("oe_hdr",
                   "Sales orders — demand promised but not yet invoiced. "
                   "Optional.",
                   required=False, reads=("sales_orders",)),
        Permission("po_hdr",
                   "Purchase orders — what is on the way from suppliers. "
                   "Optional: feeds the Supply screen.",
                   required=False, reads=("purchase_orders",)),
    ),
    build_source=_build_source,
))
