"""Record a reissued tenant data key — who replaced it, when, and why.

A DEK that no longer unwraps under the current ``CREDENTIAL_ENCRYPTION_KEY``
cannot be repaired: the plaintext key is gone unless the previous master key is
restored. ``keys.reissue`` is the way out of that state, and it costs whatever
was written under the old key — so it leaves a record here, beside the
destruction tombstone, for the same reason that one exists. An operation whose
consequence is "these payload records are now unreadable" must be answerable
from the database rather than from somebody's shell history.

Nullable and not backfilled: NULL means this key was never reissued, which is
true of every row written before this column existed.

Revision ID: x4rekey
Revises: w3plan
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "x4rekey"
down_revision = "w3plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_keys") as batch:
        batch.add_column(sa.Column("reissued_at", sa.DateTime(timezone=True),
                                   nullable=True))
        batch.add_column(sa.Column("reissued_by_user_id", sa.String(64),
                                   nullable=True))
        batch.add_column(sa.Column("reissue_reason", sa.String(512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenant_keys") as batch:
        batch.drop_column("reissue_reason")
        batch.drop_column("reissued_by_user_id")
        batch.drop_column("reissued_at")
