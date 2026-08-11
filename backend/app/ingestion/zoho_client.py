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
#: A draft credit note has been given to nobody and a void one has been taken
#: back. Neither ever reduced a receivable, so neither belongs in a
#: reconstruction of what was owed.
_EXCLUDED_CREDIT_NOTE_STATUS = {"draft", "void"}


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
    #: Where these values were configured, in words, for the one message that
    #: has to send somebody to the right screen. A stored connection and the
    #: environment fallback fail identically at the token endpoint and are
    #: fixed in completely different places — an owner told to check
    #: ``ZOHO_ACCOUNTS_BASE`` about a connection they typed into a form goes
    #: looking for a variable that has no bearing on it and cannot be edited
    #: from where they are.
    configured_in: str = "this connection"

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
            configured_in="the ZOHO_* environment variables",
        )


# ── what Zoho's token endpoint is actually telling you ──────────────────────
#
# One sentence used to be appended to every refusal here — "check that
# ZOHO_ACCOUNTS_BASE matches the data centre the account belongs to" — and it is
# right for exactly one of these codes. For the most common one it is actively
# harmful: ``invalid_client_secret`` is Zoho saying it recognised the client id
# and rejected the *secret*, which means the data centre it was asked at is the
# one setting already known to be right. An owner who follows that advice
# switches the DC, the refresh token then fails as unknown there too, and one
# fixable connection has become two broken settings.
#
# Keyed by Zoho's own ``error`` string so a code this map does not know falls
# through to a fallback that names all three parts rather than picking one.
_TOKEN_ERROR_HELP: dict[str, str] = {
    "invalid_client_secret": (
        "Zoho recognised the client id and rejected the secret, so the data "
        "centre is not what is wrong here. Either the secret does not match the "
        "client id, or it was copied from another data centre's console — one "
        "client keeps its id everywhere but has a separate secret per data "
        "centre — or the refresh token was issued by a different client, which "
        "is what replacing only the token leaves behind. Rotate again, "
        "supplying the client id and secret alongside the token."),
    "invalid_client": (
        "Zoho does not recognise this client id at {accounts}. Either it is "
        "mistyped, or the app is registered in a different data centre from the "
        "one set in {configured_in}."),
    "invalid_code": (
        "Zoho rejected the refresh token itself — revoked, already replaced, or "
        "issued in a different data centre, since a token from one is refused by "
        "every other. The data centre is set in {configured_in} and is currently "
        "{accounts}."),
}

_TOKEN_ERROR_FALLBACK = (
    "Zoho refused the sign-in without saying which part of it failed. The "
    "client id, the client secret and the refresh token must all come from one "
    "app in one data centre; that data centre is set in {configured_in} and is "
    "currently {accounts}.")


def token_error_help(error: Any, *, accounts_base: str, configured_in: str) -> str:
    """What to actually go and change, for one Zoho token-endpoint refusal."""
    template = _TOKEN_ERROR_HELP.get(str(error or "").strip(), _TOKEN_ERROR_FALLBACK)
    return template.format(accounts=accounts_base, configured_in=configured_in)


class ZohoError(RuntimeError):
    """A Zoho call failed. Carries the API's own message where there is one."""


class ZohoAuthError(ZohoError):
    """Credentials were rejected — wrong DC, revoked token, or bad client."""


class ZohoThrottleError(ZohoError):
    """The rate limiter won. Distinct from other failures because the remedy is
    different: wait and resume, rather than fix a credential."""


class ZohoWriteUncertain(ZohoError):
    """A write was sent and its outcome is unknown.

    Raised instead of retrying when a non-idempotent call fails in a way that
    cannot distinguish "Zoho never saw it" from "Zoho did it and the answer was
    lost" — a 5xx, or a dropped connection. Replaying either of those is how one
    quote becomes two estimates in a customer's inbox, so the caller is told the
    truth and given a way to look the record up instead.
    """


class ZohoScopeError(ZohoAuthError):
    """The credentials are fine; this *endpoint* was not granted.

    A subclass of ``ZohoAuthError`` so nothing that already handles an auth
    failure stops working, but distinguishable because the remedy is completely
    different. A revoked token means re-authorise the connection; a missing
    scope means re-authorise it **asking for one more permission**, and telling
    somebody to check their client secret when the secret is correct is how an
    afternoon disappears.

    Zoho signals this as HTTP 401 with body code 57 on one endpoint while every
    other endpoint keeps working — which is exactly what a fresh, valid token
    with a narrow grant looks like.
    """

    def __init__(self, message: str, *, path: str, scope: Optional[str]) -> None:
        super().__init__(message)
        self.path = path
        self.scope = scope


