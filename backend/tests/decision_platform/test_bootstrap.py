"""Bootstrap: a fresh clone must run without a manual migrate/seed step.

Regression cover for the reported failure — starting the app against a brand-new
database produced ``no such table: users`` on the first login, because
``alembic upgrade head`` and ``python -m app.seed`` were separate manual steps
(easy to miss on Windows, where the Makefile is unavailable).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, select

from app.bootstrap import bootstrap, ensure_data_dir, ensure_schema
from app.config import settings
from app.domain import models


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Point the app at a brand-new SQLite file inside a directory that does
    not exist yet — exactly the state of a fresh clone."""
    url = f"sqlite:///{tmp_path}/data/nested/platform.db"
    monkeypatch.setattr(settings, "DATABASE_URL", url, raising=False)

    import app.db as db_mod

    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(db_mod, "engine", engine, raising=False)
    monkeypatch.setattr(db_mod, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False,
                                     expire_on_commit=False, future=True),
                        raising=False)
    return url


def test_ensure_data_dir_creates_missing_sqlite_directory(tmp_path, monkeypatch):
    """SQLite will not create a missing directory; it fails with
    'unable to open database file'. Bootstrap must create it."""
    url = f"sqlite:///{tmp_path}/does/not/exist/platform.db"
    monkeypatch.setattr(settings, "DATABASE_URL", url, raising=False)
    path = ensure_data_dir(url)
    assert path is not None and path.parent.is_dir()


def test_ensure_data_dir_ignores_non_sqlite_urls():
    assert ensure_data_dir("postgresql+psycopg://u:p@host/db") is None
    assert ensure_data_dir("sqlite://") is None            # in-memory


def test_schema_is_created_by_alembic_and_stamped(fresh_db):
    ensure_data_dir(fresh_db)
    assert ensure_schema(fresh_db) == "alembic"
    tables = set(inspect(create_engine(fresh_db, future=True)).get_table_names())
    assert "users" in tables                                # the reported failure
    assert "alembic_version" in tables                      # migration history honoured
    assert {"decisions", "signals", "ai_call_logs"} <= tables


def test_bootstrap_seeds_users_so_login_can_resolve_them(fresh_db):
    summary = bootstrap(with_demo=False, database_url=fresh_db)
    assert summary["organization_id"] == settings.DEFAULT_ORG_ID

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        # the exact query platform_auth.login runs
        user = session.scalar(
            select(models.User).where(models.User.email == "r.nair@sanketh.in"))
        assert user is not None and user.active
        assert session.query(models.User).count() == 3      # all three roles
    finally:
        session.close()


def test_bootstrap_is_idempotent(fresh_db):
    bootstrap(with_demo=False, database_url=fresh_db)
    bootstrap(with_demo=False, database_url=fresh_db)       # must not raise or duplicate

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        assert session.query(models.User).count() == 3
    finally:
        session.close()


def test_bootstrap_with_demo_produces_decisions(fresh_db):
    summary = bootstrap(with_demo=True, database_url=fresh_db)
    assert "error" not in str(summary["demo"])

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        assert session.query(models.Decision).count() > 0
        assert session.query(models.Customer).count() > 0
    finally:
        session.close()


def test_production_refuses_demo_seeding(fresh_db, monkeypatch):
    """Fabricated customers must never reach a real read model."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    summary = bootstrap(with_demo=True, database_url=fresh_db)
    assert summary["demo"] == "refused: production"

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        assert session.query(models.Customer).count() == 0
    finally:
        session.close()


def test_database_credentials_are_never_logged(fresh_db, monkeypatch):
    from app.bootstrap import _redact

    assert "hunter2" not in _redact("postgresql+psycopg://user:hunter2@host:5432/db")
    assert "***" in _redact("postgresql+psycopg://user:hunter2@host:5432/db")


def test_app_startup_bootstraps_so_first_login_succeeds(tmp_path, monkeypatch):
    """End-to-end: a brand-new database + nothing but app startup ⇒ login works.

    This is the reported bug, asserted at the level the user hit it.
    """
    url = f"sqlite:///{tmp_path}/data/platform.db"
    monkeypatch.setenv("PIE_WARM", "0")
    monkeypatch.setattr(settings, "DATABASE_URL", url, raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "DEMO_SEED_ON_START", False)

    import app.db as db_mod
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    monkeypatch.setattr(db_mod, "engine", engine, raising=False)
    monkeypatch.setattr(db_mod, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False,
                                     expire_on_commit=False, future=True),
                        raising=False)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:            # 'with' runs the startup lifespan
        r = client.post("/api/v1/auth/login",
                        json={"email": "r.nair@sanketh.in", "password": "demo"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "SALESPERSON"


def test_live_zoho_source_disables_auto_demo_seed(fresh_db, monkeypatch):
    """A live account means fabricated customers must never appear at all, not
    even transiently between linking Zoho and the first sync."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    summary = bootstrap(database_url=fresh_db)   # with_demo=None: configured default
    assert summary["demo"] == "disabled: live Zoho source configured"

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        assert session.query(models.Customer).count() == 0
    finally:
        session.close()


def test_explicit_with_demo_still_wins_over_a_live_source(fresh_db, monkeypatch):
    """An explicit override (e.g. a test, or a deliberate reseed) is not
    silently defeated by the live-source gate."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    summary = bootstrap(with_demo=True, database_url=fresh_db)
    assert "error" not in str(summary["demo"])

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        assert session.query(models.Customer).count() > 0
    finally:
        session.close()
