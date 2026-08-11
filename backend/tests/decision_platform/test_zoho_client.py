"""Live Zoho client: auth, pagination, detail fetches, and what it refuses.

Exercised through an injected fake transport, so the real request/response
handling runs with no network — the same code path the live pull uses.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import settings
from app.ingestion.zoho_client import (
    ZohoApiSource,
    ZohoAuthError,
    ZohoError,
    ZohoThrottleError,
    configured_since,
)


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code = body, status
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeHttp:
    """Records calls and replays canned bodies keyed by path fragment."""

    def __init__(self, routes, token_body=None):
        self.routes = routes
        self.token_body = token_body if token_body is not None else {
            "access_token": "tok-1", "expires_in": 3600}
        self.gets: list[tuple[str, dict]] = []
        self.token_calls = 0

    def post(self, url, params=None, **kw):
        self.token_calls += 1
        return FakeResponse(self.token_body)

    def get(self, url, params=None, headers=None, **kw):
        self.gets.append((url, dict(params or {})))
        assert headers and headers.get("Authorization", "").startswith("Zoho-oauthtoken "), \
            "every data call must carry the OAuth header"
        for fragment, body in self.routes.items():
            if url.rstrip("/").endswith(fragment):
                return FakeResponse(body(params) if callable(body) else body)
        return FakeResponse({"code": 0, "page_context": {"has_more_page": False}})


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "60036630487")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_SECRET", "csec")
    monkeypatch.setattr(settings, "ZOHO_REFRESH_TOKEN", "rtok")
    monkeypatch.setattr(settings, "ZOHO_HISTORY_DAYS", 730)
    monkeypatch.setattr(settings, "ZOHO_SYNC_FROM", "")


@pytest.fixture()
def waits(monkeypatch):
    """Capture every wait instead of serving it, so the real retry and pacing
    logic runs at test speed."""
    recorded: list[float] = []
    monkeypatch.setattr(ZohoApiSource, "_sleep",
                        lambda self, seconds: recorded.append(seconds))
    return recorded


def _today(offset_days: int = 0) -> str:
    return (date.today() - timedelta(days=offset_days)).isoformat()


# ── credentials ─────────────────────────────────────────────────────────────
def test_missing_credentials_name_what_is_missing(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_REFRESH_TOKEN", "")
    with pytest.raises(ZohoAuthError) as e:
        ZohoApiSource(http=FakeHttp({})).list_items().__iter__().__next__()
    assert "refresh_token" in str(e.value)


def test_token_failure_points_at_the_data_centre():
    http = FakeHttp({}, token_body={"error": "invalid_code"})
    with pytest.raises(ZohoAuthError) as e:
        list(ZohoApiSource(http=http).list_items())
    assert "data centre" in str(e.value)


def test_access_token_is_reused_across_calls():
    http = FakeHttp({"/items": {"code": 0, "items": [], "page_context": {"has_more_page": False}},
                     "/contacts": {"code": 0, "contacts": [], "page_context": {"has_more_page": False}}})
    src = ZohoApiSource(http=http)
    list(src.list_items())
    list(src.list_contacts())
    assert http.token_calls == 1, "the hourly token must not be re-fetched per call"


def test_organization_id_is_sent_on_every_call():
    http = FakeHttp({"/items": {"code": 0, "items": [], "page_context": {"has_more_page": False}}})
    list(ZohoApiSource(http=http).list_items())
    assert all(p.get("organization_id") == "60036630487" for _, p in http.gets)


# ── explicit credentials (multi-tenant) ─────────────────────────────────────
def test_explicit_credentials_are_used_instead_of_settings():
    """The multi-tenant contract: passing credentials must fully override the
    process-wide settings, not merely supplement them — two tenants running in
    the same process must never blend into one another's Zoho account."""
    from app.ingestion.zoho_client import ZohoCredentials

    creds = ZohoCredentials(organization_id="999999", client_id="other-cid",
                            client_secret="other-csec", refresh_token="other-rtok",
                            accounts_base="https://accounts.zoho.eu",
                            api_base="https://www.zohoapis.eu/books/v3")
    http = FakeHttp({"/items": {"code": 0, "items": [], "page_context": {"has_more_page": False}}})
    src = ZohoApiSource(http=http, credentials=creds)
    list(src.list_items())

    assert all(p.get("organization_id") == "999999" for _, p in http.gets)
    assert all(u.startswith("https://www.zohoapis.eu/books/v3") for u, _ in http.gets)


def test_omitted_credentials_still_fall_back_to_settings():
    """Backward compatibility: existing single-tenant deployments and every
    test in this file that constructs ZohoApiSource(http=...) with no
    credentials argument must keep reading from settings unchanged."""
    from app.ingestion.zoho_client import ZohoCredentials

    src = ZohoApiSource(http=FakeHttp({}))
    assert src._creds == ZohoCredentials.from_settings()
    assert src._org == "60036630487"


