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
        conn.execute(text(
            f"CREATE POLICY tenant_isolation ON {TABLE} "
            f"USING (organization_id = current_setting('{tenancy.GUC}', true))"))
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
