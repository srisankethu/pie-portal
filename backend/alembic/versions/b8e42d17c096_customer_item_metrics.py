"""customer item metrics

Two changes, both foundations for Customer × Item commercial intelligence:

1. ``sales_txns`` gains ``rate`` and ``discount_percent``, mirroring the columns
   already on ``cost_records``. ``unit_price`` now holds the NET selling price
   (after the line discount) rather than the raw pre-discount Zoho rate — it
   previously disagreed with ``line_revenue``, which was already post-discount,
   so any margin computed from ``unit_price`` was overstated on a discounted
   line. Both new columns are nullable: a row synced before this fix has
   neither until its invoice is re-fetched (a full re-sync).

2. ``customer_item_metrics`` — a derived, recomputable projection of one
   customer's commercial relationship with one item. It holds no source facts;
   ``python -m app.commercial.backfill`` rebuilds it from sales_txns/cost_records.

Revision ID: b8e42d17c096
Revises: a7c3e91f5d02
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8e42d17c096'
down_revision = 'a7c3e91f5d02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('sales_txns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rate', sa.Numeric(18, 4), nullable=True))
        batch_op.add_column(sa.Column('discount_percent', sa.Numeric(9, 4), nullable=True))

    op.create_table(
        'customer_item_metrics',
        sa.Column('customer_item_metric_id', sa.String(length=64), nullable=False),
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('product_id', sa.String(length=64), nullable=False),

        sa.Column('first_transaction_date', sa.Date(), nullable=True),
        sa.Column('last_transaction_date', sa.Date(), nullable=True),
        sa.Column('transaction_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('history_months', sa.Float(), nullable=True),

        sa.Column('revenue_recent', sa.Numeric(18, 4), nullable=True),
        sa.Column('revenue_12m', sa.Numeric(18, 4), nullable=True),
        sa.Column('gross_profit_recent', sa.Numeric(18, 4), nullable=True),
        sa.Column('gross_profit_12m', sa.Numeric(18, 4), nullable=True),
        sa.Column('current_sell_price', sa.Numeric(18, 4), nullable=True),
        sa.Column('current_effective_cost', sa.Numeric(18, 4), nullable=True),

        sa.Column('current_margin', sa.Float(), nullable=True),
        sa.Column('previous_margin', sa.Float(), nullable=True),
        sa.Column('margin_3m', sa.Float(), nullable=True),
        sa.Column('margin_6m', sa.Float(), nullable=True),
        sa.Column('margin_12m', sa.Float(), nullable=True),
        sa.Column('historical_margin', sa.Float(), nullable=True),

        sa.Column('margin_change_pp', sa.Float(), nullable=True),
        sa.Column('price_change_pct', sa.Float(), nullable=True),
        sa.Column('cost_change_pct', sa.Float(), nullable=True),
        sa.Column('erosion_kind', sa.String(length=24), nullable=True),

        sa.Column('same_item_median_price', sa.Numeric(18, 4), nullable=True),
        sa.Column('same_item_median_margin', sa.Float(), nullable=True),
        sa.Column('price_deviation_pct', sa.Float(), nullable=True),
        sa.Column('margin_deviation_pp', sa.Float(), nullable=True),
        sa.Column('peer_count', sa.Integer(), nullable=False, server_default='0'),

        sa.Column('qty_recent', sa.Numeric(18, 4), nullable=True),
        sa.Column('qty_previous', sa.Numeric(18, 4), nullable=True),
        sa.Column('volume_change_pct', sa.Float(), nullable=True),

        sa.Column('historical_margin_gap', sa.Numeric(18, 4), nullable=True),
        sa.Column('peer_margin_gap', sa.Numeric(18, 4), nullable=True),
        sa.Column('annualized_historical_margin_gap', sa.Numeric(18, 4), nullable=True),

        sa.Column('signals', sa.JSON(), nullable=False),
        sa.Column('data_sufficiency', sa.String(length=16), nullable=False,
                  server_default='INSUFFICIENT'),
        sa.Column('sufficiency_reasons', sa.JSON(), nullable=False),
        sa.Column('cost_covered_txns', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_missing_txns', sa.Integer(), nullable=False, server_default='0'),

        sa.Column('thresholds_version', sa.String(length=32), nullable=False,
                  server_default=''),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('customer_item_metric_id'),
        sa.UniqueConstraint('organization_id', 'customer_id', 'product_id',
                            name='uq_cim_org_customer_product'),
    )
    with op.batch_alter_table('customer_item_metrics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customer_item_metrics_organization_id'),
                              ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_item_metrics_customer_id'),
                              ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_item_metrics_product_id'),
                              ['product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_item_metrics_revenue_12m'),
                              ['revenue_12m'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_item_metrics_last_transaction_date'),
                              ['last_transaction_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_item_metrics_data_sufficiency'),
                              ['data_sufficiency'], unique=False)

    # The two hot read paths: "this customer's items, worst first" (the customer
    # portfolio screen) and "everyone who buys this item" (the peer benchmark).
    op.create_index('ix_sales_txns_org_customer_product',
                    'sales_txns', ['organization_id', 'customer_id', 'product_id'])
    op.create_index('ix_sales_txns_org_product_date',
                    'sales_txns', ['organization_id', 'product_id', 'date'])
    op.create_index('ix_cost_records_org_product_date',
                    'cost_records', ['organization_id', 'product_id', 'date'])


def downgrade() -> None:
    op.drop_index('ix_cost_records_org_product_date', table_name='cost_records')
    op.drop_index('ix_sales_txns_org_product_date', table_name='sales_txns')
    op.drop_index('ix_sales_txns_org_customer_product', table_name='sales_txns')

    with op.batch_alter_table('customer_item_metrics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customer_item_metrics_data_sufficiency'))
        batch_op.drop_index(batch_op.f('ix_customer_item_metrics_last_transaction_date'))
        batch_op.drop_index(batch_op.f('ix_customer_item_metrics_revenue_12m'))
        batch_op.drop_index(batch_op.f('ix_customer_item_metrics_product_id'))
        batch_op.drop_index(batch_op.f('ix_customer_item_metrics_customer_id'))
        batch_op.drop_index(batch_op.f('ix_customer_item_metrics_organization_id'))
    op.drop_table('customer_item_metrics')

    with op.batch_alter_table('sales_txns', schema=None) as batch_op:
        batch_op.drop_column('discount_percent')
        batch_op.drop_column('rate')
