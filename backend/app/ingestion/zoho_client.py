"""Live Zoho Books read client.

Read-only by construction: every call is a GET, and no method here can create,
update or delete anything in Zoho. The platform treats Zoho as the system of
record and never writes back.

Three things about the Books API shape the design:

1. **Data centre matters.** A refresh token issued in one DC (``.in``, ``.com``,
   ``.eu``, ``.com.au``, ``.jp``) is rejected by every other, and the token host
   is different from the API host. Both are configurable.
2. **List endpoints omit line items.** ``/invoices`` and ``/bills`` return
   summary rows; the lines a signal is computed from only appear on the detail
   record. So a pull is one list call per page plus one detail call per
   document, which is why history is bounded by ``ZOHO_HISTORY_DAYS``.
3. **Drafts and voids are not trade.** They are excluded, so a cancelled
   invoice never counts as revenue a customer stopped spending.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Iterable, Iterator, Optional

from ..config import settings

log = logging.getLogger("pie_portal.zoho")

# Invoice/bill statuses that do not represent real trade.
_EXCLUDED_INVOICE_STATUS = {"draft", "void"}
_EXCLUDED_BILL_STATUS = {"draft", "void"}


class ZohoError(RuntimeError):
    """A Zoho call failed. Carries the API's own message where there is one."""


class ZohoAuthError(ZohoError):
    """Credentials were rejected — wrong DC, revoked token, or bad client."""