# ── shapes the normalizer expects ───────────────────────────────────────────
def test_contacts_are_mapped_to_the_normalizer_shape():
    http = FakeHttp({"/contacts": {
        "code": 0,
        "contacts": [{"contact_id": 123, "contact_name": "4U Customer", "status": "active"}],
        "page_context": {"has_more_page": False}}})
    rows = list(ZohoApiSource(http=http).list_contacts())
    # gst_no rides along for the identity layer. Present as None rather than
    # absent when the edition has no such field, so a caller never has to guess
    # whether the key was missing or the value was.
    assert rows == [{"contact_id": "123", "contact_name": "4U Customer",
                     "gst_no": None, "status": "active"}]


def test_a_contacts_gstin_reaches_the_identity_layer():
    """The strongest customer matching key there is, and it was simply not being
    read until the identity layer needed it."""
    http = FakeHttp({"/contacts": {
        "code": 0,
        "contacts": [{"contact_id": 1, "contact_name": "ABC",
                      "gst_no": "29ABCDE1234F1Z5", "status": "active"}],
        "page_context": {"has_more_page": False}}})
    assert list(ZohoApiSource(http=http).list_contacts())[0]["gst_no"] == "29ABCDE1234F1Z5"


def test_an_items_sku_reaches_the_identity_layer():
    http = FakeHttp({"/items": {
        "code": 0,
        "items": [{"item_id": 7, "name": "KCMT 090304 LF", "sku": "KCMT090304LF",
                   "status": "active"}],
        "page_context": {"has_more_page": False}}})
    assert list(ZohoApiSource(http=http).list_items())[0]["sku"] == "KCMT090304LF"


def test_invoice_detail_is_fetched_for_line_items():
    """List rows carry no line items — the detail call is what the signals need."""
    listing = {"code": 0, "invoices": [{"invoice_id": "INV1", "date": _today(10), "status": "paid"}],
               "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {
        "invoice_id": "INV1", "customer_id": 55, "date": _today(10),
        "line_items": [
            {"line_item_id": 1, "item_id": 9, "quantity": 20, "rate": 530, "item_total": 10600},
            {"line_item_id": 2, "item_id": None, "quantity": 1, "rate": 100},  # comment row
        ]}}
    http = FakeHttp({"/invoices/INV1": detail, "/invoices": listing})
    rows = list(ZohoApiSource(http=http).list_invoices())
    assert len(rows) == 1
    assert rows[0]["customer_id"] == "55"
    # the comment row (no item_id) is not a product line
    assert len(rows[0]["line_items"]) == 1
    line = rows[0]["line_items"][0]
    assert (line["line_item_id"], line["item_id"]) == ("1", "9")
    assert (line["quantity"], line["rate"], line["item_total"]) == (20, 530, 10600)


def test_bill_discount_fields_are_passed_through_raw():
    """The client must not interpret the discount — normalize.py decides which
    of item_total / discount_amount / discount is authoritative. If the client
    silently dropped these fields the bug (rate treated as cost) would return."""
    listing = {"code": 0, "bills": [{"bill_id": "B1", "date": _today(5), "status": "open"}],
               "page_context": {"has_more_page": False}}
    detail = {"code": 0, "bill": {
        "bill_id": "B1", "date": _today(5),
        "line_items": [{"line_item_id": 1, "item_id": 9, "quantity": 1, "rate": 3166,
                        "discount": "50%", "discount_amount": 1583, "item_total": 1583}]}}
    http = FakeHttp({"/bills/B1": detail, "/bills": listing})
    row = list(ZohoApiSource(http=http).list_bills())[0]
    line = row["line_items"][0]
    assert line["rate"] == 3166
    assert line["discount"] == "50%"
    assert line["discount_amount"] == 1583
    assert line["item_total"] == 1583


def test_the_currency_a_document_is_denominated_in_survives_the_projection():
    """Dropped here, a foreign document is indistinguishable from a local one
    forever after: no money row in this schema carries a currency, so `sync`
    can only refuse what the client passed through. This is the whole of the
    guard's evidence, and the projection is explicit — a field not named here
    does not reach the platform however faithfully Zoho reports it."""
    day = _today(5)
    inv_listing = {"code": 0, "invoices": [{"invoice_id": "I1", "date": day,
                                            "status": "sent"}],
                   "page_context": {"has_more_page": False}}
    inv_detail = {"code": 0, "invoice": {
        "invoice_id": "I1", "customer_id": "C1", "date": day,
        "currency_code": "EUR", "exchange_rate": 90.5,
        "line_items": [{"line_item_id": 1, "item_id": 9, "quantity": 1,
                        "rate": 100, "item_total": 100}]}}
    row = list(ZohoApiSource(http=FakeHttp(
        {"/invoices/I1": inv_detail, "/invoices": inv_listing})).list_invoices())[0]
    assert row["currency_code"] == "EUR"
    assert row["exchange_rate"] == 90.5

    bill_listing = {"code": 0, "bills": [{"bill_id": "B1", "date": day,
                                          "status": "open"}],
                    "page_context": {"has_more_page": False}}
    bill_detail = {"code": 0, "bill": {
        "bill_id": "B1", "date": day, "currency_code": "USD",
        "exchange_rate": 83.2,
        "line_items": [{"line_item_id": 1, "item_id": 9, "quantity": 1,
                        "rate": 100, "item_total": 100}]}}
    row = list(ZohoApiSource(http=FakeHttp(
        {"/bills/B1": bill_detail, "/bills": bill_listing})).list_bills())[0]
    assert row["currency_code"] == "USD"
    assert row["exchange_rate"] == 83.2


