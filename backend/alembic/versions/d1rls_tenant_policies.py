"""Tenant isolation enforced by the database, on a first set of tables.

Every tenant-scoped query in this codebase filters on ``organization_id`` and 72
of the 74 models carry the column. That is the control today and it holds
exactly as long as nobody forgets — and the survey that preceded this revision
found places where the filter is a comprehension rather than a WHERE clause. A
policy attached to the table is the version that holds for the query nobody
reviewed.

**Why this is the first migration in this repository that branches on the
dialect.** Every previous one avoided it: partial indexes pass ``sqlite_where``
and ``postgresql_where`` with identical SQL, and ALTERs go through
``render_as_batch``. There is no such trick here. SQLite has no row-level
security, no policies, and no connection settings, so there is nothing to
express — this migration is a no-op there and says so rather than pretending
otherwise. What it costs is real and belongs in the open: on the dialect dev
and most of the suite run, the Python filters remain the only tenant boundary,
and a policy defect would not show up until PostgreSQL.

**Why FORCE as well as ENABLE.** ``ENABLE ROW LEVEL SECURITY`` does not apply to
a table's owner, and the owner is the role that runs migrations — on the compose
stack it is also a superuser. ``FORCE`` binds the owner too. Nothing binds
``rolbypassrls``, which is why ``APP_DATABASE_URL`` and the ``tenant_isolation``
health component exist: a deployment that serves requests as a superuser is not
protected by anything here, and it is told so rather than left to assume.

**Why the policy is fail-closed by construction.** ``current_setting(…, true)``
is NULL when the setting has never been assigned; ``organization_id = NULL`` is
NULL; NULL is not true; no row passes. A connection that never announced a
tenant sees nothing, rather than everything, and that comes from SQL's
three-valued logic rather than from a clause somebody remembered to write.

**Why WITH CHECK as well as USING.** ``USING`` governs which rows a statement
can *see* — it already makes a cross-tenant UPDATE or DELETE match nothing.
``WITH CHECK`` governs what a row may become, and without it a tenant could
INSERT a row carrying another organization's id, or UPDATE one of its own rows
to hand it over. Reads and writes are separate halves and a policy with only
the first is a common way to leave the second open.

**Why sign-in needs a function.** It is the one request that cannot know its
tenant in advance: there is no token yet and the organization is a property of
the row being looked for. A policy clause wide enough to let an unauthenticated
caller find a user by email is wide enough to enumerate the table one query at
a time; a SECURITY DEFINER function is narrow in a way a predicate cannot be —
one email in, two columns out, no argument that returns a third.
``search_path`` is pinned in its definition because a SECURITY DEFINER function
that resolves ``users`` through the caller's path can be pointed at a table the
caller made, which is the classic way this construct becomes an escalation.

**Why only these tables.** A policy is fail-closed, so a table goes under one
only once every path that reads it in a request has a principal — and several
do not (sign-in, the Zoho OAuth callback, health). The tables below are read in
the request path only behind ``current_principal``, and written by background
jobs through the privileged connection, which is unaffected. ``users``,
``user_sessions``, ``organizations`` and ``oauth_states`` are deliberately not
here: each is read on a path with no principal yet, and each needs its own
answer. ``audit_entries`` is not here either, for a narrower reason — sign-in
records a failure for an address that may match no user at all, so there is no
tenant to attribute the row to and ``WITH CHECK`` would refuse it.

This is a first set, and calling it that in the docstring is deliberate: a
reader who assumes every tenant table is covered would draw a stronger
conclusion than the code supports.

Revision ID: d1rls
Revises: c9audit
Create Date: 2026-08-24
"""
from alembic import op

revision = "d1rls"
down_revision = "c9audit"
branch_labels = None
depends_on = None

#: The connection setting every policy below reads. Written out literally
#: rather than imported from ``app.tenancy`` (§4): this runs against schemas
#: from months ago, and the module describes today.
GUC = "app.current_org"

POLICY = "tenant_isolation"

#: Tables under the policy. Written out literally, and *not* derived from
#: ``Base.metadata`` — a migration that enumerated the models would silently
#: widen itself every time a model was added, which is exactly the change that
#: should require somebody to think about whether an unauthenticated path reads
#: the new table.
TABLES = (
    "value_events",
    "quote_decisions",
    "signals",
    "decisions",
    "customer_item_metrics",
    "approval_requests",
)

_LOOKUP = """
CREATE OR REPLACE FUNCTION app_login_lookup(p_email text)
RETURNS TABLE (user_id text, organization_id text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT u.user_id, u.organization_id
    FROM public.users AS u
    WHERE u.email = p_email AND u.active
    LIMIT 1;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # See the docstring. Nothing to express on SQLite, and a no-op is the
        # honest form of that rather than an emulation that would give the
        # suite a false sense of coverage.
        return

    op.execute(_LOOKUP)
    # EXECUTE is granted to PUBLIC by default for a new function, which for a
    # SECURITY DEFINER function is worth closing deliberately even though the
    # only roles on this database are the owner and the application.
    op.execute("REVOKE ALL ON FUNCTION app_login_lookup(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app_login_lookup(text) TO PUBLIC")

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING (organization_id = current_setting('{GUC}', true)) "
            f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    """Removes the enforcement and leaves every row where it is.

    Unlike the audit chain's migration this destroys nothing — a policy is not
    data. What it does mean is that a deployment which had been relying on the
    database for isolation goes back to relying on the application's own
    filters, silently, so this is a step to take deliberately rather than to
    reach for when something else is failing.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_login_lookup(text)")
