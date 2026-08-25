"""The rest of the tenant-scoped tables, and the three reasons some are still out.

``d1rls`` policied six tables and called itself a first set. This is the rest of
what is eligible: 58 more, bringing the count to 64 of the 72 tables carrying an
``organization_id``. The policy shape is identical and deliberately written out
again rather than imported — each revision must stand alone (§4), and a
migration that reached into its predecessor would fail on the one deployment
that ran them out of order.

**Eight tables are still out, for three different reasons, and the difference
matters more than the count.**

*Read with no tenant announced.* ``users``, ``organizations``, ``user_sessions``,
``audit_entries``, ``audit_chain_heads``, ``oauth_states``. Sign-up, the demo
workspace and the Zoho OAuth callback each touch one of these before any
principal exists, and a fail-closed policy would return nothing. Sign-in already
has its answer — ``app_login_lookup`` from ``d1rls`` — and the other two paths
need one of their own before these can move. This is unfinished work, not a
decision.

*Cross-tenant by design rather than by accident.* ``sync_runs`` and
``zoho_connections``. These are the ones worth reading carefully, because
policing them would look like tightening and would actually break something:

- ``sync_runs`` is what worker capacity is computed from
  (``observability/capacity.calculate_worker_utilization``, which asks
  ``jobs.unfinished_runs`` with no organization). Capacity is a property of the
  *deployment* — how many jobs the cluster is running against how many it can —
  and a version of it that counted only the caller's own runs would not be a
  narrower answer, it would be a wrong one. A number that silently starts
  understating cluster load is worse than no number.
- ``zoho_connections`` carries credential sharing, which is a *feature*: one
  Zoho grant reaches every company that user can see, and
  ``/data/credentials/{id}/organizations`` exists to say which. A policy there
  does not secure the feature, it removes it.

The rule those two make explicit, and it is the one to apply to the next table
somebody considers: **a policy belongs on a table whose cross-tenant reads are
leaks. Where they are the product, a policy is a regression wearing the costume
of a control.**

``zoho_credentials`` and ``process_leases`` carry no ``organization_id`` at all
and cannot take this policy shape — the first because a Zoho refresh token
belongs to a person rather than a company, the second because it is
infrastructure about processes.

**What silently narrows, and is a leak closing.** ``GET /data/credentials`` and
``GET /data/credentials/{id}/organizations`` read ``zoho_connections``, which
stays out — so they are unaffected. Nothing else in the request path reads one
of the 58 below without a principal. ``d1rls`` already recorded the one endpoint
that did change (``/observability/tenants``, via ``signals``).

Revision ID: d2rls
Revises: d1rls
Create Date: 2026-08-24
"""
from alembic import op

revision = "d2rls"
down_revision = "d1rls"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` or from
#: ``d1rls`` (§4): this runs against schemas from months ago, and each revision
#: has to stand on its own.
GUC = "app.current_org"

POLICY = "tenant_isolation"

#: Enumerated, not derived from ``Base.metadata``. A migration that read the
#: models would widen itself every time one was added — which is exactly the
#: change that should make somebody check whether an unauthenticated path reads
#: the new table.
TABLES = (
    "access_events", "access_grants", "ai_call_logs", "ai_provider_keys",
    "bill_payment_applications", "bills", "business_events", "business_states",
    "commercial_policies", "confirmed_code_mappings", "cost_records",
    "credit_note_applications", "credit_notes", "customer_account_owners",
    "customer_connector_records", "customer_credit_limits",
    "customer_identities", "customers", "erasure_receipts",
    "evaluation_baselines", "identity_events", "identity_policies",
    "identity_suggestions", "ingested_documents", "intelligence_trials",
    "invoice_sales_orders", "invoices", "item_category_overrides",
    "item_connector_records", "item_identities", "locations", "model_payloads",
    "name_vault", "org_policies", "outcome_snapshots", "outcomes",
    "payment_applications", "payment_receipts", "plan_change_requests",
    "products", "purchase_orders", "quote_drafts", "quote_outcomes",
    "sales_orders", "sales_txns", "state_transitions",
    "stock_location_snapshots", "stock_snapshots", "sync_run_logs",
    "sync_skipped_rows", "tenant_keys", "tender_results",
    "vendor_msme_statuses", "vendor_payment_terms", "vendor_payments",
    "vendor_scheme_slabs", "vendor_targets", "vendors",
)


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
    """Removes enforcement from these 58 and leaves ``d1rls``'s six alone.

    Destroys nothing — a policy is not data. What it does mean is that a
    deployment relying on the database for isolation goes back to relying on the
    application's own filters, silently and on 58 tables at once, so this is a
    step to take deliberately rather than to reach for when something else is
    failing.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
