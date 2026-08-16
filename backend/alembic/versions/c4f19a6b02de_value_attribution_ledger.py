"""The value attribution ledger and the pre-trial baseline.

Two tables, no changes to existing ones. Everything else the attribution
surface needs — priced lines, quote outcomes, decision impact, the trial window
— is already in this schema and is reused rather than duplicated.

``value_events`` is append-only and carries a unique (organization_id,
event_key). That constraint is the double-count guard: a detector re-run after a
re-sync, or an outcome recorded twice, must be a no-op at the database level
rather than a code path that has to remember. ``amount`` is nullable on purpose
— an event whose class carries no defensible rupee value is recorded as itself,
never as a zero that would sum into a total.

``evaluation_baselines`` is deliberately separate from ``intelligence_trials``:
a trial row is an entitlement fact that must survive everything, a baseline is
derived state a recompute may rewrite. One table would put both lifecycles in
one row.

Revision ID: c4f19a6b02de
Revises: b7e2d4a8c1f6
Create Date: 2026-08-15
"""
import sqlalchemy as sa
from alembic import op

revision = "c4f19a6b02de"
down_revision = "b7e2d4a8c1f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "value_events",
        sa.Column("value_event_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("value_class", sa.String(length=16), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("basis", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thresholds_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("value_event_id"),
        sa.UniqueConstraint("organization_id", "event_key",
                            name="uq_value_event_key"),
    )
    op.create_index("ix_value_events_organization_id", "value_events",
                    ["organization_id"])
    op.create_index("ix_value_events_event_type", "value_events", ["event_type"])
    op.create_index("ix_value_events_org_occurred", "value_events",
                    ["organization_id", "occurred_at"])
    op.create_index("ix_value_events_org_class", "value_events",
                    ["organization_id", "value_class"])

    op.create_table(
        "evaluation_baselines",
        sa.Column("baseline_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("trial_id", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("evidence_gaps", sa.JSON(), nullable=False),
        sa.Column("thresholds_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["trial_id"], ["intelligence_trials.trial_id"]),
        sa.PrimaryKeyConstraint("baseline_id"),
    )
    op.create_index("ix_evaluation_baselines_organization_id",
                    "evaluation_baselines", ["organization_id"])
    op.create_index("ix_evaluation_baselines_trial_id", "evaluation_baselines",
                    ["trial_id"])
    op.create_index("ix_evaluation_baselines_org_captured", "evaluation_baselines",
                    ["organization_id", "captured_at"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_baselines_org_captured",
                  table_name="evaluation_baselines")
    op.drop_index("ix_evaluation_baselines_trial_id",
                  table_name="evaluation_baselines")
    op.drop_index("ix_evaluation_baselines_organization_id",
                  table_name="evaluation_baselines")
    op.drop_table("evaluation_baselines")

    op.drop_index("ix_value_events_org_class", table_name="value_events")
    op.drop_index("ix_value_events_org_occurred", table_name="value_events")
    op.drop_index("ix_value_events_event_type", table_name="value_events")
    op.drop_index("ix_value_events_organization_id", table_name="value_events")
    op.drop_table("value_events")
