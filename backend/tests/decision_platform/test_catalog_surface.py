"""The catalogue surface: state, provenance, build, and honest failure.

Building the decoded catalogue used to be a terminal step — nothing in the
product could say whether one existed, what built it, or rebuild it. These
tests pin the surface that changed that, and its two honesty rules: a missing
catalogue is its own state (never zero records, never 0% coverage), and every
failure names its specific cause with the fix in the message.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.config import settings
from app.db import get_session
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from conftest import PIE_AVAILABLE

requires_pie = pytest.mark.requires_pie


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
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
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── state ────────────────────────────────────────────────────────────────────

@requires_pie
def test_a_built_catalogue_reports_its_provenance_and_the_parsers_numbers(client):
    """The stamp shown is the stamp on the records — pack id, version and
    ruleset checksum are what say WHICH catalogue answered a resolution."""
    body = client.get("/api/v1/data/catalog",
                      headers=_hdr(client, "r.nair@pie.example")).json()
    assert body["exists"] is True
    assert body["scope"] == "deployment"
    assert body["records"] == 6717
    # Provenance, not just a row count.
    for field in ("pack_id", "pack_version", "org_id", "ruleset_checksum", "run_id"):
        assert body["stamp"].get(field), f"stamp is missing {field}"
    # The displayed stamp must match what the records themselves carry.
    import json as jsonlib
    first = jsonlib.loads(
        Path(settings.PIE_CATALOG).open(encoding="utf-8").readline())
    assert body["stamp"]["ruleset_checksum"] == first["ruleset_checksum"]
    assert body["stamp"]["pack_id"] == first["pack_id"]
    assert body["stamp"]["run_id"] == first["run_id"]


@requires_pie
def test_the_run_report_is_the_parsers_own_not_recomputed(client):
    """Censuses, parse rates and new tokens come from the RunReport the build
    stored — eleven families exercised, nothing quarantined."""
    body = client.get("/api/v1/data/catalog",
                      headers=_hdr(client, "r.nair@pie.example")).json()
    report = body["report"]
    if report is None:
        # A catalogue from before the sidecar existed says so and names the fix.
        assert "Rebuild" in (body["report_missing"] or "")
        pytest.skip("catalogue predates the run report; the absence is named")
    assert report["total"] == 6717
    families = [f for f in report["by_family"] if f != "(unresolved)"]
    assert len(families) == 11
    assert report["unresolved_family"] == 0
    assert body["build"]["quarantined"] == 0
    # Rates are ratios in [0, 1], never percentages.
    for family, rate in report["parse_rates"].items():
        assert rate is None or 0.0 <= rate <= 1.0, (family, rate)


def test_a_missing_catalogue_is_not_built(client, monkeypatch, tmp_path):
    """NOT BUILT is its own state: records is null, never 0 — the §1 rule that
    absence of evidence is not a zero, rendered as an API shape."""
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    body = client.get("/api/v1/data/catalog",
                      headers=_hdr(client, "r.nair@pie.example")).json()
    assert body["exists"] is False
    assert body["records"] is None
    assert body["report"] is None
    assert body["stamp"] == {}


def test_an_uninitialised_submodule_is_named_as_the_cause(client, monkeypatch, tmp_path):
    """The two absences are different fixes: no engine at all points at the
    setup script, not at a generic error."""
    monkeypatch.setattr(settings, "PIE_PARSER_ROOT", tmp_path / "nowhere")
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    body = client.get("/api/v1/data/catalog",
                      headers=_hdr(client, "s.menon@pie.example")).json()
    assert body["source"]["available"] is False
    assert "submodule is not initialised" in body["source"]["reason"]
    assert "setup_pie_parser.sh" in body["source"]["reason"]

    r = client.post("/api/v1/data/catalog/build",
                    headers=_hdr(client, "s.menon@pie.example"))
    assert r.status_code == 503
    assert "setup_pie_parser.sh" in r.json()["detail"]


@requires_pie
def test_a_missing_corpus_is_named_as_the_cause(client, monkeypatch, tmp_path):
    """Engine present, corpus gone: the reason names PIE_CORPUS, not the
    submodule — sending someone to fetch what they already have wastes a day."""
    monkeypatch.setattr(settings, "PIE_CORPUS", tmp_path / "gone.csv")
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    body = client.get("/api/v1/data/catalog",
                      headers=_hdr(client, "s.menon@pie.example")).json()
    assert body["source"]["available"] is False
    assert "PIE_CORPUS" in body["source"]["reason"]
    assert "setup_pie_parser" not in body["source"]["reason"]

    r = client.post("/api/v1/data/catalog/build",
                    headers=_hdr(client, "s.menon@pie.example"))
    assert r.status_code == 503
    assert "PIE_CORPUS" in r.json()["detail"]


# ── build ────────────────────────────────────────────────────────────────────

@requires_pie
def test_an_owner_builds_a_catalogue_from_nothing_and_sees_the_real_result(
        client, monkeypatch, tmp_path):
    """Acceptance: from a state with no catalogue, the build returns the
    finished state — 6,717 rows, eleven families, zero quarantined."""
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    assert client.get("/api/v1/data/catalog", headers=hdr).json()["exists"] is False

    body = client.post("/api/v1/data/catalog/build", headers=hdr).json()
    assert body["exists"] is True
    assert body["records"] == 6717
    assert body["build"]["quarantined"] == 0
    assert len([f for f in body["report"]["by_family"] if f != "(unresolved)"]) == 11
    # The sidecar landed beside the catalogue, so the state survives a restart.
    assert (tmp_path / "products.run_report.json").exists()


def test_rebuilding_is_an_owner_action_and_reading_is_not(client, monkeypatch, tmp_path):
    """Scoped server-side, not hidden client-side: the catalogue is
    deployment-wide, so even a manager — who may sync their own org — may not
    replace what every organization resolves against."""
    # Point away from the shared catalogue so a 403 test can never rebuild it.
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    for email in ("r.nair@pie.example", "m.rao@pie.example"):
        hdr = _hdr(client, email)
        assert client.get("/api/v1/data/catalog", headers=hdr).status_code == 200
        assert client.get("/api/v1/data/catalog",
                          headers=hdr).json()["can_rebuild"] is False
        assert client.post("/api/v1/data/catalog/build",
                           headers=hdr).status_code == 403
    owner = _hdr(client, "s.menon@pie.example")
    assert client.get("/api/v1/data/catalog",
                      headers=owner).json()["can_rebuild"] is True
    if not PIE_AVAILABLE:
        return  # the owner's actual build needs the engine; scoping is proven
    assert client.post("/api/v1/data/catalog/build",
                       headers=owner).status_code == 200


@requires_pie
def test_no_cost_or_margin_crosses_this_surface(client, monkeypatch, tmp_path):
    """A catalogue carries nomenclature. The emitter excludes the corpus's
    commercial payload by default, and the status response must not grow a
    money field either."""
    monkeypatch.setattr(settings, "PIE_CATALOG", tmp_path / "products.jsonl")
    hdr = _hdr(client, "s.menon@pie.example")
    body = client.post("/api/v1/data/catalog/build", headers=hdr).json()

    import json as jsonlib
    with (tmp_path / "products.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            assert "payload" not in jsonlib.loads(line), \
                "a record carried the corpus's opaque commercial columns"

    flat = jsonlib.dumps(body).lower()
    for word in ("cost", "margin", "price"):
        assert f'"{word}"' not in flat
