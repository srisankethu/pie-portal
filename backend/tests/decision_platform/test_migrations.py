"""Migrations apply and reverse cleanly, and match the ORM metadata."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db import Base
from app.domain import models  # noqa: F401  (populate metadata)

BACKEND = Path(__file__).resolve().parents[2]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_then_downgrade(tmp_path, monkeypatch):
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db}"
    # env.py reads settings.DATABASE_URL; point it at the temp DB.
    monkeypatch.setenv("DATABASE_URL", url)
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "DATABASE_URL", url, raising=False)

    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")

    insp = inspect(create_engine(url, future=True))
    tables = set(insp.get_table_names())
    expected = set(Base.metadata.tables.keys())
    assert expected <= tables, f"missing tables after upgrade: {expected - tables}"

    command.downgrade(cfg, "base")
    insp2 = inspect(create_engine(url, future=True))
    remaining = set(insp2.get_table_names()) - {"alembic_version"}
    assert remaining == set(), f"tables left after downgrade: {remaining}"