#: Which OAuth scope each list endpoint needs, so a 401 can name the missing
#: permission instead of describing the symptom. Keyed on the first path
#: segment because detail calls (``customerpayments/12345``) need the same one.
SCOPE_FOR_PATH: dict[str, str] = {
    "contacts": "ZohoBooks.contacts.READ",
    "items": "ZohoBooks.settings.READ",
    "invoices": "ZohoBooks.invoices.READ",
    "creditnotes": "ZohoBooks.creditnotes.READ",
    # Locations and per-location stock both sit behind the settings scope, the
    # same one the item master already needs — so a connection that can read
    # items can read where they sit.
    "locations": "ZohoBooks.settings.READ",
    "itemdetails": "ZohoBooks.settings.READ",
    "bills": "ZohoBooks.bills.READ",
    "customerpayments": "ZohoBooks.customerpayments.READ",
    "purchaseorders": "ZohoBooks.purchaseorders.READ",
    "salesorders": "ZohoBooks.salesorders.READ",
    "vendorpayments": "ZohoBooks.vendorpayments.READ",
    "users": "ZohoBooks.users.READ",
}


def scope_for_path(path: str) -> Optional[str]:
    return SCOPE_FOR_PATH.get(path.lstrip("/").split("/", 1)[0].split("?", 1)[0])


#: Paths that cannot answer on their own — they need ids a previous call
#: produced, and a probe is meant to cost one request and carry no state.
_UNPROBEABLE = frozenset({"itemdetails"})


def probe_paths() -> dict[str, str]:
    """One cheap listing per distinct scope: ``{scope: path to ask it with}``.

    Derived from ``SCOPE_FOR_PATH`` rather than written out a second time, so an
    endpoint cannot be added with a scope the check never probes. First entry
    wins, which is why this returns a *scope* map and not a path map:
    ``settings.READ`` gates items, locations and per-location stock alike, and
    asking three times triples the cost of the answer without changing it.
    """
    out: dict[str, str] = {}
    for path, scope in SCOPE_FOR_PATH.items():
        if path not in _UNPROBEABLE:
            out.setdefault(scope, path)
    return out