class ZohoApiSource:
    """Read-only Zoho Books client."""

    def __init__(self, http: Any = None) -> None:
        self._base = settings.ZOHO_API_BASE.rstrip("/")
        self._accounts = settings.ZOHO_ACCOUNTS_BASE.rstrip("/")
        self._org = settings.ZOHO_ORGANIZATION_ID
        self._http = http                      # injectable for tests
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ── transport ────────────────────────────────────────────────────────────
    def _client(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=settings.ZOHO_TIMEOUT_SECONDS)
        return self._http

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("ZOHO_ORGANIZATION_ID", self._org),
                ("ZOHO_CLIENT_ID", settings.ZOHO_CLIENT_ID),
                ("ZOHO_CLIENT_SECRET", settings.ZOHO_CLIENT_SECRET),
                ("ZOHO_REFRESH_TOKEN", settings.ZOHO_REFRESH_TOKEN),
            )
            if not value
        ]
        if missing:
            raise ZohoAuthError(
                "Zoho credentials are incomplete — missing: " + ", ".join(missing))

    def _access_token(self) -> str:
        """Exchange the refresh token for an access token, cached until expiry.

        Zoho access tokens last an hour; refreshing on every call would burn the
        (limited) refresh quota for no benefit.
        """
        if self._token and time.time() < self._token_expires_at:
            return self._token
        self._require_credentials()
        resp = self._client().post(
            f"{self._accounts}/oauth/v2/token",
            params={
                "refresh_token": settings.ZOHO_REFRESH_TOKEN,
                "client_id": settings.ZOHO_CLIENT_ID,
                "client_secret": settings.ZOHO_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
        )
        try:
            body = resp.json()
        except ValueError:
            raise ZohoAuthError(f"Token endpoint returned non-JSON (HTTP {resp.status_code})")
        token = body.get("access_token")
        if not token:
            # Zoho reports auth problems in the body, often with HTTP 200.
            raise ZohoAuthError(
                f"Could not obtain an access token: {body.get('error') or body}. "
                f"Check that ZOHO_ACCOUNTS_BASE ({self._accounts}) matches the data "
                "centre the account belongs to.")
        self._token = str(token)
        # Refresh a minute early so a call never races the expiry.
        self._token_expires_at = time.time() + max(60, int(body.get("expires_in", 3600))) - 60
        return self._token

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        """One authenticated GET, with retry on throttling and transient faults."""
        url = f"{self._base}/{path.lstrip('/')}"
        params = {k: v for k, v in params.items() if v is not None}
        params["organization_id"] = self._org

        last: Optional[str] = None
        for attempt in range(4):
            token = self._access_token()
            resp = self._client().get(
                url, params=params,
                headers={"Authorization": f"Zoho-oauthtoken {token}"})

            if resp.status_code == 401:
                # Token may have been revoked mid-run; drop the cache and retry once.
                self._token, self._token_expires_at = None, 0.0
                last = "401 unauthorized"
                if attempt == 0:
                    continue
                raise ZohoAuthError(
                    "Zoho rejected the access token. Confirm the refresh token, the "
                    "client credentials and the data centre all belong to the same account.")
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                time.sleep(2 ** attempt)       # 1s, 2s, 4s
                continue
            try:
                body = resp.json()
            except ValueError:
                raise ZohoError(f"Zoho returned non-JSON for {path} (HTTP {resp.status_code})")
            if resp.status_code != 200 or body.get("code", 0) not in (0, None):
                raise ZohoError(
                    f"Zoho error on {path}: {body.get('message') or resp.status_code}")
            return body

        raise ZohoError(f"Zoho call to {path} failed after retries ({last}).")

    def _paginate(self, path: str, key: str, **params: Any) -> Iterator[dict[str, Any]]:
        """Yield every record across pages, bounded by ZOHO_MAX_PAGES."""
        for page in range(1, settings.ZOHO_MAX_PAGES + 1):
            body = self._get(path, page=page, per_page=settings.ZOHO_PAGE_SIZE, **params)
            rows = body.get(key) or []
            for row in rows:
                yield row
            ctx = body.get("page_context") or {}
            if not ctx.get("has_more_page") or not rows:
                return
        log.warning("zoho %s: stopped at the ZOHO_MAX_PAGES limit (%d) — raise it if "
                    "the account has more history than that.", path, settings.ZOHO_MAX_PAGES)

    # ── health ───────────────────────────────────────────────────────────────
    def ping(self) -> dict[str, Any]:
        """Verify credentials and the organization id without pulling data."""
        body = self._get("organizations")
        orgs = body.get("organizations") or []
        match = next((o for o in orgs if str(o.get("organization_id")) == str(self._org)), None)
        return {
            "authenticated": True,
            "organization_id": self._org,
            "organization_found": match is not None,
            "organization_name": (match or {}).get("name"),
            "currency": (match or {}).get("currency_code"),
            "visible_organizations": [
                {"organization_id": str(o.get("organization_id")), "name": o.get("name")}
                for o in orgs
            ],
        }

    # ── pulls (the ZohoSource protocol) ──────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        for c in self._paginate("contacts", "contacts", contact_type="customer"):
            yield {
                "contact_id": str(c.get("contact_id")),
                "contact_name": c.get("contact_name") or c.get("company_name") or "",
                "status": (c.get("status") or "active"),
            }

    def list_items(self) -> Iterable[dict[str, Any]]:
        for i in self._paginate("items", "items"):
            yield {
                "item_id": str(i.get("item_id")),
                "name": i.get("name") or "",
                "unit": i.get("unit"),
                "hsn_or_sac": i.get("hsn_or_sac") or i.get("hsn_code"),
                "status": (i.get("status") or "active"),
            }

    def _cutoff(self) -> date:
        return date.today() - timedelta(days=settings.ZOHO_HISTORY_DAYS)

    def _documents(self, path: str, list_key: str, detail_key: str, id_field: str,
                   excluded_status: set[str]) -> Iterator[dict[str, Any]]:
        """List documents, then fetch each one's detail for its line items."""
        cutoff = self._cutoff()
        for row in self._paginate(path, list_key, sort_column="date", sort_order="D"):
            status = str(row.get("status") or "").lower()
            if status in excluded_status:
                continue
            raw_date = str(row.get("date") or "")
            try:
                if date.fromisoformat(raw_date) < cutoff:
                    continue
            except ValueError:
                continue                      # unparseable date: skip, sync reports it
            doc_id = str(row.get(id_field))
            detail = self._get(f"{path}/{doc_id}").get(detail_key) or {}
            if detail:
                yield detail

    def list_invoices(self) -> Iterable[dict[str, Any]]:
        for inv in self._documents("invoices", "invoices", "invoice", "invoice_id",
                                   _EXCLUDED_INVOICE_STATUS):
            yield {
                "invoice_id": str(inv.get("invoice_id")),
                "customer_id": str(inv.get("customer_id")),
                "date": inv.get("date"),
                "line_items": [
                    {
                        "line_item_id": str(li.get("line_item_id")),
                        "item_id": str(li.get("item_id")),
                        "quantity": li.get("quantity"),
                        "rate": li.get("rate"),
                        "item_total": li.get("item_total"),
                    }
                    for li in (inv.get("line_items") or [])
                    # A line with no item_id is a comment/charge row, not a product.
                    if li.get("item_id")
                ],
            }

    def list_bills(self) -> Iterable[dict[str, Any]]:
        for bill in self._documents("bills", "bills", "bill", "bill_id",
                                    _EXCLUDED_BILL_STATUS):
            yield {
                "bill_id": str(bill.get("bill_id")),
                "date": bill.get("date"),
                "line_items": [
                    {
                        "line_item_id": str(li.get("line_item_id")),
                        "item_id": str(li.get("item_id")),
                        "quantity": li.get("quantity"),
                        "rate": li.get("rate"),
                    }
                    for li in (bill.get("line_items") or [])
                    if li.get("item_id")
                ],
            }
