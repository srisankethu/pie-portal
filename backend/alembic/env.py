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
from app.observability import logs  # noqa: E402

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


# Alembic's own logging config, and only when this process is alembic.
#
# ``fileConfig`` is not additive. It replaces the root handlers, forces the root
# level to WARN from ``alembic.ini``, and — because ``disable_existing_loggers``
# defaults to True — sets ``disabled = True`` on every logger that already
# exists and is not named in that file. Every ``pie_portal.*`` logger is created
# at import time, so all of them qualify.
#
# The app runs migrations **in-process** at startup (``bootstrap.ensure_schema``)
# and before a deploy cuts over. So this ran inside the API process, after
# ``logs.configure`` had set logging up, and left the server with no app
# logging at all: no INFO, no ``log.exception`` from a failing sync, nothing.
# That is why a sync that ran for an hour and failed had no server log to check
# — the log was not missing, it had been switched off by a migration.
#
# So: if the application has already configured logging, leave it alone. A
# ``python -m alembic`` from a shell has not, and still gets alembic's format.
# ``disable_existing_loggers=False`` there too, because even standing alone
# there is no reason for a migration to silence loggers it did not create.
if config.config_file_name is not None and not logs.is_configured():
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _explain_multiple_heads() -> None:
    """Say what two heads mean and how to end them, before alembic's own error.

    ``upgrade head`` refuses to guess between two heads, which is correct —
    picking one would leave the other branch's table or column missing — but its
    message ("please specify a specific target revision, '<branchname>@head'…")
    describes the API rather than the situation. What actually happened is that
    two branches each added a revision on the same parent and both were merged,
    and the fix is one command.

    Raised from here because both callers reach it: ``deploy/release.sh`` for
    the compose stack, and ``railway.json``'s ``preDeployCommand``, which runs
    ``python -m alembic upgrade head`` with no shell to wrap it in. A pre-deploy
    that stops the container should say why in the log the operator is already
    reading.
    """
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) < 2:
        return
    raise RuntimeError(
        "This checkout has {n} migration heads: {heads}. Two branches each "
        "added a revision on the same parent and both were merged, so there is "
        "no single 'head' to upgrade to — and applying one of them would leave "
        "the other branch's schema missing. Join them with:\n"
        "    cd backend && python -m alembic merge -m 'merge' {heads_args}\n"
        "then verify on an empty database (`make verify`). Do NOT resolve it by "
        "editing down_revision on a released revision — see CLAUDE.md §4.".format(
            n=len(heads), heads=", ".join(heads), heads_args=" ".join(heads)))


def run_migrations_offline() -> None:
    _explain_multiple_heads()
    context.configure(url=_database_url(), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _explain_multiple_heads()
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
