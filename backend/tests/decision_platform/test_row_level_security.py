"""Row-level security, and the two ways it is silently not there.

Tenant isolation in this codebase is a Python convention: 72 of the 74 models
carry `organization_id` and every query is expected to filter on it. That holds
exactly as long as nobody forgets, and the survey behind this file found places
where the filter is a comprehension rather than a WHERE clause. PostgreSQL row
level security is the version that holds for the query nobody reviewed.

It is also the security control most likely to be *installed and inert*, which
is why this file starts by proving the harness can tell the difference:

* **A superuser ignores every policy.** `rolbypassrls` is not a check that can
  be forced or revoked per table — it is absolute. `initdb -U pie` makes the
  sandbox's role a superuser, and `postgres:17-alpine`'s entrypoint does the
  same for `POSTGRES_USER`, so a policy suite run over the *owner* connection
  would pass while proving nothing at all.
* **A table's owner ignores its policies too**, unless the table is set to
  FORCE ROW LEVEL SECURITY. Migrations run as the owner, so this is not a
  hypothetical role nobody uses.

So every test below runs over `PIE_TEST_RLS_URL` — a connection as a role that
is neither, provisioned by `scripts/pg_sandbox.sh` — and the first test asserts
that the owner connection *does* bypass, because a harness that cannot
demonstrate the bypass cannot demonstrate its absence either.

The isolation property itself is asserted the way §1 asks for: not "the policy
exists" but "with no tenant announced, the count is zero" — a green check over
an empty set is the failure mode this whole file is written against, so the
fixtures seed rows for two tenants first and every assertion names which of
them it expected to be missing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import tenancy

#: A connection as the *application* role: NOSUPERUSER, NOBYPASSRLS, and the
#: owner of nothing. `scripts/pg_sandbox.sh app-url` prints it; `verify.sh`
#: exports it for step 6. Unset, this whole module skips — and skipping is
#: reported rather than silent, because these are the only tests in the suite
#: that can say anything about row-level security at all.
RLS_URL = os.environ.get("PIE_TEST_RLS_URL", "")

#: The owner connection to the same database. Used to build the fixtures and to
#: prove the bypass; never to assert isolation.
OWNER_URL = os.environ.get("PIE_TEST_RLS_OWNER_URL", "")

pytestmark = pytest.mark.skipif(
    not (RLS_URL and OWNER_URL),
    reason="PIE_TEST_RLS_URL/PIE_TEST_RLS_OWNER_URL unset: row-level security "
           "cannot be exercised without a PostgreSQL role that does not bypass "
           "it. `scripts/pg_sandbox.sh start` provisions one.")

TABLE = "rls_probe"
ORG_A, ORG_B = "org_alpha", "org_beta"


@pytest.fixture()
def probe():
    """A tenant-scoped table under the same policy shape the migration will use.

    A scratch table rather than a real one, deliberately. What is being pinned
    here is the *mechanism* — that a fail-closed policy is fail-closed, that
    FORCE binds an owner, that `tenancy.set_tenant` reaches the setting the
    policy reads — and pinning it on `value_events` would make these tests fail
    for reasons about the attribution ledger. The migration that puts real
    tables under this policy is the thing that has to match this shape, and it
    is asserted against this shape in `test_the_policy_shape_matches_tenancy`.
    """
    owner = create_engine(OWNER_URL, future=True)
    with owner.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        conn.execute(text(
            f"CREATE TABLE {TABLE} (id text PRIMARY KEY, "
            "organization_id text NOT NULL, secret text NOT NULL)"))
        conn.execute(text(
            f"INSERT INTO {TABLE} VALUES "
            "('a1', :a, 'alpha-only'), ('a2', :a, 'alpha-too'), "
            "('b1', :b, 'beta-only')"), {"a": ORG_A, "b": ORG_B})
        # Granted here rather than relied on from `pg_sandbox.sh`, which does
        # grant both at start. Step 6 of the gate runs
        # `verify_pg_migrations.py` against this same database first, and that
        # drops and recreates schema `public` — taking every grant on it with
        # it. The symptom is not "permission denied" but
        # `relation "rls_probe" does not exist`, because a schema the role
        # cannot USE holds nothing it can see, which is a confusing hour to
        # spend. The fixture owning both grants makes this module independent
        # of what ran before it.
        conn.execute(text("GRANT USAGE ON SCHEMA public TO pie_app"))
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} "
                          "TO pie_app"))
        conn.execute(text(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY"))
        # Named `probe_isolation`, not `tenant_isolation`. The structural
        # tests further down ask `pg_policies` which tables the *migration*
        # policied; a scratch table sharing that policy name puts itself in the
        # answer, and the first version of this file failed for exactly that
        # reason. The shape is otherwise identical, WITH CHECK included.
        conn.execute(text(
            f"CREATE POLICY probe_isolation ON {TABLE} "
            f"USING (organization_id = current_setting('{tenancy.GUC}', true)) "
            f"WITH CHECK (organization_id = "
            f"current_setting('{tenancy.GUC}', true))"))
    try:
        yield owner
    finally:
        # `lock_timeout`, following `dbsupport._fresh_postgres`. A test that
        # leaves a transaction open on the probe table makes this DROP wait for
        # it, and an unbounded wait in teardown is a gate that hangs instead of
        # failing — which was watched happening once while this file was being
        # written, and is far worse than a red test.
        with owner.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        owner.dispose()


@pytest.fixture()
def app_session(probe):
    """A session over the non-bypassing role, in its own transaction."""
    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    session = Maker()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _rows(session) -> set[str]:
    return set(session.execute(text(f"SELECT id FROM {TABLE}")).scalars())


# ── the harness can tell installed from enforcing ────────────────────────────
def test_the_owner_connection_bypasses_every_policy(probe):
    """The control on the control.

    If this ever starts failing, it does not mean isolation improved — it means
    the fixtures are no longer running as the owner, and every other test in
    this file has quietly stopped proving what it claims. The bypass is the
    reference against which the absence of a bypass is measured.
    """
    with probe.connect() as conn:
        ids = set(conn.execute(text(f"SELECT id FROM {TABLE}")).scalars())
    assert ids == {"a1", "a2", "b1"}, (
        "the fixture connection should see every tenant's rows — it is a "
        "superuser, and rolbypassrls is absolute")


def test_the_application_role_is_neither_superuser_nor_bypassing(app_session):
    """Asserted rather than assumed. A role that silently gained BYPASSRLS
    would turn every isolation test below into a green check over nothing."""
    row = app_session.execute(text(
        "SELECT rolsuper, rolbypassrls FROM pg_roles "
        "WHERE rolname = current_user")).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False


# ── fail closed ──────────────────────────────────────────────────────────────
def test_a_connection_that_names_no_tenant_sees_nothing(app_session):
    """The property the whole design rests on, and it comes from SQL's
    three-valued logic rather than from a check somebody wrote:
    `current_setting(…, true)` is NULL when unset, `column = NULL` is NULL,
    and NULL is not true, so no row passes.

    Note what is *not* asserted: that an error is raised. A query with no tenant
    succeeds and returns nothing, which is the correct shape — a caller that
    forgot gets an empty result, never another tenant's rows.
    """
    assert _rows(app_session) == set(), (
        "a connection with no tenant announced must see no tenant rows")


def test_announcing_a_tenant_reveals_exactly_that_tenant(app_session):
    tenancy.set_tenant(app_session, ORG_A)
    assert _rows(app_session) == {"a1", "a2"}

    tenancy.set_tenant(app_session, ORG_B)
    assert _rows(app_session) == {"b1"}


def test_clearing_the_tenant_closes_the_door_again(app_session):
    tenancy.set_tenant(app_session, ORG_A)
    assert _rows(app_session) == {"a1", "a2"}

    tenancy.clear_tenant(app_session)
    assert _rows(app_session) == set(), (
        "clear_tenant must restore NULL, not set an empty string — an empty "
        "string merely fails to match, and would start matching the day a "
        "column somewhere is empty too")


def test_an_empty_tenant_is_refused_rather_than_written(app_session):
    """`set_config(…, '')` would leave the GUC non-NULL. Both states deny today;
    only one of them keeps denying for a structural reason."""
    with pytest.raises(ValueError):
        tenancy.set_tenant(app_session, "")
    assert tenancy.current_tenant(app_session) is None


def test_a_tenant_id_cannot_end_the_statement_and_start_another(app_session):
    """The reason `set_config` is used rather than `SET LOCAL`: the latter
    cannot take a bind parameter, so the value — a string that arrived over the
    network inside a token — would have to be interpolated into statement text.
    """
    hostile = f"{ORG_A}'; DROP TABLE {TABLE}; --"
    tenancy.set_tenant(app_session, hostile)

    assert tenancy.current_tenant(app_session) == hostile
    assert _rows(app_session) == set(), "a forged tenant matches no rows"
    # And the table is still there, which is the actual assertion.
    tenancy.set_tenant(app_session, ORG_A)
    assert _rows(app_session) == {"a1", "a2"}


# ── the setting does not outlive the request that made it ────────────────────
def test_a_tenant_does_not_survive_the_transaction_that_announced_it(probe):
    """`set_config(…, is_local => true)` reverts at commit or rollback, which
    matters because connections are pooled: a session-scoped SET would leak one
    request's tenant into whatever the connection served next, and only under
    load.
    """
    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        first = Maker()
        tenancy.set_tenant(first, ORG_A)
        assert _rows(first) == {"a1", "a2"}
        first.commit()
        first.close()

        second = Maker()
        try:
            assert tenancy.current_tenant(second) is None, (
                "the tenant leaked past the transaction that set it")
            assert _rows(second) == set()
        finally:
            second.rollback()
            second.close()
    finally:
        engine.dispose()


# ── writes are scoped too, not only reads ────────────────────────────────────
def test_a_tenant_cannot_read_another_tenants_row_by_updating_it(app_session):
    """A USING clause governs which rows an UPDATE or DELETE can *see*, so a
    cross-tenant write matches nothing rather than succeeding silently. Worth
    pinning separately: it is easy to write a policy that scopes SELECT and
    leaves the write path open, and the failure is invisible until somebody
    audits row counts."""
    tenancy.set_tenant(app_session, ORG_A)

    updated = app_session.execute(text(
        f"UPDATE {TABLE} SET secret = 'tampered' WHERE id = 'b1'")).rowcount
    assert updated == 0

    deleted = app_session.execute(text(
        f"DELETE FROM {TABLE} WHERE id = 'b1'")).rowcount
    assert deleted == 0


# ── the component that reports whether any of this is enforcing ─────────────
def _isolation_check(monkeypatch, serving_url: str):
    """The registered `tenant_isolation` check, over a chosen serving role.

    `app.db` decides its engines at import, so the role cannot be changed by
    setting an environment variable from inside a test. Patching the two names
    the check reads is the honest alternative: it exercises the *check's*
    logic, which is what these two tests are about — that the split itself
    reaches the request path is pinned in `test_request_connection.py`.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import db
    from app.observability.health import health, register_health_checks

    engine = create_engine(serving_url, future=True)
    monkeypatch.setattr(db, "app_engine", engine)
    monkeypatch.setattr(db, "AppSessionLocal", sessionmaker(bind=engine,
                                                            future=True))
    saved = dict(health._components)
    health._components.clear()
    register_health_checks(object(), object())
    check = health._components["tenant_isolation"].check_fn
    try:
        yield check
    finally:
        health._components.clear()
        health._components.update(saved)
        engine.dispose()


