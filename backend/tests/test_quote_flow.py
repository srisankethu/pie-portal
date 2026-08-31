"""End-to-end API flow: intake, resolution grid, role-gated economics, supply
selection, pricing, and estimate creation.

Both fixtures sign in through ``/api/v1/auth/login`` — the product's only login.
The Quote Builder used to have a second one at ``/api/auth/login`` with two
fixed demo accounts and no password check, so these tests could pass while the
identity on screen belonged to nobody: the browser signed in twice, and the
second sign-in decided whether cost and margin were sent.
"""
from __future__ import annotations

from contextlib import contextmanager

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
import piesupport
from app.db import get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.ingestion.zoho_books_service import ZohoBooksService
from app.ingestion.zoho_client import ZohoCredentials
from app.routers import platform_auth, quote
from app.routers.quote import QuoteBooks, books_for_quote
from app.seed import SEED_PASSWORD, ensure_org_and_users, provision_organization
from app.zoho import MockZoho, ZohoWriteRefused, ZohoWriteUnknown

from decision_platform.test_zoho_books_service import FakeBooks

OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"


#: The one company this organization reads books from, and therefore the
#: catalogue its quotes resolve against. One rather than three on purpose: with
#: a single company nobody has to name it, which is the path every existing
#: single-entity deployment takes. The several-company refusal has its own test.
COMPANY = piesupport.company_id("cx_quote_flow")


@pytest.fixture()
def client():
    """The quote endpoints on their own database.

    Same shape as `decision_platform/test_quote_intelligence_api.py`: an
    in-memory schema, the seeded org and users, and `get_session` overridden.
    These tests used to run against whatever `data/platform.db` happened to hold
    because the login they used consulted no database at all.

    The company and its catalogue are part of the fixture because they are part
    of resolving anything at all: a quote is raised from a company and reads
    that company's decoded item master. Without one every line here would come
    back UNRESOLVED — correctly, and uselessly for these tests.
    """
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=COMPANY, organization_id="org_pie",
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.commit()
    s.close()
    piesupport.give_company_a_catalogue(COMPANY)

    api = FastAPI()
    api.include_router(platform_auth.router)
    api.include_router(quote.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    api.dependency_overrides[get_session] = _override
    tc = TestClient(api)
    tc.Maker = Maker
    return tc


def _hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def sales_hdr(client):
    return _hdr(client, SALES)


@pytest.fixture()
def mgmt_hdr(client):
    return _hdr(client, OWNER)


def test_the_quote_builder_has_no_login_of_its_own():
    """The second door is gone from the real app, not merely unused by the client.

    While it existed, a browser could hold a Quote Builder session belonging to
    a demo account nobody had authenticated, and every economics decision on
    these endpoints was taken against *that* role.
    """
    from app.main import app as real_app

    assert not [r for r in real_app.routes
                if getattr(r, "path", "").startswith("/api/auth/")]


def test_auth_required(client):
    assert client.get("/api/v1/quotes/nope").status_code == 401


def test_a_forged_token_is_refused(client):
    assert client.get(
        "/api/v1/quotes/nope",
        headers={"Authorization": "Bearer not.a.real.token"}).status_code == 401


def test_a_quote_is_invisible_to_another_tenant(client):
    """The quote store is one process-wide dict with enumerable ids and no tenant
    column of its own, so the org check at each read seam is the whole of quote
    authorization. Without it a signed-in user from any tenant could read — or
    mutate — another tenant's quote by guessing its id, cost and margin included
    (``to_dict`` gates economics on the *reader's* role, so a cross-tenant owner
    would receive them). A foreign id must be indistinguishable from an unknown
    one: 404, not 403.
    """
    # A quote owned by org_pie's salesperson.
    qid = client.post("/api/v1/quotes", json={"customer": "Pitti"},
                      headers=_hdr(client, SALES)).json()["id"]
    assert client.get(f"/api/v1/quotes/{qid}", headers=_hdr(client, SALES)).status_code == 200

    # A second, unrelated tenant with its own owner.
    s = client.Maker()
    provision_organization(s, name="Rival Distributors", owner_email="o@rival.example",
                           owner_name="Rival Owner", org_id="org_rival",
                           password=SEED_PASSWORD, must_change_password=False)
    s.commit()
    s.close()
    rival = _hdr(client, "o@rival.example")

    # Every quote seam refuses the cross-tenant id, and none confirm it exists.
    assert client.get(f"/api/v1/quotes/{qid}", headers=rival).status_code == 404
    assert client.post(f"/api/v1/quotes/{qid}/intake", json={"text": "2001174, 20"},
                       headers=rival).status_code == 404
    assert client.post(f"/api/v1/quotes/{qid}/discount",
                       json={"lineIds": [], "percent": 5},
                       headers=rival).status_code == 404


@pytest.mark.requires_pie
def test_intake_builds_resolution_grid(client, mgmt_hdr):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti Engineering"}, headers=mgmt_hdr).json()
    qid = q["id"]
    rfq = "2001174, 20\nCNMG 120408 KCP25  50\nXZ-CUSTOM-778-NOTREAL, 5"
    q = client.post(f"/api/v1/quotes/{qid}/intake", json={"text": rfq}, headers=mgmt_hdr).json()
    assert q["summary"]["total"] == 3
    rels = {ln["reqCode"]: ln["rel"] for ln in q["lines"]}
    assert rels["2001174"] == "EXACT"
    assert any(ln["rel"] == "UNRESOLVED" for ln in q["lines"])
    # filter counts present
    assert set(q["filterCounts"]) >= {"ALL", "NEEDS", "UNRES", "SUBST"}


def test_economics_are_role_gated(client, sales_hdr, mgmt_hdr):
    qs = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=sales_hdr).json()
    qid = qs["id"]
    client.post(f"/api/v1/quotes/{qid}/intake", json={"text": "2001174, 10"}, headers=sales_hdr)

    sales_view = client.get(f"/api/v1/quotes/{qid}", headers=sales_hdr).json()
    mgmt_view = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()

    sline = sales_view["lines"][0]
    mline = mgmt_view["lines"][0]
    # sales client never receives economics (cost/margin/below_floor)
    assert "economics" not in sline
    assert "marginFloor" not in sales_view
    # management sees full economics
    assert "economics" in mline
    assert "cost" in mline["economics"]
    assert "marginFloor" in mgmt_view