def test_live_rows_normalize_without_error():
    """The client's output must feed normalize.py unchanged."""
    from app.ingestion.normalize import normalize_customer, normalize_invoice

    listing = {"code": 0, "invoices": [{"invoice_id": "INV1", "date": _today(5), "status": "sent"}],
               "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {"invoice_id": "INV1", "customer_id": 55, "date": _today(5),
                                     "line_items": [{"line_item_id": 1, "item_id": 9,
                                                     "quantity": 2, "rate": 100, "item_total": 200}]}}
    http = FakeHttp({"/invoices/INV1": detail, "/invoices": listing,
                     "/contacts": {"code": 0, "contacts": [{"contact_id": 1, "contact_name": "A"}],
                                   "page_context": {"has_more_page": False}}})
    src = ZohoApiSource(http=http)
    assert normalize_customer(list(src.list_contacts())[0]).external_id == "1"
    txns = normalize_invoice(list(src.list_invoices())[0])
    assert txns[0].external_ref == "INV1:1" and float(txns[0].line_revenue) == 200.0


# ── what it refuses to count ────────────────────────────────────────────────
def test_draft_and_void_documents_are_not_trade():
    listing = {"code": 0, "invoices": [
        {"invoice_id": "D1", "date": _today(1), "status": "draft"},
        {"invoice_id": "V1", "date": _today(1), "status": "void"},
    ], "page_context": {"has_more_page": False}}
    http = FakeHttp({"/invoices": listing})
    assert list(ZohoApiSource(http=http).list_invoices()) == []
    assert not any("/invoices/" in u for u, _ in http.gets), "no detail call for excluded docs"


