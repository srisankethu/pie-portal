"""Acumatica, read through the contract-based REST API.

One Acumatica *tenant* is one set of books, addressed by name at sign-in — so
the tenant is what a connection stores as the company's identity. Auth is the
session sign-in every Acumatica build ships (``/entity/auth/login`` with the
integration user's name and password); the session cookie rides on the client
for the run and is signed out afterwards, because Acumatica counts live
sessions against its licence and a leaked session is a held seat.

The contract shape is distinctive: every field arrives wrapped —
``{"CustomerID": {"value": "ACME"}}`` — and detail lines ride under a
``Details`` array when ``$expand=Details`` is asked for. ``_plain`` unwraps
one level so the translators read like the others.

The endpoint version (``24.200.001`` by default) is per-site configuration,
not a secret, so it is a credential field with a default rather than a
constant: sites on older builds enter theirs.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Iterator, Optional

from ..errors import SourceAuthError
from ..source import SkipPredicate
from .base import (ConnectorSpec, CredentialMaterial, DocumentTally, Field,
                   first, in_window, iso_date, register)
from .transport import RestTransport

SYSTEM = "acumatica"

#: Document statuses that are not trade.
_EXCLUDED_STATUS = {"hold", "on hold", "voided", "canceled", "cancelled",
                    "pending approval", "rejected", "scheduled"}

_PAGE = 200


class AcumaticaClient(RestTransport):
    """Cookie-session REST against one Acumatica site + tenant."""

    system = "Acumatica"
    requests_per_minute = 120

    def __init__(self, *, base_url: str, username: str, password: str,
                 tenant: str, branch: str = "",
                 endpoint_version: str = "24.200.001",
                 http: Any = None) -> None:
        super().__init__(http=http)
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._tenant = tenant
        self._branch = branch
        self._entity = f"{self._base}/entity/Default/{endpoint_version.strip()}"
        self._signed_in = False

    def _client(self):
        if self._http is None:
            import httpx

            # Cookies carry the session, so one Client must live for the run.
            self._http = httpx.Client(timeout=self.timeout_seconds,
                                      follow_redirects=False)
        return self._http

    def _auth_headers(self) -> dict[str, str]:
        if not self._signed_in:
            payload: dict[str, str] = {"name": self._username,
                                       "password": self._password,
                                       "tenant": self._tenant}
            if self._branch:
                payload["branch"] = self._branch
            resp = self._client().post(f"{self._base}/entity/auth/login",
                                       json=payload)
            if resp.status_code not in (200, 204):
                raise SourceAuthError(
                    "Acumatica refused the sign-in (HTTP "
                    f"{resp.status_code}). The username, password and tenant "
                    "must belong to one integration user with API access; "
                    "the tenant is the login name of the company, not its "
                    "display title.")
            self._signed_in = True
        return {}

    def _invalidate_auth(self) -> None:
        self._signed_in = False

    def logout(self) -> None:
        """Release the licence seat. Best-effort: a failed logout expires."""
        if not self._signed_in:
            return
        try:
            self._client().post(f"{self._base}/entity/auth/logout")
        except Exception:  # noqa: BLE001 — the session will time out anyway
            pass
        self._signed_in = False

    def entities(self, name: str, *, expand: str = "",
                 filter_: str = "") -> Iterator[dict[str, Any]]:
        """Every record of one entity, paged with $top/$skip."""
        skip = 0
        while True:
            params: dict[str, Any] = {"$top": _PAGE}
            if skip:
                params["$skip"] = skip
            if expand:
                params["$expand"] = expand
            if filter_:
                params["$filter"] = filter_
            rows = self.request("GET", f"{self._entity}/{name}", params=params)
            if not isinstance(rows, list):
                rows = []
            yield from rows
            if len(rows) < _PAGE:
                return
            skip += _PAGE

    def ping(self) -> dict[str, Any]:
        # One cheap authenticated read proves the sign-in, the tenant and the
        # endpoint version in a single call.
        next(self.entities("Customer"), None)
        return {"authenticated": True, "organization_found": True,
                "organization_name": self._tenant}


def _plain(record: dict[str, Any]) -> dict[str, Any]:
    """Unwrap one level of Acumatica's ``{"value": …}`` dress.

    Lists (``Details``) are unwrapped element by element; ``custom``/``_links``
    metadata is dropped. Absent stays absent — an unwrapped None is still None.
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("custom", "_links", "note", "rowNumber", "error"):
            continue
        if isinstance(value, dict) and set(value) <= {"value"}:
            out[key] = value.get("value")
        elif isinstance(value, list):
            out[key] = [_plain(v) if isinstance(v, dict) else v for v in value]
        elif isinstance(value, dict):
            out[key] = _plain(value)
        else:
            out[key] = value
    return out


