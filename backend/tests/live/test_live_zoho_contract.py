"""Live Zoho Books contract suite. Opt-in, excluded from the default run.

Why this exists
---------------
Every Zoho test in the default suite drives the client through an injected fake
transport. That is the right design — it exercises the real request/response
handling with no network, and it is fast and deterministic. What it cannot do is
notice when *Zoho* changes.

The cost of that blind spot is written in the client's own source. From
``list_invoices``:

    These were missing when the receivables state was added, so every invoice
    header arrived with no balance, no due date and no status: the fold saw
    nothing owed by anybody, and the collection and credit-exposure cards could
    never fire against a real Zoho pull. **The tests passed throughout because
    the fixtures supply them.**

A fixture asserts what we believe the API returns. This file asserts what it
actually returns, and it is the only thing in the repository that can tell the
difference.

What it asserts, and what it deliberately does not
--------------------------------------------------
Shapes, never values. The books are live: totals move, customers are added, an
item is deactivated. A test that pinned a number would fail every week for the
wrong reason and be deleted within a month. So each test asks "is the field the
client maps still present, and still the kind of thing we treat it as" — the
questions whose answer changing is a real defect.

It is also strictly bounded. A full pull is thousands of calls; this reads one
page and a handful of documents inside a short window, so a run costs seconds of
someone's rate limit rather than their whole quota.

Run with:      pytest -m live
Requires:      ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN /
               ZOHO_ORGANIZATION_ID, and ZOHO_ACCOUNTS_BASE + ZOHO_API_BASE
               pointing at the right data centre.
Skips cleanly: when credentials are absent, and per-endpoint when this
               connection was not granted that scope.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from itertools import islice
from numbers import Real
from typing import Any, Iterable, Iterator

import pytest

from app.config import settings
from app.ingestion.zoho_client import (
    ZohoApiSource,
    ZohoCredentials,
    ZohoScopeError,
    ZohoThrottleError,
)

pytestmark = pytest.mark.live

# How much of the live book to touch. Small on purpose: enough rows to prove a
# shape, few enough that the suite is cheap to run against a real account.
DOCUMENTS = 3
MASTER_ROWS = 5
WINDOW_DAYS = 120


_ENV_CREDENTIAL_KEYS = ("ZOHO_ORGANIZATION_ID", "ZOHO_CLIENT_ID",
                        "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")


def _credentials_present() -> bool:
    return all(os.environ.get(k) for k in _ENV_CREDENTIAL_KEYS)


def _env_credentials() -> ZohoCredentials:
    """Real credentials for the live run, read straight from the environment.

    This is test scaffolding, not a runtime path. The platform itself has no
    environment credential fallback — every connection is stored per
    organization — so a live contract run against a real book supplies its own
    grant here, in the test, rather than through any application code."""
    return ZohoCredentials(
        organization_id=os.environ.get("ZOHO_ORGANIZATION_ID", ""),
        client_id=os.environ.get("ZOHO_CLIENT_ID", ""),
        client_secret=os.environ.get("ZOHO_CLIENT_SECRET", ""),
        refresh_token=os.environ.get("ZOHO_REFRESH_TOKEN", ""),
        accounts_base=os.environ.get("ZOHO_ACCOUNTS_BASE", "https://accounts.zoho.in"),
        api_base=os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.in/books/v3"),
    )


class RecordingTransport:
    """A real httpx client that records the method and host of every call.

    The client documents itself as "read-only by construction: every call is a
    GET". That claim is worth checking against the wire rather than against the
    source, because it is the one property that makes pointing this suite at a
    production book defensible at all.
    """

    def __init__(self) -> None:
        import httpx

        self._inner = httpx.Client(timeout=settings.ZOHO_TIMEOUT_SECONDS)
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str, **kw: Any):
        self.calls.append(("GET", url))
        return self._inner.get(url, **kw)

    def post(self, url: str, **kw: Any):
        self.calls.append(("POST", url))
        return self._inner.post(url, **kw)

    def close(self) -> None:
        self._inner.close()

    @property
    def non_get_api_calls(self) -> list[tuple[str, str]]:
        """Every write-shaped call that went to the API host.

        The token exchange is a POST by protocol and goes to the *accounts*
        host, so it is excluded — it is authentication, not a write to the book.

        The base comes from the resolved credentials rather than
        the ``ZOHO_API_BASE`` env default, because a connection may carry its own (a
        different data centre). Reading the setting instead would compare
        against a host nothing was sent to, and the assertion would pass by
        matching nothing — a test that cannot fail.
        """
        api = _env_credentials().api_base.rstrip("/")
        return [(m, u) for m, u in self.calls if m != "GET" and u.startswith(api)]


@pytest.fixture(scope="module")
def transport() -> Iterator[RecordingTransport]:
    if not _credentials_present():
        pytest.skip("Zoho credentials not set — live Zoho contract suite skipped")
    t = RecordingTransport()
    yield t
    t.close()


@pytest.fixture(scope="module")
def source(transport: RecordingTransport) -> ZohoApiSource:
    """A client bounded to a short window, pointed at the real API."""
    return ZohoApiSource(
        http=transport,
        since=date.today() - timedelta(days=WINDOW_DAYS),
        credentials=_env_credentials(),
    )


@pytest.fixture(autouse=True)
def _bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """One small page, so a contract check never becomes a full sync."""
    monkeypatch.setattr(settings, "ZOHO_MAX_PAGES", 1)
    monkeypatch.setattr(settings, "ZOHO_PAGE_SIZE", 25)


def _take(rows: Iterable[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Read at most ``n`` rows, turning a throttle into a skip.

    Being rate limited says nothing about the contract, and a suite that goes
    red because someone else was syncing teaches people to ignore it.
    """
    try:
        return list(islice(rows, n))
    except ZohoScopeError as exc:
        pytest.skip(f"connection not granted the scope for this endpoint: {exc}")
    except ZohoThrottleError as exc:
        pytest.skip(f"Zoho rate limited this run: {exc}")


