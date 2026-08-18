"""user_sessions — make a sign-in a row that can be revoked

Revision ID: c1f4a80b73e2
Revises: v2state_key_width
Create Date: 2026-08-17

Columns written out literally rather than imported from the models: this
migration has to keep working against the schema as it is today, and the models
describe whatever they describe by the time somebody replays it.

No data migration. Tokens minted before this revision carry no `sid`, so
`authz.load_principal` rejects them and everyone signs in once more — which is
the correct outcome for a change whose point is that a token alone is no longer
authority.
"""
from alembic import op
import sqlalchemy as sa

revision = "c1f4a80b73e2"
down_revision = "v2state_key_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("session_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_organization_id", "user_sessions",
                    ["organization_id"])
    op.create_index("ix_user_sessions_issued_at", "user_sessions", ["issued_at"])
    op.create_index("ix_user_sessions_user_live", "user_sessions",
                    ["user_id", "revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_user_sessions_user_live", table_name="user_sessions")
    op.drop_index("ix_user_sessions_issued_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_organization_id", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
