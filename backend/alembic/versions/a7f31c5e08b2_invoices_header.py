"""Invoice headers — what a customer owes, and by when.

The mirror of the ``bills`` table added in c7e2b41a8f36, and deliberately the
same shape: one is what we owe a supplier, the other what a customer owes us.
Invoices were normalised into ``sales_txns`` at line grain and the header
discarded, so nothing recorded an outstanding balance or a due date.
``payment_applications`` covers only invoices that were *paid*, which is the
wrong half of the ledger for a collections question.

Written out literally rather than imported from the models: this runs against
schemas from months ago, and the models describe today.

Revision ID: a7f31c5e08b2
Revises: f4d8a1c69e03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7f31c5e08b2"
down_revision = "f4d8a1c69e03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("invoice_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("number", sa.String(length=128), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("balance", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"]),
        sa.PrimaryKeyConstraint("invoice_id"),
        sa.UniqueConstraint("organization_id", "external_ref",
                            name="uq_invoice_org_ref"),
    )
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.create_index("ix_invoice_org_due",
                              ["organization_id", "due_date"], unique=False)
        batch_op.create_index("ix_invoices_organization_id",
                              ["organization_id"], unique=False)
        batch_op.create_index("ix_invoices_external_ref",
                              ["external_ref"], unique=False)
        batch_op.create_index("ix_invoices_customer_id",
                              ["customer_id"], unique=False)
        batch_op.create_index("ix_invoices_date", ["date"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_index("ix_invoices_date")
        batch_op.drop_index("ix_invoices_customer_id")
        batch_op.drop_index("ix_invoices_external_ref")
        batch_op.drop_index("ix_invoices_organization_id")
        batch_op.drop_index("ix_invoice_org_due")
    op.drop_table("invoices")