# ── translators: unwrapped entities → canonical payloads ─────────────────────
def translate_customer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact_id": str(first(row, "CustomerID", "id") or ""),
        "contact_name": str(first(row, "CustomerName", "CustomerID") or ""),
        "status": ("active" if str(row.get("CustomerStatus")
                                   or row.get("Status") or "Active").lower()
                   in ("active", "one-time") else "inactive"),
    }


def translate_vendor(row: dict[str, Any]) -> dict[str, Any]:
    terms = row.get("Terms")
    return {
        "contact_id": str(first(row, "VendorID", "id") or ""),
        "contact_name": str(first(row, "VendorName", "VendorID") or ""),
        "status": ("active" if str(row.get("VendorStatus")
                                   or row.get("Status") or "Active").lower()
                   == "active" else "inactive"),
        "payment_terms": None if terms in (None, "") else terms,
    }


def translate_item(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("ItemStatus") or "Active").lower()
    item_type = str(row.get("ItemType") or "").lower()
    out = {
        "item_id": str(first(row, "InventoryID", "id") or ""),
        "name": str(first(row, "Description", "InventoryID") or ""),
        "sku": str(row.get("InventoryID") or "") or None,
        "unit": str(row.get("BaseUOM") or "") or None,
        "category_name": str(row.get("ItemClass") or "") or None,
        "status": "active" if status == "active" else "inactive",
        "item_type": "service" if "non-stock" in item_type else "inventory",
        "track_inventory": "non-stock" not in item_type,
        "purchase_rate": row.get("LastCost") or row.get("AverageCost"),
    }
    if row.get("QtyOnHand") is not None:
        out["stock_on_hand"] = row.get("QtyOnHand")
    if row.get("QtyAvailable") is not None:
        out["available_stock"] = row.get("QtyAvailable")
    return out


def _document_lines(row: dict[str, Any], *, qty_key: str,
                    price_key: str) -> list[dict[str, Any]]:
    out = []
    for i, ln in enumerate(row.get("Details") or []):
        if not ln.get("InventoryID"):
            continue
        out.append({
            "line_item_id": str(first(ln, "LineNbr", "id") or i),
            "item_id": str(ln.get("InventoryID")),
            "quantity": first(ln, qty_key, "Qty", "Quantity", "OrderQty"),
            "rate": first(ln, price_key, "UnitPrice", "UnitCost"),
            # ``Amount`` is the line's post-discount extended amount.
            "item_total": first(ln, "Amount", "ExtendedPrice", "ExtendedCost"),
            "discount_amount": ln.get("DiscountAmount"),
            "name": str(first(ln, "TransactionDescription", "Description")
                        or "") or None,
        })
    return out


