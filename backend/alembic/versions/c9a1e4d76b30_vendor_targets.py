"""what each principal expects of us, per period

An authorised distributor does not set its own numbers: the principals do, per
period, and the year is run against them. Nothing in Zoho holds a target and
nothing derives one — so this is the one table in the platform whose contents
are typed rather than synced, and the only one a complete re-sync must leave
completely alone.

``basis`` carries whether the number is on what we buy from them or what we sell
of theirs. Those are different actuals, and one undifferentiated "target" column
would compare a purchase target against a sales figure without anything saying
so.

Periods are two dates, not a quarter label. Principals do not agree on a
financial year — an Indian principal's Q1 is April to June, a European parent's
is January to March — and a label has to be interpreted where two dates cannot
be misread.

Revision ID: c9a1e4d76b30
Revises: b7c24e91fa05
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op

revision = 'c9a1e4d76b30'
down_revision = 'b7c24e91fa05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'vendor_targets',
        sa.Column('target_id', sa.String(64), primary_key=True),
        sa.Column('organization_id', sa.String(64), nullable=False),
        sa.Column('vendor_id', sa.String(64), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('basis', sa.String(16), nullable=False),
        sa.Column('amount', sa.Numeric(18, 4), nullable=False),
        sa.Column('set_by_user_id', sa.String(64), nullable=True),
        sa.Column('note', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.vendor_id']),
        sa.UniqueConstraint('organization_id', 'vendor_id', 'period_start',
                            'period_end', 'basis',
                            name='uq_vendor_target_period'),
    )
    op.create_index('ix_vendor_targets_organization_id', 'vendor_targets',
                    ['organization_id'])
    op.create_index('ix_vendor_targets_vendor_id', 'vendor_targets', ['vendor_id'])
    op.create_index('ix_vendor_target_org_period', 'vendor_targets',
                    ['organization_id', 'period_start'])


def downgrade() -> None:
    op.drop_index('ix_vendor_target_org_period', table_name='vendor_targets')
    op.drop_index('ix_vendor_targets_vendor_id', table_name='vendor_targets')
    op.drop_index('ix_vendor_targets_organization_id', table_name='vendor_targets')
    op.drop_table('vendor_targets')
