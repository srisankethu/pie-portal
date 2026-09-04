"""One catalogue per connected company: its own export, its own pack.

The change these pin exists because an organization can read three Zoho books,
and those three companies have three different item masters. The tests that
matter most are the ones about *not* sharing: a company must never answer from
another company's catalogue, and an absent catalogue must never read as zero
coverage.

There is no deployment-wide catalogue behind these any more: a company with no
export resolves nothing, and the shipped corpus reaches a company only as a
seed it then owns. The tests that used to pin the shared surface
(``test_catalog_surface.py``) live here now, per company, because that is where
the same honesty rules land — a build reports the parser's own numbers, and a
missing seed names which of its two absences it is.
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
    # Each with one catalogue defined and nothing chosen for it yet — the
    # shape a migrated company arrives in. Several catalogues per company are
    # pinned in test_company_catalogues_per_manufacturer.py.
    for cid in ("cx_sls", "cx_4u"):
        s.add(models.CompanyCatalogue(organization_id="org_pie", connection_id=cid,
                                      catalogue_key="default", name=""))
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


def _upload(c, hdr, connection_id, content, filename="items.csv", decode=True):
    """Upload one file and, unless the test is about the discovery step itself,
    save the decoding config the analysis proposed for it.

    Two steps, because that is the flow: an upload is a file of unknown format
    and a *proposal*, and nothing is decoded until a person saves it. Most
    tests here are about what a build then does, so they take both; the ones
    about the proposal pass ``decode=False`` and say so.
    """
    r = c.post(
        f"/api/v1/data/catalog/companies/{connection_id}/catalogues/default/corpus"
        f"?filename={filename}",
        content=content, headers={**hdr, "Content-Type": "text/csv"})
    if decode and r.status_code == 200:
        return _confirm(c, hdr, r.json(), connection_id)
    return r


def _confirm(c, hdr, body, connection_id="cx_sls", key="default"):
    """Save the decoding config the newest file's analysis proposed."""
    source = _cat(body, connection_id, key)["sources"][-1]
    columns = source["decoding"]["columns"]
    return c.put(
        f"/api/v1/data/catalog/companies/{connection_id}/catalogues/{key}"
        f"/sources/{source['source_key']}/decoding",
        json={"record_id": columns["record_id"],
              "description": columns["description"],
              "grade": columns.get("grade"),
              "rule_set": source["decoding"]["rule_set"]},
        headers=hdr)


def _company(body, connection_id):
    return next(x for x in body["companies"] if x["connection_id"] == connection_id)


def _cat(body, connection_id="cx_sls", key="default"):
    """The one catalogue of a company, out of the company envelope every
    action returns."""
    company = body if "catalogues" in body else _company(body, connection_id)
    return next(x for x in company["catalogues"] if x["catalogue_key"] == key)


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
    assert _upload(client, hdr, "cx_sls", _corpus()).status_code == 200
    built = client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build", headers=hdr).json()
    assert _cat(built)["records"] == 6717

    body = client.get("/api/v1/data/catalog/companies", headers=hdr).json()
    other = _cat(body, "cx_4u")
    assert other["exists"] is False
    # None, never 0 — a company with no catalogue says nothing about coverage.
    assert other["records"] is None
    assert other["corpus"] is None
    assert other["report"] is None
    # And the union — what the company actually resolves against — is absent
    # for the company with nothing built, never an empty catalogue.
    assert _company(body, "cx_4u")["union"] is None
    assert _company(body, "cx_sls")["union"]["records"] == 6717


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
    _upload(client, hdr, "cx_sls", _corpus())
    first = _cat(client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build",
                             headers=hdr).json())

    catalog.company_catalog_path("cx_sls").unlink()
    body = client.get("/api/v1/data/catalog/companies", headers=hdr).json()
    gone = _cat(body)
    # Named as its own state: a row without its file is a rebuild waiting to
    # happen, which is a different fix from having uploaded nothing.
    assert gone["built_but_missing_on_disk"] is True
    assert gone["exists"] is False

    again = _cat(client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build",
                             headers=hdr).json())
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
    _upload(client, hdr, "cx_sls", _corpus())
    client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build", headers=hdr)

    raw = _corpus() + b"MM-EXTRA,SOME NEW TOOL,KC725M\n"
    after = _cat(_upload(client, hdr, "cx_sls", raw, filename="newer.csv").json())
    assert after["stale"] is True
    assert after["exists"] is True          # still a real catalogue
    assert after["corpus"]["filename"] == "newer.csv"

    rebuilt = _cat(client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build",
                               headers=hdr).json())
    assert rebuilt["stale"] is False


