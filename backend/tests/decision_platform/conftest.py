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
