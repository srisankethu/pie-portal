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
# --fast is for the edit loop, not for merging: it skips the frontend build and
# the empty-database migration check. CI always runs the full thing.
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
step "1/5  ruff"
if $PY -m ruff check . ; then pass "lint"; else fail "ruff check ."; fi

# ── 2. The §1 invariants ─────────────────────────────────────────────────────
# The rule that makes every number on every screen auditable. This is the same
# check as tests/decision_platform/test_layer_boundaries.py, which parses the
# imports properly and is what actually blocks; it is repeated here because it
# costs milliseconds and because a failure here is legible without reading a
# traceback.
step "2/5  §1 layer invariants"
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
step "3/5  backend tests"
WORKERS="${PYTEST_WORKERS:-auto}"
if [ "$WORKERS" = "0" ]; then NARG=(); else NARG=(-n "$WORKERS"); fi
if (cd backend && $PY -m pytest tests -q "${NARG[@]}"); then
  pass "backend suite"
else
  fail "backend pytest"
fi

if [ "$FAST" = "1" ]; then
  step "4-5/5  skipped (--fast)"
  printf '      frontend build and the empty-database migration check not run.\n'
  printf '      Do not merge on --fast.\n'
else
  # ── 4. Frontend ────────────────────────────────────────────────────────────
  step "4/5  frontend — types + production build"
  if [ ! -d frontend/node_modules ]; then
    printf '      installing frontend dependencies (npm ci)…\n'
    (cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci >/dev/null 2>&1) \
      || fail "npm ci"
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
  step "5/5  migrations from nothing"
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
    if [ "$PIE_AVAILABLE" = "0" ]; then
      # Still stamped: the gate did run, and nagging a developer who simply has
      # no submodule would train them to ignore the hook. But a narrowed run must
      # never read as a full one, so the verdict says which part went uncovered.
      printf '\033[32mVERIFIED\033[0m — all checks passed, \033[33mbut narrowed\033[0m:\n'
      printf '      the engine-backed (requires_pie) tests were SKIPPED, because\n'
      printf '      pie-parser is not checked out. CI covers them in pie-contract.\n'
    else
      printf '\033[32mVERIFIED\033[0m — all checks passed.\n'
    fi
  fi
  exit 0
fi
printf '\033[31mNOT VERIFIED\033[0m — %d check(s) failed:\n' "${#FAILED[@]}"
printf '  · %s\n' "${FAILED[@]}"
exit 1
