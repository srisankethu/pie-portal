"""Oracle NetSuite, read through SuiteQL.

**Why SuiteQL and not the record API.** NetSuite's REST record API returns
summary rows on list calls and needs one GET per record for lines — the same
per-document cost the Zoho pull pays, but against a much lower default rate
limit. SuiteQL returns headers and lines in bulk (one flat query per kind,
1,000 rows per page), so a two-year history is tens of calls rather than
thousands. Every query here is a plain SELECT — this connector, like every
other, never writes.

**Auth is token-based (TBA), signed per request.** An admin creates an
integration record (consumer key + secret) and an access token for a role
(token id + secret); each request carries an OAuth 1.0a HMAC-SHA256 signature
built from all four. Chosen over OAuth 2.0 because it needs no browser
round-trip and no certificate: the four values are minted once in the NetSuite
UI and pasted in, which is the same day-one experience the other connectors
give. Signing is stdlib (`hmac`) — no new dependency.

**Sign conventions.** ``transactionline`` stores amounts from the GL's point
of view, so income lines on an invoice carry *negative* quantity and
netamount. The translator negates them back for CustInvc rows and leaves
VendBill rows as stated, so a genuine credit line stays negative rather than
being flattened by ``abs()``.

Known v1 gaps, stated rather than papered over: item stock levels and payment
applications are not read (NetSuite keeps both in structures a flat query
reads poorly), so stock screens show nothing for a NetSuite book and
days-to-pay has no observations — both degrade visibly, per the working
agreement, and neither invents a number.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
import time
from datetime import date
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import quote, urlsplit

from ..errors import SourceAuthError
from ..source import SkipPredicate
from .base import (ConnectorSpec, CredentialMaterial, DocumentTally, Field,
                   first, group_lines, iso_date, register)
from .transport import RestTransport

SYSTEM = "netsuite"

#: SuiteQL's own page ceiling.
_PAGE = 1000

#: Transaction statuses that are not trade. NetSuite spells these as display
#: names through BUILTIN.DF; a voided invoice never was revenue.
_EXCLUDED_STATUS = {"voided"}


def _percent(value: str) -> str:
    """RFC 3986 percent-encoding, the dialect OAuth 1.0a signatures require."""
    return quote(str(value), safe="~-._")


class NetSuiteClient(RestTransport):
    """Signed SuiteQL against one NetSuite account."""

    system = "NetSuite"
    #: SuiteQL is generously limited relative to the record API; stay modest.
    requests_per_minute = 60

    def __init__(self, *, account_id: str, consumer_key: str,
                 consumer_secret: str, token_id: str, token_secret: str,
                 http: Any = None) -> None:
        super().__init__(http=http)
        # The URL host wants the account lowercased with dashes; the signature
        # realm wants it as NetSuite states it (uppercase, underscores).
        self._realm = account_id.strip().upper().replace("-", "_")
        host_account = account_id.strip().lower().replace("_", "-")
        self._base = f"https://{host_account}.suitetalk.api.netsuite.com"
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._token_id = token_id
        self._token_secret = token_secret

    # ── TBA signing ──────────────────────────────────────────────────────────
    def _tba_header(self, method: str, url: str,
                    params: dict[str, Any]) -> str:
        """One request's ``Authorization`` header.

        The base string covers the method, the bare URL and every query
        parameter alongside the oauth_* set; the JSON body is deliberately not
        part of an OAuth 1.0a signature. A fresh nonce per call — reusing one
        is the replay NetSuite rejects.
        """
        oauth = {
            "oauth_consumer_key": self._consumer_key,
            "oauth_token": self._token_id,
            "oauth_nonce": _secrets.token_hex(16),
            "oauth_timestamp": str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_version": "1.0",
        }
        both = {**{k: str(v) for k, v in params.items()}, **oauth}
        pairs = "&".join(f"{_percent(k)}={_percent(both[k])}"
                         for k in sorted(both))
        split = urlsplit(url)
        bare = f"{split.scheme}://{split.netloc}{split.path}"
        base = f"{method.upper()}&{_percent(bare)}&{_percent(pairs)}"
        key = f"{_percent(self._consumer_secret)}&{_percent(self._token_secret)}"
        digest = hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()
        import base64

        signature = base64.b64encode(digest).decode()
        fields = ", ".join(f'{k}="{_percent(v)}"' for k, v in oauth.items())
        return (f'OAuth realm="{self._realm}", {fields}, '
                f'oauth_signature="{_percent(signature)}"')

    def suiteql(self, query: str) -> Iterator[dict[str, Any]]:
        """Every row of one query, across pages."""
        url = f"{self._base}/services/rest/query/v1/suiteql"
        offset = 0
        while True:
            params = {"limit": _PAGE, "offset": offset}
            headers = {
                "Authorization": self._tba_header("POST", url, params),
                "Prefer": "transient",
            }
            body = self.request("POST", url, params=params, json={"q": query},
                                headers=headers, replayable=True)
            for row in body.get("items") or []:
                row.pop("links", None)
                yield row
            if not body.get("hasMore"):
                return
            offset += _PAGE

    def ping(self) -> dict[str, Any]:
        """Verify the four secrets sign and the account answers, cheaply."""
        rows = list(self.suiteql(
            "SELECT id, companyname FROM subsidiary ORDER BY id"))
        if not rows:
            raise SourceAuthError(
                "NetSuite answered but returned no subsidiaries — the token's "
                "role may have no access.")
        return {
            "authenticated": True,
            "organization_found": True,
            "organization_name": str(rows[0].get("companyname") or ""),
            "visible_organizations": [
                {"id": str(r.get("id")), "name": str(r.get("companyname") or "")}
                for r in rows],
        }


# ── translators: SuiteQL rows → canonical payloads ───────────────────────────
def translate_entity(row: dict[str, Any]) -> dict[str, Any]:
    """A customer or vendor row → canonical contact payload."""
    name = first(row, "companyname", "entityid", "altname") or ""
    inactive = str(row.get("isinactive") or "F").upper() == "T"
    return {
        "contact_id": str(row.get("id")),
        "contact_name": str(name),
        "status": "inactive" if inactive else "active",
    }


def translate_item(row: dict[str, Any]) -> dict[str, Any]:
    inactive = str(row.get("isinactive") or "F").upper() == "T"
    item_type = str(row.get("itemtype") or "")
    return {
        "item_id": str(row.get("id")),
        "name": str(first(row, "displayname", "itemid") or ""),
        "sku": str(row.get("itemid") or "") or None,
        "status": "inactive" if inactive else "active",
        # Service vs inventory matters to the stock logic downstream.
        "item_type": "service" if item_type.lower().startswith("service") else
                     item_type.lower() or None,
    }


def _negate_for_sale(value: Any, is_sale: bool) -> Any:
    """Undo the GL sign on income lines; leave supplier lines as stated."""
    if not is_sale or value in (None, ""):
        return value
    try:
        return -float(value)
    except (TypeError, ValueError):
        return value


def translate_document(header: dict[str, Any], lines: list[dict[str, Any]], *,
                       kind: str) -> dict[str, Any]:
    """One transaction header plus its item lines → a canonical document.

    ``kind`` is ``invoice`` or ``bill``; sales lines get their GL sign undone.
    """
    is_sale = kind == "invoice"
    doc_id = str(header.get("id"))
    out: dict[str, Any] = {
        f"{kind}_id": doc_id,
        f"{kind}_number": str(header.get("tranid") or "") or None,
        ("customer_id" if is_sale else "vendor_id"):
            (str(header["entity"]) if header.get("entity") else None),
        "date": iso_date(header.get("trandate")),
        "due_date": iso_date(header.get("duedate")),
        "status": str(header.get("status") or ""),
        "total": header.get("foreigntotal"),
        "balance": header.get("foreignamountunpaid"),
        "currency_code": (str(header.get("currency_code")).upper()
                          if header.get("currency_code") else None),
        "last_modified_time": header.get("lastmodified") or "",
        "line_items": [
            {
                "line_item_id": str(ln.get("line_id")),
                "item_id": str(ln.get("item")),
                "quantity": _negate_for_sale(ln.get("quantity"), is_sale),
                "rate": ln.get("rate"),
                "item_total": _negate_for_sale(ln.get("netamount"), is_sale),
                "name": str(ln.get("memo") or "") or None,
            }
            for ln in lines if ln.get("item")
        ],
    }
    return out


def translate_payment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "payment_id": str(row.get("id")),
        "customer_id": (str(row["entity"]) if row.get("entity") else None),
        "date": iso_date(row.get("trandate")),
        # A customer payment's foreigntotal carries the GL's negative sign.
        "amount": _negate_for_sale(row.get("foreigntotal"), True),
        "last_modified_time": row.get("lastmodified") or "",
        "invoices": [],
    }


def translate_purchase_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "purchaseorder_id": str(row.get("id")),
        "purchaseorder_number": str(row.get("tranid") or "") or None,
        "vendor_id": (str(row["entity"]) if row.get("entity") else None),
        "date": iso_date(row.get("trandate")),
        "expected_delivery_date": iso_date(row.get("duedate")) or "",
        "status": str(row.get("status") or ""),
        "total": row.get("foreigntotal"),
        "receives": [],
    }


def translate_sales_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "salesorder_id": str(row.get("id")),
        "salesorder_number": str(row.get("tranid") or "") or None,
        "customer_id": (str(row["entity"]) if row.get("entity") else None),
        "date": iso_date(row.get("trandate")),
        "shipment_date": iso_date(row.get("shipdate")),
        "status": str(row.get("status") or ""),
        "total": _negate_for_sale(row.get("foreigntotal"), True),
    }


def _is_trade(payload: dict[str, Any]) -> bool:
    return str(payload.get("status") or "").strip().lower() not in _EXCLUDED_STATUS


class NetSuiteSource:
    """The read source the sync layer drives — canonical payloads out.

    One flat SuiteQL query per kind (headers) plus one for lines, grouped in
    memory per window; the window keeps that bounded the same way it bounds
    the Zoho pull.
    """

    def __init__(self, client: NetSuiteClient, *,
                 subsidiary_id: Optional[str] = None,
                 since: Optional[date] = None,
                 until: Optional[date] = None) -> None:
        self._client = client
        self._subsidiary = (str(subsidiary_id) if subsidiary_id else None)
        # The names the sync's windowing and mirror sweep probe with getattr.
        self._since = since
        self._until = until
        self._tally = DocumentTally()

    # The attributes the sync layer reads off any source.
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

    # ── query building ───────────────────────────────────────────────────────
    def _window_clause(self, alias: str = "t") -> str:
        parts = []
        if self._since is not None:
            parts.append(f"{alias}.trandate >= "
                         f"TO_DATE('{self._since.isoformat()}', 'YYYY-MM-DD')")
        if self._until is not None:
            parts.append(f"{alias}.trandate <= "
                         f"TO_DATE('{self._until.isoformat()}', 'YYYY-MM-DD')")
        if self._subsidiary and self._subsidiary.isdigit():
            parts.append(f"{alias}.subsidiary = {int(self._subsidiary)}")
        return (" AND " + " AND ".join(parts)) if parts else ""

    _HEADER_COLUMNS = (
        "t.id, t.tranid, TO_CHAR(t.trandate, 'YYYY-MM-DD') AS trandate, "
        "TO_CHAR(t.duedate, 'YYYY-MM-DD') AS duedate, t.entity, "
        "BUILTIN.DF(t.status) AS status, t.foreigntotal, "
        "t.foreignamountunpaid, c.symbol AS currency_code, "
        "TO_CHAR(t.lastmodifieddate, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS lastmodified")

    def _documents(self, ns_type: str, kind: str) -> Iterator[dict[str, Any]]:
        """Headers joined to their item lines, for one transaction type."""
        where = f"t.type = '{ns_type}'" + self._window_clause()
        headers = list(self._client.suiteql(
            f"SELECT {self._HEADER_COLUMNS} "
            f"FROM transaction t LEFT JOIN currency c ON c.id = t.currency "
            f"WHERE {where} ORDER BY t.id"))
        lines = group_lines(self._client.suiteql(
            "SELECT tl.transaction AS tid, tl.id AS line_id, tl.item, "
            "tl.quantity, tl.rate, tl.netamount, tl.memo "
            "FROM transactionline tl "
            "JOIN transaction t ON t.id = tl.transaction "
            f"WHERE {where} AND tl.mainline = 'F' AND tl.taxline = 'F' "
            "AND tl.item IS NOT NULL ORDER BY tl.transaction, tl.id"),
            "tid")
        for header in headers:
            payload = translate_document(
                header, lines.get(str(header.get("id")), []), kind=kind)
            if not _is_trade(payload):
                continue
            self._tally.saw(kind, payload[f"{kind}_id"])
            self._tally.documents_fetched += 1
            yield payload
        self._tally.complete(kind)

    # ── the source protocol ──────────────────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return (translate_entity(r) for r in self._client.suiteql(
            "SELECT id, entityid, companyname, isinactive FROM customer "
            "ORDER BY id"))

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        return (translate_entity(r) for r in self._client.suiteql(
            "SELECT id, entityid, companyname, isinactive FROM vendor "
            "ORDER BY id"))

    def list_items(self) -> Iterable[dict[str, Any]]:
        return (translate_item(r) for r in self._client.suiteql(
            "SELECT id, itemid, displayname, itemtype, isinactive FROM item "
            "ORDER BY id"))

    def list_invoices(self, skip: Optional[SkipPredicate] = None
                      ) -> Iterable[dict[str, Any]]:
        return self._documents("CustInvc", "invoice")

    def list_bills(self, skip: Optional[SkipPredicate] = None
                   ) -> Iterable[dict[str, Any]]:
        return self._documents("VendBill", "bill")

    def list_customer_payments(self, skip: Optional[SkipPredicate] = None
                               ) -> Iterable[dict[str, Any]]:
        where = "t.type = 'CustPymt'" + self._window_clause()
        return (translate_payment(r) for r in self._client.suiteql(
            "SELECT t.id, TO_CHAR(t.trandate, 'YYYY-MM-DD') AS trandate, "
            "t.entity, t.foreigntotal, "
            "TO_CHAR(t.lastmodifieddate, 'YYYY-MM-DD\"T\"HH24:MI:SS') "
            "AS lastmodified "
            f"FROM transaction t WHERE {where} ORDER BY t.id"))

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        where = "t.type = 'PurchOrd'" + self._window_clause()
        return (translate_purchase_order(r) for r in self._client.suiteql(
            f"SELECT {self._HEADER_COLUMNS} "
            "FROM transaction t LEFT JOIN currency c ON c.id = t.currency "
            f"WHERE {where} ORDER BY t.id"))

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        where = "t.type = 'SalesOrd'" + self._window_clause()
        return (translate_sales_order(r) for r in self._client.suiteql(
            "SELECT t.id, t.tranid, TO_CHAR(t.trandate, 'YYYY-MM-DD') "
            "AS trandate, TO_CHAR(t.shipdate, 'YYYY-MM-DD') AS shipdate, "
            "t.entity, BUILTIN.DF(t.status) AS status, t.foreigntotal "
            f"FROM transaction t WHERE {where} ORDER BY t.id"))


def _build_source(material: CredentialMaterial,
                  since: Optional[date] = None) -> NetSuiteSource:
    account, _, subsidiary = material.external_org_id.partition(":")
    client = NetSuiteClient(
        account_id=account or material.value("company_id"),
        consumer_key=material.value("consumer_key"),
        consumer_secret=material.value("consumer_secret"),
        token_id=material.value("token_id"),
        token_secret=material.value("token_secret"),
    )
    return NetSuiteSource(client, subsidiary_id=subsidiary or None, since=since)


SPEC = register(ConnectorSpec(
    key=SYSTEM,
    label="Oracle NetSuite",
    company_term="account",
    credential_fields=(
        Field("consumer_key", "Consumer key",
              help="From the integration record (Setup → Integration → "
                   "Manage Integrations) with token-based authentication "
                   "enabled."),
        Field("consumer_secret", "Consumer secret", secret=True),
        Field("token_id", "Token ID",
              help="An access token for a role with SuiteAnalytics/REST "
                   "query permission (Setup → Users/Roles → Access Tokens)."),
        Field("token_secret", "Token secret", secret=True),
    ),
    connection_fields=(
        Field("company_id", "Account ID",
              placeholder="1234567 or 1234567:3",
              help="Your NetSuite account ID. For a OneWorld account, append "
                   ":subsidiary-id to scope this connection to one "
                   "subsidiary's transactions."),
    ),
    external_id_field="company_id",
    setup_note=(
        "Reads NetSuite through SuiteQL (read-only queries) with token-based "
        "authentication. An administrator creates one integration record and "
        "one access token; the four values it mints are entered here once. "
        "Item stock levels and payment-to-invoice applications are not read "
        "in this version and their screens will say so rather than estimate."),
    build_source=_build_source,
))
