"""Record which country's statutes reach an organization.

ISO 3166-1 alpha-2, e.g. "IN". The statutory screens (MSME payment timing,
194Q withholding) are Indian law, and until now they answered for every tenant
— an April financial year and Indian deadlines applied to a book they may not
govern. ``commercial/jurisdiction.py`` now decides from this column whether
those statutes apply at all.

Nullable, and deliberately not backfilled with a guess. NULL means "this
organization has not said", and unknown is not India: the statutory endpoints
refuse for a NULL country rather than assuming the one jurisdiction currently
supported. The demo org is seeded as "IN" by ``app.seed``, which also owns the
demo-only backfill — a real tenant's country is a fact somebody records, not a
default.

Revision ID: t5jurisdiction
Revises: d5a2e7c31b84
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "t5jurisdiction"
down_revision = "d5a2e7c31b84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("country", sa.String(2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("country")
