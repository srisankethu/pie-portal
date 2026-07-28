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
    assert "ZOHO_REFRESH_TOKEN" in str(e.value)


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


# ── shapes the normalizer expects ───────────────────────────────────────────
def test_contacts_are_mapped_to_the_normalizer_shape():
    http = FakeHttp({"/contacts": {
        "code": 0,
        "contacts": [{"contact_id": 123, "contact_name": "4U Customer", "status": "active"}],
        "page_context": {"has_more_page": False}}})
    rows = list(ZohoApiSource(http=http).list_contacts())
    assert rows == [{"contact_id": "123", "contact_name": "4U Customer", "status": "active"}]


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
    assert rows[0]["line_items"] == [
        {"line_item_id": "1", "item_id": "9", "quantity": 20, "rate": 530, "item_total": 10600}]


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
