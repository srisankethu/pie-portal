"""Record the plan a self-serve sign-up asked for — which is not the plan it gets.

A sign-up now names a plan on the form. It still lands on **free**
(``onboarding.SIGNUP_PLAN``), because ``entitlements`` is explicit that plans
are set by the operator and never by a tenant: an owner who could set their own
plan would not have one. So this column is a *request*, not a licence, and
nothing in ``entitlements`` reads it — ``licensed_plan`` still reads ``plan``
alone. It exists so the answer to "who asked for Commercial Intelligence?" is a
row rather than an email nobody kept.

Nullable, and not backfilled: NULL means "this organization never asked", which
is true of every tenant provisioned before the form had the field.

Revision ID: w3plan
Revises: c1f4a80b73e2
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "w3plan"
down_revision = "c1f4a80b73e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("requested_plan", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("requested_plan")
