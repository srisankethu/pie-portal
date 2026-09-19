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
#   ./scripts/verify.sh --fast   lint, invariants, the published spec, backend
#                                tests (~2.5 min)
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

# Run a step once, keeping its output, and show *that* output if it failed.
#
# Six checks here used to `>/dev/null 2>&1` and then, on failure, run the whole
# thing a second time to show what went wrong. That is fine for a deterministic
# failure and actively misleading for any other kind: a frontend run exited
# non-zero on CI, the re-run passed, and the log printed 902 passing tests under
# a FAIL header. The evidence of the failure was discarded by the thing whose
# job is to report it, and nobody could diagnose it from the build.
#
#   run_step "<label>" <command...>
#
# stdout and stderr are interleaved into one file so ordering survives, and the
# file is removed on the way out either way.
run_step() {
  local label="$1"; shift
  local log
  log="$(mktemp)"
  if "$@" >"$log" 2>&1; then
    pass "$label"
    rm -f "$log"
    return 0
  fi
  printf '      the failing run said:\n'
  tail -40 "$log"
  rm -f "$log"
  fail "$label"
  return 1
}

# The same, for a step that needs a subshell, a `cd` and environment variables.
# `run_step` takes a command; this takes a fragment of shell.
#
#   run_sh "<ok label>" "<shell>" ["<fail label>"]
#
# Several checks word the two differently on purpose — "row-level security is
# fail-closed" reads as a result, and the failure wants to name the command.
run_sh() {
  local label="$1" script="$2" onfail="${3:-$1}"
  local log
  log="$(mktemp)"
  if bash -c "$script" >"$log" 2>&1; then
    pass "$label"
    rm -f "$log"
    return 0
  fi
  printf '      the failing run said:\n'
  tail -40 "$log"
  rm -f "$log"
  fail "$onfail"
  return 1
}

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
step "1/8  ruff"
if $PY -m ruff check . ; then pass "lint"; else fail "ruff check ."; fi

# ── 2. The §1 invariants ─────────────────────────────────────────────────────
# The rule that makes every number on every screen auditable. This is the same
# check as tests/decision_platform/test_layer_boundaries.py, which parses the
# imports properly and is what actually blocks; it is repeated here because it
# costs milliseconds and because a failure here is legible without reading a
# traceback.
step "2/8  §1 layer invariants"
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

# ── 3. The published ingestion contract ──────────────────────────────────────
# docs/spec/ is generated from app/domain/schemas.py and committed, because the
# people it is for — somebody writing a connector against this platform — have
# this repository's docs and no interpreter to run. A committed artifact is a
# second copy, though, and a second copy of a contract that lags the code is
# worse than no contract at all: it teaches a wrong answer with authority, and
# the reader has no way to find out.
#
# So the exporter regenerates the whole directory in memory and compares. It
# writes nothing here — `--check` is a comparison, and a gate that silently
# fixed the tree would leave the stale artifact committed.
#
# Also covered by tests/decision_platform/test_spec_artifact.py, which is what
# actually blocks. It is repeated here for the reason step 2 is: it costs about
# a second, it runs before the suite rather than inside it, and its failure
# names the stale files and the one command that fixes them instead of an
# assertion message.
step "3/8  the published ingestion contract"
run_step "docs/spec matches the schemas it is generated from" \
  $PY scripts/spec_export.py --check

# ── 4. Backend tests ─────────────────────────────────────────────────────────
# Parallel by default. Each xdist worker gets its own SQLite file (see
# backend/tests/conftest.py) — without that the workers race on one `alembic
# upgrade head` and lose. Set PYTEST_WORKERS=0 to force the serial path.
step "4/8  backend tests"
WORKERS="${PYTEST_WORKERS:-auto}"
if [ "$WORKERS" = "0" ]; then NARG=(); else NARG=(-n "$WORKERS"); fi
if (cd backend && $PY -m pytest tests -q "${NARG[@]}"); then
  pass "backend suite"
else
  fail "backend pytest"
fi

