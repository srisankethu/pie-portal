"""Two roles on one database, and the refusals that keep it to that.

Row-level security is decided by the role that issued the query, and this
application had one role for everything. That cannot work: migrations create
the schema and every background job works across tenants on purpose — the
auto-sync scheduler enumerates connections for every organization with no
principal at all — while a policy is worth nothing unless the connection
serving requests is one it binds. One URL cannot be both.

So `DATABASE_URL` stays privileged and `APP_DATABASE_URL`, when set, serves
requests. The properties worth pinning are not that it works when configured —
that is one `sessionmaker` call — but the three ways it can go wrong quietly:

* **unset must change nothing**, by identity rather than by an equivalent copy,
  because a second pool against the same database for no reason is fifteen more
  connections per worker against a stock `max_connections` of 100;
* **a second *database*** — as opposed to a second role — would have requests
  and background jobs reading different data, which surfaces as rows that are
  intermittently absent depending on which code path asked;
* **a dialect with no policies** would split the pool and buy nothing.

The last two are refused at import, so they are checked in a subprocess: an
exception raised while `app.db` is being imported cannot be caught by a test
that has already imported it.
"""
from __future__ import annotations

import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

#: Never connected to. Every check that these tests exercise runs before
#: `create_engine`, which is lazy, so no server has to exist for a URL to be
#: judged — which is what lets them run in the default SQLite gate rather than
#: only where a PostgreSQL sandbox happens to be up.
PRIVILEGED = "postgresql+psycopg://owner@db.invalid:5432/pie"
SERVING = "postgresql+psycopg://app@db.invalid:5432/pie"


def _import_db(**env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import app.db"],
        cwd=BACKEND, capture_output=True, text=True,
        env={**os.environ, **env})


def test_leaving_it_unset_changes_nothing_at_all():
    """The default every existing deployment, all of dev, and the whole test
    suite are on. Two properties, and the second one was a bug first.

    `app_engine is engine` — an equivalent-but-separate engine would open a
    second pool against the same database for no reason.

    `AppSessionLocal is None` — **not** an alias for `SessionLocal`. Several
    tests build a database at a temp path and rebind `db.SessionLocal`; an
    alias captured at import time still pointed at the original engine, so
    those requests were served from a database the migrations had never
    touched. `no such table: audit_chain_heads`, raised on login, inside the
    test whose subject is that bootstrap leaves a working schema behind.
    """
    from app.db import AppSessionLocal, app_engine, engine

    assert app_engine is engine
    assert AppSessionLocal is None


def test_rebinding_the_session_factory_still_reaches_requests(monkeypatch):
    """The property the bug above broke, pinned directly rather than through
    the schema error it happened to surface as."""
    from app import db

    class _Stub:
        """Enough of a Session for `get_session`'s own commit/close path — it
        runs on the way out and an abandoned generator would otherwise raise
        inside its finalizer, which pytest reports as an unraisable warning
        rather than as a failure."""

        def commit(self) -> None: ...
        def rollback(self) -> None: ...
        def close(self) -> None: ...

    stub = _Stub()
    monkeypatch.setattr(db, "SessionLocal", lambda: stub)
    monkeypatch.setattr(db, "AppSessionLocal", None)

    generator = db.get_session()
    try:
        assert next(generator) is stub
    finally:
        generator.close()


def test_a_second_database_is_refused_rather_than_served(tmp_path):
    """Two roles on one database is the design. Two databases is a typo whose
    symptom is data that is sometimes there."""
    result = _import_db(DATABASE_URL=PRIVILEGED,
                        APP_DATABASE_URL=SERVING.replace("/pie", "/other"))
    assert result.returncode != 0
    assert "name different databases" in result.stderr


def test_the_same_database_under_a_different_role_is_accepted():
    """The control on the test above: it must be refusing the *database*, not
    merely noticing that two URLs differ — they always do, that is the point."""
    result = _import_db(DATABASE_URL=PRIVILEGED, APP_DATABASE_URL=SERVING)
    assert result.returncode == 0, result.stderr


def test_a_dialect_with_no_policies_is_refused(tmp_path):
    """SQLite has no row-level security and no connection settings, so pointing
    the serving URL at one splits the pool and buys precisely nothing. Refused
    loudly rather than accepted into a configuration that reads as protection.
    """
    result = _import_db(DATABASE_URL=PRIVILEGED,
                        APP_DATABASE_URL=f"sqlite:///{tmp_path}/x.db")
    assert result.returncode != 0
    assert "must be a PostgreSQL URL" in result.stderr


def test_requests_are_served_by_the_serving_pool():
    """`get_session` is the dependency 176 call sites reach the database
    through. If it kept using `SessionLocal` the whole split would be inert —
    configured, reported, and not actually in the request path."""
    import inspect

    from app import db

    source = inspect.getsource(db.get_session)
    assert "AppSessionLocal or SessionLocal" in source, (
        "get_session must prefer the serving pool when one exists, and fall "
        "back to the shared name — read at call time, not captured")


# ── the component that says whether any of this is enforcing ────────────────
def _isolation_check():
    from app.observability.health import health, register_health_checks

    saved = dict(health._components)
    health._components.clear()
    try:
        register_health_checks(object(), object())
        return health._components["tenant_isolation"].check_fn
    finally:
        health._components.clear()
        health._components.update(saved)


def test_sqlite_is_healthy_and_says_what_that_costs():
    """Amber on every developer's machine is a light nobody reads — the
    argument `check_scheduler` already makes about a fixture source. What it
    costs is in the message rather than hidden: on this dialect the Python
    filters are the only tenant boundary there has ever been."""
    from app.observability.health import HealthStatus

    status, message = _isolation_check()()
    assert status == HealthStatus.HEALTHY
    assert "organization_id" in (message or "")