@pytest.mark.requires_pie
def test_a_salesperson_cannot_ask_whether_a_line_is_below_the_floor(
        client, sales_hdr, mgmt_hdr):
    """The below-floor count is a margin fact, so it is absent for a salesperson.

    Not zero — absent. A zero answers the same question in the negative, and the
    question is worth money: re-price a line, read the count back, and twenty
    probes bisect the floor price. The floor is cost x (1 + margin floor), so a
    recovered floor is a recovered cost.
    """
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "2001174, 10"}, headers=mgmt_hdr).json()
    lid = q["lines"][0]["id"]
    # A rupee a piece is below any floor this catalogue can produce.
    mgmt_view = client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price",
                            json={"price": 1}, headers=mgmt_hdr).json()
    assert mgmt_view["lines"][0]["economics"]["below_floor"] is True
    assert mgmt_view["filterCounts"]["MFLOOR"] == 1, "manager sees the count"
    assert mgmt_view["marginFloor"]["count"] == 1

    sales_view = client.get(f"/api/v1/quotes/{qid}", headers=sales_hdr).json()
    assert "MFLOOR" not in sales_view["filterCounts"], (
        "the below-floor count is a margin oracle and must not be served")
    assert "marginFloor" not in sales_view
    # And nothing else in the payload answers the same question.
    assert "economics" not in sales_view["lines"][0]