# ── the upload, and what it refuses ──────────────────────────────────────────

def test_an_export_whose_columns_cannot_be_identified_is_refused(client):
    """Rejected at upload, listing the file's own headers — not accepted and
    then failing to build, because a corpus that is stored and unusable is
    worse than one refused.

    **This used to demand the pack's column names**, and the message named
    ``MM#``. That was wrong in the way that mattered most: it made every export
    other than the one this platform was written against unusable, and the fix
    it implied was to rename spreadsheet columns to match a pack a person
    cannot see. A file keeps its own headers now and a stored mapping says which
    of them fills each role, so the only thing left to refuse is a file whose
    part number and description cannot be identified *at all* — and the answer
    to that is the list of what the file does have.
    """
    hdr = _hdr(client, "s.menon@pie.example")
    r = _upload(client, hdr, "cx_sls", b"Wrong,Headers\n1,2\n", decode=False)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "record id" in detail
    assert "Wrong, Headers" in detail


def test_an_export_that_is_not_utf8_is_refused(client):
    hdr = _hdr(client, "s.menon@pie.example")
    r = _upload(client, hdr, "cx_sls", b"\xff\xfe\x00\x00not text", decode=False)
    assert r.status_code == 422
    assert "UTF-8" in r.json()["detail"]


def test_an_oversize_export_is_refused_by_its_declared_length(client, monkeypatch):
    """The header is checked before the body is stored, so an oversize upload
    is answered rather than absorbed."""
    monkeypatch.setattr(catalog, "MAX_CORPUS_BYTES", 1024)
    hdr = _hdr(client, "s.menon@pie.example")
    r = _upload(client, hdr, "cx_sls", b"MM#,Material Description\n" + b"x" * 4096, decode=False)
    assert r.status_code == 413


def test_uploading_supersedes_rather_than_overwrites(client):
    """Append-only, so a catalogue built from the earlier bytes keeps a real
    referent for the corpus its stamp names."""
    hdr = _hdr(client, "s.menon@pie.example")
    header = b"MM#,Material Description,Grade\n"
    _upload(client, hdr, "cx_sls", header + b"A,FIRST,KC725M\n", decode=False)
    _upload(client, hdr, "cx_sls", header + b"B,SECOND,KC725M\n", decode=False)

    s = client.Maker()
    rows = s.query(models.CompanyCorpus).filter_by(connection_id="cx_sls").all()
    assert len(rows) == 2
    assert sum(1 for r in rows if r.superseded_at is None) == 1
    # The bytes are kept, not a path — the durability the design turns on.
    assert all(r.content and r.sha256 == hashlib.sha256(r.content).hexdigest()
               for r in rows)
    s.close()


# ── the decoder: shipped, never uploaded, and never defaulted ────────────────

@requires_pie
def test_only_a_rule_set_this_engine_ships_can_be_saved(client):
    """A rule set is regexes the engine runs over every row, so the set of them
    is what ships — never what a tenant sends. An id naming nothing is refused
    rather than stored, since a saved config that resolves to no rule set
    leaves a file unbuildable with no statement of why."""
    hdr = _hdr(client, "s.menon@pie.example")
    _upload(client, hdr, "cx_sls", _corpus(), decode=False)
    bad = client.put(
        "/api/v1/data/catalog/companies/cx_sls/catalogues/default"
        "/sources/items.csv/decoding",
        json={"record_id": "MM#", "description": "Material Description",
              "rule_set": "../../etc"}, headers=hdr)
    assert bad.status_code == 400
    assert "zcnc" in bad.json()["detail"] or "none" in bad.json()["detail"]


@requires_pie
def test_building_without_a_file_or_a_saved_config_says_which_is_missing(client):
    """The two states a build refuses, and neither of them decodes anything
    through a default."""
    hdr = _hdr(client, "s.menon@pie.example")
    no_corpus = client.post(
        "/api/v1/data/catalog/companies/cx_sls/catalogues/default/build", headers=hdr)
    assert no_corpus.status_code == 409
    assert "upload" in no_corpus.json()["detail"].lower()

    # A file is on record, and its decoding config was never saved. The build
    # names the file rather than decoding it through anything.
    _upload(client, hdr, "cx_sls", _corpus(), decode=False)
    unconfigured = client.post(
        "/api/v1/data/catalog/companies/cx_sls/catalogues/default/build", headers=hdr)
    assert unconfigured.status_code == 409
    detail = unconfigured.json()["detail"]
    assert "items.csv" in detail
    assert "decoding config" in detail