class ZohoTransport:
    """Authenticated, paced, retrying HTTP against one Zoho Books company.

    Everything true of *any* Books call lives here: exchanging the refresh
    token, staying under the rate limit, backing off a 429, telling a missing
    scope apart from a bad token, and turning Zoho's own error body into the
    taxonomy above.

    It is a class of its own so the Quote Builder's write adapter
    (``zoho_books_service.ZohoBooksService``) shares it instead of growing a
    second OAuth stack. Two auth paths against one API is how a rotated
    credential ends up applied on one side and stale on the other.

    ``_request`` is method-aware on purpose. A GET may be replayed freely, so it
    keeps the full retry budget. Anything else is replayed only where the
    request provably never reached the books — a rejected token, a rate-limit
    refusal — and never after a 5xx, which cannot be told apart from a
    successful write whose response was lost.
    """

    #: Methods safe to replay. Replaying anything else risks a duplicate write.
    _REPLAYABLE = frozenset({"GET"})

    def __init__(self, http: Any = None,
                 credentials: Optional[ZohoCredentials] = None) -> None:
        creds = credentials or ZohoCredentials.from_settings()
        self._creds = creds
        self._base = creds.api_base.rstrip("/")
        self._accounts = creds.accounts_base.rstrip("/")
        self._org = creds.organization_id
        self._http = http                      # injectable for tests
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._last_call_at: float = 0.0
        # Observable so a sync run can report what the pull actually cost.
        self.calls = 0

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
            error = body.get("error") or body
            raise ZohoAuthError(
                f"Could not obtain an access token: {error}. "
                + token_error_help(error, accounts_base=self._accounts,
                                   configured_in=self._creds.configured_in))
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

    def _request(self, method: str, path: str, *, json: Any = None,
                 **params: Any) -> dict[str, Any]:
        """One authenticated call, with retry bounded by what the method allows."""
        url = f"{self._base}/{path.lstrip('/')}"
        params = {k: v for k, v in params.items() if v is not None}
        params["organization_id"] = self._org
        verb = method.upper()
        replayable = verb in self._REPLAYABLE

        last: Optional[str] = None
        throttled = False
        attempts = max(1, settings.ZOHO_MAX_RETRIES)
        for attempt in range(attempts):
            token = self._access_token()
            self._pace()
            kwargs: dict[str, Any] = {
                "params": params,
                "headers": {"Authorization": f"Zoho-oauthtoken {token}"},
            }
            if json is not None:
                kwargs["json"] = json
            resp = getattr(self._client(), verb.lower())(url, **kwargs)
            self._last_call_at = time.monotonic()
            self.calls += 1

            if resp.status_code == 401:
                # Token may have been revoked mid-run; drop the cache and retry
                # once. Safe for a write too: a rejected token never reached the
                # books, so nothing can have been created.
                self._token, self._token_expires_at = None, 0.0
                last = "401 unauthorized"
                if attempt == 0:
                    continue
                # A *second* 401 on a freshly minted token is not a bad token —
                # the token endpoint just issued it. It is this endpoint being
                # outside the grant, and Zoho says so in the body.
                scope = scope_for_path(path)
                if scope and self._is_scope_refusal(resp):
                    raise ZohoScopeError(
                        f"Zoho refused {path}: this connection was not granted "
                        f"{scope}. The credentials are valid — everything else "
                        f"is still readable. Re-authorise the connection with "
                        f"{scope} added to the scope list to enable it.",
                        path=path, scope=scope)
                raise ZohoAuthError(
                    "Zoho rejected the access token. Confirm the refresh token, the "
                    "client credentials and the data centre all belong to the same account.")
            if resp.status_code == 429:
                # Also safe to replay for a write: the limiter refuses the call
                # outright rather than half-applying it.
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
                if not replayable:
                    raise ZohoWriteUncertain(
                        f"Zoho returned HTTP {resp.status_code} to {verb} {path}. "
                        "Whether the record was written cannot be told from here, so "
                        "it has not been retried — check Zoho before sending again.")
                last = f"HTTP {resp.status_code}"
                self._sleep(min(settings.ZOHO_MAX_BACKOFF_SECONDS, 2 ** attempt))
                continue
            try:
                body = resp.json()
            except ValueError:
                raise ZohoError(f"Zoho returned non-JSON for {path} (HTTP {resp.status_code})")
            if resp.status_code not in (200, 201) or body.get("code", 0) not in (0, None):
                raise ZohoError(
                    f"Zoho error on {path}: {body.get('message') or resp.status_code}")
            return body

        if throttled:
            raise ZohoThrottleError(
                f"Zoho rate limited the pull at {path} and did not recover after "
                f"{attempts} attempts. Everything fetched so far has been kept — run the "
                "sync again later and it will resume from where it stopped.")
        raise ZohoError(f"Zoho call to {path} failed after retries ({last}).")

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, **params)

    @staticmethod
    def _is_scope_refusal(resp: Any) -> bool:
        """Whether a 401 body says "not authorized for this" rather than "bad token".

        Zoho uses code 57 for an out-of-scope call. The message is also matched
        because the code has moved between editions before, and treating an
        unparseable body as a scope problem would mislabel a genuinely revoked
        token — so this errs towards *not* claiming a scope issue.
        """
        try:
            body = resp.json()
        except Exception:                                    # noqa: BLE001
            return False
        if not isinstance(body, dict):
            return False
        if body.get("code") == 57:
            return True
        return "not authorized" in str(body.get("message") or "").lower()

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
            # Zoho knows which zone the books are kept in; asking the operator
            # to type it again is asking them to get it wrong.
            "time_zone": (match or {}).get("time_zone"),
            "visible_organizations": [
                {"organization_id": str(o.get("organization_id")), "name": o.get("name")}
                for o in orgs
            ],
        }

    def probe_scopes(self) -> list[dict[str, Any]]:
        """Ask Zoho, one listing per scope, what this grant can actually reach.

        ``ping`` cannot answer this and never could. It reads ``organizations``,
        which sits behind no scope at all — so a connection granted nothing but
        the login still pings green, which is precisely what a half-granted
        token looks like right up until the sync fails on it.

        Three answers, not two. A refusal is a definite no and a listing that
        returns is a definite yes, but a 5xx, a timeout or a throttle is
        ``None`` — *unknown*. Reporting an endpoint that could not be reached as
        granted is the benign default this codebase refuses to take: the caller
        is told which questions went unanswered rather than being handed a pass
        built out of missing evidence.

        Costs one call per granted scope and three per refused one (Zoho's 401
        is retried once against a freshly minted token before it counts as a
        scope refusal), so it belongs on a deliberate act — connecting,
        rotating, or pressing Check — and not on a page load.
        """
        probes = probe_paths()
        results: list[dict[str, Any]] = []
        for index, (scope, path) in enumerate(probes.items()):
            granted: Optional[bool]
            detail: Optional[str]
            try:
                self._get(path, per_page=1)
                granted, detail = True, None
            except ZohoScopeError as e:
                granted, detail = False, str(e)
            except ZohoAuthError:
                # The credential itself is the problem, so every remaining probe
                # would return the same answer for a reason that has nothing to
                # do with scopes. Raised rather than recorded ten times over.
                raise
            except ZohoThrottleError as e:
                # Every remaining probe would meet the same limiter, and each
                # one pays the full backoff before saying so. Stop, and mark
                # what was never asked as unknown rather than implying it passed.
                results.extend(
                    {"scope": s, "endpoint": p, "granted": None, "detail": str(e)}
                    for s, p in list(probes.items())[index:])
                break
            except ZohoError as e:
                granted, detail = None, f"{type(e).__name__}: {e}"
            results.append({"scope": scope, "endpoint": path,
                            "granted": granted, "detail": detail})
        return results