@pytest.mark.requires_pie
def test_supply_selection_and_pricing(client, mgmt_hdr):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "CNMG 120408 KCP25 50"}, headers=mgmt_hdr).json()
    line = q["lines"][0]
    lid = line["id"]
    opts = client.get(f"/api/v1/quotes/{qid}/lines/{lid}/options", headers=mgmt_hdr).json()
    assert opts["candidates"], "requirement should have candidates"
    alt = opts["candidates"][-1]["code"]
    q = client.post(f"/api/v1/quotes/{qid}/lines/{lid}/supply",
                    json={"code": alt}, headers=mgmt_hdr).json()
    assert q["lines"][0]["supplyCode"] == alt
    # set a manual price
    q = client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price",
                    json={"price": 999}, headers=mgmt_hdr).json()
    assert q["lines"][0]["quoted"] == 999
    assert q["lines"][0]["lineTotal"] == 999 * 50


def test_estimate_blocked_by_technical_lines(client, mgmt_hdr):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    client.post(f"/api/v1/quotes/{qid}/intake",
                json={"text": "XZ-CUSTOM-778-NOTREAL, 5"}, headers=mgmt_hdr)
    est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False
    assert est["blockers"]


def _clean_quote(client, hdr) -> str:
    """A quote with one in-books, priced, technically-clean line."""
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=hdr).json()
    qid = q["id"]
    client.post(f"/api/v1/quotes/{qid}/intake", json={"text": "2001174, 10"}, headers=hdr)
    qd = client.get(f"/api/v1/quotes/{qid}", headers=hdr).json()
    ln = qd["lines"][0]
    if ln["inBooks"] is False:
        client.post(f"/api/v1/quotes/{qid}/lines/{ln['id']}/create-item", headers=hdr)
    if ln["quoted"] is None:
        client.post(f"/api/v1/quotes/{qid}/lines/{ln['id']}/price",
                    json={"price": 500}, headers=hdr)
    return qid


# `test_sending_a_quote_requires_a_platform_identity_when_approvals_are_on` was
# here on main. It asserted a 403 when the browser sent the Quote Builder's login
# without the platform header — a case that cannot arise now that there is one
# identity, because the organization comes from the signed-in principal rather
# than from a header a caller can omit. The rule it protected is not weaker: it
# moved from "send the second header or be refused" to "there is no request
# without an organization on it".
@pytest.mark.requires_pie
def test_estimate_created_when_clean_and_nothing_needs_approval(client, mgmt_hdr):
    """The gate opens when no line on this quote asked for sign-off.

    It reads the organization off the signed-in principal, so there is no longer
    a header a caller can omit to be judged against a different org — or, as
    before, against the packaged default org with no approvals in it.
    """
    qid = _clean_quote(client, mgmt_hdr)
    est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is True, est
    assert est["documentNumber"]


# ── the price on a line, and whose it is ─────────────────────────────────────
@pytest.mark.requires_pie
def test_a_resolved_line_opens_at_list_and_says_so(client, mgmt_hdr):
    """The default is kept; what changes is that it is no longer disguised.

    A resolved, in-books line is auto-quoted at the catalogue rate so a
    forty-line tender is not forty numbers to type. Nothing said so: the screen
    claimed in three places that it never pre-filled the field, and a quote
    nobody had priced showed a Quotation total in the same weight as a finished
    one.
    """
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "2001174, 10"}, headers=mgmt_hdr).json()
    ln = q["lines"][0]
    assert ln["quoted"] is not None
    assert ln["priceSource"] == "LIST"
    assert q["summary"]["atListPrice"] == 1
    assert q["summary"]["unpriced"] == 0

    q = client.post(f"/api/v1/quotes/{qid}/lines/{ln['id']}/price",
                    json={"price": 777}, headers=mgmt_hdr).json()
    assert q["lines"][0]["priceSource"] == "USER"
    assert q["summary"]["atListPrice"] == 0


@pytest.mark.requires_pie
def test_price_source_reaches_a_salesperson_too(client, sales_hdr):
    """It says where a rate came from, not what it cost — so it is not gated.

    A salesperson is the person most likely to send an untouched quote, which
    makes them the reader this mark exists for.

    Marked, unlike its neighbour `test_economics_are_role_gated`, because it
    asserts on a line that *resolved*: without the engine there is no supply
    product, so nothing auto-prices and `priceSource` is correctly null.
    """
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=sales_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "2001174, 10"}, headers=sales_hdr).json()
    assert "economics" not in q["lines"][0]
    assert q["lines"][0]["priceSource"] in ("LIST", "USER")
    assert "atListPrice" in q["summary"]


