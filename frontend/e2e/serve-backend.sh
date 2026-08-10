#!/usr/bin/env bash
# The API, on a database of its own, for the end-to-end run.
#
# A dedicated DATABASE_URL rather than the developer's `data/platform.db`: this
# script drops and rebuilds the file every run, and doing that to somebody's
# working database because they typed `npm run e2e` would be indefensible.
#
# Alembic only, via `app.bootstrap` — CLAUDE.md §4 permits no other path to a
# schema, and a `create_all` here would leave the file unstamped and
# permanently unmigratable.
set -euo pipefail
cd "$(dirname "$0")/../../backend"

# The engine is a private submodule. Honour an explicit checkout, fall back to
# the vendored one; the spec skips itself when neither is present.
export PIE_PARSER_ROOT="${PIE_PARSER_ROOT:-$(cd .. && pwd)/pie-parser}"

# Issued accounts normally have to choose their own password before they can do
# anything, and `authz.current_principal` correctly refuses a flagged account
# everything but that change. This suite is about role scoping on the quote
# grid, not the credential lifecycle, so it seeds accounts that are already
# past it — exactly as `backend/tests/conftest.py` does, and for the same
# reason. The gate itself is not configurable and is tested directly.
export ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD=0

DB="$PWD/data/e2e.db"
mkdir -p "$PWD/data"
rm -f "$DB" "$DB-wal" "$DB-shm"
export DATABASE_URL="sqlite:///$DB"

python3 -m app.bootstrap >/dev/null
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning
