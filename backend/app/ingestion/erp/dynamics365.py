"""Microsoft Dynamics 365 Business Central, read through its API v2.0.

The standard OData surface every Business Central tenant exposes —
``/v2.0/{tenant}/{environment}/api/v2.0`` — scoped per company by GUID, which
is what the connection stores as the company's identity. Auth is an Entra ID
app registration using the client-credentials grant: an admin registers one
app, grants it Business Central API permission (``API.ReadWrite.All`` app
role, used read-only here — Microsoft ships no read-only application role),
and enters the tenant id, client id and secret once. No browser round-trip.

Lines ride on the same call as headers via ``$expand``, so a document costs no
second request, and listings filter server-side on the window
(``invoiceDate ge …``) — pagination follows ``@odata.nextLink`` exactly as
Microsoft emits it.

**Which Dynamics this is.** "Dynamics 365" names a family. This connector
reads Business Central (the ERP that NAV became — the books US distributors
in this platform's size band actually run). Finance & Operations is a
different product with a different API and would be its own connector.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Iterable, Iterator, Optional

from ..errors import SourceAuthError, SourceScopeError
from ..source import SkipPredicate
from .base import (ConnectorSpec, CredentialMaterial, DocumentTally, Field,
                   Permission, iso_date, register)
from .transport import RestTransport

SYSTEM = "dynamics365"

#: Sales-document statuses that are not trade.
_EXCLUDED_STATUS = {"draft", "in review", "canceled", "cancelled"}


class BusinessCentralClient(RestTransport):
    """Token-caching OData against one tenant + environment."""

    system = "Business Central"
    #: Microsoft's operational limit is far higher; modest by choice.
    requests_per_minute = 120

    def __init__(self, *, tenant_id: str, client_id: str, client_secret: str,
                 environment: str = "production", http: Any = None) -> None:
        super().__init__(http=http)
        self._tenant = tenant_id.strip()
        self._client_id = client_id
        self._client_secret = client_secret
        self._environment = (environment or "production").strip()
        self._api = (f"https://api.businesscentral.dynamics.com/v2.0/"
                     f"{self._tenant}/{self._environment}/api/v2.0")
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    def _auth_headers(self) -> dict[str, str]:
        if not self._token or time.time() >= self._token_expires_at:
            resp = self._client().post(
                f"https://login.microsoftonline.com/{self._tenant}"
                "/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "https://api.businesscentral.dynamics.com/.default",
                })
            try:
                body = resp.json()
            except ValueError:
                raise SourceAuthError(
                    "Microsoft's token endpoint returned non-JSON "
                    f"(HTTP {resp.status_code}).")
            token = body.get("access_token")
            if not token:
                raise SourceAuthError(
                    "Microsoft refused the sign-in: "
                    f"{body.get('error_description') or body.get('error') or body}. "
                    "The tenant id, client id and secret must belong to one "
                    "app registration that has been granted Business Central "
                    "API permission with admin consent.")
            self._token = str(token)
            self._token_expires_at = (
                time.time() + max(60, int(body.get("expires_in", 3600))) - 60)
        return {"Authorization": f"Bearer {self._token}"}

    def _invalidate_auth(self) -> None:
        self._token, self._token_expires_at = None, 0.0

    def _scope_refusal(self, resp: Any) -> Optional[SourceScopeError]:
        # Entra minted the token, Business Central refused the call: the app
        # authenticates fine and lacks the API permission — a 403, never a 401.
        if resp.status_code != 403:
            return None
        return SourceScopeError(
            "Business Central refused the call although the sign-in works: "
            "the app registration has not been granted the Dynamics 365 "
            "Business Central API application permission (with admin "
            "consent), or the environment blocks service-to-service access.",
            path=str(getattr(resp, "url", "")), scope="API.ReadWrite.All")

    def pages(self, path: str, *, company_id: Optional[str] = None,
              params: Optional[dict[str, Any]] = None
              ) -> Iterator[dict[str, Any]]:
        """Every record under one entity set, following @odata.nextLink."""
        prefix = f"/companies({company_id})" if company_id else ""
        url: Optional[str] = f"{self._api}{prefix}/{path.lstrip('/')}"
        query: Optional[dict[str, Any]] = {"$top": 200, **(params or {})}
        while url:
            body = self.request("GET", url, params=query)
            yield from body.get("value") or []
            # nextLink already carries every parameter, including $top.
            url, query = body.get("@odata.nextLink"), None

    def companies(self) -> list[dict[str, Any]]:
        return [{"id": str(c.get("id")), "name": str(c.get("name") or ""),
                 "display_name": str(c.get("displayName") or "")}
                for c in self.pages("companies")]

    def ping(self, company_id: Optional[str] = None) -> dict[str, Any]:
        rows = self.companies()
        match = next((c for c in rows if c["id"] == str(company_id)), None)
        return {
            "authenticated": True,
            "organization_found": (match is not None if company_id else bool(rows)),
            "organization_name": (match or (rows[0] if rows else {})).get(
                "display_name") or (match or {}).get("name"),
            "visible_organizations": rows,
        }


# ── translators: BC entities → canonical payloads ────────────────────────────
def translate_contact(row: dict[str, Any]) -> dict[str, Any]:
    """Customers and vendors share BC's shape; ``blocked`` non-empty means the
    account is stopped for something, which is the nearest true reading of
    "inactive" BC offers."""
    blocked = str(row.get("blocked") or "").strip()
    return {
        "contact_id": str(row.get("id")),
        "contact_name": str(row.get("displayName") or row.get("number") or ""),
        "status": "inactive" if blocked and blocked != "_x0020_" else "active",
    }


def translate_item(row: dict[str, Any]) -> dict[str, Any]:
    item_type = str(row.get("type") or "").lower()
    out = {
        "item_id": str(row.get("id")),
        "name": str(row.get("displayName") or row.get("number") or ""),
        "sku": str(row.get("number") or "") or None,
        "unit": str(row.get("baseUnitOfMeasureCode") or "") or None,
        "category_name": str(row.get("itemCategoryCode") or "") or None,
        "status": "inactive" if row.get("blocked") else "active",
        "item_type": "service" if item_type == "service" else item_type or None,
        "purchase_rate": row.get("unitCost"),
        "track_inventory": item_type == "inventory",
    }
    # BC states quantity on hand as `inventory`; absent means this API
    # version does not report it, and no stock fields means no snapshot.
    if row.get("inventory") is not None:
        out["stock_on_hand"] = row.get("inventory")
    return out


def _lines(row: dict[str, Any], lines_key: str, *,
           price_key: str) -> list[dict[str, Any]]:
    out = []
    for ln in row.get(lines_key) or []:
        if str(ln.get("lineType") or "").lower() != "item" or not ln.get("itemId"):
            continue
        out.append({
            "line_item_id": str(ln.get("id") or ln.get("sequence")),
            "item_id": str(ln.get("itemId")),
            "quantity": ln.get("quantity"),
            "rate": ln.get(price_key),
            # Post-discount, pre-tax — exactly the canonical ``item_total``.
            "item_total": ln.get("amountExcludingTax"),
            "discount_amount": ln.get("discountAmount"),
            "name": str(ln.get("description") or "") or None,
        })
    return out


def translate_sales_invoice(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": str(row.get("id")),
        "invoice_number": str(row.get("number") or "") or None,
        "customer_id": (str(row["customerId"]) if row.get("customerId") else None),
        "customer_name": str(row.get("customerName") or ""),
        "date": iso_date(row.get("invoiceDate")),
        "due_date": iso_date(row.get("dueDate")),
        "status": str(row.get("status") or ""),
        "total": row.get("totalAmountExcludingTax"),
        "balance": row.get("remainingAmount"),
        "currency_code": (str(row.get("currencyCode")).upper()
                          if row.get("currencyCode") else None),
        "last_modified_time": str(row.get("lastModifiedDateTime") or ""),
        "line_items": _lines(row, "salesInvoiceLines", price_key="unitPrice"),
    }


def translate_purchase_invoice(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "bill_id": str(row.get("id")),
        "bill_number": str(row.get("number") or row.get("vendorInvoiceNumber")
                           or "") or None,
        "vendor_id": (str(row["vendorId"]) if row.get("vendorId") else None),
        "vendor_name": str(row.get("vendorName") or ""),
        "date": iso_date(row.get("invoiceDate")),
        "due_date": iso_date(row.get("dueDate")),
        "status": str(row.get("status") or ""),
        "total": row.get("totalAmountExcludingTax"),
        "balance": row.get("remainingAmount"),
        "currency_code": (str(row.get("currencyCode")).upper()
                          if row.get("currencyCode") else None),
        "last_modified_time": str(row.get("lastModifiedDateTime") or ""),
        "line_items": _lines(row, "purchaseInvoiceLines", price_key="unitCost"),
    }


def translate_sales_order(row: dict[str, Any]) -> dict[str, Any]:
    shipped = row.get("fullyShipped")
    return {
        "salesorder_id": str(row.get("id")),
        "salesorder_number": str(row.get("number") or "") or None,
        "customer_id": (str(row["customerId"]) if row.get("customerId") else None),
        "date": iso_date(row.get("orderDate")),
        "shipment_date": iso_date(row.get("requestedDeliveryDate")),
        "status": str(row.get("status") or ""),
        "shipped_status": (None if shipped is None
                           else ("shipped" if shipped else "pending")),
        "total": row.get("totalAmountExcludingTax"),
        "currency_code": (str(row.get("currencyCode")).upper()
                          if row.get("currencyCode") else None),
    }


def translate_purchase_order(row: dict[str, Any]) -> dict[str, Any]:
    received = row.get("fullyReceived")
    return {
        "purchaseorder_id": str(row.get("id")),
        "purchaseorder_number": str(row.get("number") or "") or None,
        "vendor_id": (str(row["vendorId"]) if row.get("vendorId") else None),
        "date": iso_date(row.get("orderDate")),
        "expected_delivery_date": iso_date(row.get("requestedReceiptDate")) or "",
        "status": str(row.get("status") or ""),
        "received_status": (None if received is None
                            else ("received" if received else "to_be_received")),
        "total": row.get("totalAmountExcludingTax"),
        "receives": [],
    }


def _is_trade(payload: dict[str, Any]) -> bool:
    return str(payload.get("status") or "").strip().lower() not in _EXCLUDED_STATUS


class BusinessCentralSource:
    """The read source for one Business Central company."""

    def __init__(self, client: BusinessCentralClient, company_id: str, *,
                 since: Optional[date] = None,
                 until: Optional[date] = None) -> None:
        self._client = client
        self._company = company_id
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
        return self._client.ping(self._company)

    def _window_filter(self, date_field: str) -> Optional[dict[str, str]]:
        parts = []
        if self._since is not None:
            parts.append(f"{date_field} ge {self._since.isoformat()}")
        if self._until is not None:
            parts.append(f"{date_field} le {self._until.isoformat()}")
        return {"$filter": " and ".join(parts)} if parts else None

    def _rows(self, path: str, params: Optional[dict[str, Any]] = None
              ) -> Iterator[dict[str, Any]]:
        return self._client.pages(path, company_id=self._company, params=params)

    def _documents(self, path: str, expand: str, kind: str,
                   translate: Any) -> Iterator[dict[str, Any]]:
        params = {"$expand": expand,
                  **(self._window_filter("invoiceDate") or {})}
        for row in self._rows(path, params):
            payload = translate(row)
            if not _is_trade(payload):
                continue
            self._tally.saw(kind, payload[f"{kind}_id"])
            self._tally.documents_fetched += 1
            yield payload
        self._tally.complete(kind)

    # ── the source protocol ──────────────────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return (translate_contact(r) for r in self._rows("customers"))

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        return (translate_contact(r) for r in self._rows("vendors"))

    def list_items(self) -> Iterable[dict[str, Any]]:
        return (translate_item(r) for r in self._rows("items"))

    def list_invoices(self, skip: Optional[SkipPredicate] = None
                      ) -> Iterable[dict[str, Any]]:
        return self._documents("salesInvoices", "salesInvoiceLines",
                               "invoice", translate_sales_invoice)

    def list_bills(self, skip: Optional[SkipPredicate] = None
                   ) -> Iterable[dict[str, Any]]:
        return self._documents("purchaseInvoices", "purchaseInvoiceLines",
                               "bill", translate_purchase_invoice)

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        params = self._window_filter("orderDate")
        return (p for p in (translate_sales_order(r)
                            for r in self._rows("salesOrders", params))
                if _is_trade(p))

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        params = self._window_filter("orderDate")
        return (p for p in (translate_purchase_order(r)
                            for r in self._rows("purchaseOrders", params))
                if _is_trade(p))


def _client_from(material: CredentialMaterial) -> BusinessCentralClient:
    return BusinessCentralClient(
        tenant_id=material.value("tenant_id"),
        client_id=material.value("client_id"),
        client_secret=material.value("client_secret"),
        environment=material.value("environment", "production"),
    )


def _build_source(material: CredentialMaterial,
                  since: Optional[date] = None) -> BusinessCentralSource:
    return BusinessCentralSource(_client_from(material),
                                 material.external_org_id, since=since)


def _discover(material: CredentialMaterial) -> list[dict[str, Any]]:
    """The companies this grant can see — so the connect flow can offer a
    picker instead of asking somebody to find a GUID."""
    return [{"id": c["id"], "name": c["display_name"] or c["name"]}
            for c in _client_from(material).companies()]


SPEC = register(ConnectorSpec(
    key=SYSTEM,
    label="Dynamics 365 Business Central",
    company_term="company",
    credential_fields=(
        Field("tenant_id", "Directory (tenant) ID",
              help="From the Entra ID app registration's overview page."),
        Field("client_id", "Application (client) ID"),
        Field("client_secret", "Client secret", secret=True),
        Field("environment", "Environment", required=False,
              placeholder="production",
              help="The Business Central environment name; leave blank for "
                   "production."),
    ),
    connection_fields=(
        Field("company_id", "Company ID",
              help="The company's GUID. Enter the credentials and use "
                   "“List companies” to pick it by name."),
    ),
    external_id_field="company_id",
    setup_note=(
        "Reads Business Central's standard API (v2.0) with an Entra ID app "
        "registration using the client-credentials grant. An administrator "
        "registers the app, grants it the Dynamics 365 Business Central API "
        "application permission with admin consent, and creates one client "
        "secret. The platform only ever issues reads."),
    permission_note=(
        "Two places, and both are needed: the application permission is "
        "granted with admin consent on the Entra ID app registration, and the "
        "entity access comes from the permission sets on the app's user inside "
        "each Business Central company (Microsoft Entra Applications → the "
        "app → Permission sets)."),
    permissions=(
        Permission("API.ReadWrite.All (application permission, admin consent)",
                   "The only application permission Business Central publishes "
                   "for its standard API — there is no read-only variant. This "
                   "platform never writes; the grant is wider than the use."),
        Permission("D365 BASIC (permission set on the app's user)",
                   "Lets the app sign in to the company at all. Without it the "
                   "token is valid and every company answers 401."),
        Permission("Read on customers",
                   "Customers — who was sold to.", reads=("contacts",)),
        Permission("Read on items",
                   "Items — the product master.", reads=("items",)),
        Permission("Read on salesInvoices (with salesInvoiceLines)",
                   "Invoices — what was sold, and for how much. The lines are "
                   "part of the same read; a header-only grant imports totals "
                   "with nothing under them.",
                   reads=("invoices",)),
        Permission("Read on purchaseInvoices (with purchaseInvoiceLines)",
                   "Purchase invoices — what it cost. Without this there is no "
                   "margin anywhere in the platform, only revenue.",
                   reads=("bills",)),
        Permission("Read on vendors",
                   "Suppliers. Optional: purchase invoices still land without "
                   "it, with the supplier known only by its GUID.",
                   required=False, reads=("vendors",)),
        Permission("Read on salesOrders",
                   "Sales orders — demand promised but not yet invoiced. "
                   "Optional.",
                   required=False, reads=("sales_orders",)),
        Permission("Read on purchaseOrders",
                   "Purchase orders — what is on the way from suppliers. "
                   "Optional: feeds the Supply screen.",
                   required=False, reads=("purchase_orders",)),
    ),
    build_source=_build_source,
    discover=_discover,
))
