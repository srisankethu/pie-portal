"""users password_changed_at — retire sessions on a credential change

Nullable, and null means "never changed", which is the correct reading for every
row that exists when this runs: those accounts still hold the password they were
seeded or issued with, so there is nothing for a token to predate. Backfilling a
timestamp would have invalidated every live session on deploy for no gain.

Revision ID: 0e8d9299b0c7
Revises: d4e7b18f2a95
Create Date: 2026-08-09 06:53:49.920652
"""
from alembic import op
import sqlalchemy as sa


revision = '0e8d9299b0c7'
down_revision = 'd4e7b18f2a95'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_changed_at')
