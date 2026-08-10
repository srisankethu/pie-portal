"""The live Zoho Books quote adapter, against an injected fake transport.

Nothing here touches the network. The fake stands in for ``httpx.Client``, so
the adapter's own auth, pacing, retry, refusal and recovery logic all run for
real — the same code path a live tenant would take.

What these tests are actually protecting is narrow and specific: that a write
into a real ledger is never replayed on a guess, and that no failure is ever
reported as a success. Everything else here is in service of those two.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.ingestion.zoho_books_service import ZohoBooksService
from app.ingestion.zoho_client import ZohoCredentials, ZohoTransport
from app.zoho import (
    MockZoho,
    UnavailableZoho,
    ZohoUnavailable,
    ZohoWriteRefused,
    ZohoWriteUnknown,
    select_zoho_service,
)

from .test_zoho_client import FakeHttp, FakeResponse

CREDS = ZohoCredentials(organization_id="60036630487", client_id="cid",
                        client_secret="csec", refresh_token="rtok")

ITEM = {
    "item_id": "4400000001", "name": "CNMG 120408 KCP25", "sku": "CNMG120408KCP25",
    "rate": "1250.00", "purchase_rate": "980.50", "available_stock": "42",
    "status": "active",
}


class FakeBooks(FakeHttp):
    """``FakeHttp`` with a write side.

    Subclassed rather than rewritten: the GET routing, the OAuth-header
    assertion and the token exchange are the same behaviour the read client's
    tests already pin, and a second copy of them would be free to drift into
    agreeing with a broken adapter.

    ``writes`` maps a path fragment to a ``FakeResponse``, or to a callable
    taking the posted body — which may raise, to stand in for a socket that
    died mid-write.
    """

    def __init__(self, routes=None, writes=None, token_body=None):
        super().__init__(routes or {}, token_body=token_body)
        self.writes = writes or {}
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, params=None, headers=None, json=None, **kw):
        if "/oauth/" in url:
            return super().post(url, params=params, **kw)
        self.posts.append((url, dict(json or {})))
        for fragment, body in self.writes.items():
            if url.rstrip("/").endswith(fragment):
                return body(json) if callable(body) else body
        return FakeResponse({"code": 0})


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Run the real retry and pacing logic at test speed."""
    monkeypatch.setattr(ZohoTransport, "_sleep", lambda self, seconds: None)
    monkeypatch.setattr(settings, "ZOHO_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "ZOHO_HEALTH_TTL_SECONDS", 60.0)


def _service(**kw) -> tuple[ZohoBooksService, FakeBooks]:
    http = FakeBooks(**kw)
    return ZohoBooksService(credentials=CREDS, http=http), http


def _items(rows):
    return {"code": 0, "items": rows, "page_context": {"has_more_page": False}}


def _estimates(rows):
    return {"code": 0, "estimates": rows, "page_context": {"has_more_page": False}}


# ── selection ───────────────────────────────────────────────────────────────
def test_mock_is_the_default_and_live_needs_saying_so(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_QUOTE_SERVICE", "mock")
    assert isinstance(select_zoho_service(CREDS), MockZoho), \
        "configuration decides the adapter; passing credentials must not force live"


def test_live_without_credentials_refuses_rather_than_falling_back(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_QUOTE_SERVICE", "live")
    svc = select_zoho_service(reason="no connection")
    assert isinstance(svc, UnavailableZoho)
    assert svc.available is False
    with pytest.raises(ZohoWriteRefused):
        svc.create_estimate("Pitti", [], customer_ref="1", reference="r")


def test_live_with_credentials_selects_the_books_adapter(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_QUOTE_SERVICE", "live")
    assert isinstance(select_zoho_service(CREDS), ZohoBooksService)


# ── happy path ──────────────────────────────────────────────────────────────
def test_get_item_reads_real_price_cost_and_stock():
    svc, _ = _service(routes={"/items": _items([ITEM])})
    item = svc.get_item("CNMG 120408 KCP25")
    assert item.in_books is True
    assert item.list_price == 1250.0
    assert item.cost == 980.5
    assert item.stock == 42
    assert item.item_id == "4400000001"


def test_a_code_zoho_does_not_hold_is_not_in_books_and_has_no_price():
    svc, _ = _service(routes={"/items": _items([])})
    item = svc.get_item("XZ-NOTREAL")
    assert item.in_books is False and item.list_price is None and item.cost is None


def test_the_match_is_exact_not_zohos_ranking():
    """``search_text`` is fuzzy. Quoting the first hit would price CNMG 120408
    at the rate of CNMG 120412."""
    near = dict(ITEM, item_id="9", sku="CNMG120412KCP25", rate="1900.00")
    svc, _ = _service(routes={"/items": _items([near, ITEM])})
    assert svc.get_item("CNMG120408KCP25").item_id == "4400000001"


def test_an_item_with_no_stock_tracking_reports_unknown_not_zero():
    bare = {k: v for k, v in ITEM.items() if k != "available_stock"}
    svc, _ = _service(routes={"/items": _items([bare])})
    assert svc.get_item("CNMG120408KCP25").stock is None


def test_an_inactive_item_is_not_quotable():
    svc, _ = _service(routes={"/items": _items([dict(ITEM, status="inactive")])})
    assert svc.get_item("CNMG120408KCP25").in_books is False


def test_available_is_true_only_when_this_org_is_visible_to_the_token():
    orgs = {"code": 0, "organizations": [{"organization_id": "60036630487",
                                          "name": "SLS Engineers"}]}
    svc, http = _service(routes={"/organizations": orgs})
    assert svc.available is True
    before = len(http.gets)
    assert svc.available is True
    assert len(http.gets) == before, "the reachability probe must be cached per line"


def test_available_is_false_when_the_token_cannot_see_this_organization():
    orgs = {"code": 0, "organizations": [{"organization_id": "999", "name": "Someone else"}]}
    svc, _ = _service(routes={"/organizations": orgs})
    assert svc.available is False


def test_create_item_returns_what_zoho_actually_stored():
    svc, http = _service(writes={"/items": FakeResponse({"code": 0, "item": ITEM})})
    item = svc.create_item("CNMG120408KCP25", "CNMG 120408 KCP25", 1250.0)
    assert item.in_books is True and item.item_id == "4400000001"
    assert http.posts[0][1]["sku"] == "CNMG120408KCP25"


def test_create_estimate_posts_the_resolved_item_ids_and_the_reference():
    created = {"estimate_id": "77", "estimate_number": "EST-000123",
               "customer_name": "Pitti Engineering Ltd",
               "line_items": [{"item_id": "4400000001"}]}
    svc, http = _service(routes={"/estimates": _estimates([])},
                         writes={"/estimates": FakeResponse({"code": 0,
                                                             "estimate": created})})
    est = svc.create_estimate(
        "Pitti Engineering Ltd",
        [{"code": "CNMG120408KCP25", "itemId": "4400000001", "qty": 10, "rate": 1250.0}],
        customer_ref="3300000009", reference="QB-01234-abcd1234")
    assert est.number == "EST-000123" and est.already_existed is False
    _, body = http.posts[0]
    assert body["customer_id"] == "3300000009"
    assert body["reference_number"] == "QB-01234-abcd1234"
    assert body["line_items"] == [{"item_id": "4400000001", "quantity": "10",
                                   "rate": "1250.0"}]


# ── refusals: nothing is invented to make a write succeed ───────────────────
def test_an_estimate_without_a_contact_id_is_refused_not_guessed_by_name():
    svc, http = _service()
    with pytest.raises(ZohoWriteRefused):
        svc.create_estimate("Pitti Engineering Ltd", [{"code": "X", "itemId": "1",
                                                       "qty": 1, "rate": 10}],
                            reference="r")
    assert http.posts == [], "a refusal must not have reached Zoho"


def test_an_estimate_without_a_reference_is_refused_as_unrecheckable():
    svc, http = _service()
    with pytest.raises(ZohoWriteRefused):
        svc.create_estimate("Pitti", [{"code": "X", "itemId": "1", "qty": 1, "rate": 10}],
                            customer_ref="9")
    assert http.posts == []


def test_lines_not_in_these_books_are_refused_and_named():
    svc, http = _service(routes={"/estimates": _estimates([])})
    with pytest.raises(ZohoWriteRefused) as e:
        svc.create_estimate("Pitti", [{"code": "MISSING-1", "itemId": None,
                                       "qty": 1, "rate": 10}],
                            customer_ref="9", reference="r")
    assert e.value.codes == ["MISSING-1"]
    assert http.posts == []


def test_an_unpriced_line_is_refused_rather_than_priced_by_zoho():
    """Sending no rate lets Zoho fill one in from the item master — a number the
    salesperson never agreed to, on a document the customer will read."""
    svc, http = _service(routes={"/estimates": _estimates([])})
    with pytest.raises(ZohoWriteRefused) as e:
        svc.create_estimate("Pitti", [{"code": "CNMG", "itemId": "44", "qty": 1,
                                       "rate": None}],
                            customer_ref="9", reference="r")
    assert e.value.codes == ["CNMG"]
    assert http.posts == []


# ── idempotency ─────────────────────────────────────────────────────────────
def test_sending_the_same_quote_twice_returns_the_first_estimate():
    prior = {"estimate_id": "77", "estimate_number": "EST-000123",
             "reference_number": "QB-1-abcd", "customer_name": "Pitti",
             "line_items": [{"item_id": "44"}]}
    svc, http = _service(routes={"/estimates": _estimates([prior])})
    est = svc.create_estimate("Pitti", [{"code": "CNMG", "itemId": "44", "qty": 1,
                                         "rate": 10}],
                              customer_ref="9", reference="QB-1-abcd")
    assert est.number == "EST-000123" and est.already_existed is True
    assert http.posts == [], "the pre-flight found it; nothing may be created again"


# ── failure modes ───────────────────────────────────────────────────────────
def test_auth_failure_reads_as_books_unavailable():
    svc, _ = _service(token_body={"error": "invalid_client"})
    with pytest.raises(ZohoUnavailable):
        svc.get_item("CNMG120408KCP25")


def test_auth_failure_makes_available_false_rather_than_raising():
    svc, _ = _service(token_body={"error": "invalid_client"})
    assert svc.available is False


class Throttled(FakeBooks):
    """Every read refused by the rate limiter, however many times it is asked."""

    def get(self, url, params=None, headers=None, **kw):
        self.gets.append((url, dict(params or {})))
        return FakeResponse({}, status=429)


def test_a_rate_limited_read_is_unavailable_not_a_wrong_price():
    svc = ZohoBooksService(credentials=CREDS, http=Throttled())
    with pytest.raises(ZohoUnavailable):
        svc.get_item("CNMG120408KCP25")


def test_a_rate_limited_write_is_refused_because_it_never_reached_the_ledger():
    """A 429 is the limiter declining the call outright, so nothing was written
    and saying so — rather than "outcome unknown" — is the honest answer."""
    svc, _ = _service(routes={"/estimates": _estimates([])},
                      writes={"/estimates": FakeResponse({}, status=429)})
    with pytest.raises(ZohoWriteRefused):
        svc.create_estimate("Pitti", [{"code": "C", "itemId": "44", "qty": 1, "rate": 10}],
                            customer_ref="9", reference="r")


def test_a_write_is_never_replayed_after_a_server_error():
    """The central rule. A 5xx cannot be told from a write that landed and lost
    its response, so replaying it is how one quote becomes two estimates."""
    svc, http = _service(routes={"/estimates": _estimates([])},
                         writes={"/estimates": FakeResponse(None, status=503)})
    with pytest.raises(ZohoWriteRefused):
        svc.create_estimate("Pitti", [{"code": "C", "itemId": "44", "qty": 1, "rate": 10}],
                            customer_ref="9", reference="r")
    assert len(http.posts) == 1, "the write must be attempted exactly once"


def test_a_network_timeout_is_settled_by_reading_not_by_sending_again():
    """The estimate did land. Re-sending would duplicate it; reporting failure
    would leave a real document nobody knows about."""
    landed = {"estimate_id": "77", "estimate_number": "EST-000900",
              "reference_number": "QB-9-zz", "line_items": [{"item_id": "44"}]}
    seen = {"n": 0}

    def estimates(params):
        seen["n"] += 1
        # Empty on the pre-flight, present once the lost write has landed.
        return _estimates([] if seen["n"] == 1 else [landed])

    def die(_body):
        raise TimeoutError("connection timed out")

    svc, http = _service(routes={"/estimates": estimates}, writes={"/estimates": die})
    est = svc.create_estimate("Pitti", [{"code": "C", "itemId": "44", "qty": 1, "rate": 10}],
                              customer_ref="9", reference="QB-9-zz")
    assert est.number == "EST-000900" and est.already_existed is True
    assert len(http.posts) == 1


def test_a_timeout_that_left_nothing_behind_is_refused_as_safe_to_retry():
    def die(_body):
        raise TimeoutError("connection timed out")

    svc, _ = _service(routes={"/estimates": _estimates([])}, writes={"/estimates": die})
    with pytest.raises(ZohoWriteRefused) as e:
        svc.create_estimate("Pitti", [{"code": "C", "itemId": "44", "qty": 1, "rate": 10}],
                            customer_ref="9", reference="QB-9-zz")
    assert "safe" in str(e.value)


def test_a_timeout_that_cannot_be_re_read_is_reported_as_unknown():
    """The one outcome that is neither success nor failure. It must not be
    rounded to either, and it must carry the reference to look up."""
    calls = {"n": 0}

    def estimates(params):
        calls["n"] += 1
        if calls["n"] == 1:
            return _estimates([])
        return None                      # non-JSON: the re-read fails too

    def die(_body):
        raise TimeoutError("connection timed out")

    svc, _ = _service(routes={"/estimates": estimates}, writes={"/estimates": die})
    with pytest.raises(ZohoWriteUnknown) as e:
        svc.create_estimate("Pitti", [{"code": "C", "itemId": "44", "qty": 1, "rate": 10}],
                            customer_ref="9", reference="QB-9-zz")
    assert e.value.reference == "QB-9-zz"
    assert "QB-9-zz" in str(e.value)


def test_a_malformed_price_fails_loudly_rather_than_becoming_a_number():
    """``float('1,250.00')`` raises and ``float('')`` raises; the danger is a
    value that parses into something plausible and wrong. Money goes through the
    ingestion parser, and an unusable one stops the line."""
    svc, _ = _service(routes={"/items": _items([dict(ITEM, rate="1,250.00")])})
    with pytest.raises(ZohoUnavailable):
        svc.get_item("CNMG120408KCP25")


def test_a_malformed_payload_shape_does_not_become_a_priced_line():
    svc, _ = _service(routes={"/items": {"code": 0, "items": "not-a-list"}})
    with pytest.raises(ZohoUnavailable):
        svc.get_item("CNMG120408KCP25")
