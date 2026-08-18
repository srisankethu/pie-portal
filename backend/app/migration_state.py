"""Where this database sits relative to the migration history — as facts.

Every diagnosis in this area used to be a guess. The client turned any
undescribed 500 into "the usual cause is a pending `alembic upgrade head`",
which is a plausible sentence and not an observation: it was printed just as
readily when the cause was something else entirely, and it sent operators to run
a command that could not have helped.

This module answers the question instead of guessing at it. One cheap read of
``alembic_version`` plus the revision graph on disk gives four distinguishable
states, and they need *different* fixes — which is the whole reason to tell them
apart:

``EMPTY``       no tables at all. Run the migrations; nothing to lose.
``UNSTAMPED``   tables exist but ``alembic_version`` does not. Something built
                the schema outside Alembic. ``upgrade head`` will fail with
                "table already exists" until the database is stamped, so telling
                an operator to run it is actively wrong.
``BEHIND``      stamped, but older than head. The genuine "run the migration"
                case, and the only one where that advice is correct.
``UNKNOWN_REV`` stamped with a revision this codebase does not contain. Usually
                a rollback to older code after a newer migration ran, or a
                database from another branch. Upgrading cannot fix it and may
                make it worse.
``CURRENT``     at head.

Read-only. Nothing here migrates, stamps or repairs anything — it is the
diagnosis, and the caller decides what to do about it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("pie_portal.migration_state")

BACKEND_DIR = Path(__file__).resolve().parents[1]

EMPTY = "EMPTY"
UNSTAMPED = "UNSTAMPED"
BEHIND = "BEHIND"
UNKNOWN_REV = "UNKNOWN_REV"
CURRENT = "CURRENT"


@dataclass(frozen=True)
class MigrationState:
    state: str
    current: Optional[str]          # revision the database claims to be at
    head: Optional[str]             # revision this codebase ends at
    pending: tuple[str, ...] = ()   # revisions between the two, oldest first
    tables: int = 0

    @property
    def healthy(self) -> bool:
        return self.state == CURRENT

    @property
    def summary(self) -> str:
        """One sentence naming the state, the fix, and nothing invented."""
        if self.state == CURRENT:
            return f"Database is at head ({self.head})."
        if self.state == EMPTY:
            return ("This database is empty. Run `alembic upgrade head` to create "
                    "the schema.")
        if self.state == UNSTAMPED:
            return (
                f"This database has {self.tables} tables but no `alembic_version` "
                f"row, so its schema was created outside Alembic. `alembic upgrade "
                f"head` will fail with \"table already exists\" until it is "
                f"stamped. If the schema really is current, run `alembic stamp "
                f"head`; if you are not sure, recreate the database and migrate "
                f"it properly.")
        if self.state == UNKNOWN_REV:
            return (
                f"This database is stamped {self.current!r}, which is not a "
                f"revision in this codebase (head is {self.head}). It was most "
                f"likely migrated by newer code, or by another branch. Do not "
                f"upgrade — deploy the code that owns that revision, or restore a "
                f"database matching this one.")
        return (
            f"This database is {len(self.pending)} migration(s) behind: it is at "
            f"{self.current}, head is {self.head}. Run `alembic upgrade head` and "
            f"restart. No data is lost.")

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "current_revision": self.current,
            "head_revision": self.head,
            "pending_count": len(self.pending),
            "pending": list(self.pending),
            "healthy": self.healthy,
            "summary": self.summary,
        }


def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg)


def head_revision() -> Optional[str]:
    """The revision this codebase ends at, read from the scripts on disk."""
    try:
        return _script_directory().get_current_head()
    except Exception:  # noqa: BLE001
        log.exception("could not read the migration scripts")
        return None


def stamped_revision(engine: Engine) -> Optional[str]:
    """What the database says it is, or None when it has never been stamped."""
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        return None
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    return row[0] if row else None


def inspect_database(engine: Engine) -> MigrationState:
    """Classify this database against the migration history. Read-only."""
    inspector = inspect(engine)
    tables = [t for t in inspector.get_table_names() if t != "alembic_version"]
    head = head_revision()
    current = stamped_revision(engine)

    if current is None:
        # An empty database and one built by ``create_all`` are both unstamped,
        # and they are not the same problem: one needs migrating, the other
        # cannot be migrated until somebody decides whether to stamp or rebuild.
        state = EMPTY if not tables else UNSTAMPED
        return MigrationState(state=state, current=None, head=head,
                              tables=len(tables))

    if current == head:
        return MigrationState(state=CURRENT, current=current, head=head,
                              tables=len(tables))

    try:
        script = _script_directory()
        pending = tuple(reversed([
            rev.revision for rev in script.iterate_revisions(head, current)
            if rev.revision != current
        ]))
    except Exception:  # noqa: BLE001 — the revision is not in this graph
        return MigrationState(state=UNKNOWN_REV, current=current, head=head,
                              tables=len(tables))

    if not pending:
        # Reachable when the stamp is *ahead* of head — the database has run a
        # migration this code does not contain.
        return MigrationState(state=UNKNOWN_REV, current=current, head=head,
                              tables=len(tables))

    return MigrationState(state=BEHIND, current=current, head=head,
                          pending=pending, tables=len(tables))