def _assert_id(row: dict[str, Any], field: str) -> None:
    """An id the platform will key on: present, a string, and not empty."""
    assert field in row, f"{field} missing from the mapped row"
    assert isinstance(row[field], str), f"{field} should be mapped to a string"
    assert row[field] not in ("", "None"), f"{field} came through empty"


def _assert_numeric_or_absent(row: dict[str, Any], field: str) -> None:
    """A money/quantity field: absent or null is allowed, nonsense is not.

    Zoho sends these as numbers on some editions and numeric strings on others,
    and normalisation copes with both — what must never arrive is a value that
    is neither.
    """
    value = row.get(field)
    if value is None:
        return
    if isinstance(value, Real):
        return
    assert isinstance(value, str), f"{field} was {type(value).__name__}, not a number or string"
    float(value)  # raises, and the failure names the field


# ── credentials, data centre, organization ──────────────────────────────────
def test_ping_authenticates_and_finds_the_configured_organization(source):
    """The check that fails first when a refresh token is rotated or the data
    centre is wrong — both of which present as a bare 500 much later otherwise."""
    result = source.ping()

    assert result["authenticated"] is True
    assert result["organization_found"] is True, (
        f"ZOHO_ORGANIZATION_ID {os.environ.get('ZOHO_ORGANIZATION_ID', '')} is not among the "
        f"organizations this token can see: {result['visible_organizations']}. "
        "The credentials are valid; the org id or the data centre is not."
    )
    # The sync stamps rows with the book's currency and zone, so their absence
    # is not cosmetic.
    assert result["organization_name"]
    assert result["currency"], "Zoho stopped reporting currency_code"
    assert result["time_zone"], "Zoho stopped reporting time_zone"


def test_the_client_only_ever_reads(source, transport):
    """No call to the API host may be anything but a GET."""
    source.ping()
    assert transport.non_get_api_calls == [], (
        f"a non-GET reached the Zoho API host: {transport.non_get_api_calls}"
    )


# ── masters ─────────────────────────────────────────────────────────────────
def test_contacts_carry_the_identity_keys(source):
    rows = _take(source.list_contacts(), MASTER_ROWS)
    if not rows:
        pytest.skip("no customers in this book")

    for row in rows:
        _assert_id(row, "contact_id")
        assert "contact_name" in row
        # gst_no is the identity layer's strongest customer key and is genuinely
        # absent outside the India edition — its *key* must survive, so the
        # matcher can read "no evidence" rather than raise.
        assert "gst_no" in row
        assert row["status"], "status came through empty; the fold reads it"