def test_history_older_than_the_window_is_not_pulled(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_HISTORY_DAYS", 30)
    listing = {"code": 0, "invoices": [{"invoice_id": "OLD", "date": _today(400), "status": "paid"}],
               "page_context": {"has_more_page": False}}
    http = FakeHttp({"/invoices": listing})
    assert list(ZohoApiSource(http=http).list_invoices()) == []


# ── pagination + errors ─────────────────────────────────────────────────────
def test_pagination_follows_has_more_page():
    def items(params):
        page = int(params.get("page", 1))
        if page == 1:
            return {"code": 0, "items": [{"item_id": 1, "name": "one"}],
                    "page_context": {"has_more_page": True}}
        return {"code": 0, "items": [{"item_id": 2, "name": "two"}],
                "page_context": {"has_more_page": False}}

    rows = list(ZohoApiSource(http=FakeHttp({"/items": items})).list_items())
    assert [r["item_id"] for r in rows] == ["1", "2"]


def test_pagination_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_MAX_PAGES", 3)
    endless = {"code": 0, "items": [{"item_id": 1, "name": "x"}],
               "page_context": {"has_more_page": True}}
    http = FakeHttp({"/items": endless})
    assert len(list(ZohoApiSource(http=http).list_items())) == 3


def test_api_error_body_is_surfaced():
    http = FakeHttp({"/items": {"code": 1005, "message": "scope is not permitted"}})
    with pytest.raises(ZohoError) as e:
        list(ZohoApiSource(http=http).list_items())
    assert "scope is not permitted" in str(e.value)


def test_ping_reports_when_the_org_is_not_visible():
    http = FakeHttp({"/organizations": {
        "code": 0,
        "organizations": [{"organization_id": "999", "name": "Some Other Co",
                           "currency_code": "INR"}]}})
    out = ZohoApiSource(http=http).ping()
    assert out["authenticated"] is True
    assert out["organization_found"] is False
    assert out["visible_organizations"][0]["organization_id"] == "999"


# ── rate limiting ───────────────────────────────────────────────────────────
# A live pull is thousands of calls against a per-minute limit. The first real
# sync died on a 429 mid-invoice, so this is the behaviour that decides whether
# a pull finishes at all.
def test_a_429_is_waited_out_in_tens_of_seconds_not_one(waits):
    """A rate limiter is not a transient fault. The old 1s/2s/4s backoff simply
    spent the retry budget inside a window the limiter had not yet released."""
    state = {"n": 0}

    def items(params):
        state["n"] += 1
        if state["n"] <= 2:
            return None                      # marker: the route returns a 429 below
        return {"code": 0, "items": [{"item_id": 1, "name": "x"}],
                "page_context": {"has_more_page": False}}

    class Throttling(FakeHttp):
        def get(self, url, params=None, headers=None, **kw):
            self.gets.append((url, dict(params or {})))
            body = items(params)
            if body is None:
                return FakeResponse({"message": "too many requests"}, status=429)
            return FakeResponse(body)

    rows = list(ZohoApiSource(http=Throttling({})).list_items())
    assert [r["item_id"] for r in rows] == ["1"], "it must recover, not give up"
    backoffs = [w for w in waits if w >= 1]      # the rest is ordinary pacing
    assert len(backoffs) == 2 and min(backoffs) >= 10, \
        f"backoff was too short to outlast a rate limiter: {waits}"


def test_zoho_s_own_retry_after_beats_our_guess(waits):
    class Throttling(FakeHttp):
        def __init__(self):
            super().__init__({})
            self.n = 0

        def get(self, url, params=None, headers=None, **kw):
            self.n += 1
            if self.n == 1:
                return FakeResponse({"message": "slow down"}, status=429,
                                    headers={"Retry-After": "7"})
            return FakeResponse({"code": 0, "items": [],
                                 "page_context": {"has_more_page": False}})

    list(ZohoApiSource(http=Throttling()).list_items())
    assert 7 in waits, f"Retry-After was ignored: {waits}"


def test_being_throttled_out_says_the_work_so_far_is_kept(monkeypatch, waits):
    """The message a person reads has to name the remedy — wait and re-run —
    rather than look like a broken credential."""
    monkeypatch.setattr(settings, "ZOHO_MAX_RETRIES", 2)

    class AlwaysThrottled(FakeHttp):
        def get(self, url, params=None, headers=None, **kw):
            return FakeResponse({"message": "too many requests"}, status=429)

    with pytest.raises(ZohoThrottleError) as e:
        list(ZohoApiSource(http=AlwaysThrottled({})).list_items())
    assert "resume" in str(e.value) and "kept" in str(e.value)


def test_calls_are_paced_to_stay_under_the_limit(monkeypatch):
    """Recovering from a 429 is the fallback; not provoking one is the fix."""
    monkeypatch.setattr(settings, "ZOHO_REQUESTS_PER_MINUTE", 60)
    waited: list[float] = []
    monkeypatch.setattr(ZohoApiSource, "_sleep", lambda self, s: waited.append(s))

    def items(params):
        page = int(params.get("page", 1))
        return {"code": 0, "items": [{"item_id": page, "name": "x"}],
                "page_context": {"has_more_page": page < 3}}

    list(ZohoApiSource(http=FakeHttp({"/items": items})).list_items())
    # three calls, so two gaps to hold: 60rpm ⇒ one second apart
    assert len([w for w in waited if w > 0.5]) == 2, waited


# ── resuming an interrupted pull ────────────────────────────────────────────
def test_documents_already_held_are_not_fetched_again():
    """The detail call is the entire cost of a pull. A resumed run must pay for
    the list calls only, or being throttled out once means never finishing."""
    listing = {"code": 0, "invoices": [
        {"invoice_id": "OLD", "date": _today(3), "status": "paid",
         "last_modified_time": "2026-07-01T10:00:00+0530"},
        {"invoice_id": "NEW", "date": _today(2), "status": "paid",
         "last_modified_time": "2026-07-02T10:00:00+0530"},
    ], "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {"invoice_id": "NEW", "customer_id": "9",
                                     "date": _today(2), "line_items": []}}
    http = FakeHttp({"/invoices/NEW": detail, "/invoices": listing})

    src = ZohoApiSource(http=http)
    rows = list(src.list_invoices(skip=lambda doc_id, mod: doc_id == "OLD"))

    assert [r["invoice_id"] for r in rows] == ["NEW"]
    assert not any(u.endswith("/invoices/OLD") for u, _ in http.gets)
    assert (src.documents_fetched, src.documents_resumed) == (1, 1)


def test_a_document_edited_in_zoho_is_pulled_again():
    """Resuming must not freeze a stale copy: the modification stamp is what
    distinguishes 'already have it' from 'had it, it changed'."""
    listing = {"code": 0, "invoices": [
        {"invoice_id": "INV1", "date": _today(3), "status": "paid",
         "last_modified_time": "2026-07-09T10:00:00+0530"}],
        "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {"invoice_id": "INV1", "customer_id": "9",
                                     "date": _today(3), "line_items": []}}
    http = FakeHttp({"/invoices/INV1": detail, "/invoices": listing})

    held = {"INV1": "2026-07-01T10:00:00+0530"}       # an older stamp
    rows = list(ZohoApiSource(http=http).list_invoices(
        skip=lambda doc_id, mod: doc_id in held and mod == held[doc_id]))
    assert [r["invoice_id"] for r in rows] == ["INV1"]


