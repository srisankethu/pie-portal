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
    }


engine = create_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO, future=True,
                       **_engine_kwargs(settings.DATABASE_URL))


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


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a scoped session, committed on success, rolled back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
