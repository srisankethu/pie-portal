"""What a person taught the system by choosing: a customer's phrase, and the
product that went on the quote for it.

One table, ``customer_phrase_aliases``, read by the retrieval pass
(``app/retrieval/aliases``) so a line close to a phrase this customer already
had quoted is *offered* the same record — beneath the engine's ranking, as a
possibility, never selected. Its own table rather than a row kind on
``confirmed_code_mappings`` because that table is asserted identity and the
engine resolves it authoritatively; a phrase asserts nothing.

Policied on ``d2rls``'s rule: a cross-tenant read of this table is what a
competitor's customers ask for in their own words and what was sold to them,
which is a leak, so it carries the tenant policy from the first row.

Revision ID: l1alias
Revises: k1fields
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op

revision = "l1alias"
down_revision = "k1fields"
branch_labels = None
depends_on = None

#: Written out literally (§4): each revision stands on its own.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLE = "customer_phrase_aliases"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("alias_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("identity_id", sa.String(length=64), nullable=False),
        sa.Column("phrase", sa.String(length=512), nullable=False),
        sa.Column("phrase_key", sa.String(length=512), nullable=False),
        sa.Column("target_record_id", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("superseded_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("alias_id"),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index("ix_phrase_alias_lookup",
                              ["organization_id", "identity_id", "phrase_key", "active"],
                              unique=False)
        batch_op.create_index(batch_op.f("ix_customer_phrase_aliases_active"),
                              ["active"], unique=False)
        batch_op.create_index(batch_op.f("ix_customer_phrase_aliases_created_at"),
                              ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_customer_phrase_aliases_identity_id"),
                              ["identity_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_customer_phrase_aliases_organization_id"),
                              ["organization_id"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return                      # SQLite has no policies; see d1rls
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
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_customer_phrase_aliases_organization_id"))
        batch_op.drop_index(batch_op.f("ix_customer_phrase_aliases_identity_id"))
        batch_op.drop_index(batch_op.f("ix_customer_phrase_aliases_created_at"))
        batch_op.drop_index(batch_op.f("ix_customer_phrase_aliases_active"))
        batch_op.drop_index("ix_phrase_alias_lookup")
    op.drop_table(TABLE)
