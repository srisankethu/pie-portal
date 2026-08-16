"""Supersession on value events, so a re-run corrects instead of accumulating.

A detection run is meant to be repeatable after every sync. It was not: the
event key included the final snapshot's id, so re-pricing a line minted a fresh
key and left the earlier, smaller claim standing beside the new one. Three runs
over one line booked three overlapping amounts for one price movement.

The key is now stable over (fact, class, quote line), which makes a re-run land
on the same row — and that in turn needs a way to say "this amount replaced that
one", or the first measurement would be frozen and a later reprice never
reflected. Hence this column. The superseded row stays; every rollup filters
``superseded_at IS NULL``.

A separate revision rather than an edit to c4f19a6b02de, which is the rule in
CLAUDE.md §4 and holds even for an unmerged branch: an edited migration means two
databases that ran "the same" revision have different schemas, and a second
revision costs nothing next to that.

Revision ID: d5a2e7c31b84
Revises: c4f19a6b02de
"""
from alembic import op
import sqlalchemy as sa

revision = "d5a2e7c31b84"
down_revision = "c4f19a6b02de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "value_events",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_value_events_org_superseded",
        "value_events",
        ["organization_id", "superseded_at"],
    )
    # The plain unique constraint has to go: correcting a measurement writes a
    # second row carrying the same key, and the constraint would refuse exactly
    # the correction this revision exists to allow. Replaced by a unique index
    # over the live rows only, so "one live claim per fact" still holds.
    #
    # batch_alter_table because SQLite cannot drop a named constraint in place —
    # it rebuilds the table. On PostgreSQL this is an ordinary DROP CONSTRAINT.
    with op.batch_alter_table("value_events", schema=None) as batch:
        batch.drop_constraint("uq_value_event_key", type_="unique")
    op.create_index(
        "uq_value_event_key_live",
        "value_events",
        ["organization_id", "event_key"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_value_event_key_live", table_name="value_events")
    with op.batch_alter_table("value_events", schema=None) as batch:
        batch.create_unique_constraint(
            "uq_value_event_key", ["organization_id", "event_key"])
    op.drop_index("ix_value_events_org_superseded", table_name="value_events")
    op.drop_column("value_events", "superseded_at")
