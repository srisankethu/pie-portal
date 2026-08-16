"""Outcome snapshots: the baseline frozen when a recommendation is accepted.

The Outcome Tracker (task T11) measures the realised impact of accepted
decisions. The read model is rebuildable, so the baseline a realised delta is
measured against cannot be looked up later — it is captured at acceptance:
the signal's own metrics and window, the threshold version that judged the
signal (``th_…`` or ``ci_…``, verbatim), and the evaluation horizon in force.

Append-only: one row per decision (unique on ``decision_id``), written once by
``commercial.outcome_tracker.capture_on_accept`` and never updated. Evaluation
is computed on read from ``sales_txns``/``cost_records`` and is not stored.

``horizon_days`` is the literal value, not a config pointer, so a later config
edit cannot move a window an acceptance already anchored.

Revision ID: t11a4f8c2d9e1
Revises: d5a2e7c31b84
"""
from alembic import op
import sqlalchemy as sa

revision = "t11a4f8c2d9e1"
down_revision = "d5a2e7c31b84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outcome_snapshots",
        sa.Column("outcome_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.String(length=64),
                  sa.ForeignKey("decisions.decision_id"), nullable=False),
        sa.Column("signal_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=48), nullable=False),
        sa.Column("subject_entity_type", sa.String(length=32), nullable=False),
        sa.Column("subject_entity_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("baseline_window", sa.JSON(), nullable=False),
        sa.Column("thresholds_version", sa.String(length=32), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("outcome_snapshot_id"),
        sa.UniqueConstraint("decision_id", name="uq_outcome_snapshot_decision"),
    )
    op.create_index("ix_outcome_snapshots_organization_id", "outcome_snapshots",
                    ["organization_id"])
    op.create_index("ix_outcome_snapshots_signal_id", "outcome_snapshots",
                    ["signal_id"])
    op.create_index("ix_outcome_snapshots_category", "outcome_snapshots",
                    ["category"])
    op.create_index("ix_outcome_snapshots_subject_entity_id", "outcome_snapshots",
                    ["subject_entity_id"])
    op.create_index("ix_outcome_snapshots_org_category", "outcome_snapshots",
                    ["organization_id", "category"])
    op.create_index("ix_outcome_snapshots_org_accepted", "outcome_snapshots",
                    ["organization_id", "accepted_at"])


def downgrade() -> None:
    op.drop_index("ix_outcome_snapshots_org_accepted",
                  table_name="outcome_snapshots")
    op.drop_index("ix_outcome_snapshots_org_category",
                  table_name="outcome_snapshots")
    op.drop_index("ix_outcome_snapshots_subject_entity_id",
                  table_name="outcome_snapshots")
    op.drop_index("ix_outcome_snapshots_category",
                  table_name="outcome_snapshots")
    op.drop_index("ix_outcome_snapshots_signal_id",
                  table_name="outcome_snapshots")
    op.drop_index("ix_outcome_snapshots_organization_id",
                  table_name="outcome_snapshots")
    op.drop_table("outcome_snapshots")