def test_a_superuser_serving_role_is_reported_unhealthy(monkeypatch):
    """The state every deployment of this codebase is in until somebody sets
    `APP_DATABASE_URL`, and it must not read as fine. Deliberately not softened
    by whether any policy exists yet: a connection that cannot be governed is
    the finding, and policies arriving later would silently do nothing."""
    from app.observability.health import HealthStatus

    for check in _isolation_check(monkeypatch, OWNER_URL):
        status, message = check()
        assert status == HealthStatus.UNHEALTHY
        assert "bypasses every row-level security policy" in (message or "")


def test_a_non_bypassing_serving_role_is_reported_healthy(monkeypatch):
    from app.observability.health import HealthStatus

    for check in _isolation_check(monkeypatch, RLS_URL):
        status, message = check()
        assert status == HealthStatus.HEALTHY
        assert "pie_app" in (message or "")


# ── the policies on real tables, over a migrated schema ─────────────────────
#
# Everything above pins the *mechanism* on a scratch table. These pin what the
# migration actually did, on the schema Alembic builds — which is the only place
# a policy that was written but never applied, or applied without FORCE, shows
# up. `verify.sh` runs the migration chain against this database in the step
# before this file, so the schema here is the real one.
from app.domain import models  # noqa: E402

#: What `d1rls` and `d2rls` declare, together. Duplicated here on purpose rather
#: than imported from the migrations: a test that reads its expectations out of
#: the thing it is testing agrees with it by construction and can never
#: disagree. Adding a table must fail this until somebody has thought about
#: whether an unauthenticated path reads it.
#:
#: The complement is the interesting half and is asserted separately below —
#: eight tenant-scoped tables are deliberately *not* here, for three different
#: reasons, and a list that only says what is covered lets the uncovered ones
#: drift in unnoticed.
EXPECTED_POLICIED = {
    # d1rls
    "value_events", "quote_decisions", "signals", "decisions",
    "customer_item_metrics", "approval_requests",
    # d2rls
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
    # d3rls — each behind one narrow SECURITY DEFINER lookup for the
    # unauthenticated path that reaches it.
    "organizations", "users", "user_sessions",
    "audit_entries", "audit_chain_heads", "oauth_states",
    # d4rls — three tables that landed on `main` while these policies were
    # being written. All three hold something a neighbouring tenant reading it
    # would be a leak: the customer's own enquiry text, their document number
    # in their own ERP, and a per-organization job payload. The queue worker
    # drains across tenants on the privileged engine, so it is not bound by
    # this and does not need an exemption.
    "inbound_lines", "inbound_line_dispositions", "quote_documents",
    "queued_messages",
    # d5rls — the mirror of the four above, from the other side of the same
    # merge: tables a branch added while these policies were being written.
    # `erp_quotes` is the demand side of the business in one table; the two
    # vendor-credit tables say who supplies this book and where the
    # relationship went wrong; and `threshold_versions` holds the *pre-image*
    # of every policy stamp, which is this organization's margin floors in
    # readable form.
    "erp_quotes", "threshold_versions",
    "vendor_credits", "vendor_credit_applications",
    # e1org — the grants and the commercial relationship. A neighbouring tenant
    # reading either would learn who works for this business and what they are
    # paying, so both take the ordinary shape. The *widened* half of that
    # revision is on `users` rather than here: a member of this organization is
    # visible to it even when their identity row is filed under another, which
    # is what makes a members list correct for somebody who holds two
    # workspaces. See `test_a_foreign_member_is_visible_but_not_writable`.
    "organization_memberships", "organization_subscriptions",
    # f1api — the machine credential for the public resolution API, behind one
    # narrow SECURITY DEFINER lookup like every other unauthenticated path.
    # A cross-tenant read here hands over the name, the role and the usage of
    # every integration a competitor's book runs; the secret is a PBKDF2 hash,
    # which is the half that would matter most and the half that is safe.
    "api_keys",
    # h1attr — the decoded technical facts about a product. Every row here is
    # evidence a later compatibility rule gates on, so a cross-tenant read is
    # not only somebody else's data but somebody else's *licensed* data:
    # decision 026 scopes this table per organization precisely because the
    # attribute source is a distributor export licensed to the organization
    # that obtained it, and serving those rows to another tenant would
    # redistribute it. Listed here in the same commit that creates the table,
    # which is the standing rule the Phase 0 report leaves behind — a new table
    # outside this list is isolated by Python alone.
    "product_attribute_values",
    # j1doc
    # Every document a customer sent this organization, and the single most
    # damaging row in this schema to serve to the wrong tenant: a competitor's
    # customer's drawings, their end customer, their volumes, their letterhead.
    # The bytes are ciphertext under the tenant DEK, so a cross-tenant read
    # would return something unreadable — but "they got it and could not open
    # it" is not the promise, and a policy is what makes the query return
    # nothing at all. Listed in the same commit that creates the table.
    "rfq_documents",
}