# The connector matrix, which the line above cannot reach: `pytest.ini` carries
# `addopts = -m "not live and not matrix"`, so `pytest tests` deselects all of
# it. It landed with no runner at all — no make target, no workflow, nothing in
# here — while its own docstring said it ran nightly. That is the state
# `live.yml`'s header calls "documentation with a misleading file extension",
# and worse than the live suites were: those at least cost money and needed
# credentials, and this costs half a minute. Two of its own guards depend on it
# running — the one that fails when an eighth connector gets no row, and a
# deliberate copy of a stub whose docstring prices the duplication at "it goes
# stale loudly".
#
# Here rather than in a nightly job so that guard blocks, and outside `--fast`
# for the reason pytest.ini excludes it from the default run: the edit loop
# should not pay for it, and --fast is not enough to merge on anyway.
if [ "$FAST" = "1" ]; then
  printf '      connector matrix skipped (--fast).\n'
else
  run_step "connector matrix" \
    env -C backend $PY -m pytest -m matrix -q "${NARG[@]}"
fi

if [ "$FAST" = "1" ]; then
  step "5-8/8  skipped (--fast)"
  printf '      frontend build, the empty-database migration checks and the\n'
  printf '      restore drill not run.\n'
  printf '      Do not merge on --fast.\n'
