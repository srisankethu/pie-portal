"""Record which zone an organization's *day* is measured in.

Storage stays UTC everywhere. This answers the other question — "which day is
it for this business?" — which is not the same question for five and a half
hours out of every twenty-four in India, and which the code previously answered
with ``date.today()``: the server's zone, and UTC in every container this runs
in.

Nullable, and deliberately not backfilled with a guess. NULL means "this
organization has not said", and the application falls through to the configured
business zone rather than to UTC. A connection check fills it in from Zoho,
which already knows.

Revision ID: f3a71c9e05b2
Revises: e2b4c8f19d73
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a71c9e05b2"
down_revision = "e2b4c8f19d73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations",
                  sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "timezone")
