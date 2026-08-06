"""Commitments: sales orders, and the money-out side of cash.

Two new tables and two counters. Purely additive — nothing here alters an
existing column, so it cannot fail on a populated database, and the rollback
drops only what this revision created.

``sales_orders`` is the demand-side mirror of ``purchase_orders``: what a
customer promised us and what we promised to ship. ``vendor_payments`` is the
other half of the cash ledger — ``payment_receipts`` has recorded money in
since the cash screen was built, and one side of a ledger is not liquidity.

Both are header grain. The line-level split answers questions these do not ask
and would cost one API call per document to obtain.

Columns written out literally rather than imported from the models, per the
rule that a migration runs against a schema from months ago.

Revision ID: b6c3f80a2d51
Revises: a8d5e21b6f47
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "b6c3f80a2d51"
down_revision = "a8d5e21b6f47"
branch_labels = None
depends_on = None

_COUNTERS = ("sales_orders", "vendor_payments")


def upgrade() -> None:
    op.create_table(
        "sales_orders",
        sa.Column("sales_order_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("number", sa.String(length=128), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("expected_ship_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("invoiced_status", sa.String(length=48), nullable=True),
        sa.Column("shipped_status", sa.String(length=48), nullable=True),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("salesperson_external_id", sa.String(length=64), nullable=True),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"]),
        sa.PrimaryKeyConstraint("sales_order_id"),
        sa.UniqueConstraint("organization_id", "external_ref",
                            name="uq_sales_order_org_ref"),
    )
    with op.batch_alter_table("sales_orders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sales_orders_customer_id"),
                              ["customer_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_sales_orders_date"),
                              ["date"], unique=False)
        batch_op.create_index(batch_op.f("ix_sales_orders_external_ref"),
                              ["external_ref"], unique=False)
        batch_op.create_index(batch_op.f("ix_sales_orders_organization_id"),
                              ["organization_id"], unique=False)

    op.create_table(
        "vendor_payments",
        sa.Column("vendor_payment_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("vendor_id", sa.String(length=64), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("mode", sa.String(length=48), nullable=True),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.vendor_id"]),
        sa.PrimaryKeyConstraint("vendor_payment_id"),
        sa.UniqueConstraint("organization_id", "external_ref",
                            name="uq_vendor_payment_org_ref"),
    )
    with op.batch_alter_table("vendor_payments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_vendor_payments_date"),
                              ["date"], unique=False)
        batch_op.create_index(batch_op.f("ix_vendor_payments_external_ref"),
                              ["external_ref"], unique=False)
        batch_op.create_index(batch_op.f("ix_vendor_payments_organization_id"),
                              ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_vendor_payments_vendor_id"),
                              ["vendor_id"], unique=False)

    # Existing runs get 0, which is honest: they did not read these documents.
    for name in _COUNTERS:
        op.add_column("sync_runs",
                      sa.Column(name, sa.Integer(), nullable=False,
                                server_default="0"))


def downgrade() -> None:
    for name in reversed(_COUNTERS):
        op.drop_column("sync_runs", name)
    op.drop_table("vendor_payments")
    op.drop_table("sales_orders")
