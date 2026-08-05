"""Classify a customer for third-party incentive eligibility.

One nullable column on ``customers``. NULL means nobody has classified the
account yet, and the application treats that exactly like RESTRICTED — see
``commercial.incentive.may_pay_third_party``. Backfilling a value here would
undo that: writing "PRIVATE" across the book to make the desk work would hand
every government account an incentive path, which is the one outcome the rule
exists to prevent.

Purely additive and reversible.

Revision ID: d1e93a7c4506
Revises: cca6781337ea
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "d1e93a7c4506"
down_revision = "cca6781337ea"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers",
                  sa.Column("incentive_eligibility", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "incentive_eligibility")
