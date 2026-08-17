"""One place a test gets a disposable database, on either backend.

Thirty-six test modules used to build the same three-line in-memory SQLite
engine by hand — the exact/near-exact redundancy CLAUDE.md §2 describes, and
the reason the suite could not run against Postgres: the choice of backend was
copied into every file instead of decided once. This module is that decision.

Two modes, chosen by ``PIE_TEST_DATABASE_URL``:

**Unset (the default).** ``fresh_engine()`` returns exactly what every module
built inline: a private in-memory SQLite database with the full schema. Fast,
zero-infrastructure, and unchanged behaviour for `make verify`.

**Set to a Postgres server URL** (e.g.
``postgresql+psycopg://pie@/pie_test?host=/tmp/pg&port=5544``): the same call
returns an engine onto a per-worker Postgres database — the production
dialect. The schema is created once per worker; between tests every table is
truncated with ``RESTART IDENTITY``, so a test that assumes ids start at 1
behaves the same on both backends. ``scripts/pg_sandbox.sh`` starts a
disposable server for this.

The Postgres engine is shared within a worker, which one behaviour depends on:
a test must not run schema-changing DDL against it — the damage would outlive
the test. A test that needs a deliberately broken schema builds its own
private SQLite engine and says why (see ``test_connection_check_and_schema``).
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain import models  # noqa: F401  (imported so Base.metadata is complete)

#: The Postgres server the suite should run against, or "" for SQLite.
TEST_SERVER_URL = os.environ.get("PIE_TEST_DATABASE_URL", "")

_pg_engine = None
_truncate_sql: str | None = None


def worker_id() -> str:
    """This xdist worker's name, or "main" for a serial run."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


def sibling_database_url(server_url: str, suffix: str) -> str:
    """The same server, a database named ``<base>_<suffix>``."""
    url = make_url(server_url)
    return str(url.set(database=f"{url.database}_{suffix}"))


def ensure_database(database_url: str) -> None:
    """Create the named Postgres database if it is missing. Idempotent.

    Workers never race here: each derives a name containing its own worker id.
    """
    url = make_url(database_url)
    maint = create_engine(url.set(database="postgres"),
                          isolation_level="AUTOCOMMIT", future=True)
    try:
        with maint.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                  {"n": url.database}).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        maint.dispose()


def wipe_schema(database_url: str) -> None:
    """Drop and recreate ``public``, so the next migration starts from nothing.

    For the per-worker *application* database only — the one ``bootstrap()``
    brings to head through the real Alembic chain. A previous run's tables (or
    another checkout's revision) must not leak into this one.
    """
    eng = create_engine(database_url, future=True)
    try:
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        eng.dispose()


def fresh_engine():
    """An engine over a complete, empty schema — ready to use.

    SQLite mode: a new private in-memory database per call (exactly the old
    inline idiom). Postgres mode: this worker's fixture database, emptied.
    """
    if not TEST_SERVER_URL:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool, future=True)
        Base.metadata.create_all(eng)
        # Enforce foreign keys, exactly as Postgres does. SQLite leaves them
        # off by default, and years of that let fixtures write child rows with
        # no parent — data production could never hold, first caught the day
        # the suite ran on Postgres. One backend must never be the lenient
        # one. (StaticPool = one connection for the engine's life, so a single
        # pragma here holds for every session.)
        with eng.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        return eng
    return _fresh_postgres()


def _fresh_postgres():
    global _pg_engine, _truncate_sql
    if _pg_engine is None:
        url = sibling_database_url(TEST_SERVER_URL, f"fix_{worker_id()}")
        ensure_database(url)
        wipe_schema(url)
        _pg_engine = create_engine(url, future=True)
        Base.metadata.create_all(_pg_engine)
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        _truncate_sql = f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"
        return _pg_engine
    # Empty every table. RESTART IDENTITY keeps id sequences at 1, like a
    # fresh SQLite file; CASCADE spares us ordering the FK graph by hand.
    #
    # The two guards around the TRUNCATE exist because per-test SQLite
    # databases forgave what a shared Postgres database does not. A session a
    # test never closed — most easily a daemon thread that outlives its test —
    # sits "idle in transaction" holding a lock, and the first run of this
    # suite on Postgres hung a worker on exactly that for as long as we let
    # it. dispose() only closes checked-in connections, so: terminate every
    # OTHER backend on this throwaway database, and cap the lock wait so a new
    # leak fails in seconds with a named cause instead of hanging a worker.
    _pg_engine.dispose()
    with _pg_engine.begin() as conn:
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
        conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        conn.execute(text(_truncate_sql))
    return _pg_engine
