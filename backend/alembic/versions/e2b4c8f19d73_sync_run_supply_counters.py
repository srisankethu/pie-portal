"""Record what the supply stage read, and what a pull could not resolve.

Five additive columns on ``sync_runs``. Four are counters the sync report has
maintained since the supply stage was added but had nowhere to persist, so a run
that read four hundred suppliers reported nothing about them — which reads, from
the screen, as suppliers not being read at all.

``unresolved`` holds the skips folded by what is actually missing: one row per
thing to fix, with how many document lines it blocks and where to find it.

Purely additive and reversible. Existing rows get 0 and an empty list, which is
honest — those runs did not record it, and backfilling a number nobody measured
would be inventing history.

Revision ID: e2b4c8f19d73
Revises: d1e93a7c4506
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "e2b4c8f19d73"
down_revision = "d1e93a7c4506"
branch_labels = None
depends_on = None

_COUNTERS = ("vendors", "stock_snapshots", "payments", "purchase_orders")


def upgrade() -> None:
    for name in _COUNTERS:
        op.add_column("sync_runs",
                      sa.Column(name, sa.Integer(), nullable=False,
                                server_default="0"))
    # Not nullable, with an empty list as the server default: every other JSON
    # column on this table is a collection that is empty rather than absent, and
    # a reader that has to distinguish "no unresolved rows" from NULL will get
    # it wrong somewhere.
    op.add_column("sync_runs",
                  sa.Column("unresolved", sa.JSON(), nullable=False,
                            server_default="[]"))


def downgrade() -> None:
    op.drop_column("sync_runs", "unresolved")
    for name in reversed(_COUNTERS):
        op.drop_column("sync_runs", name)
