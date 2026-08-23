"""One process at a time ticks the schedule, across processes rather than within one.

`ingestion/scheduler` reasoned that its tick was safe because "this deployment
is one uvicorn process"; the image has shipped `--workers 2` throughout, so two
threads have always ticked, starting together and landing at nearly the same
instant. Both can read "no sync running" and both queue a pull of the same
books. This table is the lease that makes one of them the decider.

Derived and disposable: dropping it costs the schedule a couple of minutes of
confusion and nothing else.

Revision ID: b8lease
Revises: a7queue
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "b8lease"
down_revision = "a7queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "process_leases",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("holder", sa.String(128), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_process_leases_expires_at", "process_leases",
                    ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_process_leases_expires_at", table_name="process_leases")
    op.drop_table("process_leases")
