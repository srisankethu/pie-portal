"""Finding an item by hand, in the catalogue and in the books.

The gap this covers, stated because the tests below only make sense against it:
a line the engine could not answer showed its ranked candidates and nothing
else, so a line with *no* candidates could not be pointed at an item at all.
``store.select_supply`` has always taken a code that is in no candidate list;
there was no way to name one. These are the two searches that let somebody name
one, and the properties that must hold while they do.

The sharpest of those is not "search finds things". It is that an empty
catalogue result and an unsearchable catalogue are different answers — the
person reaching for this box is usually there *because* the engine did not
answer, and a deployment with no engine that reports "no matches" has told them
something false about their product (CLAUDE.md §1, "absence of evidence is not
a pass").
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
import piesupport
from app.db import get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.identity import service as identity_service
from app.pie_service import pie_service
from app.repositories import ReadModelRepository
from app.routers import platform_auth, quote
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.zoho import MockZoho

#: Every test here goes through the ``client`` fixture, and that fixture calls
#: ``piesupport.give_company_a_catalogue`` — so every test in this file needs a
#: decoded catalogue, and the requirement belongs to the module rather than to
#: whoever remembers to decorate the next one.
#:
#: It was eight decorators on fifteen tests. The seven without one did not skip
#: on a checkout with no engine; they raised out of ``_require_engine`` during
#: fixture setup, which is that guard working exactly as designed — it refuses
#: rather than skipping, precisely so an unmarked test cannot pass quietly here
#: *and* go unselected by ``pytest -m requires_pie`` in the `pie-contract` job.
#: A per-test marker on a shared fixture's requirement is a rule every future
#: test has to be told; this one is structural.
pytestmark = pytest.mark.requires_pie

OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"

COMPANY = piesupport.company_id("cx_item_search")
OTHER_COMPANY = piesupport.company_id("cx_item_search_other")

#: A designation the shipped corpus really carries, so a hit here is the engine
#: reading a catalogue rather than a fixture agreeing with itself.
#: ``corpora/kmt_zcnc_2026-07_nomenclature.csv`` holds several ``CNMG 120408``
#: rows in the WIDIA grades (TN2000, TN4000, THM-F, TTS).
IN_THE_CORPUS = "CNMG 120408"


def _item(session, *, connection_id: str, external_id: str, sku: str,
          name: str) -> None:
    """One synced master item, written the way a sync writes it.

    Through ``identity.ingest_item`` rather than by constructing rows: an
    ``ItemConnectorRecord`` needs an identity and a hand-built pair could hold
    a shape ``ingestion.sync`` never produces, which is a fixture that proves
    the search works on data that does not exist.
    """
    product = models.Product(organization_id="org_pie", connector="zoho",
                             connection_id=connection_id,
                             external_id=external_id, name=name, active=True,
                             manufacturer="WIDIA")
    session.add(product)
    session.flush()
    identity_service.ingest_item(
        session, "org_pie", connector="zoho", connection_id=connection_id,
        external_id=external_id, name=name, sku=sku, description=name,
        source_ref={"item_id": external_id, "sku": sku},
        local_id=product.product_id)


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    for cid, label in ((COMPANY, "SLS Engineers"), (OTHER_COMPANY, "4U Precision")):
        s.add(models.ZohoConnection(connection_id=cid, organization_id="org_pie",
                                    label=label, zoho_organization_id=cid))
    s.flush()
    # The item the whole question is about: in the books, sold, and not in any
    # manufacturer catalogue this company has built.
    _item(s, connection_id=COMPANY, external_id="z-1",
          sku="CNMG120408-UC-D2 YC0014",
          name="CNMG120408-UC-D2 YC0014 TURNING INSERT")
    _item(s, connection_id=COMPANY, external_id="z-2",
          sku="SC-DRILL-12", name="SC DRILL 12MM 3XD COOLANT")
    # The same part number in another company's book. Nothing this quote does
    # may reach it.
    _item(s, connection_id=OTHER_COMPANY, external_id="z-9",
          sku="CNMG120408-UC-D2 YC0014", name="CNMG120408-UC-D2 YC0014 OTHER CO")
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
    api.dependency_overrides[quote.zoho_for_quote] = lambda: MockZoho()
    tc = TestClient(api)
    tc.Maker = Maker
    return tc


def _hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _quote(c: TestClient, hdr: dict) -> str:
    r = c.post("/api/v1/quotes", json={"customer": "Pitti Engineering",
                                       "connection_id": COMPANY}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── the catalogue half ───────────────────────────────────────────────────────

def test_the_catalogue_can_be_searched_by_designation(client):
    """A real corpus row, found by the words a person types."""
    found = pie_service.search_catalogue(IN_THE_CORPUS, COMPANY, limit=10)
    assert found.available is True
    assert found.records, "the shipped corpus carries CNMG 120408 rows"
    assert found.searched > 0
    assert any("CNMG" in (r["desc"] or "").upper() for r in found.records)


def test_a_catalogue_hit_carries_no_relationship_to_the_request(client):
    """Search returns records, never a ``rel``.

    A ``rel`` is this organization's equivalence policy applied to a comparison
    between a request and a record (§1). A search compares nothing, so a
    relationship word here would be a policy verdict on a comparison that never
    ran — and one a caller could then feed back in as an input.
    """
    found = pie_service.search_catalogue(IN_THE_CORPUS, COMPANY, limit=5)
    for record in found.records:
        assert "rel" not in record
        assert "score" not in record


def test_an_unsearchable_catalogue_is_not_an_empty_one(client):
    """The invariant this whole feature could most easily have broken.

    A company with no catalogue returns ``available=False`` and says why. If it
    returned ``[]`` instead, a person searching for a product this deployment
    cannot see would read "no such item" — evidence about the product — from a
    fact about the deployment.
    """
    found = pie_service.search_catalogue(IN_THE_CORPUS, OTHER_COMPANY, limit=10)
    assert found.available is False
    assert found.records == []
    assert found.reason and "nothing to search" in found.reason.lower()


def test_a_quote_with_no_company_searches_nothing(client):
    """``connection_id=None`` is not a company and never resolves to one."""
    found = pie_service.search_catalogue(IN_THE_CORPUS, None, limit=10)
    assert found.available is False
    assert found.records == []


def test_an_empty_query_is_not_a_failed_search(client):
    """Nothing typed yet is ``available`` with no records — the box is empty,
    the catalogue is fine, and a reason printed here would read as a fault."""
    found = pie_service.search_catalogue("   ", COMPANY, limit=10)
    assert found.available is True
    assert found.reason is None
    assert found.records == []


# ── the books half ───────────────────────────────────────────────────────────

def test_the_books_are_searched_by_sku_and_by_name(client):
    s = client.Maker()
    repo = ReadModelRepository(s, "org_pie")
    by_sku = repo.search_items("CNMG120408", connection_id=COMPANY)
    assert [r["code"] for r in by_sku] == ["CNMG120408-UC-D2 YC0014 TURNING INSERT"]
    by_words = repo.search_items("drill coolant", connection_id=COMPANY)
    assert [r["code"] for r in by_words] == ["SC DRILL 12MM 3XD COOLANT"]
    s.close()


def test_every_word_must_appear(client):
    """AND across words, not OR. A search that widens as you type is a search
    that gets less useful the more you tell it."""
    s = client.Maker()
    repo = ReadModelRepository(s, "org_pie")
    assert repo.search_items("drill coolant", connection_id=COMPANY)
    assert repo.search_items("drill aerospace", connection_id=COMPANY) == []
    s.close()


def test_the_books_search_is_scoped_to_one_company(client):
    """The same part number is in both books; a quote sees only its own.

    Not a nicety — the two rows are different items with different stock in
    different ledgers, and selecting the wrong one puts a quote against a
    company that cannot fulfil it.
    """
    s = client.Maker()
    repo = ReadModelRepository(s, "org_pie")
    mine = repo.search_items("CNMG120408", connection_id=COMPANY)
    theirs = repo.search_items("CNMG120408", connection_id=OTHER_COMPANY)
    assert [r["name"] for r in mine] == ["CNMG120408-UC-D2 YC0014 TURNING INSERT"]
    assert [r["name"] for r in theirs] == ["CNMG120408-UC-D2 YC0014 OTHER CO"]
    s.close()


def test_an_exact_sku_leads_the_list(client):
    """Punctuation is not identity, so the code typed without its separators
    still ranks first — through ``normalize_sku``, the one matcher."""
    s = client.Maker()
    repo = ReadModelRepository(s, "org_pie")
    hits = repo.search_items("CNMG120408 UC D2 YC0014", connection_id=COMPANY)
    assert hits and hits[0]["code"] == "CNMG120408-UC-D2 YC0014 TURNING INSERT"
    s.close()


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_endpoint_answers_both_halves(client):
    hdr = _hdr(client, OWNER)
    qid = _quote(client, hdr)
    r = client.get(f"/api/v1/quotes/{qid}/item-search",
                   params={"q": "CNMG120408"}, headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["catalogue"]["available"] is True
    assert [x["code"] for x in body["books"]["records"]] == [
        "CNMG120408-UC-D2 YC0014 TURNING INSERT"]


def test_the_search_carries_no_money_for_either_role(client):
    """§1's second invariant, checked on the bytes rather than on the screen.

    Search answers *which item*. Price, cost and margin are the books' answer
    about a line that has one, and none of them has a field here — so a
    salesperson and a manager receive identical bytes, and there is nothing in
    this response for a network tab to read.
    """
    banned = {"cost", "margin", "price", "listPrice", "list_price", "rate",
              "marginFloor", "unit_cost"}
    bodies = []
    for email in (OWNER, SALES):
        hdr = _hdr(client, email)
        qid = _quote(client, hdr)
        r = client.get(f"/api/v1/quotes/{qid}/item-search",
                       params={"q": "CNMG120408"}, headers=hdr)
        assert r.status_code == 200, r.text
        bodies.append(r.json())

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, f"{key} reached the item search"
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for body in bodies:
        walk(body)
    assert bodies[0] == bodies[1], "the two roles must receive the same bytes"


def test_another_tenant_cannot_search_this_quote(client):
    """The search is scoped through the quote, so it inherits the quote's org
    check rather than restating one."""
    hdr = _hdr(client, OWNER)
    r = client.get("/api/v1/quotes/nope/item-search", params={"q": "x"},
                   headers=hdr)
    assert r.status_code == 404


# ── what selecting a searched code may and may not do ────────────────────────

def test_a_searched_code_can_be_put_on_a_line(client):
    """The point of the whole change: a line with no candidates gets an item."""
    hdr = _hdr(client, OWNER)
    qid = _quote(client, hdr)
    r = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "XZ-NOT-A-REAL-CODE-778 x 10"}, headers=hdr)
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["supplyCode"] is None, "the premise: nothing resolved"

    r = client.post(f"/api/v1/quotes/{qid}/lines/{line['id']}/supply",
                    json={"code": "CNMG120408-UC-D2 YC0014", "manual": True},
                    headers=hdr)
    assert r.status_code == 200, r.text
    after = next(x for x in r.json()["lines"] if x["id"] == line["id"])
    assert after["supplyCode"] == "CNMG120408-UC-D2 YC0014"
    assert after["sel"] == "MANUAL"


def test_a_hand_picked_code_never_becomes_a_confirmed_identity(client):
    """A substitution on one quote stays one.

    This is the gate CLAUDE.md §1 is most emphatic about: a confirmed mapping
    is *asserted* identity, and the engine will later derive a requirement from
    an asserted record and rank equivalents off it. A person picking a code out
    of a search box has answered no question the engine asked — there is no
    proposal on the line — so nothing may be written.
    """
    hdr = _hdr(client, OWNER)
    qid = _quote(client, hdr)
    r = client.post(f"/api/v1/quotes/{qid}/intake",
                    json={"text": "XZ-NOT-A-REAL-CODE-778 x 10"}, headers=hdr)
    line = r.json()["lines"][0]
    r = client.post(f"/api/v1/quotes/{qid}/lines/{line['id']}/supply",
                    json={"code": "CNMG120408-UC-D2 YC0014", "manual": True},
                    headers=hdr)
    assert r.status_code == 200, r.text
    # No "it will resolve on its own from now on" — that sentence is the
    # confirmation's, and there was no confirmation.
    assert "resolve on its own" not in (r.json().get("note") or "")
    s = client.Maker()
    assert s.query(models.ConfirmedCodeMapping).count() == 0
    s.close()


def test_an_identifier_is_found_under_its_own_id_and_listed_once(client):
    """Typing a catalogue number finds that row, once.

    ``lookup_material`` normalises what it is handed, so the row can come back
    under an id spelled differently from the text that found it. Publishing the
    typed text as the code would then both mislabel the row and fail to exclude
    it from the description pass, which lists it again.
    """
    found = pie_service.search_catalogue("2576285", COMPANY, limit=10)
    assert found.available is True
    codes = [r["code"] for r in found.records]
    assert "2576285" in codes
    assert len(codes) == len(set(codes)), f"a record was listed twice: {codes}"
    # The identifier pass measured nothing, so it reports no similarity.
    assert next(r for r in found.records if r["code"] == "2576285")["similarity"] is None
