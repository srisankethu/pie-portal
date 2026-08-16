"""Normalize ingested_documents.modified_at onto the UTC line (T9).

The resume cursor is ``max(modified_at)`` — a *string* max, correct only while
every stamp shares one offset format. New writes are rewritten to
``YYYY-MM-DDTHH:MM:SSZ`` by the repository (``clock.utc_stamp``); this brings
the rows that already exist onto the same line, so one table never holds two
dialects of the same instant.

A stamp that cannot be placed on the UTC line — unparseable, or carrying no
offset at all — is deliberately left verbatim: not zeroed, not nulled. Zeroing
it would invent a modification time; nulling it would make the resume check
re-fetch that document's detail on every run forever. Verbatim, the row keeps
resuming by exact-match equality, and ``ingested_high_water`` excludes
non-canonical stamps from the max by shape, so a verbatim leftover can no
longer masquerade as the newest edit.

The parsing is written out literally rather than imported from ``app.clock``:
a migration runs against codebases from months ago (CLAUDE.md §4), and stdlib
is the only import guaranteed to still mean the same thing.

Data-only — no schema change, so no batch mode.

Revision ID: t9f2c7a41d0e
Revises: a9c4e71d20f5
Create Date: 2026-08-16
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = 't9f2c7a41d0e'
down_revision = 'a9c4e71d20f5'
branch_labels = None
depends_on = None


def _utc_stamp(value):
    """``app.clock.utc_stamp`` at this revision, written out literally."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None                      # no offset: unplaceable, keep verbatim
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT ingested_document_id, modified_at FROM ingested_documents "
        "WHERE modified_at IS NOT NULL")).fetchall()
    for row_id, stamp in rows:
        canonical = _utc_stamp(stamp)
        if canonical is not None and canonical != stamp:
            bind.execute(
                sa.text("UPDATE ingested_documents SET modified_at = :stamp "
                        "WHERE ingested_document_id = :row_id"),
                {"stamp": canonical, "row_id": row_id})


def downgrade() -> None:
    # Irreversible on purpose: the offset dress each stamp originally wore is
    # recorded nowhere, and inventing one back would be fabrication. The
    # canonical form is itself a valid stamp; under pre-rewrite code the only
    # cost is that a resumed pull re-fetches details once (verbatim equality
    # against the raw listed stamp fails), which loses no data.
    pass
