#!/usr/bin/env bash
# The deliberate half of a deploy: apply migrations, then seed the organization.
#
# This is a one-shot service an operator runs, not a step in the API container's
# entrypoint, because docs/operations.md and CLAUDE.md §4 both say so — after an
# incident:
#
#     No auto-migration — schema changes become a deliberate, reviewed deploy
#     step. AUTO_BOOTSTRAP is ignored [in production].
#
# So `docker compose up` never migrates. Nothing changes this database's schema
# unless somebody asked for it, by name:
#
#     docker compose --profile release run --rm release
set -euo pipefail

# Derived from this script's own location rather than hardcoded to the image's
# /app/backend. Both are the same path inside the container, but a hardcoded one
# makes this script container-only — and a managed platform whose database is
# reachable from anywhere (Neon, RDS with a public endpoint) is most easily
# migrated by running exactly this script from a workstation. `railway run`,
# notably, executes on the caller's machine, not in the container.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"

# Where the database sits in the revision history, in the words CLAUDE.md §4
# uses. Printed before *and* after, because the pair is the record of what this
# deploy actually did — "BEHIND -> CURRENT" is a migration that ran, and
# "CURRENT -> CURRENT" is one that had nothing to do, and those look identical
# in a log that only reports success.
state() {
	python - "$1" <<'PY'
import sys
from app.db import engine
from app.migration_state import inspect_database
print(f"[release] {sys.argv[1]}: {inspect_database(engine).summary}", flush=True)
PY
}

state "before"

# Alembic is the only thing permitted to create or alter this schema. If this
# fails with "table already exists", the database is UNSTAMPED and upgrading can
# never work — see the state table in CLAUDE.md §4 before reaching for a fix.
python -m alembic upgrade head

state "after"

# The tenant-scoped role, after the migrations and before anything serves.
#
# Every table here carries ENABLE + FORCE ROW LEVEL SECURITY and a fail-closed
# tenant_isolation policy, and none of it binds the role that owns the schema's
# superuser. Provisioning it here rather than in a runbook step is the whole of
# decision 019: a control that depends on somebody having read a document is a
# control that is off on the deployment nobody read it for.
#
# After `alembic upgrade head` on purpose — the ON ALL TABLES grants cover what
# exists when they run, so a table this deploy just created is reachable without
# a second pass. Prints and exits 0 when APP_DB_PASSWORD is unset, which is a
# deployment that has chosen not to enable this; /api/health's tenant_isolation
# component says so for as long as that holds.
python ../deploy/provision_app_role.py

# app/seed.py carries a published default password so a fresh clone can sign in.
# In production that default is a public credential for an *owner* account —
# full cost, margin and AI-spend visibility — so refuse to seed rather than
# create one and trust somebody to change it later.
if [ "${APP_ENV:-}" = "production" ]; then
	case "${SEED_PASSWORD:-}" in
	"" | "change-me-now")
		cat >&2 <<'EOF'

error: SEED_PASSWORD is unset, or still the default published in app/seed.py.

Seeding now would create an owner account whose password is in this repository.
Put a real one in .env.production and re-run:

    SEED_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')

Every seeded account is flagged must_change_password, so this is the value each
person signs in with once — not the one they keep.
EOF
		exit 1
		;;
	esac
fi

# Idempotent: it creates the organization and the three role accounts if they
# are absent and leaves existing ones alone, so re-running a deploy does not
# reset anybody's password.
python -m app.seed