# ── the operator's start date ───────────────────────────────────────────────
def test_an_explicit_start_date_overrides_the_rolling_window():
    listing = {"code": 0, "invoices": [
        {"invoice_id": "IN", "date": "2025-06-01", "status": "paid"},
        {"invoice_id": "OUT", "date": "2024-06-01", "status": "paid"},
    ], "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {"invoice_id": "IN", "customer_id": "9",
                                     "date": "2025-06-01", "line_items": []}}
    http = FakeHttp({"/invoices/IN": detail, "/invoices": listing})
    rows = list(ZohoApiSource(http=http, since=date(2025, 1, 1)).list_invoices())
    assert [r["invoice_id"] for r in rows] == ["IN"]


def test_a_malformed_start_date_falls_back_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SYNC_FROM", "last january")
    assert configured_since() is None


def test_the_salesperson_on_an_invoice_is_carried_through():
    """Ownership is Zoho's fact, not something the platform infers."""
    listing = {"code": 0, "invoices": [{"invoice_id": "INV1", "date": _today(3),
                                        "status": "paid"}],
               "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {
        "invoice_id": "INV1", "customer_id": "9", "date": _today(3),
        "salesperson_id": 4455, "salesperson_name": "R. Nair", "line_items": []}}
    row = list(ZohoApiSource(http=FakeHttp({"/invoices/INV1": detail,
                                            "/invoices": listing})).list_invoices())[0]
    assert row["salesperson_id"] == "4455" and row["salesperson_name"] == "R. Nair"


def test_ping_confirms_the_configured_org():
    http = FakeHttp({"/organizations": {
        "code": 0,
        "organizations": [{"organization_id": "60036630487", "name": "4U Precision",
                           "currency_code": "INR"}]}})
    out = ZohoApiSource(http=http).ping()
    assert out["organization_found"] is True and out["organization_name"] == "4U Precision"


# ── what the pull asks Zoho for ─────────────────────────────────────────────
def test_the_item_master_is_read_including_inactive_items():
    """The bug this closes. Zoho's /items defaults to Status.Active, and a
    distributor deactivates an item the day the line is discontinued — but the
    bills that reference it do not go away. Without Status.All every historical
    line for a retired tool is skipped as UNKNOWN_PRODUCT, and because bills are
    where cost comes from, the visible symptom is missing margin rather than a
    missing item."""
    http = FakeHttp({"items": {"code": 0, "items": [],
                               "page_context": {"has_more_page": False}}})
    list(ZohoApiSource(http=http).list_items())
    _url, params = http.gets[0]
    assert params.get("filter_by") == "Status.All"


def test_contacts_are_read_including_inactive_ones():
    """Already Zoho's default, asked for explicitly so a change to that default
    cannot quietly start dropping dormant accounts the way /items does."""
    http = FakeHttp({"contacts": {"code": 0, "contacts": [],
                                  "page_context": {"has_more_page": False}}})
    src = ZohoApiSource(http=http)
    list(src.list_contacts())
    list(src.list_vendors())
    assert [p.get("filter_by") for _u, p in http.gets] == ["Status.All", "Status.All"]
    assert [p.get("contact_type") for _u, p in http.gets] == ["customer", "vendor"]


# ── a missing scope is not a bad credential ─────────────────────────────────
def test_a_scope_refusal_names_the_scope_and_not_the_credentials():
    """The 401 that made "vendors and payments are not being read" look like an
    authentication problem. The token is valid — it was just issued — and every
    other endpoint keeps working; this one endpoint is outside the grant."""
    from app.ingestion.zoho_client import ZohoScopeError

    refusal = FakeResponse({"code": 57, "message": "You are not authorized to "
                                                  "perform this operation"}, status=401)
    http = FakeHttp({})
    http.get = lambda url, params=None, headers=None, **kw: refusal   # type: ignore[assignment]

    with pytest.raises(ZohoScopeError) as caught:
        list(ZohoApiSource(http=http).list_customer_payments())
    assert caught.value.scope == "ZohoBooks.customerpayments.READ"
    message = str(caught.value)
    assert "ZohoBooks.customerpayments.READ" in message
    assert "credentials are valid" in message


def test_a_genuine_token_rejection_is_still_reported_as_one():
    """The distinction has to cut both ways, or a revoked token gets reported as
    a missing permission and somebody re-authorises with a wider scope and the
    same dead token."""
    from app.ingestion.zoho_client import ZohoAuthError, ZohoScopeError

    refusal = FakeResponse({"code": 14, "message": "Invalid oauth token"}, status=401)
    http = FakeHttp({})
    http.get = lambda url, params=None, headers=None, **kw: refusal   # type: ignore[assignment]

    with pytest.raises(ZohoAuthError) as caught:
        list(ZohoApiSource(http=http).list_customer_payments())
    assert not isinstance(caught.value, ZohoScopeError)
    assert "data centre" in str(caught.value)


