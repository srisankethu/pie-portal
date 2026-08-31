"""One catalogue per connected company: its own export, its own pack.

The change these pin exists because an organization can read three Zoho books,
and those three companies have three different item masters. The tests that
matter most are the ones about *not* sharing: a company must never answer from
another company's catalogue, and an absent catalogue must never read as zero
coverage.

Nothing resolves against these yet — the deployment-wide catalogue still
answers every quote line, and the cutover is a separate change. So there is no
test here asserting a quote used a company's catalogue; asserting it would be
asserting something this branch deliberately does not do.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import catalog
from app.config import settings
from app.db import get_session
from app.domain import models
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

requires_pie = pytest.mark.requires_pie


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
    # Two connected companies on one organization — the shape the whole change
    # is about, and the one a single-company fixture would never exercise.
    s.add(models.ZohoConnection(connection_id="cx_sls", organization_id="org_pie",
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.add(models.ZohoConnection(connection_id="cx_4u", organization_id="org_pie",
                                label="4U Precision", zoho_organization_id="z2"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(data_status.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _corpus() -> bytes:
    return settings.PIE_CORPUS.read_bytes()


def _upload(c, hdr, connection_id, content, filename="items.csv"):
    return c.post(
        f"/api/v1/data/catalog/companies/{connection_id}/corpus?filename={filename}",
        content=content, headers={**hdr, "Content-Type": "text/csv"})


def _company(body, connection_id):
    return next(x for x in body["companies"] if x["connection_id"] == connection_id)


# ── the distinction the change exists for ────────────────────────────────────

@requires_pie
def test_a_company_never_answers_from_another_companys_catalogue(client, tmp_path, monkeypatch):
    """Build one company's catalogue; the other must still have none.

    The test the whole feature is for. A shared catalogue would make the second
    company report the first's record count, which is not merely wrong — it is
    wrong while looking provenanced.
    """
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    client.put("/api/v1/data/catalog/companies/cx_sls/pack",
               json={"pack_id": "zcnc"}, headers=hdr)
    assert _upload(client, hdr, "cx_sls", _corpus()).status_code == 200
    built = client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr).json()
    assert built["records"] == 6717

    body = client.get("/api/v1/data/catalog/companies", headers=hdr).json()
    other = _company(body, "cx_4u")
    assert other["exists"] is False
    # None, never 0 — a company with no catalogue says nothing about coverage.
    assert other["records"] is None
    assert other["corpus"] is None
    assert other["report"] is None


@requires_pie
def test_a_lost_disk_costs_a_rebuild_rather_than_the_data(client, tmp_path, monkeypatch):
    """The property that makes the corpus a row instead of a file.

    Delete the decoded output, as a redeploy does. The catalogue rebuilds from
    the stored export and lands on the same ruleset checksum — so the file is a
    cache, which is exactly what an uploaded corpus on ephemeral disk would not
    have allowed.
    """
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    client.put("/api/v1/data/catalog/companies/cx_sls/pack",
               json={"pack_id": "zcnc"}, headers=hdr)
    _upload(client, hdr, "cx_sls", _corpus())
    first = client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr).json()

    catalog.company_catalog_path("cx_sls").unlink()
    body = client.get("/api/v1/data/catalog/companies", headers=hdr).json()
    gone = _company(body, "cx_sls")
    # Named as its own state: a row without its file is a rebuild waiting to
    # happen, which is a different fix from having uploaded nothing.
    assert gone["built_but_missing_on_disk"] is True
    assert gone["exists"] is False

    again = client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr).json()
    assert again["records"] == first["records"]
    assert again["stamp"]["ruleset_checksum"] == first["stamp"]["ruleset_checksum"]


@requires_pie
def test_a_newer_export_marks_the_catalogue_out_of_date_not_wrong(client, tmp_path, monkeypatch):
    """Superseding the corpus leaves a real catalogue that is merely stale.

    The distinction is the point: the built catalogue still has a true stamp
    describing what it was built from. Reporting it as absent would overstate
    the problem and hide a catalogue that still resolves.
    """
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    client.put("/api/v1/data/catalog/companies/cx_sls/pack",
               json={"pack_id": "zcnc"}, headers=hdr)
    _upload(client, hdr, "cx_sls", _corpus())
    client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr)

    raw = _corpus() + b"MM-EXTRA,SOME NEW TOOL,KC725M\n"
    after = _upload(client, hdr, "cx_sls", raw, filename="newer.csv").json()
    assert after["stale"] is True
    assert after["exists"] is True          # still a real catalogue
    assert after["corpus"]["filename"] == "newer.csv"

    rebuilt = client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr).json()
    assert rebuilt["stale"] is False


# ── the upload, and what it refuses ──────────────────────────────────────────

def test_an_export_missing_the_packs_column_is_refused_by_name(client):
    """Rejected at upload with the column named, not accepted and then failing
    to build — a corpus that is stored and unusable is worse than one refused."""
    hdr = _hdr(client, "s.menon@pie.example")
    client.put("/api/v1/data/catalog/companies/cx_sls/pack",
               json={"pack_id": "zcnc"}, headers=hdr)
    r = _upload(client, hdr, "cx_sls", b"Wrong,Headers\n1,2\n")
    assert r.status_code == 422
    assert "MM#" in r.json()["detail"]


def test_an_export_that_is_not_utf8_is_refused(client):
    hdr = _hdr(client, "s.menon@pie.example")
    r = _upload(client, hdr, "cx_sls", b"\xff\xfe\x00\x00not text")
    assert r.status_code == 422
    assert "UTF-8" in r.json()["detail"]


def test_an_oversize_export_is_refused_by_its_declared_length(client, monkeypatch):
    """The header is checked before the body is stored, so an oversize upload
    is answered rather than absorbed."""
    monkeypatch.setattr(catalog, "MAX_CORPUS_BYTES", 1024)
    hdr = _hdr(client, "s.menon@pie.example")
    r = _upload(client, hdr, "cx_sls", b"MM#,Material Description\n" + b"x" * 4096)
    assert r.status_code == 413


def test_uploading_supersedes_rather_than_overwrites(client):
    """Append-only, so a catalogue built from the earlier bytes keeps a real
    referent for the corpus its stamp names."""
    hdr = _hdr(client, "s.menon@pie.example")
    header = b"MM#,Material Description,Grade\n"
    _upload(client, hdr, "cx_sls", header + b"A,FIRST,KC725M\n")
    _upload(client, hdr, "cx_sls", header + b"B,SECOND,KC725M\n")

    s = client.Maker()
    rows = s.query(models.CompanyCorpus).filter_by(connection_id="cx_sls").all()
    assert len(rows) == 2
    assert sum(1 for r in rows if r.superseded_at is None) == 1
    # The bytes are kept, not a path — the durability the design turns on.
    assert all(r.content and r.sha256 == hashlib.sha256(r.content).hexdigest()
               for r in rows)
    s.close()


# ── the pack: chosen, never uploaded ─────────────────────────────────────────

def test_only_a_pack_this_engine_ships_can_be_chosen(client):
    """A pack is regexes the engine runs over every row, so the set of them is
    what ships — never what a tenant sends. An id naming nothing is refused
    rather than stored, since a stored choice that resolves to no pack leaves a
    company unable to build with no statement of why."""
    hdr = _hdr(client, "s.menon@pie.example")
    assert client.put("/api/v1/data/catalog/companies/cx_sls/pack",
                      json={"pack_id": "zcnc"}, headers=hdr).status_code == 200
    bad = client.put("/api/v1/data/catalog/companies/cx_sls/pack",
                     json={"pack_id": "../../etc"}, headers=hdr)
    assert bad.status_code == 400
    assert "zcnc" in bad.json()["detail"]


def test_building_without_a_pack_or_an_export_says_which_is_missing(client):
    hdr = _hdr(client, "s.menon@pie.example")
    no_pack = client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr)
    assert no_pack.status_code == 409
    assert "pack" in no_pack.json()["detail"].lower()

    client.put("/api/v1/data/catalog/companies/cx_sls/pack",
               json={"pack_id": "zcnc"}, headers=hdr)
    no_corpus = client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr)
    assert no_corpus.status_code == 409
    assert "upload" in no_corpus.json()["detail"].lower()


# ── who may do what ──────────────────────────────────────────────────────────

def test_reading_is_open_and_changing_is_owner_only(client):
    """The state is the same entitlement as knowing when the books arrived; the
    setup actions replace what a whole company resolves against."""
    for email in ("r.nair@pie.example", "m.rao@pie.example"):
        hdr = _hdr(client, email)
        body = client.get("/api/v1/data/catalog/companies", headers=hdr)
        assert body.status_code == 200
        assert body.json()["can_manage"] is False
        assert _upload(client, hdr, "cx_sls", b"MM#,Material Description\nA,B\n"
                       ).status_code == 403
        assert client.put("/api/v1/data/catalog/companies/cx_sls/pack",
                          json={"pack_id": "zcnc"}, headers=hdr).status_code == 403
        assert client.post("/api/v1/data/catalog/companies/cx_sls/build",
                           headers=hdr).status_code == 403

    owner = _hdr(client, "s.menon@pie.example")
    assert client.get("/api/v1/data/catalog/companies",
                      headers=owner).json()["can_manage"] is True


def test_a_connection_from_another_organization_reads_as_absent(client):
    """Not a permission error, which would confirm it exists."""
    s = client.Maker()
    s.add(models.Organization(organization_id="org_other", name="Someone Else"))
    s.add(models.ZohoConnection(connection_id="cx_theirs", organization_id="org_other",
                                label="Theirs", zoho_organization_id="z9"))
    s.commit()
    s.close()

    hdr = _hdr(client, "s.menon@pie.example")
    assert client.post("/api/v1/data/catalog/companies/cx_theirs/build",
                       headers=hdr).status_code == 404
    assert _upload(client, hdr, "cx_theirs", b"MM#,Material Description\nA,B\n"
                   ).status_code == 404
    listed = client.get("/api/v1/data/catalog/companies", headers=hdr).json()
    assert {c["connection_id"] for c in listed["companies"]} == {"cx_sls", "cx_4u"}


# ── the invariant that must survive the new surface ──────────────────────────

@requires_pie
def test_no_cost_or_margin_crosses_the_per_company_surface(client, tmp_path, monkeypatch):
    """Every key, recursively — the weaker `'"cost"' in json.dumps(...)` form
    matches only a key named exactly `cost` and would wave through `unit_cost`
    or `avg_margin`."""
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    client.put("/api/v1/data/catalog/companies/cx_sls/pack",
               json={"pack_id": "zcnc"}, headers=hdr)
    _upload(client, hdr, "cx_sls", _corpus())
    client.post("/api/v1/data/catalog/companies/cx_sls/build", headers=hdr)
    body = client.get("/api/v1/data/catalog/companies", headers=hdr).json()

    def keys(node, path="$"):
        if isinstance(node, dict):
            for k, v in node.items():
                yield f"{path}.{k}", str(k)
                yield from keys(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from keys(v, f"{path}[{i}]")

    for where, key in keys(body):
        low = key.lower()
        for word in ("cost", "margin", "price"):
            assert word not in low, f"{where} names {word!r} on a nomenclature surface"
