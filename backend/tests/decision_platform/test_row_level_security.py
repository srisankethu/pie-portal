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
from datetime import datetime, timezone

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
