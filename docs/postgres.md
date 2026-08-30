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
docker compose -f compose.dev.yaml up -d        # Postgres 17, loopback only
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

**`deploy/release.sh` does this for you** when `APP_DB_PASSWORD` is set —
`deploy/provision_app_role.py` runs the statements below, idempotently, after
the migrations. Prefer that to typing them: a runbook and a script that issue
the same SQL are two definitions of one thing, and the gate only exercises the
script (`test_row_level_security.py` runs the whole isolation suite against a
role it created). The SQL is kept here because a managed database where you
cannot run the script — or where the owner role is not yours to use — still
needs it, and because a control nobody can read is a control nobody can audit.

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
user — on the compose stack, set `APP_DB_PASSWORD` in `.env.production` and
`compose.yaml` builds the URL — and check it took:

```bash
curl -s localhost:8000/api/v1/internal/observability/health | python3 -m json.tool
```

The `tenant_isolation` component answers exactly one question — is the
connection serving requests one that policies apply to — and it is **unhealthy
until `APP_DATABASE_URL` is set**, naming the role and its `rolsuper` /
`rolbypassrls` flags. It is deliberately not softened by whether any policy
exists yet: a connection that cannot be governed is the finding, and policies
added later would silently do nothing.

## Which tables are under a policy

**70 of the 72 tenant-scoped tables** are under `ENABLE` + `FORCE ROW LEVEL
SECURITY` with a fail-closed `tenant_isolation` policy governing reads (`USING`)
and writes (`WITH CHECK`) — six in `d1rls_tenant_policies.py`, 58 in
`d2rls_tenant_policies_rest.py`, and the last six in
`d3rls_tenant_policies_unauthenticated.py`.

### The paths that have no tenant until they find one

Four requests reach the database before any principal exists. Each gets **one
narrow `SECURITY DEFINER` function** answering the single question it needs in
order to announce its tenant — never a policy clause wide enough to permit the
lookup, because a `WHERE` that matches "the row you asked for" is the same
`WHERE` that matches every row, one query at a time.

| path | function | answers |
|---|---|---|
| sign-in | `app_login_lookup(email)` | which organization owns an **active** address |
| sign-up | `app_email_registered(email)` | whether an address is taken **at all** — active or not, because a deactivated user still holds theirs |
| provisioning | `app_org_id_taken(id)` | whether `org_<slug>` already exists, so the collision walk does not stop at the first candidate |
| Zoho OAuth callback | `app_oauth_state_org(hash)` | which organization issued a state token |

Each has `search_path` pinned in its definition: a `SECURITY DEFINER` function
resolving `users` through the *caller's* path can be pointed at a table the
caller made, which is the classic way this construct becomes a privilege
escalation. Four functions rather than one dispatching on a `kind` argument —
each is four lines and reads in one sitting.

The demo workspace needs no function: its organization is named in settings, so
it announces the tenant it already knows.

**Two tables stay out permanently**, and this is the distinction that matters
most — their cross-tenant reads are the *product*, not leaks:

- **`sync_runs`** is what worker capacity is computed from. Capacity is a
  property of the *deployment*, and a version counting only the caller's own
  runs would not be a narrower answer, it would be a wrong one.
- **`zoho_connections`** carries credential sharing: one Zoho grant reaches
  every company that user can see, and `/data/credentials/{id}/organizations`
  exists to say which. A policy there does not secure the feature, it removes
  it.

**The rule to apply to the next table somebody considers:** a policy belongs on
a table whose cross-tenant reads are leaks. Where they are the product, a policy
is a regression wearing the costume of a control.

*No tenant column at all:* `zoho_credentials` (a Zoho refresh token belongs to a
person, not a company) and `process_leases` (infrastructure about processes).
Neither can take this policy shape.

**One named degradation.** `oauth.sweep_expired` runs with a principal, so under
a policy it reaps only the caller's own expired state rows; an organization that
never authorizes again keeps a handful of tiny expired rows. That is
garbage collection, not correctness, and the fix if it ever matters is a
scheduled sweep on the background connection — not a policy exemption.

`test_every_tenant_scoped_table_is_covered_or_deliberately_named` partitions
every tenant-scoped table into those three buckets, so a new model lands as a
failing test naming itself rather than as quiet uncovered surface.

**Sign-in.** It is the one request that cannot know its tenant in advance —
there is no token yet and the organization is a property of the row being looked
for. `app_login_lookup(email)` is a `SECURITY DEFINER` function created by the
same migration: one email in, two columns out, `search_path` pinned so it cannot
be redirected at a table the caller made. `tenancy.adopt_tenant_for_login` calls
it and announces the result. A policy clause wide enough to permit the same
lookup would be wide enough to enumerate the table one query at a time; a
function is narrow in a way a predicate is not.

**One behaviour changes, and it changes for the better.**
`/observability/tenants` had no organization predicate, so any manager or owner
read back the id and signal count of every organization on the deployment. With
`signals` policied and the role split, it returns one row — the caller's. The
response carries `scope` so a reader can tell which of the two they are looking
at rather than inferring it from a row count.

## What stays SQLite, and the honest caveat

Dev-by-default and the test suite. The caveat: SQLite stores `Numeric` as
float and drops timezone info, which is exactly why the gate's step 6 and the
`PIE_TEST_DATABASE_URL` suite mode exist — the dialect production runs is
exercised by machinery, not by hoping the dialects agree. Before a deploy
that touches money arithmetic or timestamp comparisons, run the suite once in
Postgres mode.