@pytest.mark.requires_pie
def test_a_discount_comes_off_the_rate_on_the_line_not_off_list(client, mgmt_hdr):
    """The control said "apply 10% discount" and could raise a price by 59%.

    It recomputed from ``listPrice``, so a line negotiated down to ₹300 came back
    at 90% of *list* — up, not down — and a second press changed nothing, because
    the answer never depended on where the line actually was.
    """
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "2001174, 10"}, headers=mgmt_hdr).json()
    lid = q["lines"][0]["id"]
    list_price = q["lines"][0]["quoted"]
    assert list_price > 400, "the fixture item needs headroom for this to mean anything"

    client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price", json={"price": 300},
                headers=mgmt_hdr)
    q = client.post(f"/api/v1/quotes/{qid}/discount",
                    json={"lineIds": [lid], "percent": 10}, headers=mgmt_hdr).json()
    assert q["applied"] == 1
    assert q["lines"][0]["quoted"] == 270, "10% off the negotiated ₹300"

    # And it compounds, which is what pressing it twice plainly means.
    q = client.post(f"/api/v1/quotes/{qid}/discount",
                    json={"lineIds": [lid], "percent": 10}, headers=mgmt_hdr).json()
    assert q["lines"][0]["quoted"] == 243


# ── sending ─────────────────────────────────────────────────────────────────
@pytest.mark.requires_pie
def test_sending_the_same_quote_twice_returns_the_one_estimate(client, mgmt_hdr):
    """Three presses used to put three estimates in Zoho.

    Nothing on the quote remembered that it had been sent, and the button was
    unchanged afterwards — the only acknowledgement was a three-second snackbar.
    """
    qid = _clean_quote(client, mgmt_hdr)
    first = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert first["ok"] is True, first
    again = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert again["ok"] is True
    assert again["documentNumber"] == first["documentNumber"]
    assert "already covers" in again["message"]

    # The quote itself says what it has sent, so the screen does not have to
    # have been watching when it happened.
    q = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()
    assert q["estimate"]["number"] == first["documentNumber"]
    assert q["estimate"]["current"] is True
    # And it says where, which "Sent · SQ-1001" alone does not — two connected
    # systems can both answer to that number.
    assert q["estimate"]["systemLabel"] == "Zoho Books"
    assert q["estimate"]["documentTerm"] == "estimate"


@pytest.mark.requires_pie
def test_amending_a_sent_quote_produces_a_new_estimate(client, mgmt_hdr):
    """Re-sending an amended quote is ordinary work, so this is not a lock."""
    qid = _clean_quote(client, mgmt_hdr)
    first = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    lid = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()["lines"][0]["id"]

    q = client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price", json={"price": 8000},
                    headers=mgmt_hdr).json()
    assert q["estimate"]["current"] is False, "the estimate no longer describes this quote"

    second = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert second["ok"] is True
    assert second["documentNumber"] != first["documentNumber"]


@pytest.mark.requires_pie
def test_a_resolved_line_with_no_rate_is_refused_rather_than_sent_blank(client, mgmt_hdr):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "2001174, 10"}, headers=mgmt_hdr).json()
    lid = q["lines"][0]["id"]
    client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price", json={"price": None},
                headers=mgmt_hdr)
    est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False
    assert lid in est["blockers"]
    assert "no rate" in est["message"]


@pytest.mark.requires_pie
def test_a_blocked_estimate_names_the_lines(client, mgmt_hdr):
    """"3 critical line(s) must be resolved first" left the reader to find which."""
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    client.post(f"/api/v1/quotes/{qid}/intake",
                json={"text": "XZ-CUSTOM-778-NOTREAL, 5"}, headers=mgmt_hdr)
    est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False
    assert "XZ-CUSTOM-778-NOTREAL" in est["message"]


