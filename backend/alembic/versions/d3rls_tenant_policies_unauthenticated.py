"""The last six tables, and a narrow lookup for each path that reaches them.

``d1rls`` and ``d2rls`` policied 64 tables and left eight. Two of those —
``sync_runs`` and ``zoho_connections`` — stay out permanently: their
cross-tenant reads are the product (deployment capacity, credential sharing)
rather than leaks, and ``d2rls`` argues that at length. This revision closes the
other six, which were out for a different reason: something touches each of them
before any principal exists, and a fail-closed policy would have answered
nothing.

The shape is the one ``d1rls`` established for sign-in. Each unauthenticated
path gets **one narrow SECURITY DEFINER function** that answers the single
question it needs in order to know its tenant, and then announces it. A policy
clause wide enough to permit the same lookup would be wide enough to enumerate
the table one query at a time; a function is narrow in a way a predicate cannot
be, and its body is the whole audited surface.

* **Sign-up** asks whether an address is already registered.
  ``app_email_registered`` returns a boolean and nothing else — not a user id,
  not an organization — because that is all sign-up needs and anything more
  would make it an address-to-tenant oracle for anyone who can reach the form.
  It deliberately does **not** filter on ``active``, unlike
  ``app_login_lookup``: a deactivated user still holds their address, and a
  sign-up that ignored them would create a second account on an email that
  already has one. Without this the ordinary query returns nothing under policy,
  the refusal never fires, and two organizations end up sharing an owner
  address — which is not a broken feature but a corrupted one.

* **The Zoho OAuth callback** arrives holding a state token and nothing else.
  ``app_oauth_state_org`` maps its hash to the organization that issued it. The
  argument is already a hash of a single-use secret, so it is not guessable and
  the function leaks nothing to a caller who does not hold one.

* **Provisioning a tenant** derives ``org_<slug>`` and walks past collisions.
  ``app_org_id_taken`` answers whether one exists. Under policy the ordinary
  ``session.get`` sees no other tenant's row, so the walk would stop at the
  first candidate and the INSERT would fail on the primary key — a loud failure
  rather than a silent one, but a sign-up broken by another company having a
  similar name is still broken.

* **Sign-in** already had ``app_login_lookup`` from ``d1rls``, and the demo
  workspace reuses it — its user is an ordinary active row.

Four functions rather than one dispatching on a ``kind`` argument, deliberately.
Each is four lines and reads in one sitting; a single entry point taking a
string and branching would be the one place where a mistake reaches every
table, and it is exactly the growing type-chain §5 names. The cost is four
``REVOKE``/``GRANT`` pairs, which is the cheap half.

**A correction.** ``d1rls``'s docstring said ``audit_entries`` and
``audit_chain_heads`` could not be policied because "a failed sign-in for an
unknown address has no tenant to attribute the row to, so WITH CHECK would
refuse it". That was wrong, and the code says so: the LOGIN_FAILED write in
``routers/platform_auth`` sits inside ``if user is not None and user.active``,
so an unknown address writes no audit row at all — and whenever one *is*
written the user is active, which means ``app_login_lookup`` found them and the
tenant is already announced. The reason was stated confidently and was not
checked; it is corrected here rather than left standing, because a wrong reason
in a migration docstring outlives the person who wrote it.

**One degradation, named rather than fixed.** ``oauth.sweep_expired`` deletes
state rows past their expiry and is called from ``/connections/zoho/authorize``,
which runs with a principal. Under a policy it reaps only the caller's own
expired rows, so an organization that never authorizes again keeps a handful of
tiny expired rows indefinitely. That is a garbage-collection degradation, not a
correctness one, and moving it to the privileged connection would put a
policy-exempt delete in a request path to save a few rows. If it ever matters,
the answer is a scheduled sweep on the background connection, not an exemption
here.

Revision ID: d3rls
Revises: d2rls
Create Date: 2026-08-24
"""
from alembic import op

revision = "d3rls"
down_revision = "d2rls"
branch_labels = None
depends_on = None

GUC = "app.current_org"
POLICY = "tenant_isolation"

TABLES = (
    "organizations", "users", "user_sessions",
    "audit_entries", "audit_chain_heads", "oauth_states",
)

#: ``organizations`` keys on ``organization_id`` as its *primary* key rather
#: than as a foreign one, so the policy compares the same column by a different
#: name's worth of meaning: a tenant sees its own row and no other. Written out
#: because it reads as a special case and is not one.
_EMAIL_REGISTERED = """
CREATE OR REPLACE FUNCTION app_email_registered(p_email text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (SELECT 1 FROM public.users AS u WHERE u.email = p_email);
$$;
"""

_ORG_ID_TAKEN = """
CREATE OR REPLACE FUNCTION app_org_id_taken(p_organization_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (SELECT 1 FROM public.organizations AS o
                   WHERE o.organization_id = p_organization_id);
$$;
"""

_OAUTH_STATE_ORG = """
CREATE OR REPLACE FUNCTION app_oauth_state_org(p_state_hash text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT s.organization_id
    FROM public.oauth_states AS s
    WHERE s.state_hash = p_state_hash
    LIMIT 1;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for body, signature in ((_EMAIL_REGISTERED, "app_email_registered(text)"),
                            (_ORG_ID_TAKEN, "app_org_id_taken(text)"),
                            (_OAUTH_STATE_ORG, "app_oauth_state_org(text)")):
        op.execute(body)
        # EXECUTE is granted to PUBLIC by default for a new function, which for
        # a SECURITY DEFINER one is worth closing and reopening deliberately.
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC")

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING (organization_id = current_setting('{GUC}', true)) "
            f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    """Removes enforcement from these six and the two lookups they need.

    ``app_login_lookup`` is ``d1rls``'s and is deliberately left alone: sign-in
    depends on it whenever any of that revision's tables are policied, and
    dropping it here would break authentication on a database that had only
    stepped back one revision.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_oauth_state_org(text)")
    op.execute("DROP FUNCTION IF EXISTS app_org_id_taken(text)")
    op.execute("DROP FUNCTION IF EXISTS app_email_registered(text)")
