"""Record an owner asking to change plan, and what an operator did about it.

There was no way to ask. ``set_plan`` is an operator command with deliberately
no API, so the platform sold three tiers and offered no way to buy the upper
two. This table is the asking; it grants nothing, and the plan still moves only
through ``set_plan``.

Columns are written out literally rather than imported from the models, per
CLAUDE.md §4: this migration runs against schemas from months ago and the models
describe today.

Revision ID: t13plan_requests
Revises: c1f4a80b73e2
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "t13plan_requests"
down_revision = "c1f4a80b73e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_change_requests",
        sa.Column("request_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("requested_plan", sa.String(length=32), nullable=False),
        sa.Column("plan_at_request", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="REQUESTED"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"],
                                ["organizations.organization_id"]),
    )
    op.create_index("ix_plan_change_requests_organization_id",
                    "plan_change_requests", ["organization_id"])
    op.create_index("ix_plan_change_requests_status",
                    "plan_change_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_plan_change_requests_status",
                  table_name="plan_change_requests")
    op.drop_index("ix_plan_change_requests_organization_id",
                  table_name="plan_change_requests")
    op.drop_table("plan_change_requests")
