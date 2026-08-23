"""A named, expiring lease so one process at a time does cluster-wide work.

The deployment runs two uvicorn workers, so every worker starts its own
auto-sync scheduler thread and both tick in the same minute. ``a7syncguard``
made that harmless — the partial unique indexes on ``sync_runs`` let only one
run start — but harmless is not free: the loser spends a query and writes a
confusing log line, every tick, forever.

What this is *not*: sufficient on its own for the hash-chained audit log that
follows. That needs a single writer, and an expiring lease with no fence token
cannot promise one — a leader frozen past its expiry can wake believing it still
leads. ``app/lease.py`` says so at length; read it before building on this.

``process_leases`` is the row behind ``app/lease.py``: one row per lease name,
holder and expiry, claimed by a conditional UPDATE whose rowcount decides. A
table rather than ``pg_try_advisory_lock`` because dev and test run SQLite and
an advisory lock does not exist there — the production primitive would be the
one nothing tests.

No ``organization_id``: this is about processes, not tenants. Scope, where a
lease needs any, lives in its name.

Revision ID: b8lease
Revises: a7syncguard
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "b8lease"
down_revision = "a7syncguard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns written out literally rather than imported from the models (§4):
    # this runs against schemas from months ago, and the models describe today.
    op.create_table(
        "process_leases",
        # The lease *is* the row, so the name is the primary key and the
        # database is what makes "one holder" true rather than a convention.
        sa.Column("name", sa.String(length=64), nullable=False),
        # Nullable: a lease may exist and be free. Widths match the models.
        sa.Column("holder", sa.String(length=128), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    # Safe to drop outright: the table holds no history and no tenant data, and
    # a lease that vanishes is a lease nobody holds — the code that reads it is
    # gone in the same rollback, and the work it gated goes back to running on
    # every worker, which is where it was before this revision.
    op.drop_table("process_leases")
