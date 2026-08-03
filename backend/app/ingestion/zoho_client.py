"""Live Zoho Books read client.

Read-only by construction: every call is a GET, and no method here can create,
update or delete anything in Zoho. The platform treats Zoho as the system of
record and never writes back.

Four things about the Books API shape the design:

1. **Data centre matters.** A refresh token issued in one DC (``.in``, ``.com``,
   ``.eu``, ``.com.au``, ``.jp``) is rejected by every other, and the token host
   is different from the API host. Both are configurable.
2. **List endpoints omit line items.** ``/invoices`` and ``/bills`` return
   summary rows; the lines a signal is computed from only appear on the detail
   record. So a pull is one list call per page plus one detail call per
   document, which is why the history window exists.
3. **Drafts and voids are not trade.** They are excluded, so a cancelled
   invoice never counts as revenue a customer stopped spending.
4. **The API is rate limited, and one call per document adds up fast.** Calls
   are therefore *paced* to stay under the limit rather than fired as fast as
   the network allows, and a 429 is backed off in tens of seconds — a rate
   limiter is not a transient fault, and retrying a second later just burns the
   retry budget. A pull that is throttled out anyway is resumable: the caller
   supplies a ``skip`` predicate for documents it already holds, so the next
   attempt pays only for what is missing.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Iterator, Optional

from ..config import settings

log = logging.getLogger("pie_portal.zoho")

# ``skip(doc_id, last_modified) -> bool``: True when the caller already holds
# this document unchanged and the detail call can be spared.
SkipPredicate = Callable[[str, str], bool]

# Invoice/bill statuses that do not represent real trade.
_EXCLUDED_INVOICE_STATUS = {"draft", "void"}
_EXCLUDED_BILL_STATUS = {"draft", "void"}


@dataclass(frozen=True)
class ZohoCredentials:
    """One tenant's Zoho identity — everything that varies per organization.

    Deliberately narrow: pull tuning (pacing, retries, page size, history
    window) stays a shared, global operational setting in ``config.py``, since
    it's infrastructure behaviour, not an account identity. Only what actually
    differs between two Zoho Books organizations lives here.
    """

    organization_id: str          # the Zoho Books org id, NOT the platform's
    client_id: str
    client_secret: str
    refresh_token: str
    accounts_base: str = "https://accounts.zoho.in"
    api_base: str = "https://www.zohoapis.in/books/v3"

    @classmethod
    def from_settings(cls) -> "ZohoCredentials":
        """The pre-multi-tenant configuration path: one connection, from
        environment variables. Used only as the fallback for the platform's
        default organization when it has no stored connection of its own —
        every other organization must configure its own."""
        return cls(
            organization_id=settings.ZOHO_ORGANIZATION_ID,
            client_id=settings.ZOHO_CLIENT_ID,
            client_secret=settings.ZOHO_CLIENT_SECRET,
            refresh_token=settings.ZOHO_REFRESH_TOKEN,
            accounts_base=settings.ZOHO_ACCOUNTS_BASE,
            api_base=settings.ZOHO_API_BASE,
        )


class ZohoError(RuntimeError):
    """A Zoho call failed. Carries the API's own message where there is one."""


class ZohoAuthError(ZohoError):
    """Credentials were rejected — wrong DC, revoked token, or bad client."""


class ZohoThrottleError(ZohoError):
    """The rate limiter won. Distinct from other failures because the remedy is
    different: wait and resume, rather than fix a credential."""


