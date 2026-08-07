"""confirmed customer code mappings

"This customer's part number means that manufacturer product" — the fact a
salesperson establishes once. pie-parser can resolve a scoped code
authoritatively when a confirmed mapping exists, but its own store is a CSV
inside the engine repository: shared by every tenant, and shipped empty, which
is why that path has never fired. A confirmation is made by a person about one
organization's customer, so it belongs in this database.

New table only — nothing existing is altered, so there is no backfill question
and no default to get wrong.

Revision ID: bcf507053964
Revises: 65139e67e76d
Create Date: 2026-08-07 05:02:24.619060
"""
from alembic import op
import sqlalchemy as sa


revision = 'bcf507053964'
down_revision = '65139e67e76d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('confirmed_code_mappings',
    sa.Column('mapping_id', sa.String(length=64), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('identity_id', sa.String(length=64), nullable=False),
    sa.Column('code', sa.String(length=128), nullable=False),
    sa.Column('target_record_id', sa.String(length=64), nullable=False),
    sa.Column('relationship', sa.String(length=32), nullable=False),
    sa.Column('source_ref', sa.String(length=255), nullable=False),
    sa.Column('confirmed_by_user_id', sa.String(length=64), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('superseded_by', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('mapping_id')
    )
    with op.batch_alter_table('confirmed_code_mappings', schema=None) as batch_op:
        batch_op.create_index('ix_confirmed_code_lookup', ['organization_id', 'identity_id', 'code', 'active'], unique=False)
        batch_op.create_index(batch_op.f('ix_confirmed_code_mappings_active'), ['active'], unique=False)
        batch_op.create_index(batch_op.f('ix_confirmed_code_mappings_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_confirmed_code_mappings_identity_id'), ['identity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_confirmed_code_mappings_organization_id'), ['organization_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('confirmed_code_mappings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_confirmed_code_mappings_organization_id'))
        batch_op.drop_index(batch_op.f('ix_confirmed_code_mappings_identity_id'))
        batch_op.drop_index(batch_op.f('ix_confirmed_code_mappings_created_at'))
        batch_op.drop_index(batch_op.f('ix_confirmed_code_mappings_active'))
        batch_op.drop_index('ix_confirmed_code_lookup')

    op.drop_table('confirmed_code_mappings')
