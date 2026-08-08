"""Fixtures for the decision-platform foundation tests.

Each test gets an isolated in-memory SQLite database with the schema created
from the ORM metadata (fast). Migration reversibility is verified separately in
``test_migrations.py``. These tests do NOT load pie-parser.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db import Base  # noqa: E402
from app.domain import models  # noqa: E402,F401  (populate metadata)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool, future=True)
    Base.metadata.create_all(eng)
    yield eng
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
def api_client():
    """A minimal app wired to an isolated DB, with the seeded users signed in.

    Here rather than in one test module because two suites now need the same
    owner/manager/salesperson harness to check the internal endpoints' scoping,
    and a fixture copied into the second file is a fixture that drifts.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import internal, platform_auth
    from app.seed import ensure_org_and_users

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool, future=True)
    Base.metadata.create_all(eng)
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)
