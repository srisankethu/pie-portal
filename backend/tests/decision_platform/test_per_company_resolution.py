"""A resolution belongs to one company, and says which.

The per-company catalogue is only worth its migration if it changes what an
answer *is*. These pin the four claims that make it that, and every one of them
is a claim about refusing rather than about answering:

* a company's line is resolved against that company's catalogue and no other,
  proven through the real engine rather than through a stub that could not tell
  two catalogues apart;
* an organization with several companies gets a refusal naming them, not an
  answer from whichever catalogue happened to be resident;
* a company id belonging to another tenant reads as no such company;
* the shipped corpus reaches a deployment as one company's *seed*, so an
  installation that resolved before the cutover still resolves after it — the
  regression the seed exists to prevent, and the one that would look exactly
  like the engine being down.

``tests/decision_platform/test_company_catalogues.py`` covers the setup surface
(upload, pack, build); this file covers what the setup is *for*.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
import piesupport
from app import catalog, resolution
from app.config import settings
from app.db import get_session
from app.domain import models
from app.pie_service import pie_service
from app.routers import platform_auth, quote
from app.seed import SEED_PASSWORD, ensure_org_and_users

requires_pie = pytest.mark.requires_pie

ORG = "org_pie"
OWNER = "s.menon@pie.example"
SLS = piesupport.company_id("cx_sls")
FOURU = piesupport.company_id("cx_4u")


@pytest.fixture()
def maker():
    engine = dbsupport.fresh_engine()
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


@pytest.fixture()
def two_companies(maker):
    """One organization reading two companies' books, neither with a catalogue."""
    s = maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=SLS, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.add(models.ZohoConnection(connection_id=FOURU, organization_id=ORG,
                                label="4U Precision", zoho_organization_id="z2"))
    s.commit()
    s.close()
    yield maker
    for connection_id in (SLS, FOURU):
        piesupport.forget_company_catalogue(connection_id)


def _quote_client(maker) -> TestClient:
    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(quote.router)

    def _session():
        s = maker()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def _hdr(c, email=OWNER):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── one company's catalogue answers, and only that company's ────────────────

@requires_pie
def test_a_company_answers_only_from_its_own_catalogue(two_companies):
    """The real engine, two companies, one catalogue.

    A stub could not fail this test: it would answer whatever it was told to.
    So the catalogue is real, the code is a real MM# from it, and the second
    company — which has none — must answer UNRESOLVED rather than borrow the
    first's. That borrowing is the failure the whole change exists to prevent:
    a wrong manufacturer's product on a customer's quote, carrying a real
    provenance stamp.
    """
    piesupport.give_company_a_catalogue(SLS)

    mine = pie_service.resolve("2001174", connection_id=SLS)
    assert mine.rel == "EXACT" and mine.supplyCode == "2001174"

    theirs = pie_service.resolve("2001174", connection_id=FOURU)
    assert theirs.rel == "UNRESOLVED"
    assert theirs.supplyCode is None
    assert theirs.candidates == []
    # And it says why, rather than reading as "the catalogue does not have it".
    assert any("no decoded catalogue" in note for note in theirs.notes)


@requires_pie
def test_no_company_named_resolves_against_nothing(two_companies):
    """Not against the first company, not against the last one loaded.

    ``connection_id=None`` is the state a caller reaches by forgetting to say
    which company a line is for. Answering it from any catalogue would be a
    provenanced answer about possibly the wrong company's product.
    """
    piesupport.give_company_a_catalogue(SLS)
    assert pie_service.resolve("2001174").rel == "UNRESOLVED"
    assert pie_service.catalog_available(None) is False
    assert pie_service.catalog_version(None) == ""


def test_several_companies_and_none_named_is_a_refusal_that_lists_them(two_companies):
    """A 422 with the ids, not a guess. ``CompanyNotNamed`` carries the choices
    so a caller is told what to pick rather than left to discover it."""
    s = two_companies()
    with pytest.raises(resolution.CompanyNotNamed) as e:
        resolution.company_for(s, ORG)
    assert {c["connection_id"] for c in e.value.companies} == {SLS, FOURU}
    assert "SLS Engineers" in str(e.value)
    s.close()


def test_one_company_answers_without_being_named(maker):
    """Every existing single-entity deployment keeps working unchanged: a
    picker with one option is a question with one answer."""
    s = maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=SLS, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.commit()
    assert resolution.company_for(s, ORG) == SLS
    s.close()


def test_a_company_id_from_another_tenant_reads_as_no_such_company(two_companies):
    """Named, but not this organization's. It must not resolve, and it must not
    confirm that the id exists somewhere — the refusal is the same one an
    invented id gets, listing only this organization's own companies."""
    s = two_companies()
    s.add(models.Organization(organization_id="org_rival", name="Rival",
                              currency="INR", config={}))
    s.add(models.ZohoConnection(connection_id="cx_theirs",
                                organization_id="org_rival",
                                label="Rival Co", zoho_organization_id="z9"))
    s.commit()

    with pytest.raises(resolution.CompanyNotNamed) as e:
        resolution.company_for(s, ORG, "cx_theirs")
    assert {c["connection_id"] for c in e.value.companies} == {SLS, FOURU}
    assert "cx_theirs" not in str(e.value)
    s.close()


