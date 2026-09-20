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
    ZohoCredentials,
    ZohoError,
    ZohoThrottleError,
    ZohoWriteUncertain,
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
    """Records calls and replays canned bodies keyed by path fragment.

    ``writes`` is the same idea on the POST side: a fragment maps to a
    ``FakeResponse``, or to a callable taking the posted body — which may raise,
    to stand in for a socket that died mid-write. It lives on this base rather
    than on one adapter's fake because the verb is the transport's concern:
    ``_request`` decides what may be re-sent, and both the sync's GETs and the
    Quote Builder's POSTs go through it.
    """

    def __init__(self, routes, token_body=None, writes=None):
        self.routes = routes
        self.writes = writes or {}
        self.token_body = token_body if token_body is not None else {
            "access_token": "tok-1", "expires_in": 3600}
        self.gets: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self.token_calls = 0

    def post(self, url, params=None, json=None, **kw):
        # The token exchange and a write are both POSTs; the URL is what tells
        # them apart, and counting one as the other would hide a replayed write
        # behind a refresh.
        if "/oauth/" in url:
            self.token_calls += 1
            return FakeResponse(self.token_body)
        # Recorded before the answer is produced, so a write whose connection
        # dies still counts as a call that was made — which is the whole
        # question a duplicate-write pin is asking.
        self.posts.append((url, dict(json or {})))
        for fragment, body in self.writes.items():
            if url.rstrip("/").endswith(fragment):
                return body(json) if callable(body) else body
        return FakeResponse({"code": 0})

    def get(self, url, params=None, headers=None, **kw):
        self.gets.append((url, dict(params or {})))
        assert headers and headers.get("Authorization", "").startswith("Zoho-oauthtoken "), \
            "every data call must carry the OAuth header"
        for fragment, body in self.routes.items():
            if url.rstrip("/").endswith(fragment):
                return FakeResponse(body(params) if callable(body) else body)
        return FakeResponse({"code": 0, "page_context": {"has_more_page": False}})


# One tenant's Zoho grant, for the tests that build a source directly. The
# environment credential fallback is gone, so every source is constructed with
# credentials; `_src` supplies these unless a test passes its own.
_TEST_CREDS = ZohoCredentials(
    organization_id="60036630487", client_id="cid",
    client_secret="csec", refresh_token="rtok")


def _src(http=None, **kw):
    kw.setdefault("credentials", _TEST_CREDS)
    return ZohoApiSource(http=http, **kw)


@pytest.fixture(autouse=True)
def _tuning(monkeypatch):
    # Pull tuning only — the ZOHO_* credential settings were removed with the
    # environment fallback; a source carries its own credentials now.
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
def test_missing_credentials_name_what_is_missing():
    creds = ZohoCredentials(organization_id="999999", client_id="cid",
                            client_secret="csec", refresh_token="")
    with pytest.raises(ZohoAuthError) as e:
        _src(http=FakeHttp({}), credentials=creds).list_items().__iter__().__next__()
    assert "refresh_token" in str(e.value)


def test_a_rejected_refresh_token_points_at_the_token_and_the_data_centre():
    http = FakeHttp({}, token_body={"error": "invalid_code"})
    with pytest.raises(ZohoAuthError) as e:
        list(_src(http=http).list_items())
    assert "data centre" in str(e.value)
    assert "refresh token" in str(e.value)


def test_a_rejected_client_secret_does_not_blame_the_data_centre():
    """The message that cost an afternoon.

    Every token refusal used to end with "check that ZOHO_ACCOUNTS_BASE matches
    the data centre". For ``invalid_client_secret`` that is the one thing Zoho
    has just told us is *not* wrong — it recognised the client id, which means
    it found the app at the host it was asked at, and rejected the secret. An
    owner who follows the old advice changes the DC, the refresh token then
    fails as unknown there too, and one wrong setting has become two.
    """
    http = FakeHttp({}, token_body={"error": "invalid_client_secret"})
    with pytest.raises(ZohoAuthError) as e:
        list(_src(http=http).list_items())
    message = str(e.value)

    assert "invalid_client_secret" in message, "Zoho's own code stays in the message"
    assert "not what is wrong" in message
    # The three things that actually produce it, so the reader can act.
    assert "does not match the client id" in message
    assert "separate secret per data centre" in message
    assert "issued by a different client" in message


def test_a_stored_connection_is_not_told_to_check_an_environment_variable():
    """Whoever sees this typed the data centre into a form on the same screen.

    Naming ``ZOHO_ACCOUNTS_BASE`` at them sends an owner looking for a variable
    that has no bearing on their connection and that they cannot edit from where
    they are standing. Every connection is a stored one now, so the message
    always points at the connection rather than at a variable.
    """
    stored = ZohoCredentials(organization_id="999999", client_id="cid",
                             client_secret="csec", refresh_token="rtok")
    http = FakeHttp({}, token_body={"error": "invalid_code"})
    with pytest.raises(ZohoAuthError) as e:
        list(_src(http=http, credentials=stored).list_items())
    assert "ZOHO_ACCOUNTS_BASE" not in str(e.value)
    assert "this connection" in str(e.value)


def test_an_unrecognised_token_error_names_all_three_parts_rather_than_guessing():
    """A code this map does not know must not inherit another code's remedy.

    The fallback names the client id, the secret and the refresh token together
    — which is honest about having no idea which of them Zoho objected to, and
    is the reason a new Zoho error string cannot silently acquire a confident,
    wrong explanation.
    """
    http = FakeHttp({}, token_body={"error": "some_new_zoho_code"})
    with pytest.raises(ZohoAuthError) as e:
        list(_src(http=http).list_items())
    message = str(e.value)
    assert "some_new_zoho_code" in message
    for part in ("client id", "client secret", "refresh token"):
        assert part in message


# ── the refresh quota ───────────────────────────────────────────────────────
#
# Zoho issues at most ten access tokens per refresh token per ten minutes and
# answers the eleventh with ``Access Denied``. A sync builds one source per
# window — nineteen of them, plus a reference pass, per company — so a token
# cached on the source is a quota spent twenty times over for one hour-long
# token. These pin the cache to the credential instead.
def test_a_second_source_on_one_credential_does_not_spend_another_refresh():
    """The regression. Two sources, one grant, one trip to the token endpoint.

    Sources are built per window on purpose (each carries its own listing
    state), so the fix cannot be "share the source" — it has to be a token that
    outlives the object that fetched it.
    """
    http = FakeHttp({})
    list(_src(http=http).list_items())
    list(_src(http=http).list_items())
    assert http.token_calls == 1


def test_a_full_run_of_windows_stays_far_inside_the_ten_token_quota():
    """The shape that actually failed: twenty sources against a quota of ten.

    Asserted as an exact 1 rather than "under 10", because the moment this is
    2 the cache is keyed on something that varies per source and the twentieth
    window is back over the line.
    """
    http = FakeHttp({})
    for _ in range(20):
        list(_src(http=http).list_items())
    assert http.token_calls == 1


def test_each_grant_holds_its_own_token():
    """Sharing is per credential, not global. Handing one tenant's token to
    another tenant's source would be the worst possible way to save a call."""
    other = ZohoCredentials(organization_id="60036630487", client_id="cid-2",
                            client_secret="csec-2", refresh_token="rtok-2")
    http = FakeHttp({})
    list(_src(http=http).list_items())
    list(_src(http=http, credentials=other).list_items())
    assert http.token_calls == 2


def test_the_same_secret_at_another_data_centre_is_another_token():
    """A token minted at ``.in`` is not valid at ``.com``. The data centre is
    part of what the credential authenticates as, so it is part of the key."""
    elsewhere = ZohoCredentials(
        organization_id="60036630487", client_id="cid", client_secret="csec",
        refresh_token="rtok", accounts_base="https://accounts.zoho.com",
        api_base="https://www.zohoapis.com/books/v3")
    http = FakeHttp({})
    list(_src(http=http).list_items())
    list(_src(http=http, credentials=elsewhere).list_items())
    assert http.token_calls == 2


