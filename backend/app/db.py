"""Database core for the Commercial Decision Platform.

One primary database (SQLAlchemy 2.0), the single deployable's system of record
for the internal read model, signals, decisions, and outcomes. Dev/test use
SQLite; production uses Postgres via ``DATABASE_URL``. Zoho remains the
authoritative source of commercial facts — this database is a rebuildable
projection plus the platform's own decision/audit state.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(url: str) -> dict:
    # SQLite needs check_same_thread off for the FastAPI threadpool.
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO, future=True,
                       **_engine_kwargs(settings.DATABASE_URL))

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
