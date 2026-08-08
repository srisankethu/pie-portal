"""bill_payment_applications — which bills each payment out settled

Revision ID: a1c8f42e6b03
Revises: e5c07f4a9218
Create Date: 2026-08-08

The payable mirror of ``payment_applications``, and the table
``VendorPaymentDoc`` was documented as waiting for: "no application table beside
it, deliberately… it is added when the first reader exists". The reader is the
cash projection, which placed every bill on the date the document claimed and
had no way to know this book settles them a fortnight after that date.

Grain is one payment against one bill, not one payment. A single transfer
clearing ten bills is ten observations, each with its own bill date, and
measuring the transfer instead would give a book that batches its remittances
one flattering data point.

``bill_date`` and ``bill_due_date`` are copied onto the row rather than joined
to ``bills`` for the same reason the receivable side copies them: a bill can
predate the sync window, and a join would silently drop exactly the oldest and
slowest settlements — the ones a supplier has already noticed.

``vendor_id`` is nullable where ``payment_applications.customer_id`` is not.
The asymmetry is deliberate: an inbound payment from an unknown customer is
skipped at ingest, but a payment *we* made is a fact about our own bank account
whether or not the supplier resolved, so it is kept and reported as
unattributed rather than dropped.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against schemas from months ago while models describe
today. Nothing backfills: the applications arrive on the next sync, because the
bill breakdown needs a detail call this pull did not previously make.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c8f42e6b03"
down_revision = "e5c07f4a9218"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bill_payment_applications",
        sa.Column("bill_payment_application_id", sa.String(length=64),
                  primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("vendor_payment_id", sa.String(length=64),
                  sa.ForeignKey("vendor_payments.vendor_payment_id"),
                  nullable=False),
        sa.Column("vendor_id", sa.String(length=64),
                  sa.ForeignKey("vendors.vendor_id"), nullable=True),
        sa.Column("bill_external_ref", sa.String(length=128), nullable=False),
        sa.Column("bill_number", sa.String(length=128), nullable=True),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("bill_due_date", sa.Date(), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount_applied", sa.Numeric(18, 4), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "external_ref",
                            name="uq_bill_payment_application_org_external"),
    )
    op.create_index("ix_bill_payment_applications_organization_id",
                    "bill_payment_applications", ["organization_id"])
    op.create_index("ix_bill_payment_applications_external_ref",
                    "bill_payment_applications", ["external_ref"])
    op.create_index("ix_bill_payment_applications_vendor_payment_id",
                    "bill_payment_applications", ["vendor_payment_id"])
    op.create_index("ix_bill_payment_applications_vendor_id",
                    "bill_payment_applications", ["vendor_id"])
    op.create_index("ix_bill_payment_applications_bill_external_ref",
                    "bill_payment_applications", ["bill_external_ref"])
    op.create_index("ix_bill_payment_applications_paid_on",
                    "bill_payment_applications", ["paid_on"])
    op.create_index("ix_bill_payment_app_org_bill",
                    "bill_payment_applications",
                    ["organization_id", "bill_external_ref"])


def downgrade() -> None:
    op.drop_index("ix_bill_payment_app_org_bill",
                  table_name="bill_payment_applications")
    op.drop_index("ix_bill_payment_applications_paid_on",
                  table_name="bill_payment_applications")
    op.drop_index("ix_bill_payment_applications_bill_external_ref",
                  table_name="bill_payment_applications")
    op.drop_index("ix_bill_payment_applications_vendor_id",
                  table_name="bill_payment_applications")
    op.drop_index("ix_bill_payment_applications_vendor_payment_id",
                  table_name="bill_payment_applications")
    op.drop_index("ix_bill_payment_applications_external_ref",
                  table_name="bill_payment_applications")
    op.drop_index("ix_bill_payment_applications_organization_id",
                  table_name="bill_payment_applications")
    op.drop_table("bill_payment_applications")
