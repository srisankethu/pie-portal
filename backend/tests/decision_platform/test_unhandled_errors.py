"""An unhandled error must name itself and be findable in the log.

Written after an owner reported that "Add company" failed with "The server hit
an error it could not describe. Check /api/health — it reports the migration
state and says what to run." They checked. It said CURRENT. The message was a
guess the client had no evidence for, and it cost a round trip to disprove
instead of naming the failure.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.main import app


class Boom(RuntimeError):
    """Stands in for whatever actually breaks in a route."""


@pytest.fixture()
def client():
    router = APIRouter()

    @router.get("/api/v1/_test_boom")
    def boom() -> dict:
        raise Boom("a secret-looking detail that must not reach the browser")

    app.include_router(router)
    # Exception handlers only run when the client is not re-raising for us.
    yield TestClient(app, raise_server_exceptions=False)
    app.router.routes = [r for r in app.router.routes
                         if getattr(r, "path", "") != "/api/v1/_test_boom"]


def test_a_500_names_the_exception_and_gives_a_searchable_id(client):
    r = client.get("/api/v1/_test_boom")
    assert r.status_code == 500

    body = r.json()
    assert body["error_type"] == "Boom", "the reader needs to know what kind of failure"
    assert len(body["error_id"]) == 8
    # The id in the body is the id in the log, or it cannot be looked up.
    assert body["error_id"] in body["detail"]


def test_the_exception_message_never_reaches_the_browser(client):
    """The type is safe; the message may carry a row, a token or a name."""
    body = client.get("/api/v1/_test_boom").json()
    assert "secret-looking" not in str(body)


def test_it_does_not_blame_the_migration_state(client):
    """The specific regression: a confident wrong lead.

    /api/health answers this properly and says CURRENT when it is current.
    Naming it here sends an operator to disprove a guess instead of reading the
    traceback that is sitting in the log.
    """
    text = str(client.get("/api/v1/_test_boom").json()).lower()
    for word in ("migration", "alembic", "upgrade", "/api/health"):
        assert word not in text, f"the 500 body still points at {word!r}"


def test_a_handled_error_is_untouched(client):
    """Only *unhandled* errors go through this. A 403 stays a 403 with its own
    message, or every deliberate refusal would turn into an opaque 500."""
    r = client.post("/api/v1/connections", json={"zoho_organization_id": "1"})
    assert r.status_code in (401, 403, 422)
    assert "error_id" not in r.json()
