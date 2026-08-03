"""Is this database new enough for the code reading it?

A deployment that pulls new code and forgets ``alembic upgrade head`` does not
fail at startup. It fails later, on whichever request first touches a column
that does not exist yet, as a bare 500 with an empty body — which names neither
the cause nor the fix, and which a browser console reports as an unexplained
server crash.

Worse, it can fail *selectively*. The query that reads
``sync_runs.connection_id`` only runs once a connection row exists, so an
organization with none looked completely healthy while an organization with one
could not open the same screen. A fault that appears to depend on the data is a
fault nobody diagnoses quickly.

So the check is explicit, cheap, and run once at startup: compare the columns
the models expect against the columns the database has, and say plainly what is
missing and what to run. It reports rather than refuses — a deployment that is
merely behind on one nullable column should still serve every screen that does
not touch it, and an operator who is mid-migration does not need the process
exiting under them.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from .db import Base

log = logging.getLogger("pie_portal.schema")

FIX = "alembic upgrade head"


def missing_columns(engine: Engine) -> dict[str, list[str]]:
    """Columns the models declare that the database does not have.

    Only in that direction. A database with *extra* columns is a database
    mid-rollback or ahead of the code, which is not this function's business
    and is not what breaks a request.
    """
    # ``Base.metadata`` is populated as a side effect of importing the model
    # modules, so without this the check compares the database against an empty
    # table list and cheerfully reports that everything is fine. A check that
    # passes silently because it examined nothing is worse than no check.
    from .domain import models  # noqa: F401

    if not Base.metadata.sorted_tables:
        raise RuntimeError("no models are registered; the schema check would be a no-op")

    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    gaps: dict[str, list[str]] = {}

    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            # A missing table is a database that was never migrated at all —
            # reported whole rather than as a list of every column in it.
            gaps[table.name] = ["<table missing>"]
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        absent = [c.name for c in table.columns if c.name not in have]
        if absent:
            gaps[table.name] = absent
    return gaps


def describe(gaps: dict[str, list[str]]) -> Optional[str]:
    """One sentence an operator can act on, or None when there is nothing wrong."""
    if not gaps:
        return None
    parts = [f"{t} ({', '.join(cols)})" for t, cols in sorted(gaps.items())]
    return (f"This database is behind the code: {'; '.join(parts)}. "
            f"Run `{FIX}` against it and restart. No data is lost.")


def check_at_startup(engine: Engine) -> Optional[str]:
    """Log the gap loudly and hand it back so a screen can show it too.

    Logged at ERROR because it is not a warning: every request that touches one
    of these columns will fail until it is fixed.
    """
    try:
        gaps = missing_columns(engine)
    except Exception:  # noqa: BLE001 — an unreachable database is a different problem
        log.exception("could not inspect the database schema")
        return None

    message = describe(gaps)
    if message:
        log.error("%s", message)
    return message
