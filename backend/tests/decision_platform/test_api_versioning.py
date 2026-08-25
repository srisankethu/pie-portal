"""Every HTTP surface lives under /api/v1, and the two that did not still answer.

`quotes` and `attribution` were mounted at `/api/quotes` and `/api/attribution`
while every other router served `/api/v1/...`. An unversioned surface costs
nothing until the first client integrates against it, after which every breaking
change is a negotiation — so they moved while the only client is the bundle in
this repo.

The old paths keep answering as a 307. Not for external integrators, who do not
exist yet, but for the deploy: a tab loaded five minutes before a release holds a
JS bundle full of the old paths, and the release swaps the backend under it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    """A client over the REAL app, not the minimal one `api_client` builds.

    `api_client` (conftest) mounts three routers against an isolated database,
    which is right for endpoint behaviour and useless here: these assertions are
    about which paths the deployed application answers on. Redirects are decided
    by routing before any dependency runs, so no database is needed and the
    lifespan is deliberately not entered.
    """
    return TestClient(app, raise_server_exceptions=False)


def _published(client) -> set[str]:
    """Paths as the OpenAPI document reports them.

    Read from the schema rather than walking ``app.routes``: this FastAPI
    version keeps an included router as one ``_IncludedRouter`` entry with no
    ``path`` of its own, so a naive walk sees four framework routes and
    concludes the application serves nothing. It is also the more honest source
    — the schema is what a client is told the interface is.
    """
    return set(client.get("/openapi.json").json()["paths"])


def test_every_published_api_path_is_versioned(client):
    """The check that stops the next router being mounted a level up.

    `/api/health` is deliberately exempt: it is a liveness probe read by
    container orchestration and by `compose.yaml`'s healthcheck, so moving it
    would mean a version negotiation with a Docker HEALTHCHECK line.
    """
    exempt = {"/api/health"}
    stray = sorted(p for p in _published(client)
                   if p.startswith("/api/")
                   and not p.startswith("/api/v1/")
                   and p not in exempt)
    assert not stray, f"unversioned API paths: {stray}"


def test_the_new_paths_exist(client):
    published = _published(client)
    assert any(p.startswith("/api/v1/quotes") for p in published)
    assert any(p.startswith("/api/v1/attribution") for p in published)


@pytest.mark.parametrize("old,new", [
    ("/api/quotes", "/api/v1/quotes"),
    ("/api/attribution", "/api/v1/attribution"),
])
def test_a_legacy_path_redirects_to_its_versioned_self(client, old, new):
    r = client.get(old, follow_redirects=False)
    assert r.status_code == 307, (
        f"{old} must redirect, got {r.status_code}")
    assert r.headers["location"] == new


@pytest.mark.parametrize("old,new", [
    ("/api/quotes/abc/lines", "/api/v1/quotes/abc/lines"),
    ("/api/attribution/summary", "/api/v1/attribution/summary"),
])
def test_the_tail_of_the_path_is_carried(client, old, new):
    r = client.get(old, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == new


def test_the_query_string_survives_the_redirect(client):
    r = client.get("/api/attribution/summary?window=90&x=1",
                       follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/v1/attribution/summary?window=90&x=1"


def test_a_post_is_redirected_as_a_post(client):
    """307, not 302, and this is the assertion that pins the difference.

    A 302 lets the client turn the follow-up into a GET and drop the body, so
    every quote submission made from a stale tab would arrive as an empty read.
    307 obliges the client to repeat both.
    """
    r = client.post("/api/quotes", json={"customer_ref": "x"},
                        follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/v1/quotes"


def test_the_aliases_are_not_in_the_public_schema(client):
    """They are a migration aid, not part of the interface — so a client reading
    the OpenAPI document is never told about a path that is due for deletion."""
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/api/quotes")]
    assert not [p for p in paths if p.startswith("/api/attribution")]
    assert [p for p in paths if p.startswith("/api/v1/quotes")]
