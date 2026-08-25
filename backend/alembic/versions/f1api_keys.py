"""API keys: the public resolution API's credential, and its tenant policy.

One table, one SECURITY DEFINER lookup. The table is ordinary; the lookup is
the part worth reading.

**Why a lookup function rather than a wider policy.** A caller presenting an
API key is in exactly the position sign-in and the OAuth callback are in
(``d1rls``, ``d3rls``): it holds a credential and no tenant, and the tenant is
a property of the row it is asking for. Under a fail-closed policy the ordinary
``select(ApiKey).where(key_id == …)`` returns nothing and every API call fails.
A policy clause wide enough to permit that lookup is wide enough to enumerate
the table one query at a time; ``app_api_key_org`` takes one key id, returns
one column, and there is no argument that makes it return a second row. Its
body is four lines and is the whole audited surface.

It is deliberately narrower than ``app_login_lookup``: that one returns the
user id as well, because sign-in then verifies a password against that row.
This returns the organization only — the caller announces the tenant and reads
the row through the policy like everything else, so the function is not a
key-id-to-anything oracle beyond the tenant it must reveal to work at all.

It also filters nothing. A revoked key still belongs to the organization that
minted it, and refusing to say so here would push the revocation check into a
place that cannot audit it; ``api_keys.principal_for`` refuses the revoked row
after reading it, which is where the refusal is visible.

``search_path`` is pinned inside the function for the reason ``d3rls`` gives:
a SECURITY DEFINER function resolving ``api_keys`` through the caller's
``search_path`` can be pointed at a table the caller made.

Revision ID: f1api
Revises: e1org
Create Date: 2026-08-25
"""
import sqlalchemy as sa
from alembic import op

revision = "f1api"
down_revision = "e1org"
branch_labels = None
depends_on = None

#: Written out literally rather than imported (§4): this runs against schemas
#: from months ago, and each revision has to stand on its own.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLE = "api_keys"

_API_KEY_ORG = """
CREATE OR REPLACE FUNCTION app_api_key_org(p_key_id text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT k.organization_id
    FROM public.api_keys AS k
    WHERE k.key_id = p_key_id
    LIMIT 1;
$$;
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False,
                  server_default=""),
        sa.Column("secret_hash", sa.String(length=256), nullable=False),
        sa.Column("secret_hint", sa.String(length=8), nullable=False,
                  server_default=""),
        sa.Column("role", sa.String(length=32), nullable=False,
                  server_default="SALESPERSON"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False,
                  server_default="60"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key_id"),
    )
    op.create_index("ix_api_keys_organization_id", TABLE, ["organization_id"])
    op.create_index("ix_api_keys_created_at", TABLE, ["created_at"])
    op.create_index("ix_api_keys_org", TABLE, ["organization_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies. A no-op is the honest form of that; ``d1rls``
        # says what it costs and why it is stated rather than emulated.
        return

    op.execute(_API_KEY_ORG)
    # EXECUTE is granted to PUBLIC by default for a new function, which for a
    # SECURITY DEFINER one is worth closing and reopening deliberately.
    op.execute("REVOKE ALL ON FUNCTION app_api_key_org(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app_api_key_org(text) TO PUBLIC")

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
        op.execute("DROP FUNCTION IF EXISTS app_api_key_org(text)")
    op.drop_index("ix_api_keys_org", table_name=TABLE)
    op.drop_index("ix_api_keys_created_at", table_name=TABLE)
    op.drop_index("ix_api_keys_organization_id", table_name=TABLE)
    op.drop_table(TABLE)
