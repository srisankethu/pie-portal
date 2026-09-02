"""Quote fields an organization defines, and who may change a quote.

Two things the workspace (``j1draft``) left open:

  * A quote carried nothing but a customer and its lines. A business whose
    quotes must name the customer's reference, a validity date or payment
    terms had nowhere to put them and nothing to stop a quote going out
    without them. ``quote_field_definitions`` is the organization's list of
    quote-level fields — the built-in handful plus its own — with ``required``
    as the thing the send enforces; ``quote_drafts.fields`` holds the values.
  * Every quote has an owner (``quote_drafts.salesperson_id``, whoever started
    it) and only the owner changes it. ``org_policies.managers_may_edit_any_quote``
    is the one widening, on by default: a quote whose only editor is on leave
    is a quote somebody re-types.

The new table carries ``organization_id`` and takes the same tenant policy
every other tenant-scoped table does (``d2rls``'s rule: a cross-tenant read of
which fields a competitor asks on every quote is a leak, if a small one).
Written by an owner, so no SECURITY DEFINER path.

Revision ID: k1fields
Revises: j1draft
Create Date: 2026-09-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "k1fields"
down_revision = "j1draft"
branch_labels = None
depends_on = None

#: Written out literally rather than imported (§4): each revision stands alone.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLE = "quote_field_definitions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("field_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(48), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="TEXT"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("choices", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "key", name="uq_quote_field_org_key"),
    )
    op.create_index("ix_quote_field_definitions_organization_id", TABLE,
                    ["organization_id"])

    with op.batch_alter_table("quote_drafts", schema=None) as batch:
        # NULL rather than a server default of '{}': SQLite and Postgres
        # disagree on how a JSON default is spelled, and the application reads
        # a missing value as an empty mapping anyway.
        batch.add_column(sa.Column("fields", sa.JSON(), nullable=True))
    op.execute("UPDATE quote_drafts SET fields = '{}' WHERE fields IS NULL")
    with op.batch_alter_table("quote_drafts", schema=None) as batch:
        batch.alter_column("fields", existing_type=sa.JSON(), nullable=False)

    with op.batch_alter_table("org_policies", schema=None) as batch:
        batch.add_column(sa.Column("managers_may_edit_any_quote", sa.Boolean(),
                                   nullable=False, server_default=sa.true()))

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies; see ``d1rls`` for why this is stated.
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} "
        f"USING (organization_id = current_setting('{GUC}', true)) "
        f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    with op.batch_alter_table("org_policies", schema=None) as batch:
        batch.drop_column("managers_may_edit_any_quote")
    with op.batch_alter_table("quote_drafts", schema=None) as batch:
        batch.drop_column("fields")
    op.drop_index("ix_quote_field_definitions_organization_id", table_name=TABLE)
    op.drop_table(TABLE)