def test_items_endpoint_still_honours_status_all(source):
    """The hand-verified behaviour, made a test.

    ``list_items`` passes ``filter_by=Status.All`` because ``/items`` defaults
    to ``Status.Active``, and a distributor deactivates an item the moment the
    line is discontinued while its historical bills live on. The docstring says
    this was "verified against a live book" — by a person, once. If Zoho stops
    honouring the parameter, every historical line for a retired item silently
    becomes UNKNOWN_PRODUCT, and because bills are where cost comes from, the
    visible symptom is missing *margin* rather than a missing item.
    """
    rows = _take(source.list_items(), 200)
    if not rows:
        pytest.skip("no items in this book")

    for row in rows:
        _assert_id(row, "item_id")
        assert "sku" in row, "sku key must survive even when blank"
        _assert_numeric_or_absent(row, "stock_on_hand")
        _assert_numeric_or_absent(row, "purchase_rate")
        # reorder_level is passed through raw precisely so normalisation can
        # tell blank from zero; the key disappearing would erase that.
        assert "reorder_level" in row

    statuses = {str(r.get("status", "")).lower() for r in rows}
    if not statuses & {"inactive"}:
        pytest.skip(
            "no inactive item in the first page — cannot prove Status.All from "
            "this sample. (Not a failure: the book may have none.)"
        )
    assert statuses & {"inactive"}


# ── documents ───────────────────────────────────────────────────────────────
def test_invoices_carry_the_receivables_fields(source):
    """The regression this whole file is justified by.

    ``due_date``, ``status``, ``total`` and ``balance`` were absent from the
    mapping once, and no test noticed because every fixture supplied them. If
    Zoho drops one, the fold sees nothing owed by anybody and the collection and
    credit-exposure cards go quiet — which looks like good news.
    """
    rows = _take(source.list_invoices(), DOCUMENTS)
    if not rows:
        pytest.skip(f"no invoices in the last {WINDOW_DAYS} days")

    for row in rows:
        _assert_id(row, "invoice_id")
        _assert_id(row, "customer_id")
        assert row["date"], "invoice date is what every period is folded on"
        assert row["last_modified_time"], "the resume cursor is keyed on this"
        assert row["status"], "status decides whether this counts as trade"
        for field in ("invoice_number", "due_date", "total", "balance"):
            assert field in row, f"{field} missing — receivables state cannot fold"
        _assert_numeric_or_absent(row, "total")
        _assert_numeric_or_absent(row, "balance")


def test_invoice_lines_carry_quantity_rate_and_discount(source):
    rows = _take(source.list_invoices(), DOCUMENTS)
    lines = [li for r in rows for li in r["line_items"]]
    if not lines:
        pytest.skip("no invoice line items in the window")

    for li in lines:
        _assert_id(li, "item_id")
        _assert_numeric_or_absent(li, "quantity")
        _assert_numeric_or_absent(li, "rate")
        _assert_numeric_or_absent(li, "item_total")
        # Without these the net selling price silently becomes the pre-discount
        # list rate — a margin overstated on every discounted line.
        assert "discount" in li and "discount_amount" in li


def test_bills_carry_what_cost_is_computed_from(source):
    """Bills are where cost comes from, so a shape change here moves margin."""
    rows = _take(source.list_bills(), DOCUMENTS)
    if not rows:
        pytest.skip(f"no bills in the last {WINDOW_DAYS} days")

    for row in rows:
        _assert_id(row, "bill_id")
        assert row["date"]
        assert row["last_modified_time"]
        for li in row.get("line_items") or []:
            _assert_id(li, "item_id")
            _assert_numeric_or_absent(li, "quantity")
            _assert_numeric_or_absent(li, "rate")


def test_pagination_context_is_still_how_zoho_says_there_is_more(source):
    """``_paginate`` stops on ``page_context.has_more_page``.

    If that key were renamed the client would read one page and report a
    complete pull — the failure mode where the book looks smaller than it is,
    and reconciliation concludes the difference was deleted.
    """
    body = source._get("contacts", page=1, per_page=1, contact_type="customer")
    assert "page_context" in body, "Zoho stopped sending page_context"
    assert "has_more_page" in body["page_context"], (
        "page_context.has_more_page is gone — _paginate would stop after one page"
    )
