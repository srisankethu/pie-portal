"""The event log: what happened, in the order the platform read it.

One new table, append-only. Derived and not canonical — Zoho is the system of
record and the platform never writes back, so a complete re-sync rebuilds this
from nothing. That is what keeps it honest: if the log and Zoho ever disagree,
Zoho is right.

``seq`` is an autoincrementing integer primary key rather than a uuid, because
the log's identity *is* its order. It is a single global sequence, which is
still a total order inside any one organization and cannot drift from a second
counter the way per-organization sequences can.

Purely additive: no existing table is touched, and existing rows are unaffected.
Empty until the next sync — a log is not backfilled from a read model, which
could only ever record what survived.

Revision ID: d9a4c60e7b18
Revises: c7e2b41a8f36
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "d9a4c60e7b18"
down_revision = "c7e2b41a8f36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_events",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("connector", sa.String(length=32), nullable=True),
        sa.Column("connection_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_doc_type", sa.String(length=32), nullable=False),
        sa.Column("source_doc_id", sa.String(length=128), nullable=False),
        sa.Column("source_line_id", sa.String(length=128), nullable=True),
        sa.Column("source_modified_at", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("seq"),
    )
    with op.batch_alter_table("business_events", schema=None) as batch_op:
        # The supersede lookup: asked once per document on every re-read.
        batch_op.create_index("ix_event_org_doc",
                              ["organization_id", "source_doc_type",
                               "source_doc_id"], unique=False)
        # The replay scan, in order.
        batch_op.create_index("ix_event_org_seq",
                              ["organization_id", "seq"], unique=False)
        # The reducer scan: one state's events over a window.
        batch_op.create_index("ix_event_org_type_on",
                              ["organization_id", "event_type", "occurred_on"],
                              unique=False)
        batch_op.create_index(batch_op.f("ix_business_events_connection_id"),
                              ["connection_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_events_event_type"),
                              ["event_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_events_occurred_on"),
                              ["occurred_on"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_events_organization_id"),
                              ["organization_id"], unique=False)


def downgrade() -> None:
    op.drop_table("business_events")
