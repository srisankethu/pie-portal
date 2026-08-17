#!/usr/bin/env python3
"""The gate's step 6: migrations from nothing, on PostgreSQL — then drift.

Run from ``backend/`` with DATABASE_URL pointing at a DISPOSABLE Postgres
database. The database is wiped (``DROP SCHEMA public CASCADE``), migrated to
head through the real Alembic chain, and then compared against the models —
the same two checks step 5 makes on SQLite, on the dialect production runs.

Exit 0: migrated clean, zero drift. Exit 1: the failure is printed.

A file rather than a heredoc in verify.sh so the quiet run and the
show-the-failure rerun execute the same code — two inline copies is the exact
drift §6's incident is about.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text


def main() -> int:
    url = os.environ["DATABASE_URL"]

    eng = create_engine(url, future=True)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    # ConfigParser interpolation: a literal % (every URL-encoded password has
    # one) must be doubled — same escape as app/bootstrap._alembic_config.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db import Base
    from app.domain import models  # noqa: F401  (populate Base.metadata)

    with eng.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    eng.dispose()

    if diffs:
        print(f"{len(diffs)} drift item(s) between models and the migrated "
              f"Postgres schema:")
        for diff in diffs[:20]:
            print("  ", diff)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
