"""sync runs

Records each ingestion run so the UI can show whether Zoho is connected and
when data last arrived. Purely additive.

Revision ID: c93a1e5d47b8
Revises: b2f4c81d90a7
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa


revision = 'c93a1e5d47b8'
down_revision = 'b2f4c81d90a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sync_runs',
        sa.Column('sync_run_id', sa.String(length=64), nullable=False),
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('customers', sa.Integer(), nullable=False),
        sa.Column('products', sa.Integer(), nullable=False),
        sa.Column('sales_txns', sa.Integer(), nullable=False),
        sa.Column('cost_records', sa.Integer(), nullable=False),
        sa.Column('skipped_count', sa.Integer(), nullable=False),
        sa.Column('skipped_sample', sa.JSON(), nullable=False),
        sa.Column('signals_emitted', sa.Integer(), nullable=False),
        sa.Column('decisions_created', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(length=1024), nullable=True),
        sa.Column('triggered_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('sync_run_id'),
    )
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sync_runs_organization_id'),
                              ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sync_runs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_sync_runs_started_at'), ['started_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sync_runs_started_at'))
        batch_op.drop_index(batch_op.f('ix_sync_runs_status'))
        batch_op.drop_index(batch_op.f('ix_sync_runs_organization_id'))
    op.drop_table('sync_runs')