@pytest.mark.requires_pie
def test_a_below_floor_line_cannot_be_sent_without_an_approval(client, mgmt_hdr):
    """The gap this closes, end to end.

    The approval gate judged the latest *snapshot* per line, and the only thing
    writing snapshots was a salesperson choosing to open a drawer and record an
    override. On the ordinary path nothing was written, so the gate found nothing
    to judge and answered "sendable" — for a line the screen was, at that moment,
    showing a below-the-floor warning about. The send path records first now, and
    passes its own below-floor lines to the same gate.
    """
    qid = _clean_quote(client, mgmt_hdr)
    lid = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()["lines"][0]["id"]
    q = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()
    cost = q["lines"][0]["economics"]["cost"]
    # At cost the margin is 0%, well under the floor.
    q = client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price", json={"price": cost},
                    headers=mgmt_hdr).json()
    assert q["lines"][0]["economics"]["below_floor"] is True
    assert q["marginFloor"]["count"] == 1

    refused = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr)
    assert refused.status_code == 403, refused.text
    assert "need approval" in refused.json()["detail"]

    # Pricing it back above the floor unblocks it, without anyone answering an
    # approval — the gate is about the price now on the line, not about history.
    client.post(f"/api/v1/quotes/{qid}/lines/{lid}/price", json={"price": cost * 4},
                headers=mgmt_hdr)
    est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is True, est


@pytest.mark.requires_pie
def test_a_quote_id_is_not_reused_by_the_next_process(client, mgmt_hdr):
    """Sending writes rows keyed on the quote id, so the id has to be unique.

    Quotes live in memory and their ids restarted at ``q1`` every boot. That was
    harmless while nothing outside the store remembered them; it stopped being
    harmless when the send path began filing snapshots and outcomes under the id,
    because a fresh ``q1`` would be judged on the previous ``q1``'s snapshots.
    """
    a = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    assert a["id"] != "q1"
    assert "-" in a["id"], "the id carries a per-process part"


# ── the live adapter, through the API ────────────────────────────────────────
# These drive the real ``ZohoBooksService`` (over a fake transport) and the real
# refusal paths through the endpoints, because what they protect are properties
# of the *response*, not of the adapter: that a salesperson never receives cost,
# and that a failed write is never reported as an estimate.

