"""ws3 ai call telemetry

Adds ``ai_call_logs``: one row per interpretation decision point (including
cache hits and up-front suppressions), carrying status, failure reason, latency,
token usage and estimated cost. Purely additive — no existing table is touched.

Revision ID: b2f4c81d90a7
Revises: 41730a334a54
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2f4c81d90a7'
down_revision = '41730a334a54'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_call_logs',
        sa.Column('ai_call_log_id', sa.String(length=64), nullable=False),
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('decision_type', sa.String(length=48), nullable=False),
        sa.Column('subject_entity_id', sa.String(length=64), nullable=True),
        sa.Column('recipient_role', sa.String(length=32), nullable=True),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('prompt_version', sa.String(length=32), nullable=False),
        sa.Column('context_hash', sa.String(length=64), nullable=False),
        sa.Column('ai_status', sa.String(length=16), nullable=False),
        sa.Column('provider_called', sa.Boolean(), nullable=False),
        sa.Column('cache_hit', sa.Boolean(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('estimated_cost_usd', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('failure_reason', sa.String(length=32), nullable=True),
        sa.Column('corrections', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('ai_call_log_id'),
    )
    with op.batch_alter_table('ai_call_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_call_logs_organization_id'),
                              ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_call_logs_decision_type'),
                              ['decision_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_call_logs_subject_entity_id'),
                              ['subject_entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_call_logs_context_hash'),
                              ['context_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_call_logs_ai_status'),
                              ['ai_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_call_logs_failure_reason'),
                              ['failure_reason'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_call_logs_created_at'),
                              ['created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ai_call_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_failure_reason'))
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_ai_status'))
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_context_hash'))
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_subject_entity_id'))
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_decision_type'))
        batch_op.drop_index(batch_op.f('ix_ai_call_logs_organization_id'))
    op.drop_table('ai_call_logs')
