"""Fixtures for the decision-platform foundation tests.

Each test gets an isolated database with the schema created from the ORM
metadata: in-memory SQLite by default (fast), or a per-worker Postgres
database when ``PIE_TEST_DATABASE_URL`` is set — see ``tests/dbsupport.py``.
Migration reversibility is verified separately in ``test_migrations.py``.
These tests do NOT load pie-parser.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import dbsupport  # noqa: E402
from app.db import Base  # noqa: E402
from app.domain import models  # noqa: E402,F401  (populate metadata)


@pytest.fixture()
def engine():
    eng = dbsupport.fresh_engine()
    yield eng
    if not dbsupport.TEST_SERVER_URL:
        # In-memory SQLite dies with the engine; dropping keeps teardown
        # explicit. The Postgres database is instead emptied on next acquire —
        # dropping ~100 tables per test would be pure wait.
        Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine) -> Session:
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = maker()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def api_client(engine):
    """A minimal app wired to an isolated DB, with the seeded users signed in.

    Here rather than in one test module because two suites now need the same
    owner/manager/salesperson harness to check the internal endpoints' scoping,
    and a fixture copied into the second file is a fixture that drifts.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import (enquiries, insight, internal, platform_auth,
                              quote_intelligence)
    from app.seed import ensure_org_and_users

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)
    # `insight` too, so a suite can exercise a manager-facing screen end to end
    # rather than only the builder behind it. That gap is not hypothetical — see
    # `test_daily.test_the_morning_read_is_reachable_over_http`.
    app.include_router(insight.router)
    # `quote_intelligence` too, for the same reason: the ERP outcome path is
    # only reachable through its POST, and a suite that exercised it against
    # the service alone would not notice a router that maps a refusal to the
    # wrong status.
    app.include_router(quote_intelligence.router)
    # `enquiries` too. The no-normalisation rule is tested at the function in
    # `test_inbound_line_capture`, but the layer that breaks it is the request
    # body — a `constr(strip_whitespace=True)` never reaches that suite — so
    # the corpus door has to be exercised over HTTP or its one rule is unpinned.
    app.include_router(enquiries.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)
