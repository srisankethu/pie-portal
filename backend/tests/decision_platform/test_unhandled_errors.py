"""An unhandled error must name itself and be findable in the log.

Written after an owner reported that "Add company" failed with "The server hit
an error it could not describe. Check /api/health — it reports the migration
state and says what to run." They checked. It said CURRENT. The message was a
guess the client had no evidence for, and it cost a round trip to disprove
instead of naming the failure.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.main import app

#: The route this module mounts on the shared app so it has something that
#: fails. Named once because three separate things have to agree about it: the
#: mount, the teardown that removes it, and the test that proves it is gone.
BOOM_PATH = "/api/v1/_test_boom"


class Boom(RuntimeError):
    """Stands in for whatever actually breaks in a route."""


@contextmanager
def boom_route_mounted():
    """Give the shared app a route that raises, and put the app back after.

    A context manager rather than fixture-only setup because the putting-back
    is the part that has to be *tested*, and a fixture's teardown cannot be
    asserted on from inside the test it tears down.

    **The cleanup this replaces removed nothing, and never had.** It read::

        app.router.routes = [r for r in app.router.routes
                             if getattr(r, "path", "") != BOOM_PATH]

    ``app.include_router`` does not flatten a router's routes into
    ``app.router.routes``. It appends a single ``_IncludedRouter`` wrapper,
    and that wrapper has no ``.path`` — so the comparison was ``"" != path``
    for every element, the list came back whole, and the route stayed mounted
    on an object every other test in the suite shares. Recording the length
    first and dropping exactly what was appended after it takes no view on how
    the framework represents an inclusion, or on how many objects it is.

    Nothing in this module reads the API schema, so the damage landed
    somewhere else entirely: ``tests/test_endpoint_inventory.py`` builds its
    inventory from ``app.openapi()``, found ``/api/v1/_test_boom`` in it, and
    asked for a documentation row for a test fixture.

    It failed *intermittently*, which is the expensive part. The suite runs
    under ``-n auto`` with xdist's default ``--dist load``, so whether the two
    files land on the same worker — and in which order — varies per run. It
    fails, passes on a re-run, and reads like an infrastructure flake right up
    until somebody spends an afternoon on it.

    Deliberately *not* here: an ``app.openapi_schema = None`` reset. FastAPI
    caches the generated document, and the first diagnosis of this bug assumed
    a stale cache was the mechanism. It is not — this version invalidates the
    cache when the route table changes, verified by mounting a route, reading
    the schema, unmounting, and reading it again. Removing the route is
    sufficient and a cache reset would be a line kept alive by a wrong story.
    """
    router = APIRouter()

    @router.get(BOOM_PATH)
    def boom() -> dict:
        raise Boom("a secret-looking detail that must not reach the browser")

    mark = len(app.router.routes)
    app.include_router(router)
    # Identity, not equality: two routes that compare equal are still two
    # objects, and only the ones this block added may be taken away.
    added = {id(r) for r in app.router.routes[mark:]}
    try:
        yield
    finally:
        app.router.routes = [r for r in app.router.routes if id(r) not in added]


@pytest.fixture()
def client():
    # Exception handlers only run when the client is not re-raising for us.
    with boom_route_mounted():
        yield TestClient(app, raise_server_exceptions=False)


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


def test_mounting_the_boom_route_leaves_the_shared_app_exactly_as_it_was():
    """The cleanup, asserted rather than assumed.

    This module borrows an object every other test in the suite shares, and
    the cost of not giving it back fell on a file that has nothing to do with
    error handling. So the contract is pinned here, where the borrowing
    happens.

    Both directions on purpose. The "after" assertion alone would pass just as
    happily if the mount had silently done nothing — and a guard that passes
    because the thing it guards never happened is the shape of check CLAUDE.md
    §1 is about. The "during" assertion is what makes the "after" mean
    something.

    Driven through the context manager rather than through the fixture, so it
    holds on whichever xdist worker happens to run it. A test that depended on
    a sibling test's teardown having already run would pass vacuously on any
    worker that never ran the sibling — which is the same class of defect this
    is fixing.
    """
    before = set(app.openapi()["paths"])
    assert BOOM_PATH not in before, "a previous test left this route behind"

    with boom_route_mounted():
        assert BOOM_PATH in app.openapi()["paths"], (
            "the route was not actually mounted, so the check below proves "
            "nothing")

    assert set(app.openapi()["paths"]) == before