def test_every_endpoint_the_pull_uses_has_a_named_scope():
    """A 401 on an endpoint with no mapping falls back to the credentials
    message, which is the misleading one. This fails if a new endpoint is added
    without telling the mapping about it."""
    from app.ingestion.zoho_client import scope_for_path

    for path in ("contacts", "items", "invoices", "bills", "customerpayments",
                 "purchaseorders", "salesorders", "vendorpayments", "users",
                 "customerpayments/12345"):
        assert scope_for_path(path), path


def test_every_scope_the_pull_uses_is_declared():
    """The other direction, which nothing checked and which drifted.

    ``REQUIRED_SCOPES`` is what an owner is told to paste into the Zoho console.
    A scope the client calls for and that list omits is one *nobody can ever
    have granted* — and ``ZohoBooks.creditnotes.READ`` was exactly that for as
    long as credit notes had been pulled. Because that stage degrades
    gracefully, the only symptom was a single skip line in a sync report, so
    the endpoint was refused on every connection, for ever, in silence.

    Asserted as an equality rather than a subset so the reverse also fails: a
    scope asked of an owner that nothing calls is a permission requested for no
    reason, which is its own small breach of trust.
    """
    from app.ingestion.connections import REQUIRED_SCOPES
    from app.ingestion.zoho_client import SCOPE_FOR_PATH

    declared = {s for s, _, _ in REQUIRED_SCOPES}
    used = set(SCOPE_FOR_PATH.values())
    assert used == declared, (
        f"used but never requested: {sorted(used - declared)}; "
        f"requested but never used: {sorted(declared - used)}")


def test_the_scope_probe_asks_each_scope_exactly_once():
    """``settings.READ`` gates items, locations and per-location stock alike, so
    probing per endpoint would ask one question three times and charge three
    calls for it. Also pins that nothing unprobeable creeps into the map:
    ``itemdetails`` needs ids from a previous call and cannot answer alone."""
    from app.ingestion.zoho_client import SCOPE_FOR_PATH, probe_paths

    probes = probe_paths()
    assert set(probes) == set(SCOPE_FOR_PATH.values())
    assert probes["ZohoBooks.settings.READ"] == "items"
    assert "itemdetails" not in probes.values()


def _probe_http(refused: set[str] = frozenset(), broken: set[str] = frozenset(),
                token_refusal: bool = False) -> FakeHttp:
    """A transport that answers each probe path differently: refused (401 with
    Zoho's out-of-scope code), broken (a 200 carrying no JSON), or fine."""
    scope_401 = FakeResponse({"code": 57, "message": "You are not authorized"}, status=401)
    bad_token_401 = FakeResponse({"code": 14, "message": "Invalid oauth token"}, status=401)
    no_json = FakeResponse(None)
    fine = FakeResponse({"code": 0, "page_context": {"has_more_page": False}})

    http = FakeHttp({})

    def get(url, params=None, headers=None, **kw):
        if token_refusal:
            return bad_token_401
        path = url.rstrip("/").rsplit("/", 1)[-1]
        if path in refused:
            return scope_401
        if path in broken:
            return no_json
        return fine

    http.get = get                                    # type: ignore[assignment]
    return http


def test_the_probe_separates_a_refused_scope_from_the_granted_ones():
    """What ``ping`` could never answer. ``organizations`` sits behind no scope,
    so a connection granted nothing else still pings green — the probe is the
    only thing that tells a whole grant from a login."""
    source = ZohoApiSource(http=_probe_http(refused={"customerpayments"}))

    by_scope = {r["scope"]: r for r in source.probe_scopes()}
    assert by_scope["ZohoBooks.customerpayments.READ"]["granted"] is False
    assert "ZohoBooks.customerpayments.READ" in by_scope[
        "ZohoBooks.customerpayments.READ"]["detail"]
    assert by_scope["ZohoBooks.bills.READ"]["granted"] is True
    assert by_scope["ZohoBooks.settings.READ"]["granted"] is True


def test_an_endpoint_the_probe_could_not_reach_is_unknown_and_not_granted():
    """Absence of evidence is not a pass (§1). A 5xx, a timeout or a body that
    will not parse says nothing about the grant, and recording it as granted
    would hand back a green check built out of a question nobody answered."""
    source = ZohoApiSource(http=_probe_http(broken={"bills"}))

    by_scope = {r["scope"]: r for r in source.probe_scopes()}
    assert by_scope["ZohoBooks.bills.READ"]["granted"] is None
    assert "non-JSON" in by_scope["ZohoBooks.bills.READ"]["detail"]
    assert by_scope["ZohoBooks.invoices.READ"]["granted"] is True


def test_a_dead_token_stops_the_probe_rather_than_blaming_every_scope():
    """A revoked credential refuses all ten endpoints. Reporting that as ten
    missing permissions would send somebody to the Zoho console to re-grant
    scopes they already have, when the token is the thing that died."""
    source = ZohoApiSource(http=_probe_http(token_refusal=True))

    with pytest.raises(ZohoAuthError) as caught:
        source.probe_scopes()
    from app.ingestion.zoho_client import ZohoScopeError
    assert not isinstance(caught.value, ZohoScopeError)


