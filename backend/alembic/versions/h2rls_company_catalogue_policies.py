"""Tenant policies for the two per-company catalogue tables.

Both carry ``organization_id`` and for both a cross-tenant read is a leak, which
is ``d2rls``'s rule — a policy belongs on a table whose cross-tenant reads are
leaks; where they are the product, a policy is a regression wearing the costume
of a control.

- ``company_corpora`` holds the item-master export itself: every part number,
  description and grade a company sells, in one blob. It is the closest thing in
  this schema to handing over a competitor's catalogue wholesale.
- ``company_catalogues`` says which pack and ruleset a company decodes through,
  how many of its rows the engine could classify and which tokens it could not.
  That is a description of the shape of their master data.

Neither is written by an unauthenticated path, so neither needs ``d3rls``'s
SECURITY DEFINER treatment: an owner uploads and builds, and both carry a
principal.

Revision ID: h2rls
Revises: h1cat
Create Date: 2026-08-30
"""
from alembic import op

revision = "h2rls"
down_revision = "h1cat"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` or from a
#: predecessor (§4): this runs against schemas from months ago, and each
#: revision has to stand on its own.
GUC = "app.current_org"

POLICY = "tenant_isolation"

TABLES = ("company_corpora", "company_catalogues")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies. A no-op is the honest form of that; see
        # ``d1rls`` for what it costs and why it is stated rather than emulated.
        return

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING (organization_id = current_setting('{GUC}', true)) "
            f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    """Removes enforcement from these two and leaves the rest alone."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
