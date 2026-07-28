"""resumable sync

Records which Zoho documents have already been pulled, so an interrupted sync
resumes instead of starting over, and widens sync_runs to describe the window
that was asked for and what the pull cost. Purely additive.

Revision ID: d5a71c30e8b2
Revises: c93a1e5d47b8
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a71c30e8b2'
down_revision = 'c93a1e5d47b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ingested_documents',
        sa.Column('ingested_document_id', sa.String(length=64), nullable=False),
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('doc_type', sa.String(length=16), nullable=False),
        sa.Column('doc_id', sa.String(length=64), nullable=False),
        sa.Column('modified_at', sa.String(length=64), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('ingested_document_id'),
        sa.UniqueConstraint('organization_id', 'doc_type', 'doc_id',
                            name='uq_ingested_org_type_doc'),
    )
    with op.batch_alter_table('ingested_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ingested_documents_organization_id'),
                              ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ingested_documents_doc_type'),
                              ['doc_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_ingested_documents_doc_id'),
                              ['doc_id'], unique=False)

    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_owner_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('source_owner_at', sa.Date(), nullable=True))

    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('since', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('documents_fetched', sa.Integer(),
                                      nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('documents_resumed', sa.Integer(),
                                      nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('assignments', sa.Integer(),
                                      nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.drop_column('assignments')
        batch_op.drop_column('documents_resumed')
        batch_op.drop_column('documents_fetched')
        batch_op.drop_column('since')

    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_column('source_owner_at')
        batch_op.drop_column('source_owner_id')

    with op.batch_alter_table('ingested_documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ingested_documents_doc_id'))
        batch_op.drop_index(batch_op.f('ix_ingested_documents_doc_type'))
        batch_op.drop_index(batch_op.f('ix_ingested_documents_organization_id'))
    op.drop_table('ingested_documents')