else
  # ── 5. Frontend ────────────────────────────────────────────────────────────
  step "5/8  frontend — tests, types, production build"
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
  run_step "vitest" env -C frontend npm test

  run_step "tsc -b + vite build" env -C frontend npm run build

  # ── 6. Migrations, on an EMPTY database ────────────────────────────────────
  # CLAUDE.md §6's empty-database run, and the one check that would have caught
  # the incident in §4. A developer's own database is already migrated and can
  # never exercise
  # the empty case; production always does. Note the `rm`.
  step "6/8  migrations from nothing — SQLite"
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

  # ── 7. Migrations, on an EMPTY database — PostgreSQL ───────────────────────
  # The dialect production actually runs (deploy/compose.yaml) and the one this
  # gate never used to exercise: the chain had been proven only on SQLite while
  # every real deployment migrates Postgres. Same two checks as step 6 —
  # upgrade from nothing, then models-vs-schema drift — on the real dialect.
  #
  # Where the server comes from, in order:
  #   PG_VERIFY_URL   an existing server; the named database is WIPED each run,
  #                   so point it only at a disposable one
  #   pg_sandbox.sh   a throwaway cluster in /tmp, when server binaries exist
  #                   (GitHub's ubuntu runners ship them; most laptops do too)
  # With neither, this is SKIPPED and the verdict says so — narrowed, never
  # silently passed, exactly the pie-parser arrangement above.
  step "7/8  migrations from nothing — PostgreSQL"
  PG_COVERED=1
  RLS_COVERED=1
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
    RLS_COVERED=0
    printf '\033[33mnote:\033[0m no PostgreSQL server binaries and no PG_VERIFY_URL.\n'
    printf '      The Postgres migration check will SKIP. Everything else still runs.\n'
    printf '      To cover it: install postgresql (the server), or point\n'
    printf '      PG_VERIFY_URL at a disposable database.\n'
  else
    run_sh "empty Postgres database migrates to head, no drift" \
      "cd backend && DATABASE_URL='$PG_URL' $PY ../scripts/verify_pg_migrations.py" \
      "Postgres: alembic upgrade head on an empty database, or drift"

    # Row-level security, which cannot be exercised anywhere else in this gate.
    # Step 4 runs the suite on SQLite, which has no policies and no connection
    # settings, so these tests skip there — and a security control whose tests
    # only ever skip is a control nobody has checked.
    #
    # They need *two* URLs and the difference between them is the point: the
    # sandbox's own role is the cluster superuser (`initdb -U pie`), and a
    # superuser carries `rolbypassrls`, which no policy and no FORCE can
    # override. Run over that connection the suite would pass while proving
    # nothing. `app-url` is a role that is neither superuser nor owner.
    #
    # Only the sandbox can offer it. A caller-supplied PG_VERIFY_URL points at
    # a server this script did not provision and has no business creating roles
    # on, so that path skips with a note rather than guessing a username.
    if [ "$PG_SANDBOX_STARTED" = 1 ]; then
      RLS_URL="$(./scripts/pg_sandbox.sh app-url)"
      run_sh "row-level security is fail-closed for a non-bypassing role" \
        "cd backend && PIE_TEST_RLS_URL='$RLS_URL' PIE_TEST_RLS_OWNER_URL='$PG_URL' \
           $PY -m pytest tests/decision_platform/test_row_level_security.py -q" \
        "row-level security"
    else
      RLS_COVERED=0
      printf '\033[33mnote:\033[0m PG_VERIFY_URL points at a server this script did not\n'
      printf '      provision, so it will not create the non-bypassing role the\n'
      printf '      row-level-security tests need. That check will SKIP.\n'
    fi

    # The queue, on the dialect that can actually race. Its claim is a
    # conditional UPDATE so that two processes take different messages, and
    # that property is untestable on the SQLite fixture: it hands every session
    # one shared connection, so a thread race there tests the pool, not the
    # queue. Here each session is a real connection — so this is the only place
    # the concurrency test in test_queue_operations is not skipped.
    #
    # Only the messaging suites, not the whole backend: `docs/postgres.md` has
    # the loop for running everything on Postgres, and adding four minutes to
    # every gate run is how a gate stops being run.
    run_sh "queue behaves on Postgres, concurrent claim included" \
      "cd backend && PIE_TEST_DATABASE_URL='$PG_URL' $PY -m pytest -q \
         tests/decision_platform/test_message_queue.py \
         tests/decision_platform/test_queue_operations.py" \
      "queue suites on Postgres"
  fi

  # ── 8. The documented backup, actually performed ───────────────────────────
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
  # Same server as step 7, deliberately: a second sandbox would mean a second
  # initdb for no coverage. It creates and drops its own two databases on it.
  step "8/8  restore drill — dump, restore, compare"
  if [ -z "$PG_URL" ]; then
    printf '\033[33mnote:\033[0m no PostgreSQL server — the restore drill will SKIP too.\n'
  else
    # Kept, not discarded: this one branches on the exit code (3 means "no
    # pg_dump here"), so it cannot use `run_sh` — but it must still be able to
    # show the run that failed rather than a second, different one.
    DRILL_LOG="$(mktemp)"
    PG_VERIFY_URL="$PG_URL" ./scripts/restore_drill.py >"$DRILL_LOG" 2>&1
    DRILL_RC=$?
    # 3 is "no pg_dump here", not "the backup is broken". Reporting that as a
    # failure would make this the check people learn to re-run and then ignore,
    # which is how a real one gets waved through.
    if [ "$DRILL_RC" = "0" ]; then
      pass "pg_dump → restore round-trips every row, Σ, audit chain and receipt"
    elif [ "$DRILL_RC" = "3" ]; then
      # Its own flag. This used to set PG_COVERED=0, which made a skipped drill
      # report the Postgres MIGRATION check as skipped too — even when step 7
      # had just run and passed against PG_VERIFY_URL. One flag standing for two
      # checks is how a verdict starts lying about which one it means.
      DRILL_COVERED=0
      printf '\033[33mnote:\033[0m the restore drill SKIPPED — no pg_dump/psql on this\n'
      printf '      machine. CI covers it.\n'
    else
      printf '      the failing run said:\n'
      tail -40 "$DRILL_LOG"
      fail "restore drill: the documented backup procedure did not round-trip"
    fi
    rm -f "$DRILL_LOG"
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
       || [ "${DRILL_COVERED:-1}" = "0" ] || [ "${RLS_COVERED:-1}" = "0" ]; then
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
      if [ "${RLS_COVERED:-1}" = "0" ]; then
        printf '      · the row-level-security checks were SKIPPED — they need a\n'
        printf '        PostgreSQL role that does not bypass RLS, which only the\n'
        printf '        sandbox provisions. SQLite has no policies, so nothing\n'
        printf '        else in this gate says anything about tenant isolation.\n'
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