#: Tenant-scoped and deliberately uncovered — and after `d3rls` there is only
#: one reason left, which is the point of keeping the buckets separate rather
#: than counting exclusions. Capacity is a property of the *deployment* and
#: credential sharing is a *feature*: policing either would be a regression
#: wearing the costume of a control, so these two are permanent rather than
#: pending.
#:
#: The bucket that used to sit beside this one — "read with no tenant
#: announced" — is empty now. Sign-up, the demo workspace and the Zoho callback
#: each got a narrow lookup instead of an exemption. It is kept as a name so a
#: future table can be put in it deliberately rather than quietly landing in
#: the permanent list.
NO_PRINCIPAL_YET: set[str] = set()
CROSS_TENANT_BY_DESIGN = {"sync_runs", "zoho_connections"}


def _migrated(owner) -> bool:
    with owner.connect() as conn:
        return bool(conn.execute(text(
            "SELECT to_regclass('public.value_events') IS NOT NULL")).scalar())


@pytest.fixture()
def migrated(probe):
    """The owner engine over a migrated schema, with the app role able to reach it.

    The grants are re-applied here for the same reason `probe` grants schema
    USAGE: `verify_pg_migrations.py` runs against this database in the step
    before these tests and drops and recreates schema `public`, taking every
    grant with it. Without this the app role gets
    `relation "value_events" does not exist` — a table it cannot reach reads as
    a table that is not there — and the isolation assertions below would pass
    for the wrong reason, which is the one failure mode this whole file is
    written against.
    """
    if not _migrated(probe):
        pytest.skip("this database has not been migrated; verify.sh step 6 does "
                    "that before these tests run")
    with probe.begin() as conn:
        conn.execute(text("GRANT USAGE ON SCHEMA public TO pie_app"))
        conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                          "IN SCHEMA public TO pie_app"))
        conn.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "
                          "public TO pie_app"))
    return probe


