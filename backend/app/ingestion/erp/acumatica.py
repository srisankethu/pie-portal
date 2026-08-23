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

from ..errors import (IngestionError, SourceAuthError, SourceScopeError,
                      SourceWriteRefused, SourceWriteUncertain,
                      SourceWriteUnknown)
from ..source import SkipPredicate
from ..write_settle import settle_by_read
from .base import (ConnectorSpec, CredentialMaterial, DocumentTally, Field,
                   Permission, WrittenDocument, first, in_window, iso_date,
                   money, odata_str, register, same_reference)
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

    @property
    def entity(self) -> str:
        """This tenant's contract endpoint root, for the writes that do not go
        through ``entities``."""
        return self._entity

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
def _wrapped(value: Any) -> Any:
    """The inverse of :func:`_plain`: dress a plain value for a write.

    Acumatica reads and writes the same records in different clothes — every
    scalar arrives and must leave as ``{"value": …}``. ``_plain`` undresses on
    the way in; nothing undid it, because until now nothing wrote.

    Written as the mirror of that function rather than as a payload builder so
    the two stay recognisable as one pair: a field the reader learns to unwrap
    is a field the writer already knows how to wrap.
    """
    if isinstance(value, dict):
        return {k: _wrapped(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_wrapped(v) for v in value]
    return {"value": value}


#: The reference goes out as ``CustomerOrderNbr``. Acumatica's own limit for it
#: is not something the documentation settles, so this is the smallest cap
#: *documented* by any system this platform writes to — Business Central's 35.
#: Refusing a reference no known field is proven to hold is the safe direction:
#: a reference silently truncated on the way in is one the settle read cannot
#: find, and "cannot find" is the branch that authorises sending again.
_EXTERNAL_REF_MAX = 35

#: A quote, in Acumatica's own vocabulary for the SalesOrder entity.
_QUOTE_ORDER_TYPE = "QT"


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

    # ── writing ─────────────────────────────────────────────────────────────
    def create_sales_quotes(self, customer: str, lines: list[dict], *,
                            customer_ref: Optional[str] = None,
                            reference: Optional[str] = None) -> WrittenDocument:
        """Create one sales quote in this tenant, or say what happened instead.

        One PUT, lines included. That is the shape Acumatica gives and it is a
        better one than a header-then-lines write: there is no state where the
        document exists and its lines do not, so "found" means "complete" and
        the settle read has one question to answer rather than two.

        PUT is Acumatica's insert-or-update, but with ``OrderNbr`` unset it
        always inserts — so it is not idempotent by itself and the pre-flight
        read below is what stops a second press making a second quote.

        Releases the licence seat on the way out, whatever happened. A cookie
        session holds one, this is a one-shot operation rather than a sync, and
        a send that leaks a seat per press exhausts them.
        """
        try:
            return self._create_quote(customer, lines, customer_ref, reference)
        finally:
            self._client.logout()

    def _create_quote(self, customer: str, lines: list[dict],
                      customer_ref: Optional[str],
                      reference: Optional[str]) -> WrittenDocument:
        if not reference:
            raise SourceWriteRefused(
                "This quote has no reference, so a sales quote created in "
                "Acumatica could not be found again if the reply were lost. "
                "Nothing was sent.")
        if not customer_ref:
            raise SourceWriteRefused(
                "This quote is not attached to an Acumatica customer, so there "
                "is no account to create it against. Nothing was sent.")
        if len(reference) > _EXTERNAL_REF_MAX:
            raise SourceWriteRefused(
                f"The reference {reference!r} is longer than the "
                f"{_EXTERNAL_REF_MAX} characters this platform will send as a "
                f"customer order number, so it could not be read back reliably. "
                f"Nothing was sent.")
        if not lines:
            raise SourceWriteRefused(
                "This quote has no priced lines, so there is nothing to create "
                "in Acumatica. An empty quote in a customer's ledger is worse "
                "than none. Nothing was sent.")
        # Acumatica addresses stock by its own InventoryID, which *is* the SKU
        # string rather than an internal id — so this is the line's code, not
        # the ``itemId`` Business Central needs. Same guard, different field.
        missing = [str(ln.get("code") or "?") for ln in lines if not ln.get("code")]
        if missing:
            raise SourceWriteRefused(
                "These lines carry no stock code, and an Acumatica quote line "
                "must name an InventoryID that already exists there: "
                + ", ".join(missing), codes=[c for c in missing if c != "?"])
        unpriced = [str(ln.get("code") or "?") for ln in lines
                    if ln.get("rate") is None or ln.get("qty") is None]
        if unpriced:
            raise SourceWriteRefused(
                "These lines have no price or no quantity, and Acumatica would "
                "fill an omitted price from the stock item — quoting a number "
                "nobody here chose: " + ", ".join(unpriced),
                codes=[c for c in unpriced if c != "?"])

        already = self._settled_quote(reference, customer, len(lines))
        if already is not None:
            return already

        payload = _wrapped({
            "OrderType": _QUOTE_ORDER_TYPE,
            "CustomerID": str(customer_ref),
            "CustomerOrderNbr": reference,
            "Details": [{"InventoryID": str(ln["code"]),
                         "OrderQty": money(ln.get("qty")),
                         "UnitPrice": money(ln.get("rate"))} for ln in lines],
        })
        try:
            created = self._client.request(
                "PUT", f"{self._client.entity}/SalesOrder",
                params={"$expand": "Details"}, json=payload)
        except SourceWriteUncertain as e:
            detail = str(e)
            return settle_by_read(
                lambda: self._quote_by_reference(reference),
                lambda row: self._found(row, customer, len(lines), reference),
                unknown_message=lambda err: (
                    f"The sales quote could not be completed ({detail}), and "
                    f"Acumatica could not be re-read to find out ({err}) — "
                    f"whether it was created cannot be established. Look for "
                    f"customer order number {reference} before sending again."),
                refused_message=(
                    f"The sales quote was not created ({detail}). Acumatica holds "
                    f"nothing under customer order number {reference}, so sending "
                    f"again is safe."),
                reference=reference)
        except (SourceScopeError, SourceAuthError):
            raise
        except IngestionError as e:
            raise SourceWriteRefused(
                f"Acumatica refused the sales quote: {e}") from e

        return self._found(_plain(created if isinstance(created, dict) else {}),
                           customer, len(lines), reference, already_existed=False)

    def _quote_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        """The quote carrying this customer order number, or None.

        Raises rather than returning None when the read itself fails — "not
        there" and "could not look" are the two answers the settle protocol
        exists to keep apart, and only one of them permits sending again.
        """
        rows = self._client.entities(
            "SalesOrder", expand="Details",
            filter_=(f"OrderType eq '{_QUOTE_ORDER_TYPE}' and "
                     f"CustomerOrderNbr eq '{odata_str(reference)}'"))
        for row in rows:
            plain = _plain(row)
            if same_reference(str(plain.get("CustomerOrderNbr") or ""), reference):
                return plain
        return None

    def _settled_quote(self, reference: str, customer: str,
                       sent_lines: int) -> Optional[WrittenDocument]:
        """This reference's quote as Acumatica holds it, or None.

        ``sent_lines`` is what *this* quote has, not what the found document
        has — the question is whether the thing already there is this quote.
        Passing the held count would compare a number against itself and make
        the mismatch check below unable to fire, which is a pre-flight that
        waves through a collision.

        A read that *fails* here propagates rather than reading as "not there":
        refusing to send because we could not check is recoverable, sending a
        duplicate is not.
        """
        row = self._quote_by_reference(reference)
        if row is None:
            return None
        return self._found(row, customer, sent_lines, reference)

    def _found(self, row: dict[str, Any], customer: str, sent_lines: int,
               reference: str, *, already_existed: bool = True) -> WrittenDocument:
        """A quote Acumatica holds, reported as what it *is*.

        The count is read back, never echoed from what was sent — the whole
        point of asking. One PUT carries the lines with the header, so a
        mismatch here means Acumatica kept something different from what was
        sent, which is neither a success to report nor safe to send again.
        """
        held = row.get("Details")
        if held is None:
            raise SourceWriteUnknown(
                f"Acumatica holds a sales quote under customer order number "
                f"{reference} but did not return its lines, so whether it is "
                f"complete cannot be established. Check it before sending this "
                f"quote again.", reference=reference)
        if len(held) != sent_lines:
            raise SourceWriteUnknown(
                f"Acumatica holds sales quote {row.get('OrderNbr')} under "
                f"customer order number {reference}, but with {len(held)} lines "
                f"where this quote has {sent_lines}. It is not the same "
                f"document, so it is neither safe to report as sent nor safe to "
                f"send again — check it.", reference=reference)
        number = str(row.get("OrderNbr") or "")
        if not number:
            raise SourceWriteUnknown(
                "Acumatica returned a sales quote with no order number, so it "
                "cannot be named to whoever has to find it.", reference=reference)
        return WrittenDocument(document_id=str(row.get("id") or ""),
                               number=number, customer=customer,
                               line_count=len(held),
                               already_existed=already_existed)

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
        "pull so it never holds one of Acumatica's licensed seats — including "
        "after a quote write, which is a one-shot the same rule applies to. "
        "Everything is read except one thing: a quote built here can be created "
        "as a sales quote (order type QT). Nothing else is ever written."),
    permission_note=(
        "Granted on the integration user's role, at User Security → Access "
        "Rights by Role. View Only is enough everywhere except Sales Orders, "
        "which needs Insert for the quote write — leave it View Only and "
        "everything reads, and every send refuses."),
    permissions=(
        Permission("API access on the user (Web Service Endpoints → Default)",
                   "The contract-based REST endpoint this reads through. "
                   "Without it the sign-in succeeds and every request 403s."),
        Permission("Customer (AR303000) — View Only",
                   "Customers — who was sold to.", reads=("contacts",)),
        Permission("Stock Items (IN202500) — View Only",
                   "Items — the product master.", reads=("items",)),
        Permission("Invoices (AR301000) — View Only",
                   "Invoices — what was sold, and for how much.",
                   reads=("invoices",)),
        Permission("Bills and Adjustments (AP301000) — View Only",
                   "Bills — what it cost. Without this there is no margin "
                   "anywhere in the platform, only revenue.",
                   reads=("bills",)),
        Permission("Payments and Applications (AR302000) — View Only",
                   "Payments — when money actually arrived, and which invoices "
                   "each one settled. Without it an invoice looks paid the day "
                   "it was raised.",
                   reads=("customer_payments",)),
        Permission("Vendors (AP303000) — View Only",
                   "Suppliers. Optional: bills still land without it, with the "
                   "supplier known only by its id.",
                   required=False, reads=("vendors",)),
        Permission("Sales Orders (SO301000) — View Only, plus Insert to send",
                   "Sales orders — demand promised but not yet invoiced, and "
                   "the screen a quote is created on. Reading it is optional; "
                   "Insert is what the Send action needs, and without it "
                   "everything else works and every send refuses.",
                   required=False, reads=("sales_orders",),
                   writes=("sales_quotes",)),
        Permission("Purchase Orders (PO301000) — View Only",
                   "Purchase orders — what is on the way from suppliers. "
                   "Optional: feeds the Supply screen.",
                   required=False, reads=("purchase_orders",)),
    ),
    build_source=_build_source,
))
