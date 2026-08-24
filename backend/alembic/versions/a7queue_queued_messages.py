"""A durable queue for background work, so a queued job survives the process.

Background work is dispatched to a thread today, and a thread dies with its
process: a sync that was QUEUED when the container was replaced is never run,
and ten minutes later its row is reaped as stale. This table holds the request
itself — committed before anything starts, claimed atomically, retried with a
bounded backoff, and readable during an incident.

Append-and-update, and derived: nothing computes a number off these rows.
Dropping the table loses the account of what ran and breaks nothing else.

Chained after `a7inbound` rather than beside it. Both were written from
`z6subject` on branches open at the same time, which left the history with two
heads. The textbook join is a merge revision — and this repository cannot use
one: `test_migrations_apply_one_at_a_time` and
`test_the_newest_migration_is_reversible` walk the chain with `upgrade +1` /
`downgrade -1`, and a revision with two parents makes a relative walk an
"Ambiguous walk" that Alembic refuses. The tests are the statement that this
history stays linear. Re-pointing an *unreleased* revision is the permitted
half of §4's rule; the released one it now follows was not touched.

Revision ID: a7queue
Revises: a7inbound
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "a7queue"
down_revision = "a7inbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "queued_messages",
        sa.Column("message_id", sa.String(64), primary_key=True),
        sa.Column("topic", sa.String(64), nullable=False),
        # Not nullable: a message with no payload is a handler that will be
        # called with nothing and fail three times to say so. Empty is `{}`.
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        # 191 rather than 255: the width MySQL can index under utf8mb4, and a
        # dedupe key names work ("sync:<org>:<connection>"), never free text.
        sa.Column("dedupe_key", sa.String(191), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_queued_messages_topic", "queued_messages", ["topic"])
    op.create_index("ix_queued_messages_organization_id", "queued_messages",
                    ["organization_id"])
    op.create_index("ix_queued_messages_status", "queued_messages", ["status"])
    op.create_index("ix_queued_messages_available_at", "queued_messages",
                    ["available_at"])
    op.create_index("ix_queued_messages_created_at", "queued_messages",
                    ["created_at"])
    # The claim query: the oldest message that is PENDING and due.
    op.create_index("ix_queued_messages_claimable", "queued_messages",
                    ["status", "available_at"])
    # The dedupe lookup, which runs on every enqueue.
    op.create_index("ix_queued_messages_dedupe", "queued_messages",
                    ["dedupe_key", "status"])


def downgrade() -> None:
    op.drop_index("ix_queued_messages_dedupe", table_name="queued_messages")
    op.drop_index("ix_queued_messages_claimable", table_name="queued_messages")
    op.drop_index("ix_queued_messages_created_at", table_name="queued_messages")
    op.drop_index("ix_queued_messages_available_at", table_name="queued_messages")
    op.drop_index("ix_queued_messages_status", table_name="queued_messages")
    op.drop_index("ix_queued_messages_organization_id", table_name="queued_messages")
    op.drop_index("ix_queued_messages_topic", table_name="queued_messages")
    op.drop_table("queued_messages")
