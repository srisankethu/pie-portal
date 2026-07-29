"""user credentials, approval requests, and org approval policy

Three changes, all closing the same gap: the platform computed and recorded
authority but never enforced it.

1. ``users`` gains a password hash and the provenance of its role. The login
   endpoint previously accepted any non-empty password for any known email, so
   the three roles were a presentation choice rather than a boundary. Existing
   rows get a NULL hash, which the new login treats as "cannot sign in" — the
   seed re-issues one, and an owner can reset any account.

2. ``approval_requests`` — what somebody could not authorize alone, who decided
   it, and the append-only thread between them. This is what "Escalate to
   management" always implied: previously it set the decision to OVERRIDDEN and
   wrote a note, so the item closed and nobody upstream was told.

3. ``org_policies`` — whether approvals are enforced, and whose signature is
   needed. Kept apart from CommercialThresholds on purpose: thresholds answer
   "is this price thin", policy answers "may it go out anyway, and whose call is
   that". Conflating them would force a company wanting stricter sign-off to
   distort its own margin analysis to get it.

Revision ID: f19d3b6c4a25
Revises: e4b19c7a2f83
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa


revision = 'f19d3b6c4a25'
down_revision = 'e4b19c7a2f83'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(255)))
        batch.add_column(sa.Column("must_change_password", sa.Boolean(),
                                   server_default=sa.false()))
        batch.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("created_by_user_id", sa.String(64)))
        batch.add_column(sa.Column("role_changed_by_user_id", sa.String(64)))
        batch.add_column(sa.Column("role_changed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True)))

    op.create_table(
        "approval_requests",
        sa.Column("approval_request_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), server_default="PENDING"),
        sa.Column("required_authority", sa.String(16), server_default="MANAGER"),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("subject_line_id", sa.String(64)),
        sa.Column("subject", sa.JSON()),
        sa.Column("title", sa.String(255), server_default=""),
        sa.Column("summary", sa.String(1024), server_default=""),
        sa.Column("reason_code", sa.String(48)),
        sa.Column("reason", sa.String(2048)),
        sa.Column("requested_by_user_id", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by_user_id", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.String(2048)),
        sa.Column("thread", sa.JSON()),
        sa.Column("thresholds_version", sa.String(32), server_default=""),
    )
    op.create_index("ix_approval_requests_organization_id", "approval_requests",
                    ["organization_id"])
    op.create_index("ix_approval_requests_kind", "approval_requests", ["kind"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_index("ix_approval_requests_subject_id", "approval_requests",
                    ["subject_id"])
    op.create_index("ix_approval_requests_requested_by_user_id", "approval_requests",
                    ["requested_by_user_id"])
    op.create_index("ix_approval_requests_decided_by_user_id", "approval_requests",
                    ["decided_by_user_id"])
    op.create_index("ix_approval_requests_requested_at", "approval_requests",
                    ["requested_at"])
    op.create_index("ix_approval_org_status", "approval_requests",
                    ["organization_id", "status"])
    op.create_index("ix_approval_org_subject", "approval_requests",
                    ["organization_id", "kind", "subject_id"])

    op.create_table(
        "org_policies",
        sa.Column("organization_id", sa.String(64), primary_key=True),
        sa.Column("require_approval_for_quotes", sa.Boolean(), server_default=sa.true()),
        sa.Column("require_approval_below_review_floor", sa.Boolean(),
                  server_default=sa.false()),
        sa.Column("below_cost_requires_owner", sa.Boolean(), server_default=sa.true()),
        sa.Column("allow_self_approval", sa.Boolean(), server_default=sa.false()),
        sa.Column("escalation_creates_approval", sa.Boolean(), server_default=sa.true()),
        sa.Column("updated_by_user_id", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("org_policies")
    op.drop_table("approval_requests")
    with op.batch_alter_table("users") as batch:
        for column in ("created_at", "role_changed_at", "role_changed_by_user_id",
                       "created_by_user_id", "last_login_at", "must_change_password",
                       "password_hash"):
            batch.drop_column(column)
