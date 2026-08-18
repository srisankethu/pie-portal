"""State keys widen to fit the reducers' composite keys.

``business_states.key`` and ``state_transitions.key`` were ``String(64)`` —
sized for a single local id, while the reducers have always written composites:
``supplier`` keys ``vendor:product`` (up to 129 chars of two 64-char ids), and
``trade`` keys ``customer:product:2026-03`` (up to 137). SQLite enforces no
declared length, so every deployment to date stored those keys happily;
Postgres rejects them at the first fold with ``value too long for type
character varying(64)``. 160 fits the widest legitimate composite with room.

Purely a widening — no stored value changes, no index changes.

Revision ID: v2state_key_width
Revises: u1erp_connectors
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v2state_key_width"
down_revision = "u1erp_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("business_states", schema=None) as batch:
        batch.alter_column("key", existing_type=sa.String(64),
                           type_=sa.String(160), existing_nullable=False)
    with op.batch_alter_table("state_transitions", schema=None) as batch:
        batch.alter_column("key", existing_type=sa.String(64),
                           type_=sa.String(160), existing_nullable=False)


def downgrade() -> None:
    # Both tables are projections — rebuilt in full from business_events by
    # the state engine — so rows that would no longer fit are deleted rather
    # than truncated: a silently shortened key would join two different
    # subjects into one series, which is worse than an absent row that the
    # next build recreates.
    op.execute(sa.text("DELETE FROM state_transitions WHERE length(key) > 64"))
    op.execute(sa.text("DELETE FROM business_states WHERE length(key) > 64"))
    with op.batch_alter_table("state_transitions", schema=None) as batch:
        batch.alter_column("key", existing_type=sa.String(160),
                           type_=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("business_states", schema=None) as batch:
        batch.alter_column("key", existing_type=sa.String(160),
                           type_=sa.String(64), existing_nullable=False)