def test_exactly_the_intended_tables_carry_the_policy(migrated):
    with migrated.connect() as conn:
        found = set(conn.execute(text(
            "SELECT tablename FROM pg_policies WHERE policyname = "
            "'tenant_isolation'")).scalars())
    assert found == EXPECTED_POLICIED


def test_every_policied_table_is_forced_not_merely_enabled(migrated):
    """ENABLE does not apply to a table's owner, and the owner is the role that
    runs migrations — on the compose stack it is also a superuser. A table that
    is enabled but not forced is protected against everyone except the role
    most able to read it."""
    with migrated.connect() as conn:
        rows = conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:names)"),
            {"names": sorted(EXPECTED_POLICIED)}).all()
    assert {r.relname for r in rows} == EXPECTED_POLICIED
    for row in rows:
        assert row.relrowsecurity, f"{row.relname} has RLS disabled"
        assert row.relforcerowsecurity, f"{row.relname} is not FORCEd"


def test_every_policy_governs_writes_as_well_as_reads(migrated):
    """`USING` decides what a statement can see; `WITH CHECK` decides what a row
    may become. Without the second a tenant can INSERT a row carrying another
    organization's id, or hand one of its own over by UPDATE — and a policy with
    only the first is the common way that half is left open."""
    with migrated.connect() as conn:
        rows = conn.execute(text(
            "SELECT tablename, qual, with_check FROM pg_policies "
            "WHERE policyname = 'tenant_isolation'")).all()
    for row in rows:
        assert row.qual and "current_setting" in row.qual, row.tablename
        assert row.with_check and "current_setting" in row.with_check, (
            f"{row.tablename} scopes reads but not writes")


def _seed_two_tenants(owner):
    """Two organizations and one value event each, written as the owner."""
    Maker = sessionmaker(bind=owner, future=True)
    with Maker() as session:
        for org in (ORG_A, ORG_B):
            if session.get(models.Organization, org) is None:
                session.add(models.Organization(organization_id=org, name=org))
        session.flush()
        for org, key in ((ORG_A, "rls_a"), (ORG_B, "rls_b")):
            existing = session.execute(text(
                "SELECT 1 FROM value_events WHERE event_key = :k"),
                {"k": key}).first()
            if existing is None:
                session.add(models.ValueEvent(
                    value_event_id=key, organization_id=org,
                    event_type="MARGIN_PROTECTED", value_class="ATTRIBUTED",
                    event_key=key, currency="INR", basis={}, evidence_refs=[],
                    occurred_at=datetime.now(timezone.utc),
                    thresholds_version="ci_rlstest"))
        session.commit()


