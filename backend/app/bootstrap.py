"""Database bootstrap — make the app runnable from a fresh clone.

The schema and the demo users used to be two manual steps (``alembic upgrade
head`` then ``python -m app.seed``). Skipping them — easy on Windows, where the
Makefile is unavailable — left an empty SQLite file and produced
``no such table: users`` on the first login.

This module makes that impossible: it creates the database directory, brings the
schema to head, and seeds the org + demo users (and, outside production, the
demo dataset). Every step is idempotent, so it is safe to run on every startup
and safe to re-run by hand.

Production never auto-migrates and never auto-seeds demo data: schema changes
there are a deliberate, reviewed deploy step, and fabricated customers must
never reach a real read model.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .config import settings

log = logging.getLogger("pie_portal.bootstrap")

BACKEND_DIR = Path(__file__).resolve().parents[1]


def ensure_data_dir(database_url: Optional[str] = None) -> Optional[Path]:
    """Create the parent directory of a SQLite database file if it is missing.

    A fresh clone has no ``backend/data/`` (the .db files are gitignored), and
    SQLite will not create a missing directory — it fails with
    "unable to open database file".
    """
    url = database_url or settings.DATABASE_URL
    if not url.startswith("sqlite"):
        return None
    path = url.split("///", 1)[1] if "///" in url else ""
    if not path or path == ":memory:":
        return None
    db_path = Path(path)
    if not db_path.is_absolute():
        db_path = (BACKEND_DIR / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def _alembic_config(database_url: str):
    """Alembic config built from absolute paths, so it works from any cwd."""
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # Alembic's config is a ConfigParser with %-interpolation, so a literal
    # percent must be doubled or set_main_option raises. Percent signs are
    # routine in real Postgres URLs — every URL-encoded password or socket
    # path has them ("p@ss" is "p%40ss") — which is why this never surfaced
    # on SQLite file paths. env.py reads the value back through interpolation,
    # which undoes the doubling exactly.
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


class SchemaBootstrapError(RuntimeError):
    """The schema could not be brought to head, and guessing would make it worse."""


def ensure_schema(database_url: Optional[str] = None) -> str:
    """Bring the schema to head via Alembic. Returns the action taken.

    **This function used to fall back to ``Base.metadata.create_all`` when
    Alembic raised, and that fallback was the bug.** ``create_all`` builds every
    table and writes no ``alembic_version`` row, so the database ends up holding
    a complete schema that Alembic believes has never been migrated. From then
    on ``alembic upgrade head`` tries to replay the *first* migration and dies
    with "table already exists" — meaning the fix printed in every error message
    could not possibly work. Dropping tables did not help either: the next
    startup hit the same Alembic failure, took the same fallback, and rebuilt the
    same unstamped schema. A recoverable error was being converted into a
    permanently wedged database, once per boot, while the log line describing it
    scrolled past.

    So there is no fallback now. Alembic is the only thing that may create this
    schema, and if it cannot, that is reported rather than papered over.

    The one repair kept is narrow and safe: a database whose tables were created
    outside Alembic *by that old fallback* is stamped to head when its schema
    already matches the models, because those installations exist and cannot
    migrate themselves out of it. If the schema does not match, it is left alone
    and described — a wrong stamp is worse than an unstamped database, since it
    makes every future migration skip work it needed to do.
    """
    url = database_url or settings.DATABASE_URL
    from alembic import command

    from .db import engine as default_engine
    from .migration_state import UNSTAMPED, inspect_database

    engine = default_engine if url == settings.DATABASE_URL else _engine_for(url)
    state = inspect_database(engine)

    if state.state == UNSTAMPED:
        return _adopt_unstamped(engine, url, state)

    command.upgrade(_alembic_config(url), "head")
    return "alembic"


def _engine_for(url: str):
    from sqlalchemy import create_engine

    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, **kwargs)


def _adopt_unstamped(engine, url: str, state) -> str:
    """Stamp a schema that Alembic did not create — only if it is already right.

    The legacy of the removed ``create_all`` fallback. Stamping is a claim that
    every migration up to head has effectively been applied, so it is made only
    when the schema actually matches the models; otherwise the database is left
    exactly as it is, with a message saying what to do.
    """
    from alembic import command

    from .schema_check import missing_columns

    gaps = missing_columns(engine)
    if gaps:
        raise SchemaBootstrapError(
            f"This database has {state.tables} tables but no alembic_version row, "
            f"and its schema does not match the models "
            f"({', '.join(sorted(gaps))} incomplete). It was created outside "
            f"Alembic — almost certainly by the old create_all fallback. Alembic "
            f"cannot upgrade it and stamping it would be a lie. Back up anything "
            f"you need, drop the tables, and run `alembic upgrade head` on the "
            f"empty database.")

    log.warning(
        "database has a complete schema but no alembic_version row (created "
        "outside Alembic); stamping it to head so future migrations apply.")
    command.stamp(_alembic_config(url), "head")
    return "stamped"


def bootstrap(*, with_demo: Optional[bool] = None,
              database_url: Optional[str] = None) -> dict[str, Any]:
    """Create the database, apply the schema, and seed it. Idempotent.

    ``with_demo`` defaults to the configured behaviour: demo data is seeded
    outside production only.
    """
    url = database_url or settings.DATABASE_URL
    summary: dict[str, Any] = {"database_url": _redact(url)}

    db_path = ensure_data_dir(url)
    if db_path is not None:
        summary["database_file"] = str(db_path)

    summary["schema"] = ensure_schema(url)

    from .db import SessionLocal
    from .seed import DEMO_USERS, ensure_org_and_users

    session = SessionLocal()
    try:
        org_id = ensure_org_and_users(session)
        session.commit()
        summary["organization_id"] = org_id
        summary["users"] = [u["email"] for u in DEMO_USERS]

        seed_demo_data = (settings.DEMO_SEED_ON_START and not settings.is_production
                          and settings.ZOHO_SOURCE != "api"
                          if with_demo is None else with_demo)
        if seed_demo_data and settings.is_production:
            summary["demo"] = "refused: production"
        elif seed_demo_data:
            summary["demo"] = _seed_demo(session)
        elif with_demo is None and settings.ZOHO_SOURCE == "api":
            # A live account is configured — fabricated customers must never be
            # (re-)introduced next to real Zoho data. Any left over from before
            # the account was linked are removed on the next sync, not here:
            # this is a read-only bootstrap step and must not delete data.
            summary["demo"] = "disabled: live Zoho source configured"
        else:
            summary["demo"] = "disabled"
    finally:
        session.close()

    # Make today's policy dereferenceable before anything is stamped with it.
    # After the schema step above, so it only ever runs against a database that
    # has the table; one committed transaction per organization, inside
    # ``backfill_all_organizations``, per §4's commit-at-a-natural-boundary.
    # Idempotent — a no-op on every boot after the first, because the version
    # only moves when the policy does.
    from . import threshold_registry

    summary["threshold_versions"] = threshold_registry.backfill_all_organizations(
        SessionLocal)
    return summary


def _seed_demo(session) -> Any:
    """Seed the demo dataset. Never fatal: a demo-data problem must not stop the
    app from starting — sign-in and an empty queue are still useful."""
    from .demo import seed_demo

    try:
        result = seed_demo(session)
        session.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("demo seeding failed; the app will start with no demo data.")
        return {"error": type(exc).__name__}


def _redact(url: str) -> str:
    """Never log database credentials."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<unparseable>"
    if parsed.password:
        return url.replace(parsed.password, "***")
    return url


def main() -> None:
    """CLI: ``python -m app.bootstrap`` — full setup in one command."""
    logging.basicConfig(level=logging.INFO)
    result = bootstrap()
    print("Bootstrap complete:")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