def test_two_books_under_one_grant_share_one_token():
    """Three companies on one merged credential is the state
    ``merge_credentials`` exists to produce. Keying the token by book would
    spend three refreshes to hold three copies of the same token."""
    second_book = ZohoCredentials(
        organization_id="99999999999", client_id="cid", client_secret="csec",
        refresh_token="rtok")
    http = FakeHttp({})
    list(_src(http=http).list_items())
    list(_src(http=http, credentials=second_book).list_items())
    assert http.token_calls == 1


def test_a_token_zoho_rejects_is_dropped_for_every_source_not_just_this_one():
    """A shared cache has to be invalidated where it is shared.

    A 401 means the token is dead for everything built on that credential. Left
    in the cache it would be served to the next window and refused again, and
    the retry that exists to recover from a mid-run revocation would spend the
    rest of the run failing.
    """
    http = FakeHttp({})
    calls = {"n": 0}

    def get(url, params=None, headers=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse({"code": 14, "message": "Invalid oauth token"},
                                status=401)
        return FakeResponse({"code": 0, "items": [],
                             "page_context": {"has_more_page": False}})

    http.get = get                                    # type: ignore[assignment]
    list(_src(http=http).list_items())
    assert http.token_calls == 2, "the rejected token must not be re-served"

    # And the fresh one is what the *next* source gets, without a third trip.
    list(_src(http=http).list_items())
    assert http.token_calls == 2


def test_zohos_refresh_throttle_is_not_reported_as_a_rejected_credential():
    """``Access Denied`` is Zoho's throttle, not a verdict on the credential.

    It is the response to an eleventh access token inside ten minutes, and the
    grant behind it is untouched. Reported as an auth failure it read as "Zoho
    credentials were rejected" and sent an owner to rotate a secret that was
    correct — which spends more of the same exhausted quota and keeps the
    connection shut. The class is what the sync layer reads: a throttle stops
    the pull to resume later, an auth error asks a human to go and change
    something.
    """
    from app.ingestion.errors import SourceThrottleError

    http = FakeHttp({}, token_body={
        "error": "Access Denied",
        "error_description": "You have made too many requests continuously. "
                             "Please try again after some time."})
    with pytest.raises(ZohoThrottleError) as caught:
        list(_src(http=http).list_items())

    assert isinstance(caught.value, SourceThrottleError)
    assert not isinstance(caught.value, ZohoAuthError)
    message = str(caught.value)
    assert "not a rejected credential" in message
    assert "ten minutes" in message


def test_zohos_own_sentence_survives_into_the_message():
    """``error_description`` is the half that says what happened.

    Throwing it away is how "You have made too many requests continuously"
    reached an owner as "Zoho refused the sign-in without saying which part of
    it failed" — a fallback claiming there was no evidence, printed by the code
    that had just discarded it.
    """
    http = FakeHttp({}, token_body={
        "error": "some_new_zoho_code",
        "error_description": "The account is on hold."})
    with pytest.raises(ZohoAuthError) as caught:
        list(_src(http=http).list_items())
    assert "The account is on hold." in str(caught.value)


def test_access_token_is_reused_across_calls():
    http = FakeHttp({"/items": {"code": 0, "items": [], "page_context": {"has_more_page": False}},
                     "/contacts": {"code": 0, "contacts": [], "page_context": {"has_more_page": False}}})
    src = _src(http=http)
    list(src.list_items())
    list(src.list_contacts())
    assert http.token_calls == 1, "the hourly token must not be re-fetched per call"


def test_organization_id_is_sent_on_every_call():
    http = FakeHttp({"/items": {"code": 0, "items": [], "page_context": {"has_more_page": False}}})
    list(_src(http=http).list_items())
    assert all(p.get("organization_id") == "60036630487" for _, p in http.gets)


# ── credentials are per-source (multi-tenant) ───────────────────────────────
def test_each_source_uses_the_credentials_it_was_given():
    """The multi-tenant contract: a source reads exactly the credential it was
    constructed with — two tenants running in the same process must never blend
    into one another's Zoho account. There is no process-wide credential to
    leak between them."""
    creds = ZohoCredentials(organization_id="999999", client_id="other-cid",
                            client_secret="other-csec", refresh_token="other-rtok",
                            accounts_base="https://accounts.zoho.eu",
                            api_base="https://www.zohoapis.eu/books/v3")
    http = FakeHttp({"/items": {"code": 0, "items": [], "page_context": {"has_more_page": False}}})
    src = ZohoApiSource(http=http, credentials=creds)
    list(src.list_items())

    assert all(p.get("organization_id") == "999999" for _, p in http.gets)
    assert all(u.startswith("https://www.zohoapis.eu/books/v3") for u, _ in http.gets)


def test_omitting_credentials_is_an_error_not_an_environment_fallback():
    """The environment fallback is gone: a source built with no credentials
    cannot silently borrow a process-wide ZOHO_* grant — that is exactly the
    cross-tenant leak a multi-tenant platform must not have. It refuses instead,
    naming where a connection is configured."""
    with pytest.raises(ValueError, match="requires credentials"):
        ZohoApiSource(http=FakeHttp({}))


# ── shapes the normalizer expects ───────────────────────────────────────────
def test_contacts_are_mapped_to_the_normalizer_shape():
    http = FakeHttp({"/contacts": {
        "code": 0,
        "contacts": [{"contact_id": 123, "contact_name": "4U Customer", "status": "active"}],
        "page_context": {"has_more_page": False}}})
    rows = list(_src(http=http).list_contacts())
    # gst_no rides along for the identity layer. Present as None rather than
    # absent when the edition has no such field, so a caller never has to guess
    # whether the key was missing or the value was. `source_attributes` is
    # present and empty for the same reason, and the emptiness is not a claim:
    # `normalize._source_attributes` turns it into NULL, which says only that
    # none are held. `created_time` is present and None on the same terms: this
    # payload carries no stamp, and the key says so rather than going missing.
    assert rows == [{"contact_id": "123", "created_time": None,
                     "contact_name": "4U Customer",
                     "gst_no": None, "status": "active",
                     "source_attributes": {}}]


def test_a_contacts_gstin_reaches_the_identity_layer():
    """The strongest customer matching key there is, and it was simply not being
    read until the identity layer needed it."""
    http = FakeHttp({"/contacts": {
        "code": 0,
        "contacts": [{"contact_id": 1, "contact_name": "ABC",
                      "gst_no": "29ABCDE1234F1Z5", "status": "active"}],
        "page_context": {"has_more_page": False}}})
    assert list(_src(http=http).list_contacts())[0]["gst_no"] == "29ABCDE1234F1Z5"


def test_an_items_sku_reaches_the_identity_layer():
    http = FakeHttp({"/items": {
        "code": 0,
        "items": [{"item_id": 7, "name": "KCMT 090304 LF", "sku": "KCMT090304LF",
                   "status": "active"}],
        "page_context": {"has_more_page": False}}})
    assert list(_src(http=http).list_items())[0]["sku"] == "KCMT090304LF"


# ── the taxonomy the business maintains ─────────────────────────────────────
#
# A live item row, trimmed to the fields under test. Every value here is copied
# from the SLS master on 2026-08-30, including the misfiling in the last test:
# these are the shapes the client actually meets, not invented ones.

def _items(*rows):
    return FakeHttp({"/items": {"code": 0, "items": list(rows),
                                "page_context": {"has_more_page": False}}})


def test_the_maintained_item_taxonomy_is_carried_rather_than_discarded():
    """`cf_item_type` and `cf_item_category` are on the *list* payload, so this
    costs no extra call — and the column the platform did read is empty."""
    row = {"item_id": 7, "name": "0.5x06x38x 2FL", "status": "active",
           "cf_item_type": "Endmill", "cf_item_category": "Milling"}
    payload = list(_src(http=_items(row)).list_items())[0]

    assert payload["source_item_type"] == "Endmill"
    assert payload["source_item_category"] == "Milling"
    assert payload["category_name"] is None, (
        "Zoho's own Inventory category is what these books leave empty; if it "
        "starts arriving, the two fields above are still the maintained ones")


def test_the_taxonomy_is_carried_verbatim_and_not_mapped_on_the_way_in():
    """Raw for the reason `category` and `manufacturer` are: the map onto
    anything the platform reasons with is versioned policy, and a value
    rewritten at sync time can never be re-read under a corrected map."""
    row = {"item_id": 7, "name": "x", "status": "active",
           "cf_item_type": "Tool Holder", "cf_item_category": "Grooving & Parting"}
    payload = list(_src(http=_items(row)).list_items())[0]

    assert payload["source_item_type"] == "Tool Holder"
    assert payload["source_item_category"] == "Grooving & Parting"


def test_an_unclassified_item_says_nothing_rather_than_guessing():
    """Zoho omits an unset custom field entirely. Absent must stay absent: the
    item's name is right there and inferring from it is how a wrong part gets
    quoted."""
    row = {"item_id": 7, "name": "CNMG 120408 INSERT", "status": "active"}
    payload = list(_src(http=_items(row)).list_items())[0]

    assert payload["source_item_type"] is None
    assert payload["source_item_category"] is None


def test_the_customer_lookup_on_an_item_is_not_copied_onto_the_product():
    """`cf_end_customer` is configured on this book. Carrying it would put a
    customer's identity on a catalogue row that every reader of the catalogue
    can see — `trust/`'s concern, and not solved by copying it here first."""
    row = {"item_id": 7, "name": "x", "status": "active",
           "cf_end_customer": "Some Customer Pvt Ltd",
           "cf_end_customer_unformatted": "Some Customer Pvt Ltd"}
    payload = list(_src(http=_items(row)).list_items())[0]

    assert not [k for k, v in payload.items()
                if isinstance(v, str) and "Some Customer" in v]


def test_a_misfiled_item_is_carried_as_the_person_filed_it():
    """A taper-shank reamer filed Tap / Threading, from the live master. The
    client does not correct it and does not drop it: it is evidence about the
    item, and a reader who is shown the source's own words can see it is wrong.
    Silently repairing it here would hide the one signal that the taxonomy
    needs maintaining."""
    row = {"item_id": 7, "name": "HSS Taper Shank Reamer Dia 10mm",
           "status": "active", "cf_item_type": "Tap",
           "cf_item_category": "Threading"}
    payload = list(_src(http=_items(row)).list_items())[0]

    assert payload["source_item_type"] == "Tap"


def test_the_two_item_type_fields_do_not_shadow_each_other():
    """Zoho has two unrelated notions of "item type" and this payload carries
    both: ``item_type`` is inventory-versus-service, ``cf_item_type`` is the
    tool class. The first cut of this used one key for both, the later
    assignment won, and *both* fields read as the wrong thing. The name is
    long for this reason."""
    row = {"item_id": 7, "name": "x", "status": "active",
           "item_type": "inventory", "cf_item_type": "Endmill"}
    payload = list(_src(http=_items(row)).list_items())[0]

    assert payload["item_type"] == "inventory"
    assert payload["source_item_type"] == "Endmill"


def test_the_taxonomy_survives_the_by_id_fetch_as_well():
    """`get_item` and `list_items` share `_item_payload` so the racing case
    cannot write a subtly different row. This is that promise, for the new
    fields."""
    row = {"item_id": 7, "name": "x", "status": "active",
           "cf_item_type": "Insert", "cf_item_category": "Turning"}
    http = FakeHttp({"/items/7": {"code": 0, "item": row},
                     "/items": {"code": 0, "items": [row],
                                "page_context": {"has_more_page": False}}})
    src = _src(http=http)

    assert src.get_item("7") == list(src.list_items())[0]


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
    rows = list(_src(http=http).list_invoices())
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
    row = list(_src(http=http).list_bills())[0]
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
    row = list(_src(http=FakeHttp(
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
    row = list(_src(http=FakeHttp(
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
    src = _src(http=http)
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
    assert list(_src(http=http).list_invoices()) == []
    assert not any("/invoices/" in u for u, _ in http.gets), "no detail call for excluded docs"


def test_history_older_than_the_window_is_not_pulled(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_HISTORY_DAYS", 30)
    listing = {"code": 0, "invoices": [{"invoice_id": "OLD", "date": _today(400), "status": "paid"}],
               "page_context": {"has_more_page": False}}
    http = FakeHttp({"/invoices": listing})
    assert list(_src(http=http).list_invoices()) == []


# ── pagination + errors ─────────────────────────────────────────────────────
def test_pagination_follows_has_more_page():
    def items(params):
        page = int(params.get("page", 1))
        if page == 1:
            return {"code": 0, "items": [{"item_id": 1, "name": "one"}],
                    "page_context": {"has_more_page": True}}
        return {"code": 0, "items": [{"item_id": 2, "name": "two"}],
                "page_context": {"has_more_page": False}}

    rows = list(_src(http=FakeHttp({"/items": items})).list_items())
    assert [r["item_id"] for r in rows] == ["1", "2"]


def test_pagination_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_MAX_PAGES", 3)
    endless = {"code": 0, "items": [{"item_id": 1, "name": "x"}],
               "page_context": {"has_more_page": True}}
    http = FakeHttp({"/items": endless})
    assert len(list(_src(http=http).list_items())) == 3


def test_api_error_body_is_surfaced():
    http = FakeHttp({"/items": {"code": 1005, "message": "scope is not permitted"}})
    with pytest.raises(ZohoError) as e:
        list(_src(http=http).list_items())
    assert "scope is not permitted" in str(e.value)


def test_ping_reports_when_the_org_is_not_visible():
    http = FakeHttp({"/organizations": {
        "code": 0,
        "organizations": [{"organization_id": "999", "name": "Some Other Co",
                           "currency_code": "INR"}]}})
    out = _src(http=http).ping()
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

    rows = list(_src(http=Throttling({})).list_items())
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

    list(_src(http=Throttling()).list_items())
    assert 7 in waits, f"Retry-After was ignored: {waits}"


def test_being_throttled_out_says_the_work_so_far_is_kept(monkeypatch, waits):
    """The message a person reads has to name the remedy — wait and re-run —
    rather than look like a broken credential."""
    monkeypatch.setattr(settings, "ZOHO_MAX_RETRIES", 2)

    class AlwaysThrottled(FakeHttp):
        def get(self, url, params=None, headers=None, **kw):
            return FakeResponse({"message": "too many requests"}, status=429)

    with pytest.raises(ZohoThrottleError) as e:
        list(_src(http=AlwaysThrottled({})).list_items())
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

    list(_src(http=FakeHttp({"/items": items})).list_items())
    # three calls, so two gaps to hold: 60rpm ⇒ one second apart
    assert len([w for w in waited if w > 0.5]) == 2, waited


# ── a write is sent once, or it is not sent ─────────────────────────────────
#
# One retry loop serves both: the sync's thousands of GETs and the Quote
# Builder's handful of POSTs. It used to decide what could be re-sent from two
# readings that sound safe and are not — that a 401 and a 429 each prove the
# call never reached the books. Neither does. A gateway can mint either one
# *after* the backend accepted the call, and from here that is indistinguishable
# from a refusal, so re-sending on that reading is how one estimate becomes two
# documents in a customer's books.
#
# ``erp/transport.py`` was written later against the same question and refuses
# both readings. These pin this transport to that policy, so there is one rule
# and not two for a reader to compare.
#
# A refused write is not a dead end: ``ZohoWriteUncertain`` is what both write
# paths in ``zoho_books_service`` already catch, and they settle the question by
# reading the record back rather than by sending it again.
def _send_estimate(http):
    """The Quote Builder's write, through the transport that carries it."""
    return _src(http=http)._request(
        "POST", "estimates",
        json={"customer_id": "9", "reference_number": "QB-9-zz"})


def test_a_write_refused_with_a_401_is_not_sent_again():
    """The reading this replaces said a rejected token never reached the books.

    Usually true, and the exception is the expensive one: a gateway can answer
    401 after the backend has already accepted the call. Dropping the token is
    still right — it may really be dead — but re-sending the estimate on the
    fresh one bets that nothing was written, and the losing side of that bet is
    a second document in a customer's books.
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(
        {"code": 14, "message": "Invalid oauth token"}, status=401)})
    src = _src(http=http)

    with pytest.raises(ZohoWriteUncertain):
        src._request("POST", "estimates",
                     json={"customer_id": "9", "reference_number": "QB-9-zz"})
    assert len(http.posts) == 1, "the estimate must not be sent a second time"

    # The branch has two effects and only one of them was pinned. Dropping the
    # token is the half a single call cannot see — delete the
    # ``_forget_access_token`` and this file stayed green, while in production
    # a genuinely revoked token would sit in the process-wide cache for the
    # rest of its hour and refuse every call on that credential, from every
    # source built on it. It takes a second call to observe, so this makes one.
    list(src.list_items())
    assert http.token_calls == 2, \
        "the refused token must not be served to the next call"


def test_a_rate_limited_write_is_not_waited_out_and_sent_again(waits):
    """A 429 is the limiter refusing the call — except when it is not.

    A front end can throttle a call its own backend has already accepted, and
    the two answers are identical from here. Waiting out the backoff and
    re-sending is the same bet as the 401, made more patiently.
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(
        {"message": "too many requests"}, status=429)})

    with pytest.raises(ZohoWriteUncertain):
        _send_estimate(http)
    assert len(http.posts) == 1, "the estimate must not be sent a second time"
    assert not [w for w in waits if w > 0], \
        f"a write must not be held for a retry it is never allowed to make: {waits}"


def test_a_write_whose_connection_died_is_unknown_rather_than_failed():
    """A fault this side of the answer is the same unknown as a 5xx.

    The request may well have been received; what was lost is the reply. Left
    raw it reaches the caller as a bare connection error, which reads as
    "nothing happened" — and the caller that believes that sends the estimate
    again.
    """
    def die(_body):
        raise ConnectionError("connection reset by peer")

    http = FakeHttp({}, writes={"/estimates": die})

    with pytest.raises(ZohoWriteUncertain) as caught:
        _send_estimate(http)
    assert len(http.posts) == 1, "the estimate must not be sent a second time"
    # The fault itself has to survive the re-raise, or the one piece of evidence
    # about what went wrong is gone by the time anyone reads the log.
    assert isinstance(caught.value.__cause__ or caught.value.__context__,
                      ConnectionError)


def test_a_write_outside_the_grant_is_a_definite_no_and_still_only_sent_once():
    """The case the fix could most easily break, in both directions.

    Code 57 is Zoho's own application saying the call is outside the grant, so
    nothing was written and "outcome unknown" would be a worse answer than the
    truth — an owner who is told to go and check the books cannot act on it,
    where one who is told which scope is missing can. It is also the answer that
    must not cost a second send to reach: the body says so on the first refusal.
    """
    from app.ingestion.zoho_client import ZohoScopeError

    http = FakeHttp({}, writes={"/estimates": FakeResponse(
        {"code": 57, "message": "You are not authorized to perform this "
                                "operation"}, status=401)})

    with pytest.raises(ZohoScopeError) as caught:
        _send_estimate(http)
    assert caught.value.scope == "ZohoBooks.estimates.CREATE"
    assert len(http.posts) == 1, "a refusal that names itself needs no second try"


def test_a_write_that_ends_in_a_server_error_is_still_never_replayed():
    """The one branch that already read this correctly, pinned before the rewrite.

    ``zoho_books_service`` has a test for the same 5xx, but it asserts what the
    *service* answers once settle-by-read has run — which would still look right
    if this loop started raising some other exception the service maps the same
    way. This asks the transport directly.
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(None, status=503)})

    with pytest.raises(ZohoWriteUncertain):
        _send_estimate(http)
    assert len(http.posts) == 1, "the estimate must not be sent a second time"


def test_a_write_refused_with_a_403_is_treated_exactly_like_a_401():
    """The status Books is not documented to send, which is why it is covered.

    ``erp/transport.py`` pairs 401 and 403 and this transport had a 401 branch
    only, so a 403 fell through to the generic refusal and reached the desk as
    "Zoho refused the estimate — nothing was written", with no read behind the
    claim. Nothing in this repo has seen Books answer 403; an edge, a WAF or a
    proxy in front of it can, and that is the same hop the 401 argument is
    about. A rule that holds only for the statuses the vendor documents is a
    rule about the vendor, not about the write.
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(
        {"message": "Forbidden"}, status=403)})

    with pytest.raises(ZohoWriteUncertain):
        _send_estimate(http)
    assert len(http.posts) == 1, "the estimate must not be sent a second time"


def test_a_created_record_whose_answer_is_unreadable_is_uncertain_not_refused():
    """HTTP 201 and a body that will not parse: the estimate exists.

    A truncating proxy, an intercepting gateway, half a response — the status
    has already said Zoho took the call, and only the reading failed. This was
    the last outcome in the taxonomy still reported as a definite refusal, and
    it is the worst one to get wrong in that direction: every other member says
    "maybe", this one said "no" about a record that is there.
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(None, status=201)})

    with pytest.raises(ZohoWriteUncertain) as caught:
        _send_estimate(http)
    assert len(http.posts) == 1, "the estimate must not be sent a second time"
    assert "201" in str(caught.value)


def test_an_accepted_write_zoho_does_not_confirm_is_uncertain_too():
    """A 202 parses fine and still is not a created record.

    The same hole as the unreadable body, reached through the status check
    rather than the parse: this adapter reads ``estimate`` out of a 200 or a
    201 and has no answer for anything else in the band. "Accepted" is not
    "created", but it is a great deal closer to it than "refused".
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(
        {"code": 0, "message": "accepted"}, status=202)})

    with pytest.raises(ZohoWriteUncertain):
        _send_estimate(http)
    assert len(http.posts) == 1, "the estimate must not be sent a second time"


def test_zohos_own_refusal_in_the_body_of_a_write_stays_a_definite_no():
    """The other side of the two pins above, and the one they could break.

    Zoho answers an application refusal as a 2xx carrying a non-zero ``code``.
    That is its own ledger saying no before acting, so it must stay a plain
    ``ZohoError`` — widening "2xx we cannot read" to "2xx" would turn every
    duplicate-name refusal into a lookup for a record that was never created.
    """
    http = FakeHttp({}, writes={"/estimates": FakeResponse(
        {"code": 1002, "message": "Estimate number already exists"}, status=200)})

    with pytest.raises(ZohoError) as caught:
        _send_estimate(http)
    assert not isinstance(caught.value, ZohoWriteUncertain)
    assert "already exists" in str(caught.value)


# The other half, and the half that carries the sync: a read is replayable and
# has to stay that way. Thousands of GETs per pull go through this loop, and a
# token that expires mid-run is ordinary. The 429 side of it is already pinned
# above: ``test_a_429_is_waited_out_in_tens_of_seconds_not_one`` proves a read
# still backs off and recovers, and the throttled-out test beside it proves a
# read that never recovers still ends as a throttle. Neither is written again
# here — a second copy of a pin is a pin that drifts. The 5xx is below, and it
# had nothing anywhere: every 5xx in these suites was served to a write, so the
# ``if not replayable`` fork in that branch had one side held and one side
# free.
def test_a_read_refused_with_a_401_is_still_retried_on_a_fresh_token():
    """Nothing a read can do creates a record, so the caution above is all cost.

    A revoked or expired token mid-pull is the case the retry exists for: drop
    it, mint another, ask again. Asserted as counts rather than as the exception
    alone, because a read wrongly treated as unreplayable would raise the same
    class from the first refusal.
    """
    http = FakeHttp({})
    seen = {"n": 0}

    def get(url, params=None, headers=None, **kw):
        seen["n"] += 1
        return FakeResponse({"code": 14, "message": "Invalid oauth token"},
                            status=401)

    http.get = get                                    # type: ignore[assignment]

    with pytest.raises(ZohoAuthError) as caught:
        list(_src(http=http).list_items())
    assert seen["n"] == 2, "a read must be asked again on a fresh token"
    assert http.token_calls == 2, "and the rejected token must not be re-served"
    # A second refusal on a token the endpoint has just issued is the endpoint,
    # not the token — but this body says nothing about scope, so it stays the
    # credentials answer rather than being guessed into a missing permission.
    from app.ingestion.zoho_client import ZohoScopeError
    assert not isinstance(caught.value, ZohoScopeError)


def test_a_read_refused_with_a_403_is_retried_like_a_401_not_left_uncertain():
    """The read half of pairing 403 with 401, which is where that pairing bites.

    Covering 403 was argued for the write: the hop that mints one — an edge, a
    WAF, a proxy — can mint it after the backend has accepted the call. But the
    branch is shared, so this changed a path every sync runs on. A GET answered
    403 used to fall past the auth branch to the generic refusal and raise a
    bare ``ZohoError``; it now drops its token and asks again, and ends as
    ``ZohoAuthError``. Pinned rather than left to be rediscovered, because the
    write case is the one everybody was looking at: the retry is free for a
    read, and ``ZohoAuthError`` subclasses ``ZohoError``, so nothing that
    caught the old class stops catching it.

    The half that must never happen is the write treatment. A read dressed as
    an uncertain write sends its caller to settle-by-read, hunting a ledger for
    a record that cannot exist — the only thing sent was a question.
    """
    http = FakeHttp({})
    seen = {"n": 0}

    def get(url, params=None, headers=None, **kw):
        seen["n"] += 1
        return FakeResponse({"message": "Forbidden"}, status=403)

    http.get = get                                    # type: ignore[assignment]

    with pytest.raises(ZohoAuthError) as caught:
        list(_src(http=http).list_items())
    assert seen["n"] == 2, "a read must be asked again on a fresh token"
    assert http.token_calls == 2, "and the rejected token must not be re-served"
    assert not isinstance(caught.value, ZohoWriteUncertain), (
        "a read has written nothing, so it never becomes an uncertain write")


def test_a_read_whose_connection_died_is_not_dressed_up_as_an_uncertain_write():
    """A dropped GET is a dropped GET.

    Wrapping it the way a write is wrapped would tell the sync a record may have
    been written when the only thing sent was a question, and every one of those
    lands on the settle-by-read path to look for something that cannot exist.
    """
    http = FakeHttp({})

    def die(url, params=None, headers=None, **kw):
        raise ConnectionError("connection reset by peer")

    http.get = die                                    # type: ignore[assignment]

    with pytest.raises(ConnectionError):
        list(_src(http=http).list_items())


def test_a_read_that_hits_a_server_error_backs_off_and_finishes_the_pull(waits):
    """The unpinned side of the 5xx fork, and the expensive one to lose.

    Every 5xx in these suites was served to a write, so "never replay a 5xx"
    could have been simplified to hold for all methods and the whole file would
    still have passed — while in production a single transient 502 mid-pull
    aborted the sync instead of waiting a moment and resuming. A read creates
    nothing, so there is nothing for the caution to protect.
    """
    rows = {"code": 0, "items": [{"item_id": "1", "name": "CNMG", "sku": "C1"}],
            "page_context": {"has_more_page": False}}
    http = FakeHttp({})
    served = {"n": 0}

    def get(url, params=None, headers=None, **kw):
        served["n"] += 1
        if served["n"] == 1:
            return FakeResponse(None, status=503)
        return FakeResponse(rows)

    http.get = get                                    # type: ignore[assignment]

    assert [r["sku"] for r in _src(http=http).list_items()] == ["C1"]
    assert served["n"] == 2, "a read must be asked again after a server error"
    assert waits, "and it must wait before doing so, not hammer the endpoint"


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

    src = _src(http=http)
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
    rows = list(_src(http=http).list_invoices(
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
    rows = list(_src(http=http, since=date(2025, 1, 1)).list_invoices())
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
    row = list(_src(http=FakeHttp({"/invoices/INV1": detail,
                                            "/invoices": listing})).list_invoices())[0]
    assert row["salesperson_id"] == "4455" and row["salesperson_name"] == "R. Nair"


def test_ping_confirms_the_configured_org():
    http = FakeHttp({"/organizations": {
        "code": 0,
        "organizations": [{"organization_id": "60036630487", "name": "4U Precision",
                           "currency_code": "INR"}]}})
    out = _src(http=http).ping()
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
    list(_src(http=http).list_items())
    _url, params = http.gets[0]
    assert params.get("filter_by") == "Status.All"


def test_contacts_are_read_including_inactive_ones():
    """Already Zoho's default, asked for explicitly so a change to that default
    cannot quietly start dropping dormant accounts the way /items does."""
    http = FakeHttp({"contacts": {"code": 0, "contacts": [],
                                  "page_context": {"has_more_page": False}}})
    src = _src(http=http)
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
        list(_src(http=http).list_customer_payments())
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
        list(_src(http=http).list_customer_payments())
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

    Both tables, because the write grants drifted the same way and worse. The
    platform POSTs ``/estimates`` and ``/items``; ``docs/zoho-setup.md`` told
    owners to grant ``estimates.CREATE``, ``estimates.READ`` and
    ``settings.CREATE`` for exactly that — and this list, which is what the
    connections screen actually serves, held none of the three. An owner who
    granted precisely what the screen asked for got a connection that read
    prices and refused every send, and the refusal could not even name the
    missing scope, because ``estimates`` appeared in neither table.
    """
    from app.ingestion.connections import REQUIRED_SCOPES
    from app.ingestion.zoho_client import SCOPE_FOR_PATH, WRITE_SCOPE_FOR_PATH

    declared = {p.name for p in REQUIRED_SCOPES}
    used = set(SCOPE_FOR_PATH.values()) | set(WRITE_SCOPE_FOR_PATH.values())
    assert used == declared, (
        f"used but never requested: {sorted(used - declared)}; "
        f"requested but never used: {sorted(declared - used)}")


def test_every_path_the_adapter_writes_to_has_a_write_scope():
    """The table is keyed by path, and a path it is missing fails *quietly*.

    ``scope_for_path`` returns ``None`` for an unlisted write path, which makes
    ``_scope_refusal`` short-circuit — so Zoho's own code-57 refusal, the one
    answer that is definite about having refused *before* acting, would fall
    into the ambiguous 401 branch and come back as ``ZohoWriteUncertain``. The
    operator is then sent to check the books for a record that certainly is not
    on them, instead of being told which permission to grant.

    The two tables are already pinned against ``REQUIRED_SCOPES`` and the stage
    list is pinned against the method names; neither can see a write path with
    no key, because a path is not a scope and not a stage. Read out of the
    adapter's source rather than listed here, so adding a third write without a
    grant fails this rather than passing it.
    """
    import ast
    import pathlib

    from app.ingestion.zoho_client import WRITE_SCOPE_FOR_PATH

    src = pathlib.Path(__file__).resolve().parents[2] / "app" / "ingestion" / \
        "zoho_books_service.py"
    tree = ast.parse(src.read_text())
    posted = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if getattr(node.func, "attr", None) != "_request":
            continue
        verb, path = node.args[0], node.args[1]
        if not (isinstance(verb, ast.Constant) and isinstance(path, ast.Constant)):
            # A computed verb or path would defeat the read, so it fails here
            # rather than being skipped into a false clean.
            raise AssertionError(
                f"{src.name}:{node.lineno}: _request called with a non-literal "
                "verb or path — this pin cannot see what it writes to")
        if verb.value.upper() != "GET":
            posted.add(path.value.lstrip("/").split("/", 1)[0])

    assert posted, "the read found no write at all, which means it is broken"
    assert posted <= set(WRITE_SCOPE_FOR_PATH), (
        "written to with no entry in WRITE_SCOPE_FOR_PATH: "
        f"{sorted(posted - set(WRITE_SCOPE_FOR_PATH))}")


def test_zohos_declared_writes_match_what_the_adapter_can_actually_create():
    """The both-ways pin the registry connectors get, for the one that writes.

    ``test_connector_writes`` in ``test_erp_connectors`` holds every registered
    connector's declared ``writes`` against its ``create_<stage>`` methods. It
    cannot see Zoho: Zoho is not in ``ingestion/erp``'s registry — its connect
    flow predates it — and its catalogue row is hand-written. So the one
    connector that actually writes was the one the pin was structurally blind
    to, which is how a POST endpoint went years without a declared grant.

    Same assertion, same both directions: a declared write nothing implements
    sends an owner to grant a permission for something that cannot happen, and
    an implemented write nobody declared creates records in a system nobody was
    asked to permit it in.
    """
    from app.ingestion.connections import REQUIRED_SCOPES
    from app.ingestion.erp.base import WRITE_STAGES
    from app.ingestion.zoho_books_service import ZohoBooksService

    declared = {stage for p in REQUIRED_SCOPES for stage in p.writes}
    # Zoho's adapter names the estimate write ``create_sales_quotes`` rather than
    # ``create_sales_quotes``: the stage is the platform's word for the record,
    # the method is Zoho's. The map is stated here rather than guessed from the
    # name, because a rename on either side should fail this test loudly.
    implements = {"sales_quotes": "create_sales_quotes"}
    assert set(implements) <= set(WRITE_STAGES)
    able = {stage for stage, method in implements.items()
            if callable(getattr(ZohoBooksService, method, None))}
    assert declared == able, (
        f"declared but not implemented: {sorted(declared - able)}; "
        f"implemented but not declared: {sorted(able - declared)}")


def test_the_minimum_scope_string_is_exactly_the_required_scopes():
    """The two strings are projections of one list, and must stay so.

    ``Permission.required`` already separates two different days — without a
    required grant no sync runs at all, without an optional one a screen stays
    empty. The full string was the only one offered, so an owner whose policy
    is to grant the least that works had to assemble it by hand from the table,
    which is how a scope gets missed. Asserted against the flag rather than
    against a written-out list, because a second hand-written tuple is the copy
    that stops agreeing the first time a scope changes side.
    """
    from app.ingestion.connections import (MINIMUM_SCOPE_STRING, REQUIRED_SCOPES,
                                           SCOPE_STRING)

    minimum = set(MINIMUM_SCOPE_STRING.split(","))
    assert minimum == {p.name for p in REQUIRED_SCOPES if p.required}
    # A subset, never a different set: the minimum is the full grant with the
    # optional ones dropped, so anything in it must be grantable from the same
    # console string the screen leads with.
    assert minimum < set(SCOPE_STRING.split(","))


def test_the_full_scope_string_still_asks_for_everything():
    """The minimum is offered *beside* the full set, never instead of it. A
    change that quietly narrowed what the screen leads with would cost every
    new connection the optional stages — and they fail quietly, later."""
    from app.ingestion.connections import REQUIRED_SCOPES, SCOPE_STRING

    assert set(SCOPE_STRING.split(",")) == {p.name for p in REQUIRED_SCOPES}


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
    source = _src(http=_probe_http(refused={"customerpayments"}))

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
    source = _src(http=_probe_http(broken={"bills"}))

    by_scope = {r["scope"]: r for r in source.probe_scopes()}
    assert by_scope["ZohoBooks.bills.READ"]["granted"] is None
    assert "non-JSON" in by_scope["ZohoBooks.bills.READ"]["detail"]
    assert by_scope["ZohoBooks.invoices.READ"]["granted"] is True


def test_a_dead_token_stops_the_probe_rather_than_blaming_every_scope():
    """A revoked credential refuses all ten endpoints. Reporting that as ten
    missing permissions would send somebody to the Zoho console to re-grant
    scopes they already have, when the token is the thing that died."""
    source = _src(http=_probe_http(token_refusal=True))

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
    rows = list(_src(http=FakeHttp({"/salesorders": listing})).list_sales_orders())
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
    row = list(_src(http=http).list_sales_orders())[0]
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
    src = _src(http=http)
    assert list(src.list_sales_orders()) == []
    assert list(src.list_vendor_payments()) == []


def test_a_payment_out_keeps_its_amount_and_reference():
    listing = {"code": 0, "vendorpayments": [
        {"payment_id": "P1", "vendor_id": "v1", "date": _today(1),
         "amount": 80000.25, "payment_mode": "banktransfer",
         "reference_number": "NEFT-8891"},
    ], "page_context": {"has_more_page": False}}
    row = list(_src(http=FakeHttp({"/vendorpayments": listing})).list_vendor_payments())[0]
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

    (row,) = list(_src(http=http).list_invoices())
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

    first = _src(http=FakeHttp({**details, "/invoices": listing}))
    held = {r["invoice_id"]: r["last_modified_time"] for r in first.list_invoices()}
    assert len(held) == 2

    # Exactly the predicate `SyncService._skipper` builds from what was stored.
    def already_have(doc_id: str, modified_at: str) -> bool:
        if doc_id not in held:
            return False
        return not modified_at or modified_at == held[doc_id]

    http = FakeHttp({**details, "/invoices": listing})
    second = _src(http=http)
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
    src = _src(http=FakeHttp({"/invoices/A": detail, "/invoices": listing}))
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
    (row,) = list(_src(
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


# ── the projection carries what the diagnostics promised ─────────────────────
#
# `sync._missing_item_context` documents that for an item the master lacks,
# "the line is the only place its name survives" — and the projection here was
# the one place that promise could be broken before anything downstream ran.
# On a real export every UNKNOWN_PRODUCT row had the quantity and the value and
# a blank where the item's name, its SKU and the customer should have been:
# the detail payload carried all three, and the projection dropped them.

def _one_invoice_routes():
    listing = {"code": 0, "invoices": [
        {"invoice_id": "91", "date": _today(1), "status": "sent",
         "last_modified_time": "T1"}],
        "page_context": {"has_more_page": False}}
    detail = {"code": 0, "invoice": {
        "invoice_id": "91", "customer_id": "5", "date": _today(1),
        "customer_name": "Sandvik Mining",
        "invoice_number": "HYD/FY27/INV-751",
        "line_items": [
            {"line_item_id": "l1", "item_id": "i1",
             "name": "REGRIND RECOAT D8.01", "sku": "5183438",
             "description": "Customer Mat no: 63357650",
             "quantity": 4, "rate": 3480, "item_total": 13920},
        ]}}
    return {"invoices/91": detail, "invoices": listing}


def test_the_invoice_projection_keeps_the_names_the_skip_report_reads():
    src = _src(http=FakeHttp(_one_invoice_routes()))
    [inv] = list(src.list_invoices())

    assert inv["customer_name"] == "Sandvik Mining"
    [line] = inv["line_items"]
    assert line["name"] == "REGRIND RECOAT D8.01"
    assert line["sku"] == "5183438"
    assert line["description"] == "Customer Mat no: 63357650"


def test_the_bill_projection_keeps_the_names_the_skip_report_reads():
    listing = {"code": 0, "bills": [
        {"bill_id": "71", "date": _today(1), "status": "open",
         "last_modified_time": "T1"}],
        "page_context": {"has_more_page": False}}
    detail = {"code": 0, "bill": {
        "bill_id": "71", "vendor_id": "9", "vendor_name": "Kennametal India",
        "date": _today(1), "bill_number": "KTI/25-26/0043",
        "line_items": [
            {"line_item_id": "l1", "item_id": "i1",
             "name": "CNMG 120408 KCP25", "sku": "K-1204",
             "description": "insert", "quantity": 40, "rate": 512}]}}
    src = _src(http=FakeHttp({"bills/71": detail, "bills": listing}))
    [bill] = list(src.list_bills())

    [line] = bill["line_items"]
    assert (line["name"], line["sku"], line["description"]) == (
        "CNMG 120408 KCP25", "K-1204", "insert")


# ── the by-id item fetch, and what it refuses to conclude ────────────────────

def test_get_item_matches_the_listing_shape_exactly():
    """A product row must look the same whether the master listing carried it
    or a document forced the fetch — two shapes would mean the racing case
    writes a subtly thinner record."""
    item = {"item_id": "i9", "name": "TECTYL COOL 294 - 20LTR",
            "sku": "294-20LTR", "unit": "LTR", "status": "active",
            "manufacturer": "TECTYL OIL", "stock_on_hand": 40}
    routes = {
        "items/i9": {"code": 0, "item": item},
        "items": {"code": 0, "items": [item],
                  "page_context": {"has_more_page": False}},
    }
    src = _src(http=FakeHttp(routes))

    assert src.get_item("i9") == list(src.list_items())[0]


def test_get_item_answers_none_for_a_dead_id_and_raises_for_a_throttle(waits):
    src = _src(http=FakeHttp({
        "items/gone": {"code": 1002, "message": "Resource does not exist"}}))
    assert src.get_item("gone") is None, "a 404 is an answer, not an error"

    class Throttling:
        def post(self, url, **kw):
            return FakeResponse({"access_token": "t", "expires_in": 3600})

        def get(self, url, **kw):
            return FakeResponse({"message": "rate limited"}, status=429)

    with pytest.raises(ZohoThrottleError):
        _src(http=Throttling()).get_item("i1")


# ── one dead id must not cost the whole stock stage ──────────────────────────

def test_a_dead_id_in_an_itemdetails_batch_is_isolated_not_fatal():
    """Zoho answers a batch naming any nonexistent item with one 404 for the
    entire call, without saying which id it means. This failed per-location
    stock on all three companies identically until the batch was bisected."""
    live = {
        "i1": [{"location_id": "L1", "location_stock_on_hand": 5}],
        "i3": [{"location_id": "L1", "location_stock_on_hand": 7}],
    }

    def itemdetails(params):
        ids = params["item_ids"].split(",")
        if "dead" in ids:
            return {"code": 1002, "message": "Resource does not exist"}
        return {"code": 0, "items": [
            {"item_id": i, "locations": live[i]} for i in ids]}

    src = _src(http=FakeHttp({"itemdetails": itemdetails}))
    rows = list(src.list_item_locations(["i1", "dead", "i3"]))

    assert {r["item_id"] for r in rows} == {"i1", "i3"}, (
        "exactly the dead id is dropped; every live item still answers")
    assert all(r["on_hand"] in (5, 7) for r in rows)


def test_a_throttled_itemdetails_batch_is_not_bisected(waits):
    """A rate limit refuses every call equally; halving the batch would turn
    one clear signal into a storm of doomed requests."""
    class Throttling:
        def __init__(self):
            self.gets = 0

        def post(self, url, **kw):
            return FakeResponse({"access_token": "t", "expires_in": 3600})

        def get(self, url, **kw):
            self.gets += 1
            return FakeResponse({"message": "rate limited"}, status=429)

    http = Throttling()
    src = _src(http=http)
    with pytest.raises(ZohoThrottleError):
        list(src.list_item_locations(["i1", "i2", "i3", "i4"]))
    assert http.gets <= settings.ZOHO_MAX_RETRIES, "no bisection storm"


def test_a_vendor_credit_reaches_the_platform_without_its_line_items():
    """The projection is the refusal, so it is pinned here rather than argued.

    Two independent reasons a vendor credit's lines stay out, and only the
    first is shared with the sell side. A credit line is negative cost against a
    product, and cost already has exactly one owner in ``CostRecord`` built from
    bill lines — a second signed source for the same quantity is how two screens
    start disagreeing about what stock cost. The second reason is specific to
    these books: the lines carry ``bill_item_id: ""``, naming the item without
    naming the bill line they correct, so any per-line attribution built on them
    would be an inference wearing a fact's clothes (``11-procurement.md`` §3).

    The bill linkage *is* exact and does come through, because that is the grain
    the deferred cost adjustment will need when the accounting question behind
    it is answered.
    """
    day = _today(5)
    listing = {"code": 0, "vendor_credits": [
        {"vendor_credit_id": "VC1", "date": day, "status": "closed"}],
        "page_context": {"has_more_page": False}}
    detail = {"code": 0, "vendor_credit": {
        "vendor_credit_id": "VC1", "vendor_credit_number": "01/FY25",
        "vendor_id": "V1", "date": day, "status": "closed",
        "total": 483328, "balance": 0,
        "line_items": [{"line_item_id": "L1", "item_id": "I9", "bill_item_id": "",
                        "quantity": 512, "rate": 800, "item_total": 409600}],
        "bills_credited": [{"vendor_credit_bill_id": "VCB1", "bill_id": "B1",
                            "bill_number": "BN-1", "amount": 38394.46,
                            "date": "2025-04-30"}]}}
    row = list(_src(http=FakeHttp(
        {"/vendorcredits/VC1": detail, "/vendorcredits": listing},
    )).list_vendor_credits())[0]

    assert "line_items" not in row
    assert (row["vendor_credit_number"], row["vendor_id"]) == ("01/FY25", "V1")
    assert (row["total"], row["balance"]) == (483328, 0)

    application = row["bills_credited"][0]
    assert (application["bill_id"], application["amount"]) == ("B1", 38394.46)
    # Zoho's unlabelled ``date`` on this row is not carried either: its live
    # values track the bills rather than the days the document's own system
    # comments record the credit being applied. Passing it through would invite
    # the next reader to store it as an application date.
    assert "date" not in application


def test_a_drafted_or_voided_vendor_credit_is_not_read_as_money_given_back():
    """Same two excluded statuses as every other document, and for the same
    reason: as far as this platform is concerned a void credit never happened."""
    day = _today(5)
    listing = {"code": 0, "vendor_credits": [
        {"vendor_credit_id": "VC1", "date": day, "status": "draft"},
        {"vendor_credit_id": "VC2", "date": day, "status": "void"},
        {"vendor_credit_id": "VC3", "date": day, "status": "open"}],
        "page_context": {"has_more_page": False}}
    detail = {"code": 0, "vendor_credit": {
        "vendor_credit_id": "VC3", "date": day, "status": "open",
        "total": 100, "balance": 100, "bills_credited": []}}
    rows = list(_src(http=FakeHttp(
        {"/vendorcredits/VC3": detail, "/vendorcredits": listing},
    )).list_vendor_credits())

    assert [r["vendor_credit_id"] for r in rows] == ["VC3"]


#: The stamp every case below plants, in the offset dress Zoho really sends.
_SOURCE_STAMP = "2025-06-16T18:51:32+0530"


def _one_page(key: str, row: dict) -> dict:
    return {"code": 0, key: [row], "page_context": {"has_more_page": False}}


def _source_clock_routes(day: str) -> dict[str, dict]:
    """``list_* name -> the routes one pull of it needs``.

    ``created_time`` is planted on exactly the payload that pull's projection
    reads and on no other. Zoho sends it on both the list row and the detail
    for most of these; planting it on one of the two is what makes each
    assertion say *which* payload the projection read — the distinction a
    fixture carrying it everywhere cannot express, and the one this seam had
    wrong.
    """
    stamp = {"created_time": _SOURCE_STAMP}
    return {
        # List-row pulls: no detail call is made at all.
        "list_contacts": {"/contacts": _one_page(
            "contacts", {"contact_id": "C1", "contact_name": "A", **stamp})},
        "list_vendors": {"/contacts": _one_page(
            "contacts", {"contact_id": "V1", "vendor_name": "K", **stamp})},
        "list_items": {"/items": _one_page(
            "items", {"item_id": "I9", "name": "Insert", **stamp})},
        "list_purchase_orders": {"/purchaseorders": _one_page(
            "purchaseorders", {"purchaseorder_id": "PO1", "date": day, **stamp})},
        "list_sales_orders": {"/salesorders": _one_page(
            "salesorders", {"salesorder_id": "SO1", "date": day,
                            "status": "open", **stamp})},
        # The quote pull reads its header off the list row even when the
        # detail call is skipped, so the stamp belongs there and not on the
        # detail — see ``list_quotes``.
        "list_quotes": {
            "/estimates": _one_page("estimates", {
                "estimate_id": "E1", "date": day, "status": "sent",
                "customer_id": "C1", **stamp}),
            "/estimates/E1": {"code": 0, "estimate": {"estimate_id": "E1",
                                                      "line_items": []}}},
        # Detail pulls: the projection reads the fetched document, so a stamp
        # on the list row alone must not satisfy these.
        "list_invoices": {
            "/invoices": _one_page("invoices", {"invoice_id": "INV1",
                                                "date": day, "status": "sent"}),
            "/invoices/INV1": {"code": 0, "invoice": {
                "invoice_id": "INV1", "customer_id": "C1", "date": day,
                "line_items": [], **stamp}}},
        "list_bills": {
            "/bills": _one_page("bills", {"bill_id": "B1", "date": day,
                                          "status": "open"}),
            "/bills/B1": {"code": 0, "bill": {
                "bill_id": "B1", "vendor_id": "V1", "date": day,
                "line_items": [], **stamp}}},
        "list_credit_notes": {
            "/creditnotes": _one_page("creditnotes", {"creditnote_id": "CN1",
                                                      "date": day,
                                                      "status": "open"}),
            "/creditnotes/CN1": {"code": 0, "creditnote": {
                "creditnote_id": "CN1", "customer_id": "C1", "date": day,
                "invoices_credited": [], **stamp}}},
        "list_vendor_credits": {
            "/vendorcredits": _one_page("vendor_credits",
                                        {"vendor_credit_id": "VC1",
                                         "date": day, "status": "open"}),
            "/vendorcredits/VC1": {"code": 0, "vendor_credit": {
                "vendor_credit_id": "VC1", "vendor_id": "V1", "date": day,
                "bills_credited": [], **stamp}}},
        "list_customer_payments": {
            "/customerpayments": _one_page("customerpayments",
                                           {"payment_id": "P1", "date": day}),
            "/customerpayments/P1": {"code": 0, "payment": {
                "payment_id": "P1", "customer_id": "C1", "date": day,
                "amount": 100, "invoices": [], **stamp}}},
        "list_vendor_payments": {
            "/vendorpayments": _one_page("vendorpayments",
                                         {"payment_id": "VP1", "date": day}),
            "/vendorpayments/VP1": {"code": 0, "vendorpayment": {
                "payment_id": "VP1", "vendor_id": "V1", "date": day,
                "amount": 100, "bills": [], **stamp}}},
    }


_CARRIES_SOURCE_CLOCK = frozenset(_source_clock_routes("2025-06-16"))

#: The pulls that carry no ``created_time``, each with the reason it is the
#: source's gap rather than this client's. Written out so a new pull cannot
#: join them silently: the guard below fails at a pull in neither set.
_NO_SOURCE_CLOCK = {
    "list_locations": "Zoho returns no created_time on any /locations row — "
                      "measured against the live book, and the endpoint offers "
                      "no created_time sort either. The gap is the API's.",
    "list_item_locations": "Per-location stock comes out of an item's own "
                           "`locations` array, which carries no creation stamp "
                           "of its own — only the item above it does.",
    "list_users": "A user is not a document and no normalizer builds a "
                  "SourceRef from one, so there is no record here to date.",
}


@pytest.mark.parametrize("pull", sorted(_CARRIES_SOURCE_CLOCK))
def test_the_stamp_that_says_when_the_book_knew_survives_the_projection(pull):
    """``created_time`` is the point-in-time engine's only visibility clock.

    The failure this pins is silent in the worst way. ``normalize`` reads
    ``created_time`` and degrades to ``None`` rather than raising, because a
    document whose creation stamp cannot be placed is still a document; a row
    with ``None`` is then *excluded* from point-in-time evidence and counted as
    excluded, never imputed from its own date. That is the correct bargain when
    Zoho genuinely does not say.

    Zoho does say, on every pull below. Three projections carried the stamp —
    invoices, bills and estimates — and the other nine dropped it, so on real
    data a synced contact, item, sales order, purchase order, credit note,
    vendor credit, vendor or payment landed with ``source_recorded_at = None``
    and could never be evidence. Nothing failed: the sync reported success, the
    rows were written, the numbers were right.

    It survived because the seam was never tested with a real payload shape.
    ``test_sync_persistence`` feeds ``created_time`` straight into a fake source
    and correctly asserts the sync stores it — proving the half of the chain
    that was never broken. So this runs over *every* pull rather than the three
    somebody thought of, and ``test_every_pull_either_carries_the_source_clock
    _or_records_why_not`` is what stops a tenth being added outside it.
    """
    day = _today(5)
    rows = list(getattr(_src(http=FakeHttp(_source_clock_routes(day)[pull])), pull)())

    assert rows, f"{pull} yielded nothing — a fixture fault, not a projection one"
    assert rows[0]["created_time"] == _SOURCE_STAMP


def test_every_pull_either_carries_the_source_clock_or_records_why_not():
    """The vacuity guard, in the shape ``test_normalizer_source_time`` uses.

    A table of twelve pulls proves nothing about the thirteenth. Every
    ``list_*`` on the source must be in one of the two sets above, so a pull
    added without a stamp fails here and its author has to say whether the
    source sends one — which is the question, and the one that went unasked.
    """
    pulls = {name for name in dir(ZohoApiSource) if name.startswith("list_")}
    assert pulls, "found no pulls at all — a screen with nothing in it reports clean"
    assert pulls == _CARRIES_SOURCE_CLOCK | set(_NO_SOURCE_CLOCK), (
        "a pull is in neither set: "
        f"{sorted(pulls ^ (_CARRIES_SOURCE_CLOCK | set(_NO_SOURCE_CLOCK)))}")
    for name, reason in _NO_SOURCE_CLOCK.items():
        assert len(reason.strip()) > 40, (
            f"{name}: say why the source has no stamp, not that it has none")


def test_the_by_id_item_fetch_carries_the_stamp_the_listing_does():
    """The racing path, which is the one nobody exercises.

    ``get_item`` and ``list_items`` share ``_item_payload`` precisely so the
    two cannot drift, and an item created mid-pull reaches the platform only
    through the by-id fetch. A product written on that path with no source
    clock would be indistinguishable from one Zoho never dated.
    """
    detail = {"code": 0, "item": {"item_id": "I9", "name": "Insert",
                                  "created_time": _SOURCE_STAMP}}
    row = _src(http=FakeHttp({"/items/I9": detail})).get_item("I9")

    assert row is not None
    assert row["created_time"] == _SOURCE_STAMP


def test_a_pull_with_no_stamp_on_it_invents_none():
    """Absent stays absent — and is never the document's own ``date``.

    The projection's job is to carry what the source sent, so the interesting
    case is the payload that sent nothing. ``normalize._recorded_at`` turns the
    missing key into ``None`` and the row is then counted out of evidence,
    which is the honest answer; a stamp quietly filled from ``date`` would
    instead let ``is_knowable`` pass a row the quoter could not have seen.
    """
    day = _today(5)
    routes = {"/purchaseorders": _one_page("purchaseorders",
                                           {"purchaseorder_id": "PO1", "date": day})}
    row = list(_src(http=FakeHttp(routes)).list_purchase_orders())[0]

    assert row["created_time"] is None
    assert row["date"] == day


def test_a_bill_the_client_projected_normalizes_to_a_usable_cost_row():
    """End to end across the seam: Zoho payload in, usable evidence out.

    The assertion that matters is the last one. A ``CostRecordIn`` with
    ``recorded_at`` of ``None`` is not a slightly worse cost row — it is one the
    diagnosis engine cannot use at all, so a cost that is present and correct
    still buys nothing.
    """
    from app.ingestion.normalize import normalize_bill

    day = _today(5)
    listing = {"code": 0, "bills": [{"bill_id": "B1", "date": day,
                                     "status": "open"}],
               "page_context": {"has_more_page": False}}
    # The real shape, from bill YGCT9941 on the 4U Precision book: a list rate
    # well above what was paid, and a creation stamp six days after the date.
    detail = {"code": 0, "bill": {
        "bill_id": "B1", "date": day, "created_time": "2025-06-16T18:51:32+0530",
        "vendor_id": "V1",
        "line_items": [{"line_item_id": 1, "item_id": 9, "quantity": 50,
                        "rate": 754, "discount": "83.42%", "item_total": 6251.5}]}}
    row = list(_src(http=FakeHttp({"/bills/B1": detail, "/bills": listing})).list_bills())[0]

    cost = normalize_bill(row)[0]
    assert float(cost.unit_cost) == 125.03          # paid, not the ₹754 list rate
    assert float(cost.rate) == 754.0                # kept for audit
    assert cost.source_ref.recorded_at is not None  # and it is usable evidence
    assert cost.source_ref.recorded_at.date() == date(2025, 6, 16)