def test_a_real_table_is_fail_closed_and_scoped(migrated):
    """The end-to-end statement, on the ledger whose every row is gross profit.

    Seeded through the owner connection — which bypasses, as background jobs and
    migrations need it to — and read through the application role, which does
    not. Both tenants' rows exist throughout, so "sees nothing" is a fact about
    the policy rather than about an empty table.
    """
    _seed_two_tenants(migrated)
    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            keys = text("SELECT event_key FROM value_events "
                        "WHERE event_key IN ('rls_a', 'rls_b')")
            assert set(session.execute(keys).scalars()) == set(), (
                "no tenant announced: the ledger must be empty, not complete")

            tenancy.set_tenant(session, ORG_A)
            assert set(session.execute(keys).scalars()) == {"rls_a"}

            # And the other tenant's row is not reachable by naming it.
            assert session.execute(text(
                "SELECT event_key FROM value_events WHERE event_key = 'rls_b'"
            )).first() is None
    finally:
        engine.dispose()


def test_a_tenant_cannot_write_a_row_belonging_to_another(migrated):
    """`WITH CHECK`, exercised rather than read off `pg_policies`."""
    from sqlalchemy.exc import ProgrammingError

    _seed_two_tenants(migrated)
    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            tenancy.set_tenant(session, ORG_A)
            with pytest.raises(ProgrammingError) as caught:
                session.execute(text(
                    "INSERT INTO value_events (value_event_id, organization_id, "
                    "event_type, value_class, event_key, currency, basis, "
                    "evidence_refs, occurred_at, thresholds_version) VALUES "
                    "('rls_x', :other, 'X', 'ATTRIBUTED', 'rls_x', 'INR', "
                    "'{}', '[]', now(), 'ci_rlstest')"), {"other": ORG_B})
            assert "row-level security policy" in str(caught.value)
            session.rollback()
    finally:
        engine.dispose()


def test_sign_in_can_find_a_tenant_without_being_able_to_enumerate(migrated):
    """The one hole, and its shape.

    `app_login_lookup` is what lets sign-in work at all once `users` is under a
    policy — it answers "which organization owns this address" for a caller with
    no tenant. What is asserted here is that it stays a *question*: an address
    that matches nothing returns nothing, and there is no argument that returns
    a second row.
    """
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        if session.get(models.Organization, ORG_A) is None:
            session.add(models.Organization(organization_id=ORG_A, name=ORG_A))
            session.flush()
        if session.execute(text("SELECT 1 FROM users WHERE user_id = 'rls_u1'"
                                )).first() is None:
            session.add(models.User(
                user_id="rls_u1", organization_id=ORG_A,
                email="rls@example.test", name="RLS", role="OWNER",
                password_hash="x", active=True))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            assert tenancy.adopt_tenant_for_login(
                session, "rls@example.test") == ORG_A
            assert tenancy.current_tenant(session) == ORG_A
        with Maker() as session:
            assert tenancy.adopt_tenant_for_login(
                session, "nobody@example.test") is None
            assert tenancy.current_tenant(session) is None, (
                "an unknown address must announce no tenant, not a blank one")
    finally:
        engine.dispose()


def test_every_tenant_scoped_table_is_covered_or_deliberately_named(migrated):
    """The complement, asserted rather than left implicit.

    `test_exactly_the_intended_tables_carry_the_policy` says what is covered. It
    cannot say anything about a table nobody thought about — a new model with an
    `organization_id` lands uncovered and that test stays green, which is how a
    partial control quietly becomes a smaller one.

    So this partitions *every* tenant-scoped table into covered, waiting on a
    tenant-announcing path, or cross-tenant on purpose. A model added without a
    decision fails here, naming itself, which is the moment to make one.
    """
    from app.db import Base

    scoped = {t.name for t in Base.metadata.sorted_tables
              if "organization_id" in t.c}
    accounted = EXPECTED_POLICIED | NO_PRINCIPAL_YET | CROSS_TENANT_BY_DESIGN

    assert scoped - accounted == set(), (
        "a tenant-scoped table is neither policied nor deliberately excluded — "
        "decide which it is and say so here")
    assert accounted - scoped == set(), (
        "a table named here no longer carries organization_id")


def test_the_two_tables_without_a_tenant_column_are_still_the_same_two(migrated):
    """`zoho_credentials` belongs to a person rather than a company and
    `process_leases` is infrastructure about processes, so neither can take this
    policy shape at all. A *third* one appearing means a model was added without
    a tenant column, which is a decision worth making on purpose rather than
    discovering when it leaks."""
    from app.db import Base

    unscoped = {t.name for t in Base.metadata.sorted_tables
                if "organization_id" not in t.c}
    assert unscoped == {"zoho_credentials", "process_leases"}


# ── the unauthenticated paths, which have no tenant until they find one ─────
#
# `d3rls` policied the last six tables by giving each path that reaches them one
# narrow SECURITY DEFINER lookup rather than an exemption. What follows checks
# the two halves that matter: the lookup answers, and it stays a *question* —
# an argument that matches nothing returns nothing, and there is none that
# returns a second row.
def test_signup_can_still_tell_a_taken_address_from_a_free_one(migrated):
    """The defect this lookup exists to prevent is not a broken feature but a
    corrupted one: under a policy the ordinary query answers "free" for every
    address, the refusal never fires, and two organizations end up sharing an
    owner address — after which sign-in is ambiguous."""
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        if session.get(models.Organization, ORG_A) is None:
            session.add(models.Organization(organization_id=ORG_A, name=ORG_A))
            session.flush()
        if session.execute(text("SELECT 1 FROM users WHERE user_id = 'rls_u2'"
                                )).first() is None:
            session.add(models.User(
                user_id="rls_u2", organization_id=ORG_A,
                email="taken@example.test", name="Taken", role="OWNER",
                password_hash="x", active=False))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            # No tenant announced — a signing-up stranger has none.
            assert tenancy.current_tenant(session) is None
            assert tenancy.email_registered(session, "taken@example.test") is True
            assert tenancy.email_registered(session, "free@example.test") is False

            # And the ordinary query, the one that used to be the authority,
            # cannot see it — which is exactly why the lookup had to exist.
            assert session.execute(text(
                "SELECT 1 FROM users WHERE email = 'taken@example.test'"
            )).first() is None
    finally:
        engine.dispose()