def translate_invoice(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": str(first(row, "ReferenceNbr", "id") or ""),
        "invoice_number": str(row.get("ReferenceNbr") or "") or None,
        "customer_id": (str(first(row, "CustomerID", "Customer"))
                        if first(row, "CustomerID", "Customer") else None),
        "customer_name": str(row.get("CustomerName") or ""),
        "date": iso_date(row.get("Date")),
        "due_date": iso_date(row.get("DueDate")),
        "status": str(row.get("Status") or ""),
        "total": row.get("Amount"),
        "balance": row.get("Balance"),
        "currency_code": (str(row.get("CurrencyID")).upper()
                          if row.get("CurrencyID") else None),
        "last_modified_time": str(row.get("LastModifiedDateTime") or ""),
        "line_items": _document_lines(row, qty_key="Qty",
                                      price_key="UnitPrice"),
    }


def translate_bill(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "bill_id": str(first(row, "ReferenceNbr", "id") or ""),
        "bill_number": str(first(row, "VendorRef", "ReferenceNbr") or "") or None,
        "vendor_id": (str(row["Vendor"]) if row.get("Vendor") else None),
        "date": iso_date(row.get("Date")),
        "due_date": iso_date(row.get("DueDate")),
        "status": str(row.get("Status") or ""),
        "total": row.get("Amount"),
        "balance": row.get("Balance"),
        "currency_code": (str(row.get("CurrencyID")).upper()
                          if row.get("CurrencyID") else None),
        "last_modified_time": str(row.get("LastModifiedDateTime") or ""),
        "line_items": _document_lines(row, qty_key="Qty", price_key="UnitCost"),
    }


def translate_payment(row: dict[str, Any]) -> dict[str, Any]:
    applications = []
    for i, app in enumerate(row.get("DocumentsToApply")
                            or row.get("ApplicationHistory") or []):
        doc = first(app, "ReferenceNbr", "DisplayRefNbr")
        if not doc:
            continue
        applications.append({
            "invoice_payment_id": f"{row.get('ReferenceNbr')}:{doc}:{i}",
            "invoice_id": str(doc),
            "date": iso_date(first(app, "DocDate", "ApplicationDate")),
            "due_date": iso_date(app.get("DueDate")),
            "amount_applied": first(app, "AmountPaid", "AppliedToDocument"),
        })
    return {
        "payment_id": str(first(row, "ReferenceNbr", "id") or ""),
        "customer_id": (str(row["CustomerID"]) if row.get("CustomerID") else None),
        "date": iso_date(row.get("ApplicationDate") or row.get("Date")),
        "amount": row.get("PaymentAmount"),
        "payment_mode": str(row.get("PaymentMethod") or "") or None,
        "unused_amount": row.get("AvailableBalance"),
        "last_modified_time": str(row.get("LastModifiedDateTime") or ""),
        "invoices": [a for a in applications if a["date"]],
    }


def translate_sales_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "salesorder_id": str(first(row, "OrderNbr", "id") or ""),
        "salesorder_number": str(row.get("OrderNbr") or "") or None,
        "customer_id": (str(row["CustomerID"]) if row.get("CustomerID") else None),
        "date": iso_date(row.get("Date")),
        "shipment_date": iso_date(row.get("RequestedOn")),
        "status": str(row.get("Status") or ""),
        "total": row.get("OrderTotal"),
        "currency_code": (str(row.get("CurrencyID")).upper()
                          if row.get("CurrencyID") else None),
    }


def translate_purchase_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "purchaseorder_id": str(first(row, "OrderNbr", "id") or ""),
        "purchaseorder_number": str(row.get("OrderNbr") or "") or None,
        "vendor_id": (str(row["VendorID"]) if row.get("VendorID") else None),
        "date": iso_date(row.get("Date")),
        "expected_delivery_date": iso_date(row.get("PromisedOn")) or "",
        "status": str(row.get("Status") or ""),
        "total": row.get("OrderTotal"),
        "receives": [],
    }


def _is_trade(payload: dict[str, Any]) -> bool:
    return str(payload.get("status") or "").strip().lower() not in _EXCLUDED_STATUS


