"""Database core for the Commercial Decision Platform.

One primary database (SQLAlchemy 2.0), the single deployable's system of record
for the internal read model, signals, decisions, and outcomes. Dev/test use
SQLite; production uses Postgres via ``DATABASE_URL``. Zoho remains the
authoritative source of commercial facts — this database is a rebuildable
projection plus the platform's own decision/audit state.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(url: str) -> dict:
    # SQLite needs check_same_thread off for the FastAPI threadpool.
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    # Postgres: a bounded pool with loud failure. Sizes and the reasoning
    # behind them live on the settings themselves (config.py) — the short
    # version is 2 uvicorn workers × (5 + 10) = 30 connections worst case,
    # against stock max_connections=100. pre_ping turns a connection the
    # server quietly dropped into a reconnect instead of a request-time error;
    # recycle retires connections before proxy idle cutoffs get there first.
    return {
        "pool_pre_ping": True,
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        # Notice a connection that has died mid-statement. Everything above
        # acts at *checkout*: pre_ping validates a connection before handing it
        # out, recycle retires an idle one. None of them help once a statement
        # is in flight — TCP has no timeout of its own, so if the peer stops
        # answering, ``recv`` blocks forever and the caller simply never
        # returns.
        #
        # That is not hypothetical here. A managed endpoint reached over the
        # public internet sits behind a proxy and a NAT that can drop a flow
        # without sending a FIN, and a first deploy against an empty database
        # hung for ten minutes on exactly this: the migration context was
        # established, then nothing, with no error to log because from the
        # client's side the connection was still open.
        #
        # keepalives turn that into a named failure after roughly a minute and
        # a half (idle 30s, then 5 probes 10s apart), which the pool then
        # replaces. connect_timeout bounds the other end of the same problem:
        # a scale-to-zero endpoint waking up should fail loudly if it cannot
        # be reached, not stall the whole startup.
        "connect_args": {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    }


engine = create_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO, future=True,
                       **_engine_kwargs(settings.DATABASE_URL))


def _same_database(privileged: str, serving: str) -> bool:
    """Whether two URLs name the same database, ignoring who connects as whom.

    The *point* of a second URL is a different role; a different host, port or
    database name is a mistake, and one that would surface as rows that are
    intermittently absent depending on which code path asked. Compared here
    rather than trusted, because the failure is quiet and the check is four
    fields.
    """
    left, right = make_url(privileged), make_url(serving)
    return (left.host, left.port, left.database) == (right.host, right.port,
                                                     right.database)


# The connection that serves HTTP requests, which is not always the one above.
#
# This is a second *engine*, not a second declarative base — the distinction is
# the whole of CLAUDE.md §4's "one engine, one Base" rule, whose stated reason
# is that a second base gives Alembic a metadata object it never sees and half
# the models silently never get migrations. There is still exactly one `Base`,
# one `metadata`, and one URL that Alembic is ever pointed at. What is split is
# *who connects*, and it is split because PostgreSQL row-level security is
# decided by the role: a privileged role ignores every policy, and a
# tenant-scoped one cannot run the migrations or the cross-tenant background
# jobs. See `settings.APP_DATABASE_URL`.
#
# Unset — the default, and what dev, the whole test suite and every existing
# deployment do — this is the same engine and the same sessionmaker, by
# identity rather than by an equivalent copy. A second pool against the same
# database for no reason is 15 more connections per worker against a stock
# `max_connections` of 100.
if settings.APP_DATABASE_URL is None:
    app_engine = engine
else:
    if not settings.APP_DATABASE_URL.startswith("postgresql"):
        raise RuntimeError(
            "APP_DATABASE_URL must be a PostgreSQL URL. Its only purpose is to "
            "serve requests as a role that row-level security applies to, and "
            "no other dialect this codebase supports has policies at all — so "
            "any other value silently buys nothing while splitting the pool.")
    if not _same_database(settings.DATABASE_URL, settings.APP_DATABASE_URL):
        raise RuntimeError(
            "APP_DATABASE_URL and DATABASE_URL name different databases "
            f"({make_url(settings.APP_DATABASE_URL).database!r} vs "
            f"{make_url(settings.DATABASE_URL).database!r}). They are two roles "
            "on one database, never two databases; requests and background "
            "jobs reading different data is not a configuration this "
            "application has.")
    app_engine = create_engine(settings.APP_DATABASE_URL, echo=settings.SQL_ECHO,
                               future=True,
                               **_engine_kwargs(settings.APP_DATABASE_URL))


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """Make SQLite survive one process doing a long write while others read.

    Nothing here applies to Postgres, which handles this properly on its own.

    **WAL.** In the default rollback-journal mode a writer that spills its page
    cache takes an EXCLUSIVE lock and holds it until commit, which blocks every
    reader for the duration. A background sync writing thousands of rows does
    exactly that, so while one ran, ordinary requests — including
    ``/api/health`` — failed with "database is locked". Under WAL, readers never
    block on a writer; they read the last committed snapshot. This is the single
    change that makes a single-file database usable with a background job.

    **busy_timeout.** WAL still serialises writer against writer, and the
    default five seconds is short enough that a normal request colliding with a
    sync's commit gives up. Thirty seconds waits instead of failing; a lock held
    longer than that is a bug worth surfacing rather than absorbing.

    Set per connection because a pooled connection is reused for the life of the
    process. ``journal_mode`` is a property of the database file and persists,
    but issuing it is idempotent and costs one pragma at connect time.
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        # Ordinary durability with WAL: fsync at checkpoints rather than at
        # every commit. The commit-per-phase change in ``ingestion/jobs`` makes
        # commits far more frequent, and FULL would pay a disk sync for each.
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True, class_=Session)

#: Sessions for the request path, when the serving role has been split out —
#: see ``app_engine`` above. ``None`` when it has not, which is the default and
#: what every existing deployment, dev and the test suite run on. Background
#: jobs, CLIs and Alembic keep using ``SessionLocal`` either way, deliberately:
#: they work across tenants and a tenant-scoped role could not.
AppSessionLocal = (None if app_engine is engine else
                   sessionmaker(bind=app_engine, autoflush=False,
                                expire_on_commit=False, future=True,
                                class_=Session))


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a scoped session, committed on success, rolled back on error."""
    # ``SessionLocal`` is read through the module at call time rather than
    # captured, and ``AppSessionLocal`` is None rather than an alias for it.
    #
    # Aliasing looked tidier and was wrong: several tests build a database at a
    # temp path and rebind ``db.SessionLocal`` to a sessionmaker over it. An
    # alias captured at *import* still pointed at the original engine, so the
    # requests those tests made were served from a database the migrations had
    # not touched. It surfaced as `no such table: audit_chain_heads` on login —
    # a schema error from a test whose whole subject is that bootstrap leaves a
    # working schema behind, which is about as misleading as a failure gets.
    #
    # So when nothing is split this resolves to exactly the name it always did,
    # and a rebind is seen. When something is split, a test that wants the
    # serving pool redirected has to say so — which is correct, because at that
    # point there really are two.
    session = (AppSessionLocal or SessionLocal)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
