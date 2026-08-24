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

from ..errors import (IngestionError, SourceAuthError, SourceScopeError,
                      SourceWriteRefused, SourceWriteUncertain,
                      SourceWriteUnknown)
from ..source import SkipPredicate
from ..write_settle import settle_by_read
from .base import (EXTERNAL_REF_MAX, ConnectorSpec, CredentialMaterial,
                   DocumentTally, Field, Permission, WrittenDocument,
                   first, group_lines, iso_date, money, quote_literal,
                   register)
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

    def record(self, method: str, path: str, *, json: Any = None,
               headers: Optional[dict[str, str]] = None,
               replayable: Optional[bool] = None,
               expect_json: bool = True) -> Any:
        """One call against the REST *record* API, signed the same way.

        A different surface from ``suiteql`` — that one queries, this one holds
        records — but the same account, the same token and the same TBA
        signature, so nothing about the credential changes to reach it.
        """
        url = f"{self._base}/services/rest/record/v1/{path.lstrip('/')}"
        sent = {"Authorization": self._tba_header(method, url, {}),
                **(headers or {})}
        return self.request(method, url, json=json, headers=sent,
                            replayable=replayable, expect_json=expect_json)

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
    # ── writing ─────────────────────────────────────────────────────────────
    def create_sales_quotes(self, customer: str, lines: list[dict], *,
                            customer_ref: Optional[str] = None,
                            reference: Optional[str] = None) -> WrittenDocument:
        """Create one estimate, through an upsert that cannot duplicate it.

        NetSuite is the one system here whose write is idempotent by
        construction. ``PUT …/estimate/eid:{externalId}`` upserts: a record of
        this type carrying that external id is updated, and one is created if
        none exists. So sending twice cannot make two estimates — which is the
        failure the Business Central and Acumatica writers each need a
        pre-flight read to prevent, and which no amount of care fully closes
        there.

        That difference is why this marks the call replayable. The transport
        refuses by default to re-send a non-GET past a 5xx, because it cannot
        tell "never arrived" from "arrived, answer lost" — a distinction that
        stops mattering when arriving twice and arriving once have the same
        result. The ``NetSuite-Idempotency-Key`` header is NetSuite's own belt
        to that braces, and it is sent for the same reason.

        The settle read is still here, because "cannot duplicate" is not "must
        have worked": after the retries are spent the outcome is still unknown,
        and a person still needs to be told which of the three it was.
        """
        if not reference:
            raise SourceWriteRefused(
                "This quote has no reference, so it could not be given an "
                "external id — the thing that makes the write idempotent and "
                "findable afterwards. Nothing was sent.")
        if not customer_ref:
            raise SourceWriteRefused(
                "This quote is not attached to a NetSuite customer, so there is "
                "no entity to create it against. Nothing was sent.")
        if len(reference) > EXTERNAL_REF_MAX:
            raise SourceWriteRefused(
                f"The reference {reference!r} is longer than the "
                f"{EXTERNAL_REF_MAX} characters this platform will send as an "
                f"external id, so it could not be read back reliably. Nothing "
                f"was sent.")
        if not lines:
            raise SourceWriteRefused(
                "This quote has no priced lines, so there is nothing to create "
                "in NetSuite. An empty estimate in a customer's ledger is worse "
                "than none. Nothing was sent.")
        missing = [str(ln.get("code") or "?") for ln in lines if not ln.get("itemId")]
        if missing:
            raise SourceWriteRefused(
                "These lines carry no NetSuite internal id, and an estimate line "
                "must name an item that already exists there: " + ", ".join(missing),
                codes=[c for c in missing if c != "?"])
        unpriced = [str(ln.get("code") or "?") for ln in lines
                    if ln.get("rate") is None or ln.get("qty") is None]
        if unpriced:
            raise SourceWriteRefused(
                "These lines have no price or no quantity, and NetSuite would "
                "price an omitted rate from the item record — quoting a number "
                "nobody here chose: " + ", ".join(unpriced),
                codes=[c for c in unpriced if c != "?"])

        # The pre-flight is not here for safety — the upsert cannot duplicate,
        # which is the whole point of keying it. It is here for *honesty*: an
        # upsert answers 204 and cannot say whether it created an estimate or
        # updated one already there, and those are different things to tell
        # somebody. Asking first is the only way to know which claim to make.
        existed = self._estimate_by_reference(reference) is not None

        body = {
            "externalId": reference,
            "entity": {"id": str(customer_ref)},
            "item": {"items": [{"item": {"id": str(ln["itemId"])},
                                "quantity": money(ln.get("qty")),
                                "rate": money(ln.get("rate"))} for ln in lines]},
        }
        try:
            self._client.record(
                "PUT", f"estimate/eid:{reference}", json=body,
                headers={"NetSuite-Idempotency-Key": reference},
                # Safe to re-send precisely because the upsert is keyed. See the
                # docstring: this is the one connector where that is true.
                replayable=True,
                # A successful upsert answers 204 with no body, so there is
                # nothing to parse — and nothing to learn from it either, which
                # is the other half of why the read below is not optional.
                expect_json=False)
        except (SourceScopeError, SourceAuthError):
            raise
        except SourceWriteUncertain as e:
            detail = str(e)
            return settle_by_read(
                lambda: self._estimate_by_reference(reference),
                lambda row: self._found(row, customer, len(lines), reference,
                                        already_existed=True),
                unknown_message=lambda err: (
                    f"The estimate could not be completed ({detail}), and "
                    f"NetSuite could not be re-read to find out ({err}) — "
                    f"whether it was created cannot be established. Look for "
                    f"external id {reference} before sending again."),
                refused_message=(
                    f"The estimate was not created ({detail}). NetSuite holds "
                    f"nothing under external id {reference}, and because the "
                    f"write is keyed on that id, sending again is safe."),
                reference=reference)
        except IngestionError as e:
            raise SourceWriteRefused(
                f"NetSuite refused the estimate: {e}") from e

        # Read back. Not belt-and-braces: the upsert answers 204 with no body
        # at all, so there is no echo to trust — and even a body would not say
        # whether it created the estimate or updated one already there, which
        # are different things to tell somebody.
        landed = self._estimate_by_reference(reference)
        if landed is None:
            raise SourceWriteUnknown(
                f"NetSuite accepted the estimate under external id {reference} "
                f"and then did not return it, so whether it is there cannot be "
                f"established. Look it up before sending again.",
                reference=reference)
        return self._found(landed, customer, len(lines), reference,
                           already_existed=existed)

    def _estimate_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        """The estimate carrying this external id, with its line count.

        Read through SuiteQL rather than the record API: the connector already
        speaks it, and one query answers both halves — the document and how many
        lines it holds — where the record API would need a second call.
        """
        rows = list(self._client.suiteql(
            "SELECT t.id, t.tranid, "
            "(SELECT COUNT(*) FROM transactionline tl "
            " WHERE tl.transaction = t.id AND tl.item IS NOT NULL) AS lines "
            "FROM transaction t "
            f"WHERE t.type = 'Estim' AND t.externalid = '{quote_literal(reference)}'"))
        return rows[0] if rows else None

    def _found(self, row: dict[str, Any], customer: str, sent_lines: int,
               reference: str, *, already_existed: bool) -> WrittenDocument:
        """The estimate NetSuite holds, reported as what it *is*.

        ``already_existed`` is passed in because nothing here can derive it: the
        upsert answers 204, and a record read back afterwards looks identical
        whether this call created it or found it. Only the caller, which looked
        before writing, knows. It used to be hardcoded ``False``, so sending the
        same quote twice reported "created" both times — about an estimate the
        second call had merely updated.

        The line count is still read back and still checked: a count that does
        not match is a different document under our external id, which is
        neither a send to report nor safe to overwrite silently.
        """
        held = row.get("lines")
        if held is None:
            raise SourceWriteUnknown(
                f"NetSuite holds an estimate under external id {reference} but "
                f"did not say how many lines it has, so whether it is complete "
                f"cannot be established. Check it before sending again.",
                reference=reference)
        if int(held) != sent_lines:
            raise SourceWriteUnknown(
                f"NetSuite holds estimate {row.get('tranid')} under external id "
                f"{reference}, but with {int(held)} lines where this quote has "
                f"{sent_lines}. It is not the same document, so it is neither "
                f"safe to report as sent nor safe to send again — check it.",
                reference=reference)
        number = str(row.get("tranid") or "")
        if not number:
            raise SourceWriteUnknown(
                "NetSuite returned an estimate with no document number, so it "
                "cannot be named to whoever has to find it.", reference=reference)
        return WrittenDocument(document_id=str(row.get("id") or ""),
                               number=number, customer=customer,
                               line_count=int(held),
                               already_existed=already_existed)

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
        "Reads NetSuite through SuiteQL (read-only queries) and writes one "
        "record type through the REST record API, both with token-based "
        "authentication. An administrator creates one integration record and "
        "one access token; the four values it mints are entered here once. "
        "Item stock levels and payment-to-invoice applications are not read "
        "in this version and their screens will say so rather than estimate."),
    permission_note=(
        "Granted on the role the access token is issued for, at Setup → "
        "Users/Roles → Manage Roles. View level is enough everywhere except "
        "Estimate, which needs Create for the Send action — leave it at View "
        "and everything reads, and every send refuses."),
    permissions=(
        Permission("Setup → Log in using Access Tokens",
                   "The token-based sign-in itself. Without it the four values "
                   "are refused before any record is asked for."),
        Permission("Setup → REST Web Services",
                   "SuiteQL is served over the REST endpoint, so this gates "
                   "every read below."),
        Permission("Reports → SuiteAnalytics Workbook",
                   "The permission SuiteQL queries themselves run under. A role "
                   "with every list below and not this one authenticates and "
                   "then answers nothing."),
        Permission("Lists → Customers (View)",
                   "Customers — who was sold to.", reads=("contacts",)),
        Permission("Lists → Items (View)",
                   "Items — the product master.", reads=("items",)),
        Permission("Transactions → Invoice (View)",
                   "Invoices — what was sold, and for how much.",
                   reads=("invoices",)),
        Permission("Transactions → Bill (View)",
                   "Vendor bills — what it cost. Without this there is no "
                   "margin anywhere in the platform, only revenue.",
                   reads=("bills",)),
        Permission("Transactions → Customer Payment (View)",
                   "Payments — when money actually arrived. Without it an "
                   "invoice looks paid the day it was raised.",
                   reads=("customer_payments",)),
        Permission("Lists → Vendors (View)",
                   "Suppliers. Optional: bills still land without it, with the "
                   "supplier known only by its internal id.",
                   required=False, reads=("vendors",)),
        Permission("Transactions → Estimate (Create)",
                   "Estimates — the record a quote built here becomes. The one "
                   "thing this platform writes to NetSuite, and the only "
                   "permission on this list above View level. Without it "
                   "everything else works and every send refuses.",
                   required=False, writes=("sales_quotes",)),
        Permission("Transactions → Sales Order (View)",
                   "Sales orders — demand promised but not yet invoiced. "
                   "Optional: without it the platform sees only what has "
                   "already been billed.",
                   required=False, reads=("sales_orders",)),
        Permission("Transactions → Purchase Order (View)",
                   "Purchase orders — what is on the way from suppliers, and "
                   "how late. Optional: feeds the Supply screen.",
                   required=False, reads=("purchase_orders",)),
    ),
    build_source=_build_source,
))
