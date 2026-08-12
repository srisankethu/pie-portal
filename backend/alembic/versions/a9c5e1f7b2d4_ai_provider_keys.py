"""Per-organization AI provider keys (BYOK).

Until now the AI layer had exactly one live provider and one key, both set as
environment variables — turning the AI on was a deployment act. This table lets
an organization bring its own key for Anthropic, OpenAI or Google Gemini from
the Settings screen instead: one row per (organization, provider), the key
encrypted at rest exactly as Zoho credentials are, with a stored last-four hint
so no read path ever needs to decrypt. Which stored key actually runs is the
organization's active-provider choice in ``organizations.config`` — a JSON key,
so no schema change is needed for it.

Revision ID: a9c5e1f7b2d4
Revises: e4f7a2c9d310
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "a9c5e1f7b2d4"
down_revision = "e4f7a2c9d310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_keys",
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_key_encrypted", sa.String(length=2048), nullable=False),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"],
                                ["organizations.organization_id"]),
        sa.PrimaryKeyConstraint("key_id"),
        sa.UniqueConstraint("organization_id", "provider",
                            name="uq_ai_provider_key_org_provider"),
    )
    op.create_index("ix_ai_provider_keys_organization_id", "ai_provider_keys",
                    ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_provider_keys_organization_id",
                  table_name="ai_provider_keys")
    op.drop_table("ai_provider_keys")
