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
- **Least privilege**: the app's database user owns its own database and
  nothing else — it needs no SUPERUSER, no CREATEDB, no CREATEROLE. The
  compose stack's single-purpose user satisfies this by construction; on a
  shared server, create the role yourself rather than reusing an admin one.
  Migrations run as the same user (they create tables in its own database),
  as a deliberate operator step — never automatically at boot.

## What stays SQLite, and the honest caveat

Dev-by-default and the test suite. The caveat: SQLite stores `Numeric` as
float and drops timezone info, which is exactly why the gate's step 6 and the
`PIE_TEST_DATABASE_URL` suite mode exist — the dialect production runs is
exercised by machinery, not by hoping the dialects agree. Before a deploy
that touches money arithmetic or timestamp comparisons, run the suite once in
Postgres mode.
