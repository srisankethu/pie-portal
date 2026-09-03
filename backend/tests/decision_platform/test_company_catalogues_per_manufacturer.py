"""A company keeps a catalogue per manufacturer, and resolves against all of them.

``test_company_catalogues.py`` pins one catalogue per company and
``test_catalogue_sources.py`` the files inside one. These pin the layer above:
a distributor sells Kennametal *and* YG-1, each manufacturer's price lists
decode through that manufacturer's own pack, so a company holds several
catalogues — and what it resolves against is the union of the ones it has
built.

The three worth reading:

* **a resolution names which catalogue answered.** Two catalogues, two part
  numbers, and each resolves EXACT carrying its own catalogue's key — the
  provenance a multi-manufacturer answer has to carry, because "which
  manufacturer's product is this" is part of the answer.
* **a part number two catalogues both claim resolves to the newest build, and
  is counted.** The same rule ``combined_corpus`` applies between files, one
  level up, for the same reason: pie-parser's ``AuthoritativeIndex`` treats a
  duplicate inside one namespace as a collision that never resolves, so a
  union that kept both would silently stop the part number resolving at all.
* **removing a catalogue takes effect at once, and keeps its files.** The
  company stops answering from that manufacturer without a rebuild, and the
  uploaded bytes — the one thing that cannot be recreated — are superseded,
  not deleted.

One pack ships with the pinned engine, so both catalogues here decode through
``zcnc``. That is exactly the case the de-duplication exists for: two
catalogues through one pack share one identity namespace, and the tests below
are only honest because they exercise it.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import catalog
from app.config import settings
from app.db import get_session
from app.domain import models
from app.pie_service import pie_service
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

requires_pie = pytest.mark.requires_pie

HEADER = b"MM#,Material Description,Grade\n"
BASE = "/api/v1/data/catalog/companies/cx_sls"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Every catalogue file under a private directory: the union is assembled
    # from the disk, and two tests sharing a company id must not share a disk.
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id="cx_sls", organization_id="org_pie",
                                label="SLS Engineers", zoho_organization_id="z1"))
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
    yield tc
    pie_service.reload("cx_sls")


def _hdr(c, email="s.menon@pie.example"):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create(c, hdr, name, pack="zcnc"):
    return c.post(f"{BASE}/catalogues", json={"name": name, "pack_id": pack},
                  headers=hdr)


def _upload(c, hdr, key, content, filename="prices.csv", source_key=None):
    url = f"{BASE}/catalogues/{key}/corpus?filename={filename}"
    if source_key is not None:
        url += f"&source_key={source_key}"
    return c.post(url, content=content, headers={**hdr, "Content-Type": "text/csv"})


def _build(c, hdr, key):
    return c.post(f"{BASE}/catalogues/{key}/build", headers=hdr)


def _cat(body, key):
    return next(x for x in body["catalogues"] if x["catalogue_key"] == key)


def _keys(body):
    return [x["catalogue_key"] for x in body["catalogues"]]


# ── the union: every built catalogue answers, and says so ───────────────────

@requires_pie
def test_a_company_resolves_against_every_catalogue_it_has_built(client):
    """Two manufacturers, two catalogues, one company — and each answer names
    the catalogue it came from."""
    hdr = _hdr(client)
    assert _create(client, hdr, "Kennametal").status_code == 200
    body = _create(client, hdr, "YG-1").json()
    assert _keys(body) == ["kennametal", "yg-1"]
    assert body["union"] is None                # nothing built yet

    _upload(client, hdr, "kennametal", HEADER + b"KMT-1,SC DRILL 6.00MM 3XD,KC7315\n")
    _upload(client, hdr, "yg-1", HEADER + b"YG-1,SC DRILL 8.00MM 5XD,KC7315\n")
    first = _build(client, hdr, "kennametal").json()
    assert _cat(first, "kennametal")["records"] == 1
    assert first["union"]["records"] == 1
    assert [c["catalogue_key"] for c in first["union"]["catalogues"]] == ["kennametal"]
    second = _build(client, hdr, "yg-1").json()
    assert second["union"]["records"] == 2
    assert second["union"]["duplicates"] == 0
    assert sorted(c["catalogue_key"] for c in second["union"]["catalogues"]) == [
        "kennametal", "yg-1"]
    # Each member keeps its own stamp beside the union: which pack and ruleset
    # decoded that manufacturer's rows.
    for member in second["union"]["catalogues"]:
        assert member["ruleset_checksum"] and member["run_id"]

    kmt = pie_service.resolve("KMT-1", connection_id="cx_sls")
    yg = pie_service.resolve("YG-1", connection_id="cx_sls")
    assert kmt.rel == "EXACT" and kmt.candidates[0].catalogue == "kennametal"
    assert yg.rel == "EXACT" and yg.candidates[0].catalogue == "yg-1"
    assert kmt.candidates[0].to_dict()["catalogue"] == "kennametal"
    # The provenance the resolution API reports: the members, and a version
    # over their builds.
    assert sorted(c["catalogue_key"] for c in pie_service.catalogues("cx_sls")) == [
        "kennametal", "yg-1"]
    assert pie_service.catalog_version("cx_sls") == second["union"]["version"]


@requires_pie
def test_the_version_follows_the_builds_not_only_the_pack(client):
    """Two companies decoding different files through one pack must not share
    a resolution-cache key.

    The ruleset checksum is the *pack's* hash — identical for every catalogue
    ``zcnc`` decodes — and a version made of it alone let one company's cached
    answer serve another's. The version is over the run ids, which pie-parser
    derives from the input bytes as well, so it moves with the files.
    """
    hdr = _hdr(client)
    _create(client, hdr, "Kennametal")
    _upload(client, hdr, "kennametal", HEADER + b"KMT-1,SC DRILL 6.00MM 3XD,KC7315\n")
    one = _build(client, hdr, "kennametal").json()["union"]["version"]
    assert one and one != _cat(_build(client, hdr, "kennametal").json(),
                               "kennametal")["stamp"]["ruleset_checksum"]
    # The same bytes again: the same version, so an identical rebuild keeps
    # its cache entries valid.
    assert _build(client, hdr, "kennametal").json()["union"]["version"] == one

    _upload(client, hdr, "kennametal", HEADER + b"KMT-2,SC DRILL 6.00MM 3XD,KC7315\n")
    two = _build(client, hdr, "kennametal").json()["union"]["version"]
    assert two != one
    assert pie_service.catalog_version("cx_sls") == two


@requires_pie
def test_a_part_number_two_catalogues_both_claim_resolves_to_the_newest_build(client):
    """The collision rule, one level up from files.

    Two catalogues through one pack share one identity namespace, so a part
    number in both would never resolve if the union carried both rows. The
    most recently built catalogue's row is kept — a later build is a later
    statement — and the overlap is counted and named, never absorbed.
    """
    hdr = _hdr(client)
    _create(client, hdr, "Kennametal")
    _create(client, hdr, "YG-1")
    _upload(client, hdr, "kennametal", HEADER + b"SAME-1,SC DRILL 6.00MM 3XD,KC7315\n")
    _upload(client, hdr, "yg-1", HEADER + b"SAME-1,SC DRILL 8.00MM 5XD,KC7315\n")
    _build(client, hdr, "kennametal")
    body = _build(client, hdr, "yg-1").json()

    assert body["union"]["records"] == 1
    assert body["union"]["duplicates"] == 1
    assert body["union"]["duplicate_examples"] == ["SAME-1"]
    # Each catalogue still reports its own build in full; the union is where
    # the rule applied.
    assert _cat(body, "kennametal")["records"] == 1
    assert _cat(body, "yg-1")["records"] == 1

    res = pie_service.resolve("SAME-1", connection_id="cx_sls")
    assert res.rel == "EXACT"
    assert res.candidates[0].catalogue == "yg-1"
    assert "8.00MM" in res.candidates[0].desc         # the newer statement


@requires_pie
def test_removing_a_catalogue_takes_effect_at_once_and_keeps_its_files(client):
    hdr = _hdr(client)
    _create(client, hdr, "Kennametal")
    _create(client, hdr, "YG-1")
    _upload(client, hdr, "kennametal", HEADER + b"KMT-1,SC DRILL 6.00MM 3XD,KC7315\n")
    _upload(client, hdr, "yg-1", HEADER + b"YG-1,SC DRILL 8.00MM 5XD,KC7315\n")
    _build(client, hdr, "kennametal")
    _build(client, hdr, "yg-1")
    assert pie_service.resolve("YG-1", connection_id="cx_sls").rel == "EXACT"

    after = client.delete(f"{BASE}/catalogues/yg-1", headers=hdr).json()
    assert _keys(after) == ["kennametal"]
    assert after["union"]["records"] == 1
    assert [c["catalogue_key"] for c in after["union"]["catalogues"]] == ["kennametal"]
    assert not catalog.catalogue_dir("cx_sls", "yg-1").exists()
    # No rebuild, no restart: the manufacturer is gone from what resolves.
    assert pie_service.resolve("YG-1", connection_id="cx_sls").rel == "UNRESOLVED"
    assert pie_service.resolve("KMT-1", connection_id="cx_sls").rel == "EXACT"

    s = client.Maker()
    rows = [r for r in s.query(models.CompanyCorpus).all() if r.catalogue_key == "yg-1"]
    assert rows and all(r.superseded_at is not None for r in rows)
    assert all(r.content for r in rows)         # kept, not deleted
    s.close()

    assert client.delete(f"{BASE}/catalogues/yg-1", headers=hdr).status_code == 404


# ── defining catalogues ─────────────────────────────────────────────────────

def test_a_catalogue_is_keyed_by_its_name_and_refused_when_that_cannot_work(
        client, monkeypatch):
    hdr = _hdr(client)
    body = _create(client, hdr, "Kennametal (2026)", pack=None).json()
    assert _keys(body) == ["kennametal-2026"]
    assert _cat(body, "kennametal-2026")["name"] == "Kennametal (2026)"
    assert _cat(body, "kennametal-2026")["pack_id"] is None
    assert _cat(body, "kennametal-2026")["exists"] is False
    assert _cat(body, "kennametal-2026")["records"] is None

    taken = _create(client, hdr, "kennametal 2026")
    assert taken.status_code == 409
    assert "kennametal-2026" in taken.json()["detail"]

    unusable = _create(client, hdr, "!!!")
    assert unusable.status_code == 422
    # The union's own directory is not a name a catalogue may take.
    assert _create(client, hdr, "_union").status_code == 422

    bad_pack = _create(client, hdr, "YG-1", pack="../../etc")
    assert bad_pack.status_code == 400
    assert "zcnc" in bad_pack.json()["detail"] or "none" in bad_pack.json()["detail"]

    monkeypatch.setattr(catalog, "MAX_CATALOGUES", 2)
    assert _create(client, hdr, "YG-1", pack=None).status_code == 200
    third = _create(client, hdr, "Sandvik", pack=None)
    assert third.status_code == 409
    assert "limit of 2" in third.json()["detail"]


def test_a_catalogue_can_be_renamed_but_keeps_its_key(client):
    hdr = _hdr(client)
    _create(client, hdr, "default", pack=None)
    body = client.patch(f"{BASE}/catalogues/default", json={"name": "Kennametal"},
                        headers=hdr).json()
    assert _keys(body) == ["default"]
    assert _cat(body, "default")["name"] == "Kennametal"
    assert client.patch(f"{BASE}/catalogues/default", json={"name": "  "},
                        headers=hdr).status_code == 422
    assert client.patch(f"{BASE}/catalogues/nope", json={"name": "x"},
                        headers=hdr).status_code == 404


def test_files_belong_to_one_catalogue(client, monkeypatch):
    """A Kennametal price list supersedes nothing in YG-1's catalogue, the
    source ceiling is per catalogue, and a file is addressed only through the
    catalogue it was uploaded to."""
    hdr = _hdr(client)
    _create(client, hdr, "Kennametal", pack=None)
    _create(client, hdr, "YG-1", pack=None)
    monkeypatch.setattr(catalog, "MAX_SOURCES", 1)

    a = _upload(client, hdr, "kennametal", HEADER + b"A,A TOOL,KC725M\n",
                "prices.csv", "prices").json()
    b = _upload(client, hdr, "yg-1", HEADER + b"B,B TOOL,KC725M\n",
                "prices.csv", "prices").json()
    # The same source key in two catalogues is two files.
    assert [s["source_key"] for s in _cat(b, "kennametal")["sources"]] == ["prices"]
    assert [s["source_key"] for s in _cat(b, "yg-1")["sources"]] == ["prices"]
    assert (_cat(a, "kennametal")["sources"][0]["corpus_id"]
            != _cat(b, "yg-1")["sources"][0]["corpus_id"])
    # At the ceiling in one catalogue, not in the other.
    assert _upload(client, hdr, "kennametal", HEADER + b"C,C TOOL,KC725M\n",
                   "more.csv", "more").status_code == 409

    # Addressed through its own catalogue only.
    assert client.delete(f"{BASE}/catalogues/yg-1/sources/nope",
                         headers=hdr).status_code == 404
    gone = client.delete(f"{BASE}/catalogues/kennametal/sources/prices",
                         headers=hdr).json()
    assert _cat(gone, "kennametal")["sources"] == []
    assert [s["source_key"] for s in _cat(gone, "yg-1")["sources"]] == ["prices"]

    # A catalogue the company does not have is not there, for every action.
    assert _upload(client, hdr, "sandvik", HEADER + b"S,S TOOL,KC725M\n"
                   ).status_code == 404
    assert client.post(f"{BASE}/catalogues/sandvik/build", headers=hdr).status_code == 404
    assert client.put(f"{BASE}/catalogues/sandvik/pack", json={"pack_id": "zcnc"},
                      headers=hdr).status_code == 404


def test_defining_and_removing_catalogues_is_owner_only(client):
    hdr = _hdr(client)
    _create(client, hdr, "Kennametal", pack=None)
    for email in ("r.nair@pie.example", "m.rao@pie.example"):
        other = _hdr(client, email)
        assert _create(client, other, "YG-1", pack=None).status_code == 403
        assert client.patch(f"{BASE}/catalogues/kennametal", json={"name": "x"},
                            headers=other).status_code == 403
        assert client.delete(f"{BASE}/catalogues/kennametal",
                             headers=other).status_code == 403
        listed = client.get("/api/v1/data/catalog/companies", headers=other).json()
        assert _keys(listed["companies"][0]) == ["kennametal"]
        assert listed["max_catalogues"] == catalog.MAX_CATALOGUES


# ── a file built before catalogues had directories ──────────────────────────

def test_a_catalogue_built_before_directories_is_moved_not_rebuilt(client, monkeypatch):
    """The legacy flat file is adopted into ``default/`` at boot.

    Moved rather than rebuilt: its bytes are the ones the row's stamp
    describes, so the union assembled from it is the one a rebuild would
    produce, at no cost — and ``ensure_company_catalogues`` then has nothing
    to build, which is what the empty result asserts.
    """
    monkeypatch.setattr(settings, "AUTO_BUILD_CATALOG", True)
    s = client.Maker()
    s.add(models.CompanyCatalogue(
        organization_id="org_pie", connection_id="cx_sls", catalogue_key="default",
        name="", pack_choice="zcnc", records=1, built_at=__import__("app.clock").clock.now(),
        ruleset_checksum="abc", run_id="r1"))
    s.add(models.CompanyCorpus(
        organization_id="org_pie", connection_id="cx_sls", catalogue_key="default",
        source_key="old.csv", filename="old.csv", size_bytes=1, sha256="x", content=b"x"))
    s.commit()

    legacy = settings.PIE_CATALOG.parent / "catalogues" / "cx_sls" / "products.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"record_id":"OLD-1","description":"OLD TOOL","org_id":"zcnc",'
                      '"ruleset_checksum":"abc","run_id":"r1"}\n', encoding="utf-8")
    (legacy.parent / "retrieval.jsonl").write_text("{}\n", encoding="utf-8")

    assert catalog.ensure_company_catalogues(s) == []
    s.close()
    assert not legacy.exists()
    moved = catalog.company_catalog_path("cx_sls")
    assert moved.read_text(encoding="utf-8").count("OLD-1") == 1
    assert (moved.parent / "retrieval.jsonl").exists()
    assert [m["catalogue_key"] for m in catalog.built_catalogues("cx_sls")] == ["default"]
    union = catalog.union_catalogue("cx_sls")
    assert union is not None and union.records == 1
    assert union.catalogues[0]["run_id"] == "r1"


# ── the migration: a company arrives with the one catalogue it had ───────────

BACKEND = Path(__file__).resolve().parents[2]


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=BACKEND, env=env, capture_output=True, text=True)


def test_the_migration_gives_an_existing_company_its_default_catalogue(tmp_path):
    """``m1cats`` re-keys the table and carries the pack choice across.

    A company that had built keeps its row under ``default`` with the pack it
    chose on the connection; a company that had only uploaded gets a
    ``default`` definition so its files still belong to a catalogue; every
    corpus row is filed under ``default``. Reversible, and the reverse keeps
    the uploaded bytes.
    """
    import json

    db = tmp_path / "m.db"
    assert _alembic(db, "upgrade", "l1alias").returncode == 0
    c = sqlite3.connect(db)
    c.execute("INSERT INTO organizations (organization_id, name, currency, config, "
              "created_at) VALUES ('o1','O','INR','{}',CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO zoho_connections (connection_id, organization_id, label, "
              "enabled, zoho_organization_id, config, created_at, updated_at) "
              "VALUES ('c1','o1','A',1,'z1',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
              (json.dumps({"pie_pack": "zcnc"}),))
    c.execute("INSERT INTO zoho_connections (connection_id, organization_id, label, "
              "enabled, zoho_organization_id, config, created_at, updated_at) "
              "VALUES ('c2','o1','B',1,'z2',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    for cid, corpus in (("c1", "k1"), ("c2", "k2")):
        c.execute("INSERT INTO company_corpora (corpus_id, organization_id, "
                  "connection_id, filename, content_type, size_bytes, sha256, content, "
                  "uploaded_at) VALUES (?,?,?,'f.csv','text/csv',1,'x',X'41',"
                  "CURRENT_TIMESTAMP)", (corpus, "o1", cid))
    c.execute("INSERT INTO company_catalogues (organization_id, connection_id, "
              "corpus_id, pack, records, rows_read, quarantined, built_at) "
              "VALUES ('o1','c1','k1','/p',5,5,0,CURRENT_TIMESTAMP)")
    c.commit()
    c.close()

    assert _alembic(db, "upgrade", "head").returncode == 0
    c = sqlite3.connect(db)
    pk = [r[1] for r in c.execute("PRAGMA table_info(company_catalogues)") if r[5]]
    assert pk == ["organization_id", "connection_id", "catalogue_key"]
    rows = list(c.execute(
        "SELECT connection_id, catalogue_key, pack_choice, built_at IS NOT NULL "
        "FROM company_catalogues ORDER BY connection_id"))
    assert rows == [("c1", "default", "zcnc", 1), ("c2", "default", None, 0)]
    assert list(c.execute("SELECT catalogue_key FROM company_corpora")) == [
        ("default",), ("default",)]
    c.close()

    assert _alembic(db, "downgrade", "-1").returncode == 0
    c = sqlite3.connect(db)
    pk = [r[1] for r in c.execute("PRAGMA table_info(company_catalogues)") if r[5]]
    assert pk == ["organization_id", "connection_id"]
    # The built row survives the reverse; the unbuilt definition cannot be
    # represented and goes; every uploaded file stays.
    assert list(c.execute("SELECT connection_id FROM company_catalogues")) == [("c1",)]
    assert c.execute("SELECT COUNT(*) FROM company_corpora").fetchone() == (2,)
    c.close()
