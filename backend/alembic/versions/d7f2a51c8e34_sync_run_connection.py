"""record which company a sync run pulled from

``sync_runs`` predates an organization being able to read more than one set of
books, so a run says when data last arrived but not *which* company it arrived
for. With three connected, "last synced 2 hours ago" is unreadable: it may mean
all three, or the one nobody was worried about.

Existing rows keep a null connection_id, which is the honest answer for them —
they were pulled when there was only one connection to pull from, and inventing
an id for them would assert something the row never recorded.

Revision ID: d7f2a51c8e34
Revises: c5d81a3e6b47
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op


revision = 'd7f2a51c8e34'
down_revision = 'c5d81a3e6b47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_runs", sa.Column("connection_id", sa.String(64)))
    op.create_index("ix_sync_runs_connection_id", "sync_runs", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_connection_id", table_name="sync_runs")
    op.drop_column("sync_runs", "connection_id")