# ── commitments and money out ───────────────────────────────────────────────
def test_a_draft_order_is_not_a_commitment():
    """A draft promises nobody anything and can be deleted without trace.
    Counting one as demand would show a commitment that never existed."""
    listing = {"code": 0, "salesorders": [
        {"salesorder_id": "D1", "date": _today(1), "status": "draft"},
        {"salesorder_id": "S1", "date": _today(1), "status": "open",
         "customer_id": "c1", "total": 1000},
    ], "page_context": {"has_more_page": False}}
    rows = list(ZohoApiSource(http=FakeHttp({"/salesorders": listing})).list_sales_orders())
    assert [r["salesorder_id"] for r in rows] == ["S1"]


def test_an_order_is_read_from_the_list_row_alone():
    """Header grain: the questions this answers are all on the list row, and a
    detail call per order would cost one request each to answer none of them."""
    listing = {"code": 0, "salesorders": [
        {"salesorder_id": "S1", "salesorder_number": "SO-1", "date": _today(1),
         "status": "open", "customer_id": "c1", "shipment_date": _today(-10),
         "invoiced_status": "not_invoiced", "shipped_status": "pending",
         "total": 1000, "salesperson_id": "u9"},
    ], "page_context": {"has_more_page": False}}
    http = FakeHttp({"/salesorders": listing})
    row = list(ZohoApiSource(http=http).list_sales_orders())[0]
    assert not any("/salesorders/" in u for u, _ in http.gets)
    assert row["salesorder_number"] == "SO-1" and row["shipped_status"] == "pending"


def test_orders_and_payments_older_than_the_window_are_not_pulled(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_HISTORY_DAYS", 30)
    http = FakeHttp({
        "/salesorders": {"code": 0, "salesorders": [
            {"salesorder_id": "OLD", "date": _today(400), "status": "open"}],
            "page_context": {"has_more_page": False}},
        "/vendorpayments": {"code": 0, "vendorpayments": [
            {"payment_id": "OLD", "date": _today(400), "amount": 100}],
            "page_context": {"has_more_page": False}},
    })
    src = ZohoApiSource(http=http)
    assert list(src.list_sales_orders()) == []
    assert list(src.list_vendor_payments()) == []


def test_a_payment_out_keeps_its_amount_and_reference():
    listing = {"code": 0, "vendorpayments": [
        {"payment_id": "P1", "vendor_id": "v1", "date": _today(1),
         "amount": 80000.25, "payment_mode": "banktransfer",
         "reference_number": "NEFT-8891"},
    ], "page_context": {"has_more_page": False}}
    row = list(ZohoApiSource(http=FakeHttp({"/vendorpayments": listing})).list_vendor_payments())[0]
    assert row["payment_id"] == "P1" and row["vendor_id"] == "v1"
    assert row["reference_number"] == "NEFT-8891"


def test_the_stamp_stored_is_the_stamp_the_next_run_compares():
    """The whole of the "every sync re-reads the book" bug.

    The sync stores `last_modified_time` from the document it is handed, and
    the resume check compares against the one the *list* endpoint reports. Zoho
    does not promise those two strings are identical — and when they differ by
    so much as a format, every document compares unequal on every run: nothing
    is skipped, every document costs its detail call again, and every row is
    re-upserted.

    So the client hands back the list's stamp, and this asserts it: the detail
    here deliberately carries a *different* stamp, and it must not survive.
    """
    listing = {"code": 0, "invoices": [
        {"invoice_id": "A", "date": _today(2), "status": "paid",
         "last_modified_time": "2026-07-02T10:00:00+0530"},
    ], "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {
        "invoice_id": "A", "customer_id": "9", "date": _today(2),
        # The same document, stamped differently by the detail endpoint.
        "last_modified_time": "2026-07-02T04:30:00Z",
        "line_items": []}}
    http = FakeHttp({"/invoices/A": detail, "/invoices": listing})

    (row,) = list(ZohoApiSource(http=http).list_invoices())
    assert row["last_modified_time"] == "2026-07-02T10:00:00+0530", (
        "the stamp handed to the sync must be the one the resume check will "
        "see next time, or nothing is ever skipped")


