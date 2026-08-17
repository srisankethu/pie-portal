"""Add login failure tracking for account throttling.

Track consecutive failed login attempts per user to implement exponential
backoff and protect against online password guessing. Fields:
- login_failures_count: number of consecutive failed attempts (resets on success)
- login_failures_last_at: timestamp of the last failure (for backoff calculation)

After 5 failures, the login endpoint blocks requests for 30s * 2^(failures-5)
before allowing retry.

Revision ID: t12login_throttle
Revises: t11a4f8c2d9e1
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "t12login_throttle"
down_revision = "t11a4f8c2d9e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("login_failures_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("login_failures_last_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("login_failures_last_at")
        batch.drop_column("login_failures_count")
