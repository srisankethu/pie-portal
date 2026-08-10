"""invoice to sales order links

Revision ID: b71c4f0a83d5
Revises: 2eab6a5748d7
Create Date: 2026-08-10 21:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b71c4f0a83d5'
down_revision = '2eab6a5748d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns written out literally rather than taken from the model: this runs
    # against schemas older than today's `app.domain.models`. CLAUDE.md §4.
    #
    # No foreign key at either end, matching `payment_applications` and
    # `credit_note_applications`: an invoice can name an order raised before the
    # sync window opens, and a constraint would refuse the link rather than
    # record it as an order whose date is not held.
    op.create_table(
        'invoice_sales_orders',
        sa.Column('invoice_sales_order_id', sa.String(length=64), nullable=False),
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('invoice_external_ref', sa.String(length=128), nullable=False),
        sa.Column('sales_order_external_ref', sa.String(length=128), nullable=False),
        sa.Column('sales_order_number', sa.String(length=128), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('source_ref', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('invoice_sales_order_id'),
        sa.UniqueConstraint('organization_id', 'invoice_external_ref',
                            'sales_order_external_ref',
                            name='uq_invoice_sales_order_org_pair'),
    )
    with op.batch_alter_table('invoice_sales_orders', schema=None) as batch_op:
        batch_op.create_index('ix_invoice_so_org_invoice',
                              ['organization_id', 'invoice_external_ref'],
                              unique=False)
        batch_op.create_index('ix_invoice_so_org_order',
                              ['organization_id', 'sales_order_external_ref'],
                              unique=False)
        batch_op.create_index(
            batch_op.f('ix_invoice_sales_orders_invoice_external_ref'),
            ['invoice_external_ref'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_invoice_sales_orders_organization_id'),
            ['organization_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_invoice_sales_orders_sales_order_external_ref'),
            ['sales_order_external_ref'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('invoice_sales_orders', schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_invoice_sales_orders_sales_order_external_ref'))
        batch_op.drop_index(
            batch_op.f('ix_invoice_sales_orders_organization_id'))
        batch_op.drop_index(
            batch_op.f('ix_invoice_sales_orders_invoice_external_ref'))
        batch_op.drop_index('ix_invoice_so_org_order')
        batch_op.drop_index('ix_invoice_so_org_invoice')
    op.drop_table('invoice_sales_orders')
