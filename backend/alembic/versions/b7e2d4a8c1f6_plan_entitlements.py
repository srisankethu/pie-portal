"""Plans and the one-per-books intelligence trial.

The pricing model's rules were contractual only — nothing in the schema knew
what plan an organization was on, nothing gated a second connected company,
and nothing remembered that a set of books had already had its free month of
Commercial Intelligence. A term that is not mechanically enforced is a
suggestion; this revision adds the two pieces of state that turn the terms
into behaviour (see app/entitlements.py).

``organizations.plan`` is nullable on purpose: NULL resolves to the
deployment's DEFAULT_PLAN, so existing databases need no backfill and keep
their current behaviour. ``intelligence_trials`` is keyed unique on the Zoho
organization id — the connected books are the one thing a signup cannot mint
a fresh copy of, so they are what a trial belongs to.

Revision ID: b7e2d4a8c1f6
Revises: a9c5e1f7b2d4
Create Date: 2026-08-12
"""
import sqlalchemy as sa
from alembic import op

revision = "b7e2d4a8c1f6"
down_revision = "a9c5e1f7b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations",
                  sa.Column("plan", sa.String(length=32), nullable=True))
    op.create_table(
        "intelligence_trials",
        sa.Column("trial_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("zoho_organization_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"],
                                ["organizations.organization_id"]),
        sa.PrimaryKeyConstraint("trial_id"),
        sa.UniqueConstraint("zoho_organization_id"),
    )
    op.create_index("ix_intelligence_trials_organization_id",
                    "intelligence_trials", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_intelligence_trials_organization_id",
                  table_name="intelligence_trials")
    op.drop_table("intelligence_trials")
    op.drop_column("organizations", "plan")