class _StubBooks:
    """A ``ZohoService`` whose writes fail on demand, priced like the mock."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self._mock = MockZoho()

    def get_item(self, code):
        return self._mock.get_item(code)

    def create_item(self, code, name, list_price=None):
        if self.error:
            raise self.error
        return self._mock.create_item(code, name, list_price)

    def create_sales_quotes(self, customer, lines, *, customer_ref=None, reference=None):
        if self.error:
            raise self.error
        return self._mock.create_sales_quotes(customer, lines)

    @property
    def available(self) -> bool:
        return True


@contextmanager
def _books(client, service):
    """Bind every Zoho-touching quote endpoint to ``service`` for one test."""
    client.app.dependency_overrides[books_for_quote] = lambda: QuoteBooks(
        zoho=service, contact_id="3300000009")
    try:
        yield
    finally:
        client.app.dependency_overrides.pop(books_for_quote, None)


def _live_service():
    """The real live adapter over a fake transport, holding a real cost."""
    item = {"item_id": "4400000001", "name": "Insert 2001174", "sku": "2001174",
            "rate": "1250.00", "purchase_rate": "980.50",
            "available_stock": "42", "status": "active"}
    http = FakeBooks(routes={
        "/items": {"code": 0, "items": [item], "page_context": {"has_more_page": False}},
        "/organizations": {"code": 0, "organizations": [
            {"organization_id": "60036630487", "name": "SLS Engineers"}]},
    })
    creds = ZohoCredentials(organization_id="60036630487", client_id="cid",
                            client_secret="sec", refresh_token="rtok")
    return ZohoBooksService(credentials=creds, http=http)


@pytest.mark.requires_pie
def test_a_live_items_cost_never_reaches_a_sales_response(client, sales_hdr, mgmt_hdr):
    """A live lookup returns ``purchase_rate``. It is management data, and the
    server omitting it is the whole mechanism — there is nothing in the payload
    for a network tab to reveal."""
    with _books(client, _live_service()):
        q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=sales_hdr).json()
        qid = q["id"]
        client.post(f"/api/v1/quotes/{qid}/intake", json={"text": "2001174, 10"},
                    headers=sales_hdr)
        sales_raw = client.get(f"/api/v1/quotes/{qid}", headers=sales_hdr).text
        mgmt_view = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()

    assert "980.5" not in sales_raw and "economics" not in sales_raw
    assert mgmt_view["lines"][0]["economics"]["cost"] == 980.5, \
        "management must still get the real landed cost"
    # The list price is operational and does reach sales; only cost does not.
    assert '"quoted":1250.0' in sales_raw.replace(" ", "")


@pytest.mark.requires_pie
def test_a_refused_estimate_does_not_report_one(client, mgmt_hdr):
    with _books(client, _StubBooks(ZohoWriteRefused("no ledger for this customer"))):
        qid = _clean_quote(client, mgmt_hdr)
        est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False
    assert est["documentNumber"] is None
    assert "no ledger for this customer" in est["message"]


@pytest.mark.requires_pie
def test_a_refusal_points_at_the_lines_that_caused_it(client, mgmt_hdr):
    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        qd = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()
    code = qd["lines"][0]["supplyCode"]

    with _books(client, _StubBooks(ZohoWriteRefused("not in these books", codes=[code]))):
        est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False and est["blockers"] == [qd["lines"][0]["id"]]


@pytest.mark.requires_pie
def test_a_restart_does_not_make_a_sent_quote_look_unsent(client, mgmt_hdr):
    """What the in-memory copy got wrong, on the screen rather than in the send.

    The quote's own ``estimate`` block was three attributes on a process-wide
    object. After a restart it read as None, so a quote that had been sent
    showed no chip at all and an unchanged primary button — the only evidence
    anything had happened was gone, and the obvious move was to press send
    again.
    """
    from app import store as store_mod

    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        sent = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert sent["ok"] is True

    # The process forgets everything it held about this quote's send.
    quote = store_mod.store.get(qid)
    assert not hasattr(quote, "estimateNumber"), (
        "the in-memory copy is back, and it is the one that lies after a restart")

    q = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()
    assert q["estimate"] is not None, "a sent quote read as unsent"
    assert q["estimate"]["number"] == sent["documentNumber"]
    assert q["estimate"]["current"] is True


@pytest.mark.requires_pie
def test_an_edit_after_sending_marks_the_document_out_of_date(client, mgmt_hdr):
    """``current`` is what makes the block worth having. Without it the chip
    implies the customer holds what is on screen, and the priced content has
    moved since."""
    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr)
        qd = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()
        assert qd["estimate"]["current"] is True

        line = qd["lines"][0]
        client.post(f"/api/v1/quotes/{qid}/lines/{line['id']}/price",
                    headers=mgmt_hdr, json={"price": 9999.0})
        after = client.get(f"/api/v1/quotes/{qid}", headers=mgmt_hdr).json()

    assert after["estimate"]["current"] is False, (
        "the quote was re-priced and still claimed the sent document describes it")


@pytest.mark.requires_pie
def test_the_send_names_the_system_it_wrote_into(client, mgmt_hdr):
    """The response used to say "Zoho estimate" whatever it had written to.

    Business Central has no record type called an estimate, so telling its user
    one was created sends them looking for something their system does not
    have. The words come from the server because the screen cannot know which
    system a given quote's books are.
    """
    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        sent = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()

    assert sent["ok"] is True
    assert sent["system"] == "zoho"
    assert sent["systemLabel"] == "Zoho Books"
    assert sent["documentTerm"] == "estimate"
    assert sent["alreadyExisted"] is False
    assert f"Zoho Books estimate {sent['documentNumber']} created" in sent["message"]

    # And the second press is a different claim, not the same one reworded.
    with _books(client, _StubBooks()):
        again = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert again["alreadyExisted"] is True
    assert "already covers this quote" in again["message"]


@pytest.mark.requires_pie
def test_the_send_response_carries_no_economics(client, mgmt_hdr):
    """§1, on the one payload a salesperson sees after pressing send.

    Naming the system is the change this test guards; the risk it carries is
    that a naming field is an easy place to slip a count or a flag in beside.
    MFLOOR and NEGATIVE_MARGIN both entered through exactly that kind of small
    extra field, so the whole payload is asserted rather than the new keys.
    """
    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        sent = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()

    assert set(sent) == {"ok", "documentNumber", "lineCount", "blockers",
                         "message", "system", "systemLabel", "documentTerm",
                         "alreadyExisted"}, (
        "a field was added to the send response — if it answers a margin "
        "question, in any form, it does not belong here")
    body = json.dumps(sent).lower()
    for word in ("cost", "margin", "floor", "profit"):
        assert word not in body


@pytest.mark.requires_pie
def test_a_sent_quote_is_still_sent_after_the_process_forgets_it(client, mgmt_hdr):
    """The whole point of persisting the document, and what nothing did before.

    Everything about a sent estimate lived on an in-memory dataclass in a
    process-wide dict. A restart erased it, so the duplicate check said "never
    sent" and pressed the source again — relying on the reference round trip to
    undo what it had just asked for, on every send, for ever. Clearing the
    in-memory quote store is what a restart does to this state.
    """
    from app import store as store_mod

    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        first = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
        assert first["ok"] is True and first["documentNumber"]

        # The process forgets. The ledger does not.
        quote = store_mod.store.get(qid)
        quote.estimateNumber = None
        quote.estimateFingerprint = None

        again = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()

    assert again["ok"] is True
    assert again["documentNumber"] == first["documentNumber"], (
        "a restart made the platform send the same quote a second time")
    assert "already covers this quote" in again["message"]


@pytest.mark.requires_pie
def test_the_document_a_send_produced_names_its_system_and_its_policy(
        client, mgmt_hdr):
    """A row nobody can read back is not a record.

    ``external_system`` rather than an assumed "zoho", because the write seam
    exists so this will not always say Zoho — and ``thresholds_version``,
    because an append-only row that a human's send is recorded in keeps the
    policy that was in force, which is what makes a past send explainable after
    the margin policy is edited.
    """
    with _books(client, _StubBooks()):
        qid = _clean_quote(client, mgmt_hdr)
        sent = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert sent["ok"] is True

    with client.Maker() as s:
        rows = s.query(models.QuoteDocument).filter_by(quote_id=qid).all()
    assert len(rows) == 1, "one send, one row"
    (doc,) = rows
    assert doc.external_document_number == sent["documentNumber"]
    assert doc.external_system, "the row does not say which system holds it"
    assert doc.thresholds_version, "a signed send with no policy stamp"
    assert doc.fingerprint, "without the content stamp the duplicate check is blind"


@pytest.mark.requires_pie
def test_an_unknown_write_outcome_is_neither_success_nor_silence(client, mgmt_hdr):
    """The state that must never be rounded off. The response says the outcome
    is unresolved and carries the reference to look up in Zoho."""
    with _books(client, _StubBooks(ZohoWriteUnknown(
            "sent, reply lost — look for reference QB-1-abcd", reference="QB-1-abcd"))):
        qid = _clean_quote(client, mgmt_hdr)
        est = client.post(f"/api/v1/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False and est["documentNumber"] is None
    assert "QB-1-abcd" in est["message"]


@pytest.mark.requires_pie
def test_a_failed_item_creation_leaves_the_line_in_create_failed(client, mgmt_hdr):
    """It used to leave the line on "CREATING…" and 500 the request: the mock
    could not fail, so the CREATE FAILED state had no path into it."""
    with _books(client, _StubBooks(ZohoWriteRefused("Zoho would not create item 2001174"))):
        q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
        qid = q["id"]
        q = client.post(f"/api/v1/quotes/{qid}/intake", json={"text": "2001174, 10"},
                        headers=mgmt_hdr).json()
        lid = q["lines"][0]["id"]
        r = client.post(f"/api/v1/quotes/{qid}/lines/{lid}/create-item", headers=mgmt_hdr)

    assert r.status_code == 200, "one failed line must not take the whole quote down"
    body = r.json()
    assert body["lines"][0]["status"]["label"] == "CREATE FAILED"
    assert "2001174" in body["createItemError"]


def test_every_quote_carries_a_reference_a_person_could_search_for(client, mgmt_hdr):
    a = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    b = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    assert a["reference"] and a["reference"] != b["reference"], \
        "the reference is this quote's idempotency key; two quotes may never share one"


# ── the enquiry corpus, filled from work that already happens ───────────────
#
# `inbound_lines` was designed in `a7inbound` and held zero rows, so every text
# technique in `14-machine-learning.md` §5.17–§5.21 waited on an empty table.
# This is the one place real customer text already arrives: a salesperson pastes
# the enquiry to have it resolved, and it was read into lines and then dropped.
#
# What this door supplies is the *benchmark* corpus — real wording with the
# reading it produced. It does not supply the coverage denominator and cannot:
# a line captured here reached the Quote Builder, so somebody chose to work it,
# and the enquiries nobody worked never come through. `source_ref` carries the
# quote id so that subset stays identifiable when an adapter starts writing the
# rest.

#: Deliberately horrible, and the same shape the capture suites use. If any
#: layer between the paste and the column tidies this, the corpus measures the
#: tidier rather than the resolver.
MESSY_RFQ = "  pls quote\t2001174, 20\r\n  ⌀12 mm end mill  x3  "


def _lines(client):
    from app.domain import models as m
    s = client.Maker()
    try:
        return list(s.query(m.InboundLine).all())
    finally:
        s.close()


def test_a_stated_channel_captures_the_enquiry_word_for_word(client, sales_hdr):
    qid = client.post("/api/v1/quotes", json={"customer": "Acme"},
                      headers=sales_hdr).json()["id"]
    r = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": MESSY_RFQ, "channel": "WHATSAPP"},
                    headers=sales_hdr)

    assert r.status_code == 200, r.text
    assert r.json()["intake"]["captured"] is True
    line = _lines(client)[0]
    assert line.raw_text == MESSY_RFQ, (
        "the intake path tidied the customer's words on the way to the corpus")
    assert line.channel == "WHATSAPP"
    # The handle that marks this row as part of the worked subset, and the join
    # back to what was made of the text.
    assert line.source_ref == f"quote:{qid}"


def test_no_channel_captures_nothing_rather_than_guessing_one(client, sales_hdr):
    """`InboundChannel` has no UNKNOWN member because "an enquiry that arrived
    some other way has no honest value to store". A default here would file
    every row under a route nobody chose, in the one index the corpus is
    grouped by."""
    qid = client.post("/api/v1/quotes", json={"customer": "Acme"},
                      headers=sales_hdr).json()["id"]
    r = client.post(f"/api/v1/quotes/{qid}/intake", json={"text": MESSY_RFQ},
                    headers=sales_hdr)

    assert r.status_code == 200, r.text
    assert r.json()["intake"]["captured"] is False
    assert _lines(client) == []


def test_a_bad_channel_costs_the_corpus_a_row_and_the_quote_nothing(
        client, sales_hdr):
    """The quote is the work; the corpus is a by-product. An adapter sending a
    channel this platform does not know must not cost somebody their RFQ."""
    qid = client.post("/api/v1/quotes", json={"customer": "Acme"},
                      headers=sales_hdr).json()["id"]
    r = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": MESSY_RFQ, "channel": "CARRIER_PIGEON"},
                    headers=sales_hdr)

    assert r.status_code == 200, r.text
    assert r.json()["intake"]["captured"] is False
    assert r.json()["summary"]["total"] > 0, "the lines still resolved"
    assert _lines(client) == []


def test_the_same_enquiry_pasted_twice_is_two_rows(client, sales_hdr):
    """No deduplication, here or anywhere: two identical asks are two enquiries,
    and merging them would under-report exactly the repeat demand the table
    measures. A re-paste onto the same quote is the closest thing to a false
    positive this door has, and it is still two asks."""
    qid = client.post("/api/v1/quotes", json={"customer": "Acme"},
                      headers=sales_hdr).json()["id"]
    for _ in range(2):
        client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": MESSY_RFQ, "channel": "EMAIL"},
                    headers=sales_hdr)

    assert len(_lines(client)) == 2
