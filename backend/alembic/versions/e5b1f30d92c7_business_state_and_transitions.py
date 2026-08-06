"""Business state, and the working that produced it.

Two new tables, both projections: every row is folded from ``business_events``
and can be dropped and rebuilt. Nothing here is a fact of its own.

``business_states`` is keyed on (organization, state, key, as_of). ``as_of`` is
part of the key on purpose — a state is a *series*, not a current value that
overwrites its own history, which is the only reason "what was inventory worth
in March" is answerable at all.

``state_transitions`` records which event moved which state key on which day,
and by how much. Replaced per (state, as_of) on each rebuild rather than
appended forever: the event log is the permanent record, and this is the
working of one fold.

Purely additive. Both tables are empty until a build runs — a projection is not
backfilled, it is computed.

Revision ID: e5b1f30d92c7
Revises: d9a4c60e7b18
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b1f30d92c7"
down_revision = "d9a4c60e7b18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_states",
        sa.Column("business_state_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("thresholds_version", sa.String(length=64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("business_state_id"),
        sa.UniqueConstraint("organization_id", "state", "key", "as_of",
                            name="uq_state_org_state_key_asof"),
    )
    with op.batch_alter_table("business_states", schema=None) as batch_op:
        # The read every consumer does: one state, one day, every key.
        batch_op.create_index("ix_state_org_state_asof",
                              ["organization_id", "state", "as_of"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_states_as_of"),
                              ["as_of"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_states_key"),
                              ["key"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_states_organization_id"),
                              ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_business_states_state"),
                              ["state"], unique=False)

    op.create_table(
        "state_transitions",
        sa.Column("state_transition_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        # No foreign key to business_events on purpose: a complete re-sync can
        # prune events while a rebuild is in flight, and a constraint would
        # turn a re-derivable projection into a blocking failure.
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_transition_id"),
    )
    with op.batch_alter_table("state_transitions", schema=None) as batch_op:
        # "Why does this number exist": every event that moved one key.
        batch_op.create_index("ix_transition_org_state_key",
                              ["organization_id", "state", "as_of", "key"],
                              unique=False)
        batch_op.create_index("ix_transition_event",
                              ["organization_id", "event_seq"], unique=False)
        batch_op.create_index(batch_op.f("ix_state_transitions_as_of"),
                              ["as_of"], unique=False)
        batch_op.create_index(batch_op.f("ix_state_transitions_event_seq"),
                              ["event_seq"], unique=False)
        batch_op.create_index(batch_op.f("ix_state_transitions_key"),
                              ["key"], unique=False)
        batch_op.create_index(batch_op.f("ix_state_transitions_occurred_on"),
                              ["occurred_on"], unique=False)
        batch_op.create_index(batch_op.f("ix_state_transitions_organization_id"),
                              ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_state_transitions_state"),
                              ["state"], unique=False)


def downgrade() -> None:
    op.drop_table("state_transitions")
    op.drop_table("business_states")