def test_a_deactivated_account_still_holds_its_address(migrated):
    """`app_email_registered` deliberately does not filter on `active`, unlike
    `app_login_lookup`. The row seeded above is inactive; sign-up must still
    refuse it, or deactivating a user quietly frees their address."""
    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            assert tenancy.email_registered(session, "taken@example.test") is True
            # The sign-in lookup, on the same address, correctly finds nothing.
            assert tenancy.adopt_tenant_for_login(
                session, "taken@example.test") is None
    finally:
        engine.dispose()


def test_provisioning_can_see_an_id_collision_it_would_otherwise_walk_past(
        migrated):
    """The org-id walk stops at the first free candidate. Under a policy every
    candidate looks free, so it would stop at the first and the insert would
    fail on the primary key — loud, but a sign-up broken by another company
    having a similar name is still broken."""
    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            assert tenancy.org_id_taken(session, ORG_A) is True
            assert tenancy.org_id_taken(session, "org_never_created") is False
    finally:
        engine.dispose()


def test_the_oauth_callback_learns_its_tenant_from_the_state_it_holds(migrated):
    """The callback arrives with a state token and nothing else. Its key is
    already the hash of a single-use secret, so the lookup leaks nothing to a
    caller who does not hold one — and an unknown hash announces no tenant,
    leaving the caller's own refusal exactly as it was."""
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        if session.execute(text(
                "SELECT 1 FROM oauth_states WHERE state_hash = 'rls_state'"
        )).first() is None:
            session.add(models.OAuthState(
                state_hash="rls_state", organization_id=ORG_A,
                accounts_base="https://accounts.example.test",
                api_base="https://api.example.test",
                expires_at=datetime.now(timezone.utc)))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            assert tenancy.adopt_tenant_for_oauth_state(
                session, "rls_state") == ORG_A
            assert tenancy.current_tenant(session) == ORG_A
        with Maker() as session:
            assert tenancy.adopt_tenant_for_oauth_state(
                session, "not-a-state") is None
            assert tenancy.current_tenant(session) is None
    finally:
        engine.dispose()


def test_a_tenant_cannot_read_another_tenants_users(migrated):
    """`users` is the table this whole exercise is most about. Two tenants'
    owners exist throughout, so "sees one" is a fact about the policy."""
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        for org, uid in ((ORG_A, "rls_u1"), (ORG_B, "rls_u3")):
            if session.get(models.Organization, org) is None:
                session.add(models.Organization(organization_id=org, name=org))
                session.flush()
            if session.execute(text("SELECT 1 FROM users WHERE user_id = :u"),
                               {"u": uid}).first() is None:
                session.add(models.User(
                    user_id=uid, organization_id=org, email=f"{uid}@example.test",
                    name=uid, role="OWNER", password_hash="x", active=True))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            everyone = text("SELECT user_id FROM users WHERE user_id IN "
                            "('rls_u1', 'rls_u3')")
            assert set(session.execute(everyone).scalars()) == set()

            tenancy.set_tenant(session, ORG_A)
            assert set(session.execute(everyone).scalars()) == {"rls_u1"}
    finally:
        engine.dispose()


