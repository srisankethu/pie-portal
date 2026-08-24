# PostgreSQL — the production database

PostgreSQL is the database this platform deploys on. SQLite remains what a
fresh clone and the test suite run on by default — zero infrastructure, and
the honest fidelity caveats below — but **nothing in production assumes
SQLite**, and the gate proves the Postgres path on every run
(`scripts/verify.sh`, step 6: the full Alembic chain onto an empty Postgres
database, then models-vs-schema drift).

One URL decides everything: set `DATABASE_URL` to a
`postgresql+psycopg://user:password@host:5432/dbname` URL and the app,
Alembic, `/api/health`, and every script use it — the same single-source rule
CLAUDE.md §4 states for SQLite. There is no second switch.

## Running it

**Self-hosted**: `compose.yaml` runs Postgres 17 next to the API; the runbook
is [hosting.md](hosting.md). Migrations stay a deliberate step
(`make deploy-release`) — bringing the stack up never migrates.

**Managed Postgres** (RDS, Cloud SQL, Neon, Supabase, …): point
`DATABASE_URL` at it and run the same release step. Choose a provider plan
with **automated backups and point-in-time recovery** and a retention you can
say out loud — do not build backup machinery into the app; the platform's own
state (signals, decisions, approvals, the value ledger) exists nowhere else,
which is the whole argument in
[operations.md](operations.md#backup-and-recovery).

**Local development on the production dialect**:

```bash
docker compose -f compose.dev.yaml up -d        # Postgres 17 + Redis, loopback only
export DATABASE_URL=postgresql+psycopg://pie_portal:pie_portal@localhost:5432/pie_portal
make bootstrap && make dev
```

No Docker? `scripts/pg_sandbox.sh start` builds a throwaway cluster in /tmp
from nothing but the postgresql server package and prints its URL.

**The suite on the production dialect**:

```bash
export PIE_TEST_DATABASE_URL="$(scripts/pg_sandbox.sh start)"
cd backend && python3 -m pytest tests -q -n auto
```

Each worker gets its own databases; tables are truncated between tests with
identities restarted, so tests behave as they do on a fresh SQLite file. The
default (unset) keeps the suite on in-memory SQLite — fast, and what
`make verify` runs.

## Moving an existing SQLite deployment

`scripts/migrate_to_postgres.py` is the one supported path. It backs up the
SQLite file and opens it read-only, refuses a source that is not at this
codebase's Alembic head and a target that is not empty, migrates the target
through the real chain, copies every table in foreign-key order inside one
transaction, resets the serial sequences, and **validates before committing**
— per-table row counts, exact-decimal sums of every money column, and
column-for-column comparison of representative rows. A validation failure
rolls the whole copy back: the target is left migrated and empty, never
partial.

```bash
# 1. Stop the app (a moving source cannot be copied consistently).
# 2. Create an empty database and run the move:
python3 scripts/migrate_to_postgres.py \
    --source backend/data/platform.db \
    --target "postgresql+psycopg://pie_portal:...@host:5432/pie_portal"
# 3. Read the printed report. COMMITTED + zero mismatches, or it rolled back.
# 4. Point DATABASE_URL at Postgres, start the app, and confirm:
curl -s localhost:8000/api/health | python3 -m json.tool   # CURRENT, dialect postgresql
# 5. Run a full sync so the read model is freshly Zoho-true.
```

Foreign keys are validated by construction: Postgres enforces every
constraint as the rows are inserted — for data that lived under SQLite (which
ran with foreign keys off) this is the first time an enforcing database has
seen it, and a violation aborts the run rather than surviving the move.

Timestamps: the app stores UTC (`app/clock.py`); the tool copies under an
explicit `SET timezone='UTC'` so `TIMESTAMP WITH TIME ZONE` columns hold the
same instant, now timezone-aware on the way out. `clock.aware` handles both
shapes, which is why the same code runs on both backends.

## Decisions, with their reasons

- **Primary keys stay as they are** (string ids, plus a few serials). They
  are identifiers in Zoho's world and in every existing row; changing key
  strategy during a database move is how relationships get subtly broken.
- **`sa.JSON` stays `JSON`, not JSONB.** Nothing queries inside JSON columns
  server-side today — they are read and written whole — so a JSONB rewrite of
  every table buys nothing now. The day a feature needs `payload @> …` or a
  GIN index, migrate that column deliberately, with the query in hand.
- **Timestamps stay UTC.** Columns are `DateTime(timezone=True)` — real
  `timestamptz` on Postgres. Historical naive values are UTC by the clock
  convention and are copied as exactly that; nothing is reinterpreted.
- **No per-tenant databases.** `organization_id` is the tenant boundary on
  every owned row (it already was); one database, role-scoped by the API.
- **Least privilege**: the app's database user should own its own database and
  nothing else — no SUPERUSER, no CREATEDB, no CREATEROLE. Migrations run as
  that user (they create tables in its own database), as a deliberate operator
  step, never automatically at boot.

  **The compose stack does not satisfy this, and this bullet used to claim it
  did — "by construction", which was the opposite of true.** `postgres:17-alpine`
  runs `initdb -U "$POSTGRES_USER"`, and `initdb -U` creates the cluster's
  *bootstrap superuser*. So `pie_portal` is a superuser and the owner of every
  table Alembic builds. On a shared or managed server, where you create the
  role yourself with `CREATE ROLE … LOGIN NOSUPERUSER`, the bullet is accurate;
  on the compose stack it never was.

  This matters far beyond tidiness now that row-level security is arriving: a
  superuser carries `rolbypassrls`, which **no policy can override and
  `FORCE ROW LEVEL SECURITY` does not touch** — FORCE binds a table's owner,
  nothing binds BYPASSRLS. Tenant isolation enforced by policy is therefore
  inert for this connection. Verified rather than reasoned about: a fail-closed
  policy on a probe table returned every row to it.

  `scripts/pg_sandbox.sh` provisions a second role, `pie_app`
  (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`, owner of nothing),
  and prints its URL from `pg_sandbox.sh app-url`. That is the role
  `tests/decision_platform/test_row_level_security.py` connects as.

## Two roles on one database

A policy binds the *role that issued the query*, and this application had one
role for everything. That cannot work: Alembic creates the schema, and every
background job works across tenants on purpose — the auto-sync scheduler
enumerates connections for every organization with no principal at all — while
a policy is worth nothing unless the connection serving requests is one it
binds. One URL cannot be both.

So there are two, and only the second is new:

| | role | used by |
|---|---|---|
| `DATABASE_URL` | privileged, owns the schema | Alembic, background jobs, every CLI, `scripts/` |
| `APP_DATABASE_URL` | tenant-scoped, owns nothing | HTTP request handlers, via `get_session` |

**Unset, they are the same connection** — the same engine and the same
sessionmaker, by identity — which is what every existing deployment, all of
dev, and the whole test suite run on. Setting it is the deliberate act.

Two things are refused at import rather than served: a URL that is not
PostgreSQL (no other dialect has policies, so it would split the pool and buy
nothing), and one naming a different *database* (two roles on one database is
the design; two databases means requests and background jobs read different
data, which surfaces as rows that are sometimes there).

On a server where you can create roles:

```sql
CREATE ROLE pie_app LOGIN PASSWORD '…' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO pie_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pie_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pie_app;
-- So each new table Alembic creates is reachable without a second pass:
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pie_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO pie_app;
```

Then set `APP_DATABASE_URL` to the same database as `DATABASE_URL` with that
user, and check it took:

```bash
curl -s localhost:8000/api/v1/internal/observability/health | python3 -m json.tool
```

The `tenant_isolation` component answers exactly one question — is the
connection serving requests one that policies apply to — and it is **unhealthy
until `APP_DATABASE_URL` is set**, naming the role and its `rolsuper` /
`rolbypassrls` flags. It is deliberately not softened by whether any policy
exists yet: a connection that cannot be governed is the finding, and policies
added later would silently do nothing.

**Policies themselves are not on any table yet.** The connection split and the
tenant setting (`app/tenancy.py`, announced from the signed token before the
first query of a request) are the prerequisites; the migration that puts real
tables under `ENABLE`/`FORCE ROW LEVEL SECURITY` is separate, and it has one
open problem in front of it: sign-in looks a user up by email with no token and
therefore no tenant, which a fail-closed policy answers with nothing. A
`SECURITY DEFINER` function returning one user's id and organization is the
narrow answer.

## What stays SQLite, and the honest caveat

Dev-by-default and the test suite. The caveat: SQLite stores `Numeric` as
float and drops timezone info, which is exactly why the gate's step 6 and the
`PIE_TEST_DATABASE_URL` suite mode exist — the dialect production runs is
exercised by machinery, not by hoping the dialects agree. Before a deploy
that touches money arithmetic or timestamp comparisons, run the suite once in
Postgres mode.
