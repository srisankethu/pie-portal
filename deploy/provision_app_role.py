"""Create the tenant-scoped role that row-level security actually binds.

Every table in this schema carries `ENABLE` + `FORCE ROW LEVEL SECURITY` and a
fail-closed `tenant_isolation` policy — and none of it does anything while the
connection serving requests is the role that owns the schema. A policy binds the
role that issued the query; `FORCE` binds the owner too, but nothing binds
`BYPASSRLS`, and a superuser is exempt outright. So the policies were written,
tested against a non-bypassing role in the gate, and then never reached, because
`APP_DATABASE_URL` appeared in the documentation and in no deployment recipe.

This is the missing half: it creates `pie_app` — LOGIN, NOSUPERUSER,
NOCREATEDB, NOCREATEROLE, NOBYPASSRLS, and the owner of nothing — and grants it
what an application needs and no more.

It is the same role `scripts/pg_sandbox.sh` provisions for the row-level
security suite, deliberately, so what the gate proves is what production runs.

Run it **after** `alembic upgrade head`: the `ON ALL TABLES` grants cover what
exists at the moment they run, and `ALTER DEFAULT PRIVILEGES` covers every table
a later migration adds. Running it before the first migration would grant on an
empty schema and the next deploy would have to repeat it.

Idempotent. Re-running re-issues the grants (which is how a table added by a
migration that ran before this script became reachable) and rotates the password
to whatever `APP_DB_PASSWORD` currently holds.

    APP_DB_PASSWORD=… python3 deploy/provision_app_role.py

Silently doing nothing is not an option here: a security control that quietly
did not get installed reads exactly like one that did. With no password set it
says so and exits 0 — that is a deployment which has chosen not to enable this,
and `/api/v1/internal/observability/health` reports `tenant_isolation` as
unhealthy for as long as that holds.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import text                                  # noqa: E402
from sqlalchemy.engine import make_url                       # noqa: E402

from app.config import settings                              # noqa: E402
from app.db import engine                                    # noqa: E402

ROLE = os.environ.get("APP_DB_ROLE", "pie_app")


def main() -> int:
    password = os.environ.get("APP_DB_PASSWORD", "")
    if not password:
        print("[app-role] APP_DB_PASSWORD is unset — not provisioning a "
              "tenant-scoped role. Row-level security has nothing to bind and "
              "the tenant_isolation health component will report UNHEALTHY. "
              "See docs/postgres.md.", flush=True)
        return 0

    url = make_url(settings.DATABASE_URL)
    if not url.drivername.startswith("postgresql"):
        print(f"[app-role] DATABASE_URL is {url.drivername}, not PostgreSQL. "
              "No other dialect has policies, so there is nothing to bind and "
              "nothing to do.", flush=True)
        return 0

    # `format(%I)` quotes the identifier server-side; this check is in front of
    # it rather than instead of it, because a role name that needs quoting is a
    # deployment mistake worth naming before it becomes a quoted oddity.
    if not ROLE.replace("_", "").isalnum() or not ROLE[0].isalpha():
        print(f"[app-role] refusing APP_DB_ROLE={ROLE!r}: expected a plain "
              "identifier (letters, digits, underscores, leading letter).",
              file=sys.stderr)
        return 1

    # A `DO` block is one string literal to the server and cannot take a bind
    # parameter, so the password is handed over as a transaction-local setting
    # that the block reads back. The alternative is splicing it into the
    # statement text, where `DB_SLOW_QUERY_MS` or any server-side statement log
    # would eventually write a password to disk. `is_local` is true, so it is
    # gone at COMMIT and no other session ever sees it.
    provision = """
        DO $$
        DECLARE
          pw   text := current_setting('pie.provision_pw');
          role text := current_setting('pie.provision_role');
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB '
                           'NOCREATEROLE NOBYPASSRLS PASSWORD %L', role, pw);
          ELSE
            EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB '
                           'NOCREATEROLE NOBYPASSRLS PASSWORD %L', role, pw);
          END IF;
        END
        $$;"""
    grants = [
        f'GRANT USAGE ON SCHEMA public TO "{ROLE}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES '
        f'IN SCHEMA public TO "{ROLE}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{ROLE}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{ROLE}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT USAGE, SELECT ON SEQUENCES TO "{ROLE}"',
    ]
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('pie.provision_pw', :v, true)"),
                     {"v": password})
        conn.execute(text("SELECT set_config('pie.provision_role', :v, true)"),
                     {"v": ROLE})
        conn.execute(text(provision))
        for sql in grants:
            conn.execute(text(sql))
        row = conn.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"
        ), {"r": ROLE}).one()

    # Asserted rather than assumed. A role that came back exempt would leave
    # every policy inert while this script printed success, which is the exact
    # shape of failure the whole control exists to prevent.
    if row.rolsuper or row.rolbypassrls:
        print(f"[app-role] {ROLE} is rolsuper={row.rolsuper} "
              f"rolbypassrls={row.rolbypassrls} — policies would not bind it. "
              "Fix the role before serving requests as it.", file=sys.stderr)
        return 1

    print(f"[app-role] {ROLE} ready on {url.database}: NOSUPERUSER, "
          f"NOBYPASSRLS, owner of nothing. Set APP_DATABASE_URL to this "
          f"database as {ROLE} so requests are served through it.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
