"""customer_credit_limits, customer_account_owners — the line, and whose book

Revision ID: c4e7a91d2f38
Revises: b2d95e11c74a
Create Date: 2026-08-08

Two tables, added together because they answer one question between them. The
platform could say a customer takes 69 days to pay; it could not say they are
past the limit somebody gave them, because no limit existed anywhere. And it
could not say who should call them, because no account belonged to anyone a
person had decided on.

**Tables rather than columns on ``customers``.** ``upsert_customer`` rewrites
the synced fields from the payload on every pull and a complete re-sync rebuilds
every customer row from nothing, so a limit typed onto that row would survive
until the next sync and no longer — the same reason ``vendor_payment_terms``,
``item_category_overrides`` and ``vendor_targets`` are their own tables.

``customer_account_owners`` is deliberately *beside* ``customers.assigned_user_id``
rather than replacing it. That column is derived — the sync writes Zoho's
salesperson into it whenever the email maps — and it keeps doing so. This table
is the decision, it wins where it exists, and
``commercial/ownership.effective`` is the single place that says so.

Unique on (organization, customer) in both: a limit is revised and a book is
handed over, so both write paths are upserts. Two rows for one account would
make "the limit" a question about which row won, and an order cannot be held or
released on an ambiguous answer.

``amount`` is ``Numeric``, never a float — money is ``Decimal`` throughout this
platform. Nullable it is not: a row exists precisely because somebody decided a
number, and "no limit recorded" is expressed by the absence of the row rather
than by a NULL inside one. That distinction is the whole design and is enforced
here rather than left to the application.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against schemas from months ago while models describe
today. Nothing is backfilled and nothing can be: no limit has ever been recorded
in this book, and inventing one for an account that has none would be exactly
the failure the feature exists to prevent.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4e7a91d2f38"
down_revision = "0e8d9299b0c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_credit_limits",
        sa.Column("credit_limit_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64),
                  sa.ForeignKey("customers.customer_id"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("set_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "customer_id",
                            name="uq_customer_credit_limit_customer"),
    )
    op.create_index("ix_customer_credit_limits_organization_id",
                    "customer_credit_limits", ["organization_id"])
    op.create_index("ix_customer_credit_limits_customer_id",
                    "customer_credit_limits", ["customer_id"])

    op.create_table(
        "customer_account_owners",
        sa.Column("account_owner_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64),
                  sa.ForeignKey("customers.customer_id"), nullable=False),
        sa.Column("user_id", sa.String(length=64),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("set_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "customer_id",
                            name="uq_customer_account_owner_customer"),
    )
    op.create_index("ix_customer_account_owners_organization_id",
                    "customer_account_owners", ["organization_id"])
    op.create_index("ix_customer_account_owners_customer_id",
                    "customer_account_owners", ["customer_id"])
    # "Everything in my book" is the query this table exists to answer.
    op.create_index("ix_customer_account_owners_user_id",
                    "customer_account_owners", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_account_owners_user_id",
                  table_name="customer_account_owners")
    op.drop_index("ix_customer_account_owners_customer_id",
                  table_name="customer_account_owners")
    op.drop_index("ix_customer_account_owners_organization_id",
                  table_name="customer_account_owners")
    op.drop_table("customer_account_owners")

    op.drop_index("ix_customer_credit_limits_customer_id",
                  table_name="customer_credit_limits")
    op.drop_index("ix_customer_credit_limits_organization_id",
                  table_name="customer_credit_limits")
    op.drop_table("customer_credit_limits")
