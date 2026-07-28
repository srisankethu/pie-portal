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
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def ensure_schema(database_url: Optional[str] = None) -> str:
    """Bring the schema to head. Returns the strategy actually used.

    Prefers Alembic so the migration history is honoured and the version is
    stamped. Falls back to creating the ORM metadata directly only if Alembic
    cannot run at all — better a working app than a failed boot — and says so
    loudly, because that path leaves the database unstamped.
    """
    url = database_url or settings.DATABASE_URL
    try:
        from alembic import command

        command.upgrade(_alembic_config(url), "head")
        return "alembic"
    except Exception:  # noqa: BLE001 - fall back rather than fail to boot
        log.exception("alembic upgrade failed; creating tables from ORM metadata "
                      "instead. Run 'alembic upgrade head' manually to stamp the "
                      "migration version.")
        from .db import Base, engine
        from .domain import models  # noqa: F401  (populate metadata)

        Base.metadata.create_all(engine)
        return "metadata"


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
