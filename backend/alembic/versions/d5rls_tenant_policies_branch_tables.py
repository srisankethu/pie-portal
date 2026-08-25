"""The four tenant-scoped tables this branch added while the policies were written.

The mirror of ``d4rls``, in the other direction. That revision covered the
tables that landed on ``main`` while the policies were being written; these four
landed on a branch while the same thing was happening, and the merge is where
they meet. ``test_every_tenant_scoped_table_is_covered_or_deliberately_named``
named all four rather than letting them arrive quietly, which is the whole
reason that test exists.

All four are covered, on ``d2rls``'s rule — **a policy belongs on a table whose
cross-tenant reads are leaks; where they are the product, a policy is a
regression wearing the costume of a control** — and for each of these a
cross-tenant read is a leak:

- ``erp_quotes`` is every quote an ERP raised for this book: the customer, the
  document number in their own system, the value offered, and how it ended. It
  is the demand side of the business in one table.
- ``vendor_credits`` and ``vendor_credit_applications`` are returns and price
  corrections a supplier issued, against named bills. What a competitor could
  read off them is who supplies this book and where the relationship went wrong.
- ``threshold_versions`` holds the *pre-image* of every policy stamp — the
  margin floors and targets themselves, serialized. It is the one table in this
  set where a cross-tenant read hands over commercial policy verbatim, and it
  exists precisely so a stamp can be dereferenced, so "nobody would look" is not
  an argument.

**``erp_quotes`` was ``quote_documents`` on the branch, and the name is now
main's.** ``d4rls`` policies a *different* table under that name — the record of
a quote this platform wrote *into* a source system. Two tables pointing opposite
ways cannot share a name, the released one keeps it, and this covers the
renamed one. A reader comparing the two revisions should not conclude that
either policies the same table twice.

Revision ID: d5rls
Revises: 04b3c45610eb
Create Date: 2026-08-25
"""
from alembic import op

revision = "d5rls"
down_revision = "04b3c45610eb"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` or from a
#: predecessor (§4): this runs against schemas from months ago, and each
#: revision has to stand on its own.
GUC = "app.current_org"

POLICY = "tenant_isolation"

TABLES = ("erp_quotes", "threshold_versions", "vendor_credits",
          "vendor_credit_applications")


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
    """Removes enforcement from these four and leaves the rest alone."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
