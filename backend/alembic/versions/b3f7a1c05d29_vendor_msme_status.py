"""Whether a supplier is protected by the MSME 45-day rule.

One additive table. Every other input to the section 43B(h) test — bill dates,
payment dates, balances, agreed terms — is already in the schema; this is the
one fact the platform could not derive and had no home for.

A separate table rather than columns on ``vendors``, for the reason
``vendor_payment_terms`` is separate: the vendor row is rewritten from the Zoho
payload on every sync, so anything typed by a person has to live outside it.

Empty on upgrade, and deliberately so. There is no backfill and there must not
be one: the whole discipline of the feature is that a status is captured with
evidence or left UNKNOWN, and a migration that guessed at 400 suppliers would
be the exact inference the model docstring forbids.

Revision ID: b3f7a1c05d29
Revises: 0e8d9299b0c7
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa

revision = "b3f7a1c05d29"
down_revision = "0e8d9299b0c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_msme_statuses",
        sa.Column("vendor_msme_status_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("vendor_id", sa.String(length=64), nullable=False),
        # MICRO | SMALL | MEDIUM | NOT_REGISTERED | UNKNOWN. UNKNOWN is the
        # default and is not NOT_REGISTERED — see the model.
        sa.Column("classification", sa.String(length=16), nullable=False,
                  server_default="UNKNOWN"),
        # MANUFACTURER | SERVICE | TRADER | UNKNOWN. A registered trader is out
        # of scope, which for a distributor is most of the supplier base.
        sa.Column("enterprise_activity", sa.String(length=16), nullable=False,
                  server_default="UNKNOWN"),
        sa.Column("udyam_number", sa.String(length=32), nullable=True),
        # Nullable on purpose: absent an agreement the limit is 15 days, and
        # NULL means nobody has established which of the three states applies.
        sa.Column("written_agreement", sa.Boolean(), nullable=True),
        sa.Column("agreed_days", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.String(length=24), nullable=False,
                  server_default="NONE"),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("set_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.vendor_id"]),
        sa.PrimaryKeyConstraint("vendor_msme_status_id"),
        sa.UniqueConstraint("organization_id", "vendor_id",
                            name="uq_vendor_msme_status_vendor"),
    )
    with op.batch_alter_table("vendor_msme_statuses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_vendor_msme_statuses_organization_id"),
            ["organization_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_vendor_msme_statuses_vendor_id"),
            ["vendor_id"], unique=False)


def downgrade() -> None:
    op.drop_table("vendor_msme_statuses")
