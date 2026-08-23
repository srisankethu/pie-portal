#!/usr/bin/env bash
# The gate from CLAUDE.md §6, as one command.
#
# This is the ONLY definition of "verified" in this repository. `make verify`
# runs it, `.github/workflows/gate.yml` runs it, and the Claude Code stop-hook
# runs it. That is the whole design, and it is a direct response to what
# happened without it: the workflow and CLAUDE.md §6 were two separate lists of
# the same checks, they drifted, and the drift was invisible for eight
# consecutive merges to main while the gate stayed red.
#
#   ./scripts/verify.sh          everything (~4 min)
#   ./scripts/verify.sh --fast   lint, invariants, backend tests (~2.5 min)
#
# --fast is for the edit loop, not for merging: it skips the frontend build, the
# empty-database migration check and the restore drill. CI always runs the full
# thing.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

FAILED=()
step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL\033[0m  %s\n' "$1"; FAILED+=("$1"); }
pass() { printf '\033[32mok\033[0m    %s\n' "$1"; }

PY="${PYTHON:-python3}"

# pie-parser is imported in-process (backend/app/pie_service.py) from a pinned
# submodule. Its absence is a *narrowing* of what this run proves, not a reason
# to refuse to run: `backend/tests/conftest.py` skips the `requires_pie` tests
# when the engine is missing, so everything else still executes.
#
# This used to be a hard stop, which was wrong in the way that matters — it let
# one missing credential decide whether lint, 1174 tests and the migration check
# ran at all. A red check nobody can fix is a check people learn to ignore.
#
# Skipped, never silently passed: the seam is covered by the `pie-contract` job,
# which fetches the engine and runs exactly the marked set.
export PIE_PARSER_ROOT="${PIE_PARSER_ROOT:-$REPO/pie-parser}"
PIE_AVAILABLE=1
if [ ! -f "$PIE_PARSER_ROOT/tools/resolve_rfq.py" ]; then
  PIE_AVAILABLE=0
  printf '\033[33mnote:\033[0m pie-parser is not checked out at %s\n' "$PIE_PARSER_ROOT"
  printf '      The engine-backed tests will SKIP. Everything else still runs.\n'
  printf '      To cover them: git submodule update --init pie-parser\n'
  printf '      (or set PIE_PARSER_ROOT to an existing checkout).\n'
fi

# ── 1. Lint ──────────────────────────────────────────────────────────────────
# Rule set in ruff.toml, version pinned in backend/requirements-dev.txt. Both
# halves are needed; the header of ruff.toml has the incident.
step "1/7  ruff"
if $PY -m ruff check . ; then pass "lint"; else fail "ruff check ."; fi

# ── 2. The §1 invariants ─────────────────────────────────────────────────────
# The rule that makes every number on every screen auditable. This is the same
# check as tests/decision_platform/test_layer_boundaries.py, which parses the
# imports properly and is what actually blocks; it is repeated here because it
# costs milliseconds and because a failure here is legible without reading a
# traceback.
step "2/7  §1 layer invariants"
INV_OK=1
if grep -rnE '^\s*(from|import)\s+\.*\.?ai[. ]' \
     backend/app/commercial backend/app/signals \
     backend/app/ingestion backend/app/state 2>/dev/null; then
  printf '      A deterministic layer imports ai/. Prices, margins and priorities\n'
  printf '      are computed there and only ever *read* in ai/.\n'
  INV_OK=0
fi
if grep -rnE '^\s*(from|import)\s+.*commercial' backend/app/ai 2>/dev/null; then
  printf '      ai/ imports commercial/. It receives facts; it must never be able\n'
  printf '      to compute one.\n'
  INV_OK=0
fi
if [ "$INV_OK" = "1" ]; then pass "deterministic layers never import ai/"; else fail "§1 layer boundary violated"; fi

# ── 3. Backend tests ─────────────────────────────────────────────────────────
# Parallel by default. Each xdist worker gets its own SQLite file (see
# backend/tests/conftest.py) — without that the workers race on one `alembic
# upgrade head` and lose. Set PYTEST_WORKERS=0 to force the serial path.
step "3/7  backend tests"
WORKERS="${PYTEST_WORKERS:-auto}"
if [ "$WORKERS" = "0" ]; then NARG=(); else NARG=(-n "$WORKERS"); fi
if (cd backend && $PY -m pytest tests -q "${NARG[@]}"); then
  pass "backend suite"
else
  fail "backend pytest"
fi

if [ "$FAST" = "1" ]; then
  step "4-7/7  skipped (--fast)"
  printf '      frontend build, the empty-database migration checks and the\n'
  printf '      restore drill not run.\n'
  printf '      Do not merge on --fast.\n'
else
  # ── 4. Frontend ────────────────────────────────────────────────────────────
  step "4/7  frontend — tests, types, production build"
  if [ ! -d frontend/node_modules ]; then
    printf '      installing frontend dependencies (npm ci)…\n'
    (cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci >/dev/null 2>&1) \
      || fail "npm ci"
  fi

  # Tests before the build: a failing assertion names what broke, where a
  # failing bundle only says the bundle failed. Until these landed, `tsc -b` and
  # `vite build` were the *entire* frontend gate — a screen could render the
  # wrong number and pass, so long as the types lined up.
  #
  # The one to watch is LineGrid.test.tsx. It renders the quote grid for a sales
  # role from a fixture deliberately carrying cost and margin, and asserts
  # neither appears. The server omitting them is the real guarantee and is
  # tested in the backend suite; this catches the other way it could break — a
  # component rendering whatever it is handed.
  if (cd frontend && npm test >/dev/null 2>&1); then
    pass "vitest"
  else
    printf '      re-running to show the failure:\n'
    (cd frontend && npm test 2>&1 | tail -30)
    fail "frontend tests"
  fi

  if (cd frontend && npm run build >/dev/null 2>&1); then
    pass "tsc -b + vite build"
  else
    printf '      re-running to show the error:\n'
    (cd frontend && npm run build 2>&1 | tail -30)
    fail "frontend build"
  fi

  # ── 5. Migrations, on an EMPTY database ────────────────────────────────────
  # CLAUDE.md §6 step 5, and the one check that would have caught the incident
  # in §4. A developer's own database is already migrated and can never exercise
  # the empty case; production always does. Note the `rm`.
  step "5/7  migrations from nothing — SQLite"
  MIGDB="$(mktemp -u /tmp/verify-mig-XXXXXX.db)"
  rm -f "$MIGDB"
  if (cd backend && DATABASE_URL="sqlite:///$MIGDB" $PY -m alembic upgrade head >/dev/null 2>&1); then
    # Drift: the models and the migrations must describe the same schema.
    if (cd backend && DATABASE_URL="sqlite:///$MIGDB" \
          $PY -m pytest tests/decision_platform/test_migrations_integrity.py -q >/dev/null 2>&1); then
      # Two heads means two branches each added a migration and nobody merged
      # them. It fails on the one deployment that runs them in the wrong order,
      # which is always the production one.
      HEADS=$(cd backend && DATABASE_URL="sqlite:///$MIGDB" $PY -m alembic heads 2>/dev/null | grep -c .)
      if [ "$HEADS" -eq 1 ]; then
        pass "empty database migrates to a single head, no drift"
      else
        (cd backend && DATABASE_URL="sqlite:///$MIGDB" $PY -m alembic heads)
        printf '      %s heads. Run: alembic merge -m "merge" <rev> <rev>\n' "$HEADS"
        fail "more than one migration head"
      fi
    else
      printf '      Schema and models disagree. To see exactly how, CLAUDE.md §4\n'
      printf '      "Schema drift" has the compare_metadata snippet.\n'
      fail "migration integrity / schema drift"
    fi
  else
    fail "alembic upgrade head on an empty database"
  fi
  rm -f "$MIGDB"

  # ── 6. Migrations, on an EMPTY database — PostgreSQL ───────────────────────
  # The dialect production actually runs (deploy/compose.yaml) and the one this
  # gate never used to exercise: the chain had been proven only on SQLite while
  # every real deployment migrates Postgres. Same two checks as step 5 —
  # upgrade from nothing, then models-vs-schema drift — on the real dialect.
  #
  # Where the server comes from, in order:
  #   PG_VERIFY_URL   an existing server; the named database is WIPED each run,
  #                   so point it only at a disposable one
  #   pg_sandbox.sh   a throwaway cluster in /tmp, when server binaries exist
  #                   (GitHub's ubuntu runners ship them; most laptops do too)
  # With neither, this is SKIPPED and the verdict says so — narrowed, never
  # silently passed, exactly the pie-parser arrangement above.
  step "6/7  migrations from nothing — PostgreSQL"
  PG_COVERED=1
  PG_SANDBOX_STARTED=0
  PG_URL="${PG_VERIFY_URL:-}"
  if [ -z "$PG_URL" ]; then
    if PG_URL=$(./scripts/pg_sandbox.sh start 2>/dev/null); then
      PG_SANDBOX_STARTED=1
    else
      PG_URL=""
    fi
  fi
  if [ -z "$PG_URL" ]; then
    PG_COVERED=0
    printf '\033[33mnote:\033[0m no PostgreSQL server binaries and no PG_VERIFY_URL.\n'
    printf '      The Postgres migration check will SKIP. Everything else still runs.\n'
    printf '      To cover it: install postgresql (the server), or point\n'
    printf '      PG_VERIFY_URL at a disposable database.\n'
  else
    # One script for the quiet run and the show-the-failure rerun — two inline
    # copies would be this file's own §6 drift story all over again.
    if (cd backend && DATABASE_URL="$PG_URL" $PY ../scripts/verify_pg_migrations.py) >/dev/null 2>&1; then
      pass "empty Postgres database migrates to head, no drift"
    else
      printf '      re-running to show the failure:\n'
      (cd backend && DATABASE_URL="$PG_URL" $PY ../scripts/verify_pg_migrations.py) 2>&1 | tail -25
      fail "Postgres: alembic upgrade head on an empty database, or drift"
    fi
  fi

  # ── 7. The documented backup, actually performed ───────────────────────────
  # docs/hosting.md tells an operator to pg_dump this database and restore the
  # dump into an empty one. Nothing had ever run that instruction, which made it
  # a hypothesis about a file — and the half of this database a re-sync cannot
  # rebuild (signals, decisions, approvals, the audit chain) is precisely the
  # half nobody would find out about until they needed it.
  #
  # The sharpest assertion is the audit chain: entries are HMAC-linked and
  # anchored, so a restore that brings the chain back failing `trust/audit.verify`
  # is indistinguishable, from the operator's chair, from somebody having altered
  # the log. The script's docstring states exactly what this does and does not
  # prove — it is not evidence about any particular production backup.
  #
  # Same server as step 6, deliberately: a second sandbox would mean a second
  # initdb for no coverage. It creates and drops its own two databases on it.
  step "7/7  restore drill — dump, restore, compare"
  if [ -z "$PG_URL" ]; then
    printf '\033[33mnote:\033[0m no PostgreSQL server — the restore drill will SKIP too.\n'
  else
    PG_VERIFY_URL="$PG_URL" ./scripts/restore_drill.py >/dev/null 2>&1
    DRILL_RC=$?
    # 3 is "no pg_dump here", not "the backup is broken". Reporting that as a
    # failure would make this the check people learn to re-run and then ignore,
    # which is how a real one gets waved through.
    if [ "$DRILL_RC" = "0" ]; then
      pass "pg_dump → restore round-trips every row, Σ, audit chain and receipt"
    elif [ "$DRILL_RC" = "3" ]; then
      # Its own flag. This used to set PG_COVERED=0, which made a skipped drill
      # report the Postgres MIGRATION check as skipped too — even when step 6
      # had just run and passed against PG_VERIFY_URL. One flag standing for two
      # checks is how a verdict starts lying about which one it means.
      DRILL_COVERED=0
      printf '\033[33mnote:\033[0m the restore drill SKIPPED — no pg_dump/psql on this\n'
      printf '      machine. CI covers it.\n'
    else
      printf '      re-running to show the failure:\n'
      PG_VERIFY_URL="$PG_URL" ./scripts/restore_drill.py 2>&1 | tail -30
      fail "restore drill: the documented backup procedure did not round-trip"
    fi
  fi

  [ "$PG_SANDBOX_STARTED" = "1" ] && ./scripts/pg_sandbox.sh stop >/dev/null 2>&1
fi

# ── Verdict ──────────────────────────────────────────────────────────────────
printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  if [ "$FAST" = "1" ]; then
    printf '\033[32mVERIFIED (fast)\033[0m — run without --fast before merging.\n'
  else
    # Stamp the source signature so the Claude Code stop-hook can tell "verified"
    # from "not run yet". Only a full run stamps: --fast skipped two checks, and
    # a stamp that lies is worse than no stamp.
    ./scripts/source_signature.sh > .verify-stamp 2>/dev/null || true
    if [ "$PIE_AVAILABLE" = "0" ] || [ "${PG_COVERED:-1}" = "0" ] \
       || [ "${DRILL_COVERED:-1}" = "0" ]; then
      # Still stamped: the gate did run, and nagging a developer who simply has
      # no submodule would train them to ignore the hook. But a narrowed run must
      # never read as a full one, so the verdict says which part went uncovered.
      printf '\033[32mVERIFIED\033[0m — all checks passed, \033[33mbut narrowed\033[0m:\n'
      if [ "$PIE_AVAILABLE" = "0" ]; then
        printf '      · the engine-backed (requires_pie) tests were SKIPPED, because\n'
        printf '        pie-parser is not checked out. CI covers them in pie-contract.\n'
      fi
      if [ "${PG_COVERED:-1}" = "0" ]; then
        printf '      · the PostgreSQL migration check was SKIPPED — no server\n'
        printf '        binaries and no PG_VERIFY_URL. CI covers it, and production\n'
        printf '        migrates Postgres, so cover it locally before a deploy.\n'
      fi
      if [ "${DRILL_COVERED:-1}" = "0" ]; then
        printf '      · the restore drill was SKIPPED — no pg_dump/psql client on\n'
        printf '        this machine. CI covers it. Production is the only place a\n'
        printf '        backup matters, so cover it locally before a deploy.\n'
      fi
    else
      printf '\033[32mVERIFIED\033[0m — all checks passed.\n'
    fi
  fi
  exit 0
fi
printf '\033[31mNOT VERIFIED\033[0m — %d check(s) failed:\n' "${#FAILED[@]}"
printf '  · %s\n' "${FAILED[@]}"
exit 1