class AcumaticaSource:
    """The read source for one Acumatica tenant.

    Window filtering is client-side (`in_window`): the contract API's date
    filter grammar varies across builds, and a filter that silently matched
    nothing would read as an empty book — the one failure mode worse than a
    slower listing.
    """

    def __init__(self, client: AcumaticaClient, *,
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
        try:
            return self._client.ping()
        finally:
            self._client.logout()

    def _rows(self, entity: str, expand: str = "") -> Iterator[dict[str, Any]]:
        return (_plain(r) for r in self._client.entities(entity, expand=expand))

    def _documents(self, entity: str, kind: str,
                   translate: Any) -> Iterator[dict[str, Any]]:
        for row in self._rows(entity, expand="Details"):
            payload = translate(row)
            if not _is_trade(payload):
                continue
            if not in_window(payload.get("date"), self._since, self._until):
                continue
            self._tally.saw(kind, payload[f"{kind}_id"])
            self._tally.documents_fetched += 1
            yield payload
        self._tally.complete(kind)

    # ── the source protocol ──────────────────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return (translate_customer(r) for r in self._rows("Customer"))

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        return (translate_vendor(r) for r in self._rows("Vendor"))

    def list_items(self) -> Iterable[dict[str, Any]]:
        return (translate_item(r) for r in self._rows("StockItem"))

    def list_invoices(self, skip: Optional[SkipPredicate] = None
                      ) -> Iterable[dict[str, Any]]:
        return self._documents("SalesInvoice", "invoice", translate_invoice)

    def list_bills(self, skip: Optional[SkipPredicate] = None
                   ) -> Iterable[dict[str, Any]]:
        return self._documents("Bill", "bill", translate_bill)

    def list_customer_payments(self, skip: Optional[SkipPredicate] = None
                               ) -> Iterable[dict[str, Any]]:
        for row in self._rows("Payment", expand="DocumentsToApply"):
            payload = translate_payment(row)
            if not _is_trade(payload):
                continue
            if not in_window(payload.get("date"), self._since, self._until):
                continue
            yield payload

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        return (p for p in (translate_sales_order(r)
                            for r in self._rows("SalesOrder"))
                if _is_trade(p)
                and in_window(p.get("date"), self._since, self._until))

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        return (p for p in (translate_purchase_order(r)
                            for r in self._rows("PurchaseOrder"))
                if _is_trade(p)
                and in_window(p.get("date"), self._since, self._until))


def _build_source(material: CredentialMaterial,
                  since: Optional[date] = None) -> AcumaticaSource:
    client = AcumaticaClient(
        base_url=material.value("base_url"),
        username=material.value("username"),
        password=material.value("password"),
        tenant=material.external_org_id or material.value("tenant"),
        branch=material.value("branch"),
        endpoint_version=material.value("endpoint_version", "24.200.001"),
    )
    return AcumaticaSource(client, since=since)


SPEC = register(ConnectorSpec(
    key=SYSTEM,
    label="Acumatica",
    company_term="tenant",
    credential_fields=(
        Field("base_url", "Site URL", placeholder="https://erp.example.com",
              help="The address of your Acumatica site, without a path."),
        Field("username", "API username",
              help="A dedicated integration user with API access — not a "
                   "person's login, so a password change never breaks the "
                   "sync."),
        Field("password", "Password", secret=True),
        Field("endpoint_version", "Endpoint version", required=False,
              placeholder="24.200.001",
              help="The Default endpoint version under Web Service "
                   "Endpoints; leave blank for 24.200.001."),
    ),
    connection_fields=(
        Field("tenant", "Tenant",
              help="The tenant's login name (as on the sign-in screen), "
                   "not its display title."),
        Field("branch", "Branch", required=False,
              help="Optional: sign in to one branch."),
    ),
    external_id_field="tenant",
    setup_note=(
        "Reads the contract-based REST API with a session sign-in as a "
        "dedicated integration user. The session is signed out after every "
        "pull so it never holds one of Acumatica's licensed seats. Only "
        "reads are ever issued."),
    build_source=_build_source,
))