# ── the quote carries it ────────────────────────────────────────────────────

@requires_pie
def test_a_quote_resolves_against_the_company_it_was_raised_from(two_companies):
    """Raised from the company with a catalogue: the line resolves, and the
    quote says which company answered it."""
    piesupport.give_company_a_catalogue(SLS)
    client = _quote_client(two_companies)
    hdr = _hdr(client)

    q = client.post("/api/v1/quotes",
                    json={"customer": "Pitti", "connection_id": SLS},
                    headers=hdr).json()
    assert q["connectionId"] == SLS

    body = client.post(f"/api/v1/quotes/{q['id']}/intake",
                       json={"text": "2001174, 20"}, headers=hdr).json()
    assert body["lines"][0]["rel"] == "EXACT"

    # The other company, same code, no catalogue: an honest UNRESOLVED rather
    # than the first company's product.
    other = client.post("/api/v1/quotes",
                        json={"customer": "Pitti", "connection_id": FOURU},
                        headers=hdr).json()
    body = client.post(f"/api/v1/quotes/{other['id']}/intake",
                       json={"text": "2001174, 20"}, headers=hdr).json()
    assert body["lines"][0]["rel"] == "UNRESOLVED"


def test_a_quote_in_a_multi_company_org_must_say_which_company(two_companies):
    """The refusal reaches the wire, and names the companies to choose from."""
    client = _quote_client(two_companies)
    r = client.post("/api/v1/quotes", json={"customer": "Pitti"},
                    headers=_hdr(client))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert {c["connection_id"] for c in detail["companies"]} == {SLS, FOURU}


# ── the seed: an existing deployment still resolves after the cutover ───────

@requires_pie
def test_the_shipped_corpus_seeds_the_first_company_and_it_resolves(maker, tmp_path,
                                                                    monkeypatch):
    """The regression the seed exists to prevent, end to end.

    Before the cutover every deployment resolved against one catalogue built
    from the shipped corpus. Removing that default without putting it anywhere
    turns every quote line UNRESOLVED on deploy — which looks exactly like the
    engine being down. So the corpus becomes the first company's corpus, the
    catalogue builds from it, and the same code still resolves.
    """
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    s = maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=SLS, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.commit()

    assert catalog.seed_company_catalogues(s) == [
        {"organization_id": ORG, "connection_id": SLS}]
    s.commit()

    corpus = catalog.current_corpus(s, ORG, SLS)
    assert corpus is not None
    assert corpus.size_bytes == settings.PIE_CORPUS.stat().st_size
    # The pack it was decoded through, chosen for the catalogue rather than
    # left unset — a corpus with no pack builds nothing.
    row = catalog.catalogue_row(s, ORG, SLS, catalog.DEFAULT_CATALOGUE)
    assert catalog.pack_for(row) is not None

    built = catalog.ensure_company_catalogues(s)
    s.commit()
    assert built == [{"organization_id": ORG, "connection_id": SLS,
                      "catalogue_key": catalog.DEFAULT_CATALOGUE}]
    pie_service.reload(SLS)
    assert pie_service.resolve("2001174", connection_id=SLS).rel == "EXACT"
    piesupport.forget_company_catalogue(SLS)
    s.close()


def test_the_seed_never_overwrites_a_company_that_has_its_own_export(maker):
    """A company that has uploaded its own item master must never be handed a
    different manufacturer's. The seed skips an organization that has any
    corpus at all, which is deliberately broader than "this company has one":
    an organization that has started uploading is one whose companies a
    deployment default has no business guessing at."""
    s = maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=SLS, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.add(models.CompanyCorpus(
        corpus_id="cor_mine", organization_id=ORG, connection_id=SLS,
        filename="mine.csv", content_type="text/csv", size_bytes=4,
        sha256="x", content=b"a,b\n", uploaded_by="owner"))
    s.commit()

    assert catalog.seed_company_catalogues(s) == []
    assert catalog.current_corpus(s, ORG, SLS).corpus_id == "cor_mine"
    s.close()


def test_the_seed_is_idempotent_and_skips_an_organization_with_no_company(maker):
    """It runs on every boot, so running it twice must change nothing — and an
    organization with nothing connected is left with no catalogue, which
    reports NOT BUILT rather than being seeded a company it does not have."""
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    assert catalog.seed_company_catalogues(s) == []

    s.add(models.ZohoConnection(connection_id=SLS, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.commit()
    first = catalog.seed_company_catalogues(s)
    s.commit()
    assert first == [{"organization_id": ORG, "connection_id": SLS}]
    assert catalog.seed_company_catalogues(s) == []
    assert s.query(models.CompanyCorpus).count() == 1
    s.close()
