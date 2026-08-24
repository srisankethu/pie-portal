"""The tenant-scoped tables that arrived while the policies were being written.

``d3rls`` closed the last of the tables this branch knew about. Three more
landed on ``main`` in the meantime — ``inbound_lines`` and
``inbound_line_dispositions`` (the enquiry text a customer sent, and how each
line of it ended), ``quote_documents`` (the document a quote became in an ERP)
and ``queued_messages`` (durable background work) — each carrying an
``organization_id`` and none carrying a policy.

That is exactly the state
``test_every_tenant_scoped_table_is_covered_or_deliberately_named`` exists to
make impossible to reach quietly: a new model with a tenant column must be
sorted into covered, waiting, or cross-tenant on purpose, and this revision is
the decision for these four. All four are covered, for the reason ``d2rls``
states — **a policy belongs on a table whose cross-tenant reads are leaks; where
they are the product, a policy is a regression wearing the costume of a
control** — and for all three a cross-tenant read is a leak:

- ``inbound_lines`` holds the customer's own words, stored verbatim and
  deliberately never encrypted (``trust/erasure`` says so in its disclosure).
  It is the most directly readable thing in the schema, and
  ``inbound_line_dispositions`` says what each of those lines became.
- ``quote_documents`` names a customer's document number in their own ERP.
- ``queued_messages`` carries a payload per organization.

**Why the queue worker is unaffected, which is the one that needed checking.**
``messaging.worker`` drains across tenants by construction — it claims the
oldest available message, whoever's it is — and a fail-closed policy would stop
it dead if it ran on the request connection. It does not: ``worker.py`` opens
``db.SessionLocal``, the privileged engine, the same one migrations and the
scheduler use. ``AppSessionLocal`` is the one policies bind, and only requests
use it. ``routers/internal`` reads the queue with an explicit
``organization_id`` filter already, so the policy narrows nothing there and
holds the line if that filter is ever dropped.

Revision ID: d4rls
Revises: d3rls
Create Date: 2026-08-24
"""
from alembic import op

revision = "d4rls"
down_revision = "d3rls"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` or from a
#: predecessor (§4): this runs against schemas from months ago, and each
#: revision has to stand on its own.
GUC = "app.current_org"

POLICY = "tenant_isolation"

TABLES = ("inbound_lines", "inbound_line_dispositions", "quote_documents",
          "queued_messages")


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
