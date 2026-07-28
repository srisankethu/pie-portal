"""bill line discount

cost_records.unit_cost was being written from the bill line's raw ``rate``,
ignoring any line-item discount. It now stores the effective, post-discount
unit cost, and this migration adds two nullable audit columns alongside it:

- ``rate``            the original, pre-discount list rate
- ``discount_percent``  the discount applied, for traceability

Both are nullable because a row written before this fix has neither value
available locally — Zoho's discount was never stored, only the (wrong)
resulting cost. Historical rows are corrected by re-fetching the bill from
Zoho (a full re-sync), not by a migration — there is nothing here to compute
a correction from. See docs/zoho-setup.md.

Revision ID: f2a9c4b6d1e7
Revises: d5a71c30e8b2
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2a9c4b6d1e7'
down_revision = 'd5a71c30e8b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('cost_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rate', sa.Numeric(18, 4), nullable=True))
        batch_op.add_column(sa.Column('discount_percent', sa.Numeric(9, 4), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cost_records', schema=None) as batch_op:
        batch_op.drop_column('discount_percent')
        batch_op.drop_column('rate')
