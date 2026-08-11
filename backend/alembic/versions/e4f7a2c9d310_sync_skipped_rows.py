"""Keep every skipped row of a sync, not the first twenty.

``sync_runs.skipped_count`` said 1,304 and ``sync_runs.skipped_sample`` held 20
of them; the other 1,284 existed only in the running job's memory and were gone
the moment it ended. The screen was honest about the truncation — "first 20 of
1304" — but honest about a gap nobody could work: which 1,284, on which
documents, from which supplier, and is it one discontinued item or four hundred?
The worklist in ``sync_runs.unresolved`` answers the shape of it, capped at 40
distinct problems; this table is the underlying evidence, uncapped, so it can be
exported and reconciled line by line against Zoho.

The context is flattened into real columns rather than kept as JSON because the
destination is a spreadsheet: a CSV assembled by walking a JSON dict has a column
set that depends on which rows happened to carry which keys.

``skipped_sample`` is deliberately left in place. It is what the sync status card
reads without a second request, and removing a released column to save twenty
JSON rows would be a schema change for no gain.

Revision ID: e4f7a2c9d310
Revises: d7b3e91c4f05
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "e4f7a2c9d310"
down_revision = "d7b3e91c4f05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_skipped_rows",
        sa.Column("skip_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("sync_run_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=False),
        sa.Column("missing_id", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("sku", sa.String(length=128), nullable=True),
        sa.Column("document", sa.String(length=128), nullable=True),
        sa.Column("document_date", sa.String(length=32), nullable=True),
        sa.Column("party", sa.String(length=255), nullable=True),
        sa.Column("qty", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("line_value", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("fix", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("skip_id"),
    )
    op.create_index("ix_sync_skipped_rows_organization_id", "sync_skipped_rows",
                    ["organization_id"])
    op.create_index("ix_sync_skipped_rows_sync_run_id", "sync_skipped_rows",
                    ["sync_run_id"])
    op.create_index("ix_sync_skipped_rows_connection_id", "sync_skipped_rows",
                    ["connection_id"])
    op.create_index("ix_sync_skipped_rows_code", "sync_skipped_rows", ["code"])
    op.create_index("ix_sync_skipped_rows_missing_id", "sync_skipped_rows",
                    ["missing_id"])
    # The one query this table has: every row of one run, in the order the pull
    # met them.
    op.create_index("ix_sync_skips_run_seq", "sync_skipped_rows",
                    ["sync_run_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_sync_skips_run_seq", table_name="sync_skipped_rows")
    op.drop_index("ix_sync_skipped_rows_missing_id", table_name="sync_skipped_rows")
    op.drop_index("ix_sync_skipped_rows_code", table_name="sync_skipped_rows")
    op.drop_index("ix_sync_skipped_rows_connection_id", table_name="sync_skipped_rows")
    op.drop_index("ix_sync_skipped_rows_sync_run_id", table_name="sync_skipped_rows")
    op.drop_index("ix_sync_skipped_rows_organization_id", table_name="sync_skipped_rows")
    op.drop_table("sync_skipped_rows")
