"""A managed provider's DATABASE_URL must select the driver we actually ship.

This exists because of a deploy that crash-looped: Railway's Neon linking
injected ``postgresql://…``, SQLAlchemy resolved that to its default psycopg2
driver, and the image ships psycopg 3. ``create_engine`` raised
``ModuleNotFoundError: No module named 'psycopg2'`` at *import* time, so the
process died before any logging and the traceback named a library that appeared
nowhere in the configuration.
"""
from sqlalchemy import create_engine

from app.config import _normalize_database_url


def test_a_provider_url_naming_no_driver_selects_psycopg3():
    """The form every managed Postgres hands you must just work.

    Neon, Railway's linking, and Render all emit ``postgresql://``; Heroku
    still emits the older ``postgres://``. None of them can be told to write
    the driver in, and a linked variable is re-injected over any hand-edit.
    """
    for url in ("postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require",
                "postgres://u:p@ep-x.neon.tech/neondb"):
        assert _normalize_database_url(url).startswith("postgresql+psycopg://")


def test_an_explicitly_named_driver_is_left_alone():
    """Naming a driver is a deliberate act; only silence is filled in."""
    for url in ("postgresql+psycopg://u:p@h/db",
                "postgresql+psycopg2://u:p@h/db",
                "postgresql+asyncpg://u:p@h/db",
                "sqlite:////app/backend/data/platform.db"):
        assert _normalize_database_url(url) == url


def test_the_normalized_url_actually_builds_an_engine():
    """The real assertion: the exact failure cannot recur.

    ``create_engine`` imports the DBAPI without connecting, which is precisely
    where the deploy died — so this reproduces the outage without a database.
    """
    engine = create_engine(
        _normalize_database_url("postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require"))
    assert engine.dialect.driver == "psycopg"
    assert engine.dialect.name == "postgresql"