class ZohoApiSource:
    """Read-only Zoho Books client.

    ``since`` bounds how far back documents are pulled. When omitted it falls
    back to ``ZOHO_SYNC_FROM`` and then to the rolling ``ZOHO_HISTORY_DAYS``
    window, so an operator can choose an explicit start date per run without
    changing configuration.
    """

    def __init__(self, http: Any = None, since: Optional[date] = None,
                 credentials: Optional[ZohoCredentials] = None,
                 until: Optional[date] = None) -> None:
        creds = credentials or ZohoCredentials.from_settings()
        self._creds = creds
        self._base = creds.api_base.rstrip("/")
        self._accounts = creds.accounts_base.rstrip("/")
        self._org = creds.organization_id
        self._http = http                      # injectable for tests
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._since = since or configured_since()
        # Upper bound of the window this source reads. Set when a long pull is
        # split into calendar slices so each one asks Zoho for its own months
        # instead of every source walking the whole ledger.
        self._until = until
        self._last_call_at: float = 0.0
        # Observable so a sync run can report what the pull actually cost.
        self.calls = 0
        self.documents_fetched = 0
        self.documents_resumed = 0

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
                ("organization_id", self._org),
                ("client_id", self._creds.client_id),
                ("client_secret", self._creds.client_secret),
                ("refresh_token", self._creds.refresh_token),
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
                "refresh_token": self._creds.refresh_token,
                "client_id": self._creds.client_id,
                "client_secret": self._creds.client_secret,
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

    # ── throttling ───────────────────────────────────────────────────────────
    def _sleep(self, seconds: float) -> None:
        """Single seam for every wait, so tests can run the real retry logic."""
        if seconds > 0:
            time.sleep(seconds)

    def _pace(self) -> None:
        """Hold calls to ZOHO_REQUESTS_PER_MINUTE.

        Staying under the limit is worth far more than recovering from it: a
        pull is thousands of calls, and one 429 used to end the whole run.
        """
        rpm = settings.ZOHO_REQUESTS_PER_MINUTE
        if rpm <= 0 or self._last_call_at == 0.0:
            return
        self._sleep(self._last_call_at + (60.0 / rpm) - time.monotonic())

    @staticmethod
    def _retry_after(resp: Any) -> Optional[float]:
        """Zoho's own instruction, when it sends one, beats any guess."""
        try:
            raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
            return max(0.0, float(raw)) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        """One authenticated GET, with retry on throttling and transient faults."""
        url = f"{self._base}/{path.lstrip('/')}"
        params = {k: v for k, v in params.items() if v is not None}
        params["organization_id"] = self._org

        last: Optional[str] = None
        throttled = False
        attempts = max(1, settings.ZOHO_MAX_RETRIES)
        for attempt in range(attempts):
            token = self._access_token()
            self._pace()
            resp = self._client().get(
                url, params=params,
                headers={"Authorization": f"Zoho-oauthtoken {token}"})
            self._last_call_at = time.monotonic()
            self.calls += 1

            if resp.status_code == 401:
                # Token may have been revoked mid-run; drop the cache and retry once.
                self._token, self._token_expires_at = None, 0.0
                last = "401 unauthorized"
                if attempt == 0:
                    continue
                raise ZohoAuthError(
                    "Zoho rejected the access token. Confirm the refresh token, the "
                    "client credentials and the data centre all belong to the same account.")
            if resp.status_code == 429:
                throttled = True
                last = "HTTP 429 (rate limited)"
                delay = self._retry_after(resp)
                if delay is None:
                    delay = min(settings.ZOHO_MAX_BACKOFF_SECONDS,
                                settings.ZOHO_THROTTLE_BACKOFF_SECONDS * (2 ** attempt))
                log.warning("zoho %s: rate limited, waiting %.0fs (attempt %d/%d)",
                            path, delay, attempt + 1, attempts)
                self._sleep(delay)
                continue
            if resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                self._sleep(min(settings.ZOHO_MAX_BACKOFF_SECONDS, 2 ** attempt))
                continue
            try:
                body = resp.json()
            except ValueError:
                raise ZohoError(f"Zoho returned non-JSON for {path} (HTTP {resp.status_code})")
            if resp.status_code != 200 or body.get("code", 0) not in (0, None):
                raise ZohoError(
                    f"Zoho error on {path}: {body.get('message') or resp.status_code}")
            return body

        if throttled:
            raise ZohoThrottleError(
                f"Zoho rate limited the pull at {path} and did not recover after "
                f"{attempts} attempts. Everything fetched so far has been kept — run the "
                "sync again later and it will resume from where it stopped.")
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
                # The identity layer's strongest customer key. Zoho names it
                # gst_no on the India edition; other editions omit it entirely,
                # which the matcher treats as "no evidence", not "no match".
                "gst_no": c.get("gst_no") or c.get("gst_treatment_gstin"),
                "status": (c.get("status") or "active"),
            }

    def list_items(self) -> Iterable[dict[str, Any]]:
        for i in self._paginate("items", "items"):
            yield {
                "item_id": str(i.get("item_id")),
                "name": i.get("name") or "",
                # The identity layer's item key. Often blank in Zoho — an item
                # with no SKU simply gets no suggestion, which is the honest
                # outcome rather than a guess from the name.
                "sku": i.get("sku"),
                "unit": i.get("unit"),
                "hsn_or_sac": i.get("hsn_or_sac") or i.get("hsn_code"),
                "status": (i.get("status") or "active"),
            }

    def _cutoff(self) -> date:
        return self._since or (date.today() - timedelta(days=settings.ZOHO_HISTORY_DAYS))

    def _window(self) -> dict[str, Optional[str]]:
        """The date bounds, as Zoho's own list filters.

        Sent to the API rather than applied after the fact. Filtering in Python
        still costs one list call per page of *every* document the company has
        ever issued, which is the bulk of a pull's calls and is charged against
        the same rate limit as useful work. Zoho does the filtering server-side
        for free.
        """
        return {
            "date_start": self._cutoff().isoformat(),
            "date_end": self._until.isoformat() if self._until else None,
        }

    def _documents(self, path: str, list_key: str, detail_key: str, id_field: str,
                   excluded_status: set[str],
                   skip: Optional[SkipPredicate] = None) -> Iterator[dict[str, Any]]:
        """List documents, then fetch each one's detail for its line items.

        ``skip`` lets the caller say "I already have this one, unchanged", which
        turns a resumed pull from thousands of detail calls into a handful of
        list calls.
        """
        cutoff = self._cutoff()
        until = self._until
        for row in self._paginate(path, list_key, sort_column="date", sort_order="D",
                                  **self._window()):
            status = str(row.get("status") or "").lower()
            if status in excluded_status:
                continue
            # Re-checked locally as well: the bounds above are a request to
            # Zoho, and a source that quietly ignored them would otherwise
            # double-count a document into two windows.
            raw_date = str(row.get("date") or "")
            try:
                doc_date = date.fromisoformat(raw_date)
            except ValueError:
                continue                      # unparseable date: skip, sync reports it
            if doc_date < cutoff or (until is not None and doc_date > until):
                continue
            doc_id = str(row.get(id_field))
            if skip is not None and skip(doc_id, str(row.get("last_modified_time") or "")):
                self.documents_resumed += 1
                continue
            detail = self._get(f"{path}/{doc_id}").get(detail_key) or {}
            if detail:
                self.documents_fetched += 1
                yield detail

    def list_invoices(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        for inv in self._documents("invoices", "invoices", "invoice", "invoice_id",
                                   _EXCLUDED_INVOICE_STATUS, skip=skip):
            yield {
                "invoice_id": str(inv.get("invoice_id")),
                "customer_id": str(inv.get("customer_id")),
                "date": inv.get("date"),
                "last_modified_time": inv.get("last_modified_time"),
                # Zoho's own record of who owns the sale. Mapped onto a platform
                # user by the sync layer; never guessed at when it is absent.
                "salesperson_id": (str(inv["salesperson_id"])
                                   if inv.get("salesperson_id") else None),
                "salesperson_name": inv.get("salesperson_name"),
                "line_items": [
                    {
                        "line_item_id": str(li.get("line_item_id")),
                        "item_id": str(li.get("item_id")),
                        "quantity": li.get("quantity"),
                        "rate": li.get("rate"),
                        "item_total": li.get("item_total"),
                        # A line-item discount, and Zoho's own resolved values
                        # for it — passed through raw exactly as on bills;
                        # normalize.py decides which is authoritative. Without
                        # these the net selling price silently becomes the
                        # pre-discount list rate.
                        "discount": li.get("discount"),
                        "discount_amount": li.get("discount_amount"),
                    }
                    for li in (inv.get("line_items") or [])
                    # A line with no item_id is a comment/charge row, not a product.
                    if li.get("item_id")
                ],
            }

    def list_bills(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        for bill in self._documents("bills", "bills", "bill", "bill_id",
                                    _EXCLUDED_BILL_STATUS, skip=skip):
            yield {
                "bill_id": str(bill.get("bill_id")),
                "date": bill.get("date"),
                "last_modified_time": bill.get("last_modified_time"),
                "line_items": [
                    {
                        "line_item_id": str(li.get("line_item_id")),
                        "item_id": str(li.get("item_id")),
                        "quantity": li.get("quantity"),
                        "rate": li.get("rate"),
                        # A line-item discount, and Zoho's own resolved values for
                        # it — passed through raw; normalize.py decides which is
                        # most authoritative. Never computed or interpreted here.
                        "discount": li.get("discount"),
                        "discount_amount": li.get("discount_amount"),
                        "item_total": li.get("item_total"),
                    }
                    for li in (bill.get("line_items") or [])
                    if li.get("item_id")
                ],
            }

    def list_users(self) -> Iterable[dict[str, Any]]:
        """Zoho's user list — the only thing that turns an invoice's
        ``salesperson_id`` into a person the platform knows."""
        for u in self._paginate("users", "users"):
            yield {
                "user_id": str(u.get("user_id")),
                "email": str(u.get("email") or "").strip().lower(),
                "name": u.get("name") or "",
                "status": str(u.get("status") or "active"),
            }


def configured_since() -> Optional[date]:
    """``ZOHO_SYNC_FROM`` as a date, or None for the rolling window.

    A malformed value is ignored rather than guessed at — the rolling window is
    the documented default, and silently pulling from the wrong date would be
    worse than pulling from the default one.
    """
    raw = (settings.ZOHO_SYNC_FROM or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        log.warning("ZOHO_SYNC_FROM=%r is not an ISO date (YYYY-MM-DD); "
                    "falling back to the rolling %d-day window.",
                    raw, settings.ZOHO_HISTORY_DAYS)
        return None
