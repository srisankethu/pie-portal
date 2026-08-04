"""Alembic environment — wired to the app's metadata and DATABASE_URL."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the app importable when alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.domain import models  # noqa: E402,F401  (import populates metadata)

config = context.config


def _database_url() -> str:
    """The database these migrations run against.

    Precedence matters, and getting it wrong is how a migration silently edits
    the wrong database. This module used to overwrite ``sqlalchemy.url`` with
    ``settings.DATABASE_URL`` unconditionally, which meant a caller doing
    ``Config.set_main_option("sqlalchemy.url", other_db)`` — the documented way
    to point Alembic somewhere else, and what ``app.bootstrap`` does — was
    ignored. Alembic then migrated whatever ``DATABASE_URL`` happened to say
    while reporting success for the database the caller asked about. In testing
    this stamped a live development database to head without applying anything
    to it, leaving it claiming a schema it did not have.

    So: an explicitly supplied URL wins, because supplying one is a deliberate
    act. Otherwise the application's own setting is used, which keeps
    ``alembic upgrade head`` from a shell operating on exactly the database the
    app opens. ``alembic.ini`` deliberately carries no ``sqlalchemy.url`` line,
    so there is no placeholder that could win by accident.
    """
    explicit = config.get_main_option("sqlalchemy.url", None)
    return explicit or settings.DATABASE_URL


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # render_as_batch makes ALTERs work on SQLite too.
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