# ── who may do what ──────────────────────────────────────────────────────────

def test_reading_is_open_and_changing_is_owner_only(client):
    """The state is the same entitlement as knowing when the books arrived; the
    setup actions replace what a whole company resolves against."""
    for email in ("r.nair@pie.example", "m.rao@pie.example"):
        hdr = _hdr(client, email)
        body = client.get("/api/v1/data/catalog/companies", headers=hdr)
        assert body.status_code == 200
        assert body.json()["can_manage"] is False
        assert _upload(client, hdr, "cx_sls", b"MM#,Material Description\nA,B\n",
                       decode=False).status_code == 403
        assert client.put(
            "/api/v1/data/catalog/companies/cx_sls/catalogues/default"
            "/sources/items.csv/decoding",
            json={"record_id": "MM#", "description": "Material Description",
                  "rule_set": "zcnc"}, headers=hdr).status_code == 403
        assert client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build",
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
    assert client.post("/api/v1/data/catalog/companies/cx_theirs/catalogues/default/build",
                       headers=hdr).status_code == 404
    assert _upload(client, hdr, "cx_theirs", b"MM#,Material Description\nA,B\n",
                   decode=False).status_code == 404
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
    _upload(client, hdr, "cx_sls", _corpus())
    client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build", headers=hdr)
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


# ── the parser's own numbers, and the seed that produced them ────────────────

@requires_pie
def test_a_built_catalogue_reports_its_provenance_and_the_parsers_numbers(
        client, tmp_path, monkeypatch):
    """The stamp shown is the stamp on the records, and the counts are the
    parser's own — pack id, version and ruleset checksum are what say WHICH
    catalogue answered a resolution, and a recomputed census would be a second
    answer to a question the parser has already answered."""
    import json as jsonlib

    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    _upload(client, hdr, "cx_sls", _corpus())
    body = _cat(client.post("/api/v1/data/catalog/companies/cx_sls/catalogues/default/build",
                            headers=hdr).json())

    assert body["exists"] is True
    assert body["records"] == 6717
    assert body["quarantined"] == 0
    for field in ("pack_id", "pack_version", "org_id", "ruleset_checksum", "run_id"):
        assert body["stamp"].get(field), f"stamp is missing {field}"

    # The displayed stamp must match what the records themselves carry.
    with catalog.company_catalog_path("cx_sls").open(encoding="utf-8") as fh:
        first = jsonlib.loads(fh.readline())
    for field in ("ruleset_checksum", "pack_id", "run_id"):
        assert body["stamp"][field] == first[field]

    report = body["report"]
    assert report["total"] == 6717
    families = [f for f in report["by_family"] if f != "(unresolved)"]
    assert len(families) == 11
    assert report["unresolved_family"] == 0
    # Rates are ratios in [0, 1], never percentages.
    for family, rate in report["parse_rates"].items():
        assert rate is None or 0.0 <= rate <= 1.0, (family, rate)


def test_an_uninitialised_submodule_is_named_as_the_cause(client, monkeypatch, tmp_path):
    """The two absences of the *seed* corpus are different fixes: no engine at
    all points at the setup script, not at a generic error.

    The seed is what a first company inherits, so its absence is worth naming
    even though nothing resolves against it: without it an existing deployment
    has no catalogue to carry across, and the screen would otherwise say only
    that nothing is built.
    """
    monkeypatch.setattr(settings, "PIE_PARSER_ROOT", tmp_path / "nowhere")
    body = client.get("/api/v1/data/catalog/companies",
                      headers=_hdr(client, "s.menon@pie.example")).json()
    assert body["source"]["available"] is False
    assert "submodule is not initialised" in body["source"]["reason"]
    assert "setup_pie_parser.sh" in body["source"]["reason"]


@requires_pie
def test_a_missing_seed_corpus_is_named_as_the_cause(client, monkeypatch, tmp_path):
    """Engine present, corpus gone: the reason names PIE_CORPUS, not the
    submodule — sending someone to fetch what they already have wastes a day."""
    monkeypatch.setattr(settings, "PIE_CORPUS", tmp_path / "gone.csv")
    body = client.get("/api/v1/data/catalog/companies",
                      headers=_hdr(client, "s.menon@pie.example")).json()
    assert body["source"]["available"] is False
    assert "PIE_CORPUS" in body["source"]["reason"]
    assert "setup_pie_parser" not in body["source"]["reason"]
