"""Keep each sync's own log, so "see the server log" names something reachable.

A pull runs for an hour in a background thread; its account of itself lived in
process stdout and nowhere else. This table holds the lines one run emitted,
against the run, so the screen that reports a failure can also show what led to
it — to somebody with a browser rather than a shell.

Append-only and derived: a re-sync writes a new run with its own lines, and
nothing computes off these. Dropping the table loses history and breaks nothing.

Revision ID: y5runlog
Revises: x4rekey
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "y5runlog"
down_revision = "x4rekey"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_run_logs",
        sa.Column("log_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("sync_run_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("logger", sa.String(128), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_sync_run_logs_organization_id", "sync_run_logs",
                    ["organization_id"])
    op.create_index("ix_sync_run_logs_sync_run_id", "sync_run_logs",
                    ["sync_run_id"])
    op.create_index("ix_sync_run_logs_level", "sync_run_logs", ["level"])
    # The one query this table has: one run's lines, in order, from a cursor.
    op.create_index("ix_sync_run_logs_run_seq", "sync_run_logs",
                    ["sync_run_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_sync_run_logs_run_seq", table_name="sync_run_logs")
    op.drop_index("ix_sync_run_logs_level", table_name="sync_run_logs")
    op.drop_index("ix_sync_run_logs_sync_run_id", table_name="sync_run_logs")
    op.drop_index("ix_sync_run_logs_organization_id", table_name="sync_run_logs")
    op.drop_table("sync_run_logs")