class ZohoApiSource(ZohoTransport):
    """Read-only Zoho Books client.

    Read-only by construction: every call it makes is a GET, and no method here
    can create, update or delete anything. The write side is
    ``zoho_books_service.ZohoBooksService``, which shares only the transport.

    ``since`` bounds how far back documents are pulled. When omitted it falls
    back to ``ZOHO_SYNC_FROM`` and then to the rolling ``ZOHO_HISTORY_DAYS``
    window, so an operator can choose an explicit start date per run without
    changing configuration.
    """

    def __init__(self, http: Any = None, since: Optional[date] = None,
                 credentials: Optional[ZohoCredentials] = None,
                 until: Optional[date] = None) -> None:
        super().__init__(http=http, credentials=credentials)
        self._since = since or configured_since()
        # Upper bound of the window this source reads. Set when a long pull is
        # split into calendar slices so each one asks Zoho for its own months
        # instead of every source walking the whole ledger.
        self._until = until
        # ── incremental listing ─────────────────────────────────────────────
        #
        # ``{document kind: newest modification stamp already held}``. Set by
        # ``SyncService`` on a nightly pull, so the listing sorts by
        # modification time and stops as soon as it reaches something known,
        # instead of paging through every document in the history window to
        # discover that almost none of them moved.
        #
        # Per kind, not one stamp for the pull: invoices and bills move at
        # different rates, and a single mark would either re-list one of them
        # needlessly or — far worse — skip the other's changes.
        #
        # Empty for a full pass. A full pass costs those list calls on purpose:
        # it is the only thing that sees the *whole* book, and so the only thing
        # that can notice a document Zoho no longer has.
        self.modified_since: dict[str, str] = {}
        # Forces the complete, date-ordered listing even when a high-water mark
        # is available — the weekly reconciliation.
        self._full_listing = False
        self.documents_fetched = 0
        self.documents_resumed = 0
        # How many listings stopped early, for the run summary: a nightly pull
        # that reports zero of these did not go incremental and nobody would
        # otherwise know why it took an hour.
        self.listings_short_circuited = 0
        # ── what the listing saw, for mirroring ──────────────────────────────
        #
        # Every document id Zoho currently reports as real trade inside this
        # pull's window, per document kind — including the ones the resume
        # cursor then skipped. That inclusion is the whole point: a resumed
        # pull *yields* almost nothing, so a caller that reconciled against
        # what it received would conclude the entire book had been deleted.
        self.listed: dict[str, set[str]] = {}
        # Kinds whose listing ran to the end without raising. Only these may be
        # reconciled — a pull that was throttled out halfway saw part of the
        # book, and treating the part it missed as deleted would destroy real
        # history on a bad network day.
        self.listing_complete: set[str] = set()

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


    # ── pulls (the ZohoSource protocol) ──────────────────────────────────────
    def list_contacts(self) -> Iterable[dict[str, Any]]:
        # ``Status.All`` is already Zoho's default for contacts — stated
        # explicitly so a change to that default cannot quietly start dropping
        # every dormant account, the way ``/items`` drops inactive stock.
        for c in self._paginate("contacts", "contacts", contact_type="customer",
                                filter_by="Status.All"):
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
        """The whole item master, **including inactive items.**

        ``filter_by`` is not optional here. Zoho's ``/items`` endpoint defaults
        to ``Status.Active``, and a distributor deactivates an item the moment
        the line is discontinued — but the bills and invoices that reference it
        do not disappear with it. Without this parameter every historical line
        for a retired item is skipped as UNKNOWN_PRODUCT, and because bills are
        where cost comes from, the effect is silently missing *margin* on real
        trade rather than a visibly missing item.

        Verified against a live book: ``Status.Inactive`` on 4U Precision
        returns pages of genuine tooling that ``Status.Active`` does not.

        Inactive items are stored with ``status`` as Zoho reports it, so they
        are still marked inactive downstream — the point is that they exist to
        be resolved against, not that they are treated as live.
        """
        for i in self._paginate("items", "items", filter_by="Status.All"):
            yield {
                "item_id": str(i.get("item_id")),
                "name": i.get("name") or "",
                # The identity layer's item key. Often blank in Zoho — an item
                # with no SKU simply gets no suggestion, which is the honest
                # outcome rather than a guess from the name.
                "sku": i.get("sku"),
                "unit": i.get("unit"),
                "hsn_or_sac": i.get("hsn_or_sac") or i.get("hsn_code"),
                # The catalogue's own line for this item, where the books use
                # Zoho's Inventory categories. Measured against the live
                # masters it is set on **none** of them — 0 of 800 on SLS
                # Engineers, 0 of 400 on 4U Precision — because the feature is
                # not turned on. Kept because it costs nothing, is a person's
                # answer where it exists, and reads as "no answer" otherwise;
                # but the HSN map is what actually places an item here, not the
                # fallback it was described as.
                "category_name": i.get("category_name") or i.get("category"),
                # Who makes the item. The one curated field these masters really
                # do keep — 67% of SLS items and 92% of 4U's, and *clean*: six
                # distinct principals in one book and two in the other, with no
                # spelling variants at all. It matters because it has no sync
                # horizon: an item sold today out of stock bought four years ago
                # has no bill inside the window and therefore no vendor, but it
                # still knows whose product it is. See
                # ``commercial/principals.py`` for where that fallback applies
                # and, more importantly, where it does not.
                #
                # ``brand`` is a *different* Zoho field, and these books do not
                # use it — 0 of 800 items on SLS Engineers, 3 of 400 on 4U
                # Precision. It is read behind ``manufacturer`` rather than
                # dropped because it costs nothing and a book that starts
                # filling it in should not need a code change to be heard. This
                # is the only place the two are weighed against each other:
                # everything downstream sees one value under one name, so
                # nothing else has to know there were two candidates.
                "manufacturer": i.get("manufacturer") or i.get("brand"),
                "status": (i.get("status") or "active"),
                # Stock travels on the item list Zoho already returns, so this
                # costs nothing extra. Passed through raw — including the blank
                # reorder_level, which normalisation must not read as zero.
                "stock_on_hand": i.get("stock_on_hand"),
                "available_stock": i.get("available_stock"),
                "actual_available_stock": i.get("actual_available_stock"),
                "reorder_level": i.get("reorder_level"),
                "purchase_rate": i.get("purchase_rate"),
                "track_inventory": i.get("track_inventory"),
                "item_type": i.get("item_type"),
            }

    #: How many item ids one ``itemdetails`` call carries. Zoho accepts a list;
    #: batching is the whole reason per-location stock is affordable at all —
    #: the per-item detail endpoint would be one call per item, which on an
    #: 800-line master is 800 calls a sync to answer a question about three
    #: branches. Kept modest because the ids travel in the query string.
    ITEM_DETAIL_BATCH = 25

    def list_locations(self) -> Iterable[dict[str, Any]]:
        """Where this company trades from.

        Small, unpaginated in practice, and read once per sync. On this book it
        returns three: a head office, a branch in another state with its own
        GSTIN, and an inactive godown nested under the head office.
        """
        for loc in self._paginate("locations", "locations"):
            yield {
                "location_id": str(loc.get("location_id")),
                "location_name": loc.get("location_name") or "",
                "type": loc.get("type"),
                # Zoho nests one location under another. Passed through so a
                # roll-up can avoid counting a godown inside the head office
                # that already contains it.
                "parent_location_id": (str(loc["parent_location_id"])
                                       if loc.get("parent_location_id") else None),
                "is_location_active": loc.get("is_location_active"),
                "is_primary_location": loc.get("is_primary_location"),
                "tax_reg_no": loc.get("tax_reg_no"),
            }

    def list_item_locations(self, item_ids: list[str]) -> Iterable[dict[str, Any]]:
        """Per-location stock for the given items, in batches.

        The item *list* endpoint the master pull already reads carries only the
        organization-wide totals; ``locations`` appears on the detail payload.
        Rather than fetching one detail per item, ids go to ``itemdetails`` in
        batches of ``ITEM_DETAIL_BATCH``.

        Yields one row per (item, location) pair, so a caller never has to know
        the batching happened.
        """
        for start in range(0, len(item_ids), self.ITEM_DETAIL_BATCH):
            batch = item_ids[start:start + self.ITEM_DETAIL_BATCH]
            if not batch:
                continue
            payload = self._get("itemdetails", item_ids=",".join(batch))
            for item in (payload.get("items") or []):
                item_id = str(item.get("item_id") or "")
                if not item_id:
                    continue
                for loc in (item.get("locations") or []):
                    location_id = str(loc.get("location_id") or "")
                    if not location_id:
                        continue
                    yield {
                        "item_id": item_id,
                        "location_id": location_id,
                        # Named for what they are rather than for Zoho's
                        # `location_` prefix, which would read as redundant on a
                        # row already keyed by location.
                        "on_hand": loc.get("location_stock_on_hand"),
                        "available": loc.get("location_available_stock"),
                        # Zoho's own valuation of this location's holding. Cost.
                        "asset_value": loc.get("location_asset_value"),
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
        kind = detail_key
        seen: set[str] = self.listed.setdefault(kind, set())

        # Incremental listing: ask Zoho for the most recently *modified* first
        # and stop at the newest stamp already held. The resume predicate below
        # already saves the detail call for a document that has not changed —
        # this saves the *list* call as well, which is the rest of the bill. A
        # nightly pull over two years of history was paying one list call per
        # 200 documents to discover that almost none of them had moved.
        #
        # The cost is completeness, and it is charged honestly: a listing that
        # stopped early has not seen the whole book, so `listing_complete` is
        # not set for it and the deletion sweep in SyncService correctly refuses
        # to run. A document deleted or voided in Zoho is therefore caught by
        # the periodic full pass, not by this one. See `modified_since`.
        high_water = None if self._full_listing else self.modified_since.get(kind)
        sort_column = "last_modified_time" if high_water else "date"
        stopped_early = False

        for row in self._paginate(path, list_key, sort_column=sort_column,
                                  sort_order="D", **self._window()):
            if high_water:
                stamp = str(row.get("last_modified_time") or "")
                # Sorted newest-modified first, so the first row at or below the
                # high-water mark means every row after it is too.
                if stamp and stamp <= high_water:
                    stopped_early = True
                    self.listings_short_circuited += 1
                    break
            status = str(row.get("status") or "").lower()
            if status in excluded_status:
                # Deliberately *not* recorded as seen. A voided or drafted
                # document is not trade, so as far as this platform is
                # concerned it is the same as absent — which is what makes
                # voiding an invoice in Zoho remove it from PIE.
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
            # Recorded before the resume check, not after. See `self.listed`.
            seen.add(doc_id)
            # The stamp the *list* reports. This is the value the resume check
            # will see next time, so it is also the value that must be stored —
            # see below.
            listed_stamp = str(row.get("last_modified_time") or "")
            if skip is not None and skip(doc_id, listed_stamp):
                self.documents_resumed += 1
                continue
            detail = self._get(f"{path}/{doc_id}").get(detail_key) or {}
            if detail:
                self.documents_fetched += 1
                # Overwrite the detail's own stamp with the list's.
                #
                # This is the whole of the "every sync re-reads the entire book"
                # bug. The sync stored `last_modified_time` from the *detail*
                # payload and the check above compares against the *list*
                # payload, and Zoho does not promise those two strings are
                # identical. Whenever they differ by so much as a format, every
                # document compares unequal on every run: nothing is ever
                # skipped, every document costs its detail call again, and every
                # row is re-upserted — which is what a full re-population looks
                # like from the outside.
                #
                # Fixed here rather than at the three call sites that store it,
                # because storing "whatever we will compare later" is a property
                # of the resume protocol and not of any one document type.
                detail = {**detail, "last_modified_time": listed_stamp}
                yield detail
        # Reached only when the loop above was not abandoned by an exception or
        # by the consumer breaking out early. An incremental listing that
        # stopped at the high-water mark is *deliberately* not complete: it saw
        # only what changed, so the set of ids it produced says nothing about
        # what Zoho no longer holds, and letting the deletion sweep read it as
        # authoritative would delete the entire unchanged book.
        if not stopped_early:
            self.listing_complete.add(kind)

    def list_invoices(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        for inv in self._documents("invoices", "invoices", "invoice", "invoice_id",
                                   _EXCLUDED_INVOICE_STATUS, skip=skip):
            yield {
                "invoice_id": str(inv.get("invoice_id")),
                "customer_id": str(inv.get("customer_id")),
                "date": inv.get("date"),
                "last_modified_time": inv.get("last_modified_time"),
                # The receivable terms, from the document already fetched: no
                # extra call, no extra scope — the mirror of what `list_bills`
                # passes through for payables. These were missing when the
                # receivables state was added, so every invoice header arrived
                # with no balance, no due date and no status: the fold saw
                # nothing owed by anybody, and the collection and credit-
                # exposure cards could never fire against a real Zoho pull.
                # The tests passed throughout because the fixtures supply them.
                "invoice_number": inv.get("invoice_number"),
                "due_date": inv.get("due_date"),
                "status": inv.get("status"),
                "total": inv.get("total"),
                "balance": inv.get("balance"),
                # Zoho's own record of who owns the sale. Mapped onto a platform
                # user by the sync layer; never guessed at when it is absent.
                "salesperson_id": (str(inv["salesperson_id"])
                                   if inv.get("salesperson_id") else None),
                "salesperson_name": inv.get("salesperson_name"),
                # Which customer orders this invoice bills against. The detail
                # payload above already carries them, so this is a passthrough:
                # no extra call, no extra scope — the same trade `due_date` and
                # `balance` make two fields up.
                #
                # Both forms are passed on. The array is the truth — one invoice
                # can consolidate several orders, which is why Zoho returns a
                # list at all — and the scalar is Zoho's own "primary". Sending
                # only the scalar would silently drop every order past the first
                # on a consolidated invoice; sending only the array would lose
                # which one Zoho itself considered primary. `normalize.py`
                # unions them and marks the scalar.
                "salesorder_id": (str(inv["salesorder_id"])
                                  if inv.get("salesorder_id") else None),
                "salesorder_number": inv.get("salesorder_number"),
                "salesorders": [
                    {"salesorder_id": str(so.get("salesorder_id")),
                     "salesorder_number": so.get("salesorder_number")}
                    for so in (inv.get("salesorders") or [])
                    if so.get("salesorder_id")
                ],
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

    def list_credit_notes(self,
                          skip: Optional[SkipPredicate] = None,
                          ) -> Iterable[dict[str, Any]]:
        """Credit notes, with the invoices each was applied to.

        Read through ``_documents`` rather than ``_paginate`` because the
        applications live on the detail payload — ``invoices_credited`` is not
        on the list response — and ``_documents`` is already fetching that
        detail for every other document type. No extra call beyond the one the
        detail fetch makes, and the same resume predicate applies.

        Line items are deliberately not passed through. A credit note's lines
        would be negative revenue against a product, and revenue already has one
        owner in ``SalesTxn``; a second signed source for the same quantity is
        how two screens start disagreeing about what was sold. What this pull is
        for is the *money*, at header and application grain.
        """
        for note in self._documents("creditnotes", "creditnotes", "creditnote",
                                    "creditnote_id", _EXCLUDED_CREDIT_NOTE_STATUS,
                                    skip=skip):
            yield {
                "creditnote_id": str(note.get("creditnote_id")),
                "creditnote_number": note.get("creditnote_number"),
                "customer_id": (str(note["customer_id"])
                                if note.get("customer_id") else None),
                "date": note.get("date"),
                "last_modified_time": note.get("last_modified_time"),
                "status": note.get("status"),
                "total": note.get("total"),
                # What is still unapplied. Zoho's own figure — never derived
                # from total minus the applications below, because a refund
                # against the note would make that subtraction overstate the
                # credit a customer still holds.
                "balance": note.get("balance"),
                "invoices_credited": [
                    {
                        "creditnote_invoice_id": ic.get("creditnote_invoice_id"),
                        "invoice_id": str(ic.get("invoice_id")),
                        "invoice_number": ic.get("invoice_number"),
                        "invoice_date": ic.get("invoice_date"),
                        "date": ic.get("date"),
                        "amount_applied": ic.get("amount_applied"),
                        "credited_amount": ic.get("credited_amount"),
                    }
                    for ic in (note.get("invoices_credited") or [])
                    if ic.get("invoice_id")
                ],
            }

    def list_bills(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        for bill in self._documents("bills", "bills", "bill", "bill_id",
                                    _EXCLUDED_BILL_STATUS, skip=skip):
            yield {
                "bill_id": str(bill.get("bill_id")),
                "date": bill.get("date"),
                "last_modified_time": bill.get("last_modified_time"),
                # The payable terms, from the document already fetched: no
                # extra call, no extra scope. Until these were passed through,
                # a bill was read purely for what the stock cost and the fact
                # that the money was still owed was thrown away — so accounts
                # payable and working capital had no source at all.
                "bill_number": bill.get("bill_number"),
                "vendor_id": bill.get("vendor_id"),
                "vendor_name": bill.get("vendor_name"),
                "due_date": bill.get("due_date"),
                "status": bill.get("status"),
                "total": bill.get("total"),
                "balance": bill.get("balance"),
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

    def list_vendors(self) -> Iterable[dict[str, Any]]:
        """Suppliers. The same ``contacts`` endpoint, the other contact_type."""
        for v in self._paginate("contacts", "contacts", contact_type="vendor",
                                filter_by="Status.All"):
            yield {
                "contact_id": str(v.get("contact_id")),
                "contact_name": (v.get("vendor_name") or v.get("contact_name")
                                 or v.get("company_name") or ""),
                "gst_no": v.get("gst_no") or v.get("gst_treatment_gstin"),
                "pan_no": v.get("pan_no"),
                # 0 is "due on receipt" — a real term, not a missing value, so
                # it is passed through and only ``None`` means unknown.
                "payment_terms": v.get("payment_terms"),
                "status": (v.get("status") or "active"),
            }

    def list_customer_payments(
            self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        """Money in, with the invoices each payment settled.

        The list call alone cannot answer "how long did they take to pay": it
        carries invoice *numbers* but neither the invoice date nor the amount
        applied. The detail call carries both, so it is fetched per payment —
        and ``skip`` makes a resumed pull cost one list call instead of
        hundreds of detail calls.
        """
        cutoff = self._cutoff()
        until = self._until
        for row in self._paginate("customerpayments", "customerpayments",
                                  sort_column="date", sort_order="D", **self._window()):
            try:
                paid_on = date.fromisoformat(str(row.get("date") or ""))
            except ValueError:
                continue
            if paid_on < cutoff or (until is not None and paid_on > until):
                continue
            payment_id = str(row.get("payment_id"))
            if skip is not None and skip(payment_id,
                                         str(row.get("last_modified_time") or "")):
                self.documents_resumed += 1
                continue
            detail = self._get(f"customerpayments/{payment_id}").get("payment") or {}
            if not detail:
                continue
            self.documents_fetched += 1
            yield {
                "payment_id": payment_id,
                "customer_id": str(detail.get("customer_id") or ""),
                "date": detail.get("date"),
                "last_modified_time": (detail.get("last_modified_time")
                                       or row.get("last_modified_time")),
                "amount": detail.get("amount"),
                "payment_mode": detail.get("payment_mode"),
                "is_advance_payment": bool(detail.get("is_advance_payment")),
                "unused_amount": detail.get("unused_amount"),
                "invoices": [
                    {
                        "invoice_payment_id": str(a.get("invoice_payment_id") or ""),
                        "invoice_id": str(a.get("invoice_id") or ""),
                        "invoice_number": a.get("invoice_number"),
                        # The invoice's own dates, carried on the application:
                        # an invoice older than the sync window still has to
                        # produce a days-to-pay, and those are the slow ones.
                        "date": a.get("date"),
                        "due_date": a.get("due_date"),
                        "amount_applied": a.get("amount_applied"),
                    }
                    for a in (detail.get("invoices") or [])
                    if a.get("invoice_id")
                ],
            }

    def list_purchase_orders(self) -> Iterable[dict[str, Any]]:
        """Orders on suppliers. Header grain — the list call carries it all.

        No detail call: the questions this feeds are "what is outstanding, with
        whom, for how long", and ordered/pending quantity and status are all on
        the list row. Fetching every PO's lines to answer none of them would be
        a per-document call bought for nothing.
        """
        cutoff = self._cutoff()
        until = self._until
        for po in self._paginate("purchaseorders", "purchaseorders",
                                 sort_column="date", sort_order="D", **self._window()):
            try:
                ordered = date.fromisoformat(str(po.get("date") or ""))
            except ValueError:
                continue
            if ordered < cutoff or (until is not None and ordered > until):
                continue
            yield {
                "purchaseorder_id": str(po.get("purchaseorder_id")),
                "purchaseorder_number": po.get("purchaseorder_number"),
                "vendor_id": (str(po["vendor_id"]) if po.get("vendor_id") else None),
                "date": po.get("date"),
                # Blank on most of this book's orders. Passed through as-is so
                # normalisation can report "no promised date" rather than
                # inventing one from an assumed lead time.
                "expected_delivery_date": (po.get("expected_delivery_date")
                                           or po.get("delivery_date")),
                "status": po.get("status") or po.get("order_status") or "",
                "received_status": po.get("received_status"),
                "total_ordered_quantity": po.get("total_ordered_quantity"),
                "quantity_yet_to_receive": po.get("quantity_yet_to_receive"),
                "total": po.get("total"),
                "receives": po.get("receives") or [],
            }

    def list_sales_orders(self) -> Iterable[dict[str, Any]]:
        """Orders from customers — demand that has been promised, not yet billed.

        The other half of the commitment picture. Purchase orders say what we
        have promised a supplier; these say what a customer has promised us and
        what we have promised to ship. Neither is an accounting entry, and both
        change what the business is exposed to before any invoice exists.

        Header grain, no detail call, for the same reason as purchase orders:
        "what is open, for whom, for how much, how late" is entirely on the list
        row. The line-level breakdown would cost one call per order to answer
        questions this does not ask.
        """
        cutoff = self._cutoff()
        until = self._until
        for so in self._paginate("salesorders", "salesorders",
                                 sort_column="date", sort_order="D", **self._window()):
            try:
                ordered = date.fromisoformat(str(so.get("date") or ""))
            except ValueError:
                continue
            if ordered < cutoff or (until is not None and ordered > until):
                continue
            status = str(so.get("status") or "").lower()
            # Drafts are not commitments. A draft order promises nobody
            # anything, and counting one as demand would show a commitment that
            # can be deleted without trace.
            if status in _EXCLUDED_INVOICE_STATUS:
                continue
            yield {
                "salesorder_id": str(so.get("salesorder_id")),
                "salesorder_number": so.get("salesorder_number"),
                "customer_id": (str(so["customer_id"]) if so.get("customer_id") else None),
                "date": so.get("date"),
                "shipment_date": so.get("shipment_date") or so.get("expected_shipment_date"),
                "status": so.get("status") or "",
                # Zoho tracks these separately: an order can be fully invoiced
                # and not shipped, or shipped and not invoiced. Both matter and
                # they are not the same fact.
                "invoiced_status": so.get("invoiced_status"),
                "shipped_status": so.get("shipped_status"),
                "total": so.get("total"),
                "salesperson_id": so.get("salesperson_id"),
            }

    def list_vendor_payments(
            self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]:
        """Money out, with the bills each payment settled.

        Customer payments have been read since the cash screen was built;
        without this, "cash" is receipts with nothing subtracted, which is not
        cash — it is revenue collected. Liquidity and working capital are not
        computable from one side of the ledger.

        The detail call is here for the same reason it is on the receivable
        side: the list row carries the amount and the date but not which bills
        went out with it, and "how long do we take to pay" is per bill settled,
        not per transfer sent. ``skip`` makes a resumed pull cost one list call
        instead of hundreds of detail calls, exactly as for customer payments.
        """
        cutoff = self._cutoff()
        until = self._until
        for p in self._paginate("vendorpayments", "vendorpayments",
                                sort_column="date", sort_order="D", **self._window()):
            try:
                paid_on = date.fromisoformat(str(p.get("date") or ""))
            except ValueError:
                continue
            if paid_on < cutoff or (until is not None and paid_on > until):
                continue
            payment_id = str(p.get("payment_id"))
            if skip is not None and skip(payment_id,
                                         str(p.get("last_modified_time") or "")):
                self.documents_resumed += 1
                continue
            detail = self._get(f"vendorpayments/{payment_id}").get("vendorpayment") or {}
            # The list row is a complete payment on its own — amount, date and
            # supplier are all on it. A detail call that comes back empty costs
            # the bill breakdown, not the payment, so the row is still yielded
            # rather than dropped: money out with no measurable lag beats no
            # money out at all.
            if detail:
                self.documents_fetched += 1
            source = detail or p
            yield {
                "payment_id": payment_id,
                "vendor_id": (str(source["vendor_id"]) if source.get("vendor_id")
                              else None),
                "date": source.get("date"),
                "last_modified_time": (source.get("last_modified_time")
                                       or p.get("last_modified_time")),
                "amount": source.get("amount"),
                "payment_mode": source.get("payment_mode"),
                "reference_number": source.get("reference_number"),
                "bills": [
                    {
                        "bill_payment_id": str(a.get("bill_payment_id") or ""),
                        "bill_id": str(a.get("bill_id") or ""),
                        "bill_number": a.get("bill_number"),
                        # The bill's own dates, carried on the application: a
                        # bill older than the sync window still has to produce
                        # a days-to-pay, and those are the slow ones.
                        "date": a.get("date"),
                        "due_date": a.get("due_date"),
                        "amount_applied": a.get("amount_applied"),
                    }
                    for a in (detail.get("bills") or [])
                    if a.get("bill_id")
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