# ── memberships: the grant, and the one place the policy is deliberately wide ─
def test_a_tenant_cannot_read_another_tenants_memberships(migrated):
    """Who works for a business is as much theirs as what they sell."""
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        for org, uid in ((ORG_A, "rls_m1"), (ORG_B, "rls_m2")):
            if session.get(models.Organization, org) is None:
                session.add(models.Organization(organization_id=org, name=org))
                session.flush()
            if session.execute(text("SELECT 1 FROM users WHERE user_id = :u"),
                               {"u": uid}).first() is None:
                session.add(models.User(
                    user_id=uid, organization_id=org, email=f"{uid}@example.test",
                    name=uid, role="OWNER", password_hash="x", active=True))
                session.flush()
            if session.execute(
                    text("SELECT 1 FROM organization_memberships "
                         "WHERE user_id = :u"), {"u": uid}).first() is None:
                session.add(models.OrganizationMembership(
                    membership_id=f"mem_{uid}", organization_id=org, user_id=uid,
                    role="OWNER", status="ACTIVE",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            both = text("SELECT user_id FROM organization_memberships "
                        "WHERE user_id IN ('rls_m1', 'rls_m2')")
            assert set(session.execute(both).scalars()) == set(), (
                "with no tenant announced, a membership table must be empty")

            tenancy.set_tenant(session, ORG_A)
            assert set(session.execute(both).scalars()) == {"rls_m1"}
    finally:
        engine.dispose()


def test_a_foreign_member_is_visible_but_not_writable(migrated):
    """The widened `users` policy, and both halves of why it is that shape.

    A person can belong to two organizations while their identity row is filed
    under one of them. Under the original policy the *other* organization's
    members list could not print their name — the row simply was not there —
    so the read half is widened: a user is visible to a tenant that holds a
    membership for them.

    The write half is left narrow on purpose, and that is the part worth
    pinning. `WITH CHECK` still compares `organization_id` against the
    announced tenant, so a tenant that can *see* a foreign member cannot
    re-home them, and cannot create one either. Seeing a colleague's name is
    not the same permission as taking their account.
    """
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        for org in (ORG_A, ORG_B):
            if session.get(models.Organization, org) is None:
                session.add(models.Organization(organization_id=org, name=org))
                session.flush()
        if session.execute(text("SELECT 1 FROM users WHERE user_id = 'rls_two'"
                                )).first() is None:
            # Filed under A…
            session.add(models.User(
                user_id="rls_two", organization_id=ORG_A,
                email="two@example.test", name="Two", role="OWNER",
                password_hash="x", active=True))
            session.flush()
        if session.execute(
                text("SELECT 1 FROM organization_memberships "
                     "WHERE membership_id = 'mem_two_b'")).first() is None:
            # …and a member of B as well.
            session.add(models.OrganizationMembership(
                membership_id="mem_two_b", organization_id=ORG_B,
                user_id="rls_two", role="SALESPERSON", status="ACTIVE",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            tenancy.set_tenant(session, ORG_B)
            found = session.execute(
                text("SELECT user_id FROM users WHERE user_id = 'rls_two'")
            ).scalars().all()
            assert found == ["rls_two"], (
                "B holds a membership for this person, so B's members screen "
                "must be able to name them")

            # But B cannot take the account — and the two halves of "cannot"
            # fail differently, which is worth asserting separately because
            # only one of them is loud.
            #
            # The UPDATE **matches nothing**. `tenant_isolation` is the only
            # policy that applies to UPDATE, its USING still compares
            # `organization_id` against the announced tenant, and a row it does
            # not select is a row that is not there: no error, no rows, and the
            # account stays exactly where it was. That is the silent half, and
            # asserting it as a raise (which this test first did) would pass
            # against a policy that had no UPDATE clause at all.
            updated = session.execute(text(
                "UPDATE users SET organization_id = :b "
                "WHERE user_id = 'rls_two'"), {"b": ORG_B})
            assert updated.rowcount == 0, (
                "B can read this member and must not be able to re-home them")
            session.commit()
        with Maker() as session:
            tenancy.set_tenant(session, ORG_A)
            assert session.execute(text(
                "SELECT organization_id FROM users WHERE user_id = 'rls_two'"
            ).bindparams()).scalar() == ORG_A, "still filed under A"
        with Maker() as session:
            tenancy.set_tenant(session, ORG_B)
            # The INSERT is the loud half: WITH CHECK governs what a row may
            # *become*, so writing one that carries another tenant's id is an
            # error rather than a no-op.
            with pytest.raises(Exception):
                session.execute(text(
                    "INSERT INTO users (user_id, organization_id, email, name, "
                    "role, active, must_change_password, login_failures_count, "
                    "created_at) VALUES ('rls_new', :a, 'new@example.test', "
                    "'New', 'OWNER', true, false, 0, now())"), {"a": ORG_A})
                session.flush()
    finally:
        engine.dispose()


def test_a_person_can_find_their_own_workspaces_across_tenants(migrated):
    """`app_user_memberships`, the second and narrower hole.

    A request acting for A is by construction forbidden to see B's membership
    rows, so "where else can I go" needs a lookup that crosses the boundary.
    What is asserted here is that it stays a *question about one person*: it
    answers for the id it is given and there is no argument that widens it.
    """
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        for org in (ORG_A, ORG_B):
            if session.get(models.Organization, org) is None:
                session.add(models.Organization(organization_id=org, name=org))
                session.flush()
        for uid, home in (("rls_multi", ORG_A), ("rls_solo", ORG_B)):
            if session.execute(text("SELECT 1 FROM users WHERE user_id = :u"),
                               {"u": uid}).first() is None:
                session.add(models.User(
                    user_id=uid, organization_id=home,
                    email=f"{uid}@example.test", name=uid, role="OWNER",
                    password_hash="x", active=True))
                session.flush()
        for mid, org, uid in (("mem_multi_a", ORG_A, "rls_multi"),
                              ("mem_multi_b", ORG_B, "rls_multi"),
                              ("mem_solo_b", ORG_B, "rls_solo")):
            if session.get(models.OrganizationMembership, mid) is None:
                session.add(models.OrganizationMembership(
                    membership_id=mid, organization_id=org, user_id=uid,
                    role="OWNER", status="ACTIVE",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            # No tenant announced at all, which is the state the switcher's
            # caller is *not* in — but the strongest place to assert from.
            rows = tenancy.user_memberships(session, "rls_multi")
            assert {r[0] for r in rows} == {ORG_A, ORG_B}
            # And it answers for that person only.
            assert {r[0] for r in tenancy.user_memberships(session, "rls_solo")
                    } == {ORG_B}
            assert tenancy.user_memberships(session, "nobody") == []
    finally:
        engine.dispose()


def test_an_ended_membership_stops_answering_for_the_switcher(migrated):
    """A grant that ended is not a door. The function filters on ACTIVE, so a
    removed member cannot even find the workspace to try it."""
    Maker = sessionmaker(bind=migrated, future=True)
    with Maker() as session:
        if session.get(models.Organization, ORG_B) is None:
            session.add(models.Organization(organization_id=ORG_B, name=ORG_B))
            session.flush()
        if session.execute(text("SELECT 1 FROM users WHERE user_id = 'rls_gone'"
                                )).first() is None:
            session.add(models.User(
                user_id="rls_gone", organization_id=ORG_B,
                email="gone@example.test", name="Gone", role="SALESPERSON",
                password_hash="x", active=True))
            session.flush()
        if session.get(models.OrganizationMembership, "mem_gone") is None:
            session.add(models.OrganizationMembership(
                membership_id="mem_gone", organization_id=ORG_B,
                user_id="rls_gone", role="SALESPERSON", status="REMOVED",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)))
        session.commit()

    engine = create_engine(RLS_URL, future=True)
    Maker = sessionmaker(bind=engine, future=True)
    try:
        with Maker() as session:
            assert tenancy.user_memberships(session, "rls_gone") == []
    finally:
        engine.dispose()


# ── the role itself, and the script that creates it ─────────────────────────
#
# Everything above proves that policies bind a role which does not bypass them.
# This proves the deployment actually gets such a role. The two halves were
# separate for a while, and the gap between them is the whole of decision 019:
# 81 tables of tested, fail-closed policy, and `APP_DATABASE_URL` in no
# deployment recipe — so every documented deployment served requests as the
# schema's owner and none of it bound anything.

def _provision(env: dict) -> subprocess.CompletedProcess:
    script = (Path(__file__).resolve().parents[3] / "deploy"
              / "provision_app_role.py")
    return subprocess.run([sys.executable, str(script)], env={**os.environ, **env},
                          capture_output=True, text=True)


def test_the_provisioning_script_creates_a_role_policies_can_bind():
    """The flags are the control. A role that came back rolsuper or
    rolbypassrls would leave every policy in this file inert while the deploy
    log said success."""
    role = "rls_made_by_script"
    done = _provision({"DATABASE_URL": OWNER_URL, "APP_DB_ROLE": role,
                       "APP_DB_PASSWORD": "p" + "a" * 20})
    assert done.returncode == 0, done.stderr

    with create_engine(OWNER_URL).connect() as conn:
        row = conn.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, "
            "rolcreaterole FROM pg_roles WHERE rolname = :r"), {"r": role}).one()
    assert (row.rolsuper, row.rolbypassrls) == (False, False)
    assert row.rolcanlogin is True
    assert (row.rolcreatedb, row.rolcreaterole) == (False, False)


def test_provisioning_twice_is_the_same_as_once():
    """It runs on every release, and a release is not a first deploy. Re-running
    is how a table added by this deploy's migrations becomes reachable."""
    role = "rls_made_twice"
    env = {"DATABASE_URL": OWNER_URL, "APP_DB_ROLE": role,
           "APP_DB_PASSWORD": "p" + "a" * 20}
    assert _provision(env).returncode == 0
    second = _provision({**env, "APP_DB_PASSWORD": "q" + "b" * 20})
    assert second.returncode == 0, second.stderr

    with create_engine(OWNER_URL).connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM pg_roles WHERE rolname = :r"),
                         {"r": role}).scalar()
    assert n == 1


def test_a_password_never_reaches_the_statement_text():
    """`DB_SLOW_QUERY_MS` logs statements, and so does any server-side statement
    log. The password travels as a bind parameter into a transaction-local
    setting that the DO block reads back, so what either one could write is
    `set_config($1, $2, true)`."""
    source = (Path(__file__).resolve().parents[3] / "deploy"
              / "provision_app_role.py").read_text()
    assert "set_config('pie.provision_pw', :v, true)" in source
    assert "PASSWORD %L" in source, (
        "the password must be quoted server-side by format(), not spliced in")
    assert "PASSWORD {" not in source and 'PASSWORD " +' not in source


def test_it_says_so_rather_than_quietly_doing_nothing():
    """A security control that silently did not install reads exactly like one
    that did. Exit 0 — this is a deployment's choice, not an error — but never
    in silence."""
    done = _provision({"DATABASE_URL": OWNER_URL, "APP_DB_PASSWORD": ""})
    assert done.returncode == 0
    assert "APP_DB_PASSWORD is unset" in done.stdout
    assert "UNHEALTHY" in done.stdout


def test_a_role_name_that_is_not_an_identifier_is_refused(tmp_path):
    done = _provision({"DATABASE_URL": OWNER_URL, "APP_DB_PASSWORD": "x" * 12,
                       "APP_DB_ROLE": 'evil"; DROP TABLE products; --'})
    assert done.returncode == 1
    assert "refusing APP_DB_ROLE" in done.stderr

    with create_engine(OWNER_URL).connect() as conn:
        assert conn.execute(text(
            "SELECT to_regclass('public.products') IS NOT NULL")).scalar()


def test_on_a_dialect_with_no_policies_it_does_nothing_and_says_why(tmp_path):
    done = _provision({"DATABASE_URL": f"sqlite:///{tmp_path}/x.db",
                       "APP_DB_PASSWORD": "x" * 12})
    assert done.returncode == 0
    assert "not PostgreSQL" in done.stdout
