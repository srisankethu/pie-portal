"""tender results

The one place share of wallet can be *measured* rather than estimated. A
government or PSU tender publishes the quantity and value it is buying, and the
award says who supplied it — so for that customer, over that tender, won over
tendered is arithmetic across two published facts rather than an inference about
spend the platform cannot see.

``tendered_value`` is what the document said and is never edited to reconcile a
share. ``won_value`` is nullable and fills in when the award is known: NULL is
an undecided bid, which is excluded from the denominator rather than counted as
a loss — counting open bids as losses would understate every share by however
many are still out.

``tender_ref`` is unique per organization. The same bid recorded twice would
count its value twice in a denominator and inflate a share silently, which is
the kind of error that shows up as a number nobody can reproduce.

Nothing syncs this table. Tender portals are not a connector and an award notice
is a PDF, so every row is a record of what somebody read — which is why ``source``
exists and is required at the router. A measured share whose measurement cannot
be looked up is not measured.

Revision ID: c3f8a2b19e47
Revises: b7c41e0a9d38
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3f8a2b19e47'
down_revision = 'b7c41e0a9d38'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tender_results',
        sa.Column('tender_result_id', sa.String(64), primary_key=True),
        sa.Column('organization_id', sa.String(64), nullable=False),
        sa.Column('tender_ref', sa.String(128), nullable=False),
        sa.Column('customer_id', sa.String(64), nullable=True),
        sa.Column('customer_ref', sa.String(255), nullable=False, server_default=''),
        sa.Column('tendered_value', sa.Numeric(18, 2), nullable=False),
        sa.Column('won_value', sa.Numeric(18, 2), nullable=True),
        sa.Column('categories', sa.JSON(), nullable=False),
        sa.Column('closed_on', sa.Date(), nullable=False),
        sa.Column('awarded_on', sa.Date(), nullable=True),
        sa.Column('source', sa.String(512), nullable=False, server_default=''),
        sa.Column('recorded_by_user_id', sa.String(64), nullable=True),
        sa.Column('note', sa.String(1024), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('organization_id', 'tender_ref',
                            name='uq_tender_result_org_ref'),
    )
    op.create_index('ix_tender_results_organization_id', 'tender_results',
                    ['organization_id'])
    op.create_index('ix_tender_results_tender_ref', 'tender_results', ['tender_ref'])
    op.create_index('ix_tender_results_customer_id', 'tender_results', ['customer_id'])
    op.create_index('ix_tender_results_closed_on', 'tender_results', ['closed_on'])
    op.create_index('ix_tender_results_org_customer', 'tender_results',
                    ['organization_id', 'customer_id'])


def downgrade() -> None:
    op.drop_index('ix_tender_results_org_customer', table_name='tender_results')
    op.drop_index('ix_tender_results_closed_on', table_name='tender_results')
    op.drop_index('ix_tender_results_customer_id', table_name='tender_results')
    op.drop_index('ix_tender_results_tender_ref', table_name='tender_results')
    op.drop_index('ix_tender_results_organization_id', table_name='tender_results')
    op.drop_table('tender_results')