def test_a_second_run_skips_everything_when_nothing_changed():
    """The behaviour the fix above is for, end to end through the predicate the
    sync actually builds: store what run one reports, feed it back as run two's
    cursor, and no detail call may be made."""
    listing = {"code": 0, "invoices": [
        {"invoice_id": "A", "date": _today(2), "status": "paid",
         "last_modified_time": "2026-07-02T10:00:00+0530"},
        {"invoice_id": "B", "date": _today(3), "status": "paid",
         "last_modified_time": "2026-07-01T10:00:00+0530"},
    ], "page_context": {"has_more_page": False}}
    details = {
        "/invoices/A": {"code": 0, "invoice": {
            "invoice_id": "A", "customer_id": "9", "date": _today(2),
            "last_modified_time": "SOMETHING ELSE ENTIRELY", "line_items": []}},
        "/invoices/B": {"code": 0, "invoice": {
            "invoice_id": "B", "customer_id": "9", "date": _today(3),
            "last_modified_time": "ALSO DIFFERENT", "line_items": []}},
    }

    first = ZohoApiSource(http=FakeHttp({**details, "/invoices": listing}))
    held = {r["invoice_id"]: r["last_modified_time"] for r in first.list_invoices()}
    assert len(held) == 2

    # Exactly the predicate `SyncService._skipper` builds from what was stored.
    def already_have(doc_id: str, modified_at: str) -> bool:
        if doc_id not in held:
            return False
        return not modified_at or modified_at == held[doc_id]

    http = FakeHttp({**details, "/invoices": listing})
    second = ZohoApiSource(http=http)
    rows = list(second.list_invoices(skip=already_have))

    assert rows == [], "an unchanged book must cost no detail calls at all"
    assert not any("/invoices/" in u for u, _ in http.gets)
    assert (second.documents_fetched, second.documents_resumed) == (0, 2)


def test_an_edited_document_is_still_re_read():
    """The other half: the cursor must not be so sticky that a real edit is
    missed. A moved stamp means a changed document."""
    held = {"A": "2026-07-02T10:00:00+0530"}
    listing = {"code": 0, "invoices": [
        {"invoice_id": "A", "date": _today(2), "status": "paid",
         "last_modified_time": "2026-07-09T18:00:00+0530"},   # edited since
    ], "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {"invoice_id": "A", "customer_id": "9",
                                     "date": _today(2), "line_items": []}}
    src = ZohoApiSource(http=FakeHttp({"/invoices/A": detail, "/invoices": listing}))
    rows = list(src.list_invoices(
        skip=lambda d, m: d in held and (not m or m == held[d])))
    assert [r["invoice_id"] for r in rows] == ["A"]


def test_the_invoice_header_reaches_the_receivables_fold():
    """The receivables state reads balance, due date and status off the invoice
    header. They were not passed through at all, so against a real Zoho pull
    every invoice arrived with nothing owed by anybody — while the fixtures
    supplied them and every test passed."""
    listing = {"code": 0, "invoices": [
        {"invoice_id": "A", "date": _today(2), "status": "overdue",
         "last_modified_time": "2026-07-02T10:00:00+0530"},
    ], "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {
        "invoice_id": "A", "invoice_number": "INV-1", "customer_id": "9",
        "date": _today(2), "due_date": _today(1), "status": "overdue",
        "total": "16700", "balance": "16700", "line_items": []}}
    (row,) = list(ZohoApiSource(
        http=FakeHttp({"/invoices/A": detail, "/invoices": listing})).list_invoices())

    assert row["balance"] == "16700"
    assert row["total"] == "16700"
    assert row["status"] == "overdue"
    assert row["due_date"] == _today(1)
    assert row["invoice_number"] == "INV-1"


# ── the cursor belongs to a connection ──────────────────────────────────────
def test_two_connections_keep_separate_resume_cursors(session):
    """Three Zoho companies run under one PIE organization, and a document id
    is unique only inside the company that issued it. A shared cursor lets one
    company's invoice id suppress another company's fetch of an unrelated
    document — and made a full sync of one company clear the cursor for all
    three, which is a re-read of every document in every company."""
    from app.repositories import ReadModelRepository

    org = "org_two_conns"
    a = ReadModelRepository(session, org, connector="zoho", connection_id="conn_a")
    b = ReadModelRepository(session, org, connector="zoho", connection_id="conn_b")

    a.mark_ingested("invoice", "12345", "STAMP-A")
    session.flush()

    assert a.ingested_index("invoice") == {"12345": "STAMP-A"}
    assert b.ingested_index("invoice") == {}, (
        "connection B has never read document 12345 — the same id in another "
        "Zoho company is a different document")

    b.mark_ingested("invoice", "12345", "STAMP-B")
    session.flush()
    assert a.ingested_index("invoice") == {"12345": "STAMP-A"}
    assert b.ingested_index("invoice") == {"12345": "STAMP-B"}


def test_a_full_sync_of_one_connection_leaves_the_others_cursor_alone(session):
    """Asking for a full re-read of one company is reasonable. Making the other
    two re-read their entire history as a side effect is not."""
    from app.repositories import ReadModelRepository

    org = "org_clear"
    a = ReadModelRepository(session, org, connector="zoho", connection_id="conn_a")
    b = ReadModelRepository(session, org, connector="zoho", connection_id="conn_b")
    a.mark_ingested("invoice", "1", "x")
    b.mark_ingested("invoice", "2", "y")
    session.flush()

    assert a.clear_ingested() == 1
    session.flush()
    assert a.ingested_index("invoice") == {}
    assert b.ingested_index("invoice") == {"2": "y"}, "B's history is untouched"
