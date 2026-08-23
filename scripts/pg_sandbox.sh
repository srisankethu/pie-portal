#!/usr/bin/env bash
# A disposable PostgreSQL server for verification and tests — no root, no
# docker, no system service. One cluster in a temp directory, listening on a
# unix socket only (nothing to collide with, nothing exposed), removed without
# trace by `stop`.
#
#   scripts/pg_sandbox.sh start   prints the server URL on stdout
#   scripts/pg_sandbox.sh stop    stops the server and deletes the cluster
#   scripts/pg_sandbox.sh url     prints the URL of a running sandbox
#   scripts/pg_sandbox.sh bin     prints the directory the server binaries are in
#
# `bin` exists for restore_drill.py's sake: the documented backup procedure runs
# `pg_dump` and `psql`, and those must be the versioned pair that matches the
# server — on Debian /usr/bin/pg_dump is a wrapper that picks a cluster, not a
# binary. One find_pg_bin, used by both.
#
# Why this exists: the gate's step 5 migrates an EMPTY database, and until the
# Postgres leg landed it only ever did so on SQLite — so the dialect production
# actually runs (deploy/compose.yaml) was the one dialect the gate never
# exercised. This script is what lets verify.sh and the backend suite
# (PIE_TEST_DATABASE_URL) prove the Postgres path anywhere PostgreSQL server
# binaries exist: a developer laptop, CI's ubuntu image, a bare container.
#
# Durability settings (fsync=off etc.) are deliberate and safe HERE ONLY:
# every database this server holds is disposable by construction.
set -euo pipefail

SANDBOX="${PG_SANDBOX_DIR:-${TMPDIR:-/tmp}/pie-pg-sandbox-$(id -u)}"
PORT="${PG_SANDBOX_PORT:-5599}"
SOCKET_DIR="$SANDBOX/sock"
DATA_DIR="$SANDBOX/data"
LOG="$SANDBOX/server.log"

find_pg_bin() {
  # Prefer PATH; fall back to versioned install trees (Debian/Ubuntu, RHEL,
  # macOS/Homebrew), newest first. GitHub's ubuntu runners ship one of these.
  if command -v initdb >/dev/null 2>&1; then
    dirname "$(command -v initdb)"; return
  fi
  local d
  # `if` rather than `[ ] &&`: under set -e + pipefail, a final false test
  # would fail the whole pipeline and silently kill the script.
  for d in /usr/lib/postgresql/*/bin /usr/pgsql-*/bin /opt/homebrew/opt/postgresql@*/bin /usr/local/opt/postgresql@*/bin; do
    if [ -x "$d/initdb" ]; then echo "$d"; fi
  done | sort -rV | head -1
}

# initdb/postgres refuse to run as root. In a root container (dev sandboxes,
# some CI images) the server runs as the `postgres` system user instead; the
# sandbox directory is handed to that user, and root can still connect over
# the socket. Everywhere else this is a plain passthrough.
as_pg_owner() {
  if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
    chown -R postgres "$SANDBOX" 2>/dev/null || true
    su -s /bin/bash postgres -c "$*"
  else
    bash -c "$*"
  fi
}

url() {
  # psycopg treats a query-string `host` as the socket directory. No password:
  # the socket directory is only reachable by this user (and trust auth is
  # scoped to that socket — the server never listens on TCP).
  echo "postgresql+psycopg://pie@/pie_verify?host=$SOCKET_DIR&port=$PORT"
}

# The same database as `url`, reached as the *application* role rather than as
# the owner. This is the only URL a row-level-security test may use, and the
# distinction is the whole reason the role exists.
#
# `pie` is the cluster's bootstrap superuser: `initdb -U pie` makes it one, and
# a superuser carries `rolbypassrls`, which means **every** row-level security
# policy is ignored for it. A policy suite run over `url` would pass while
# proving nothing — a fail-closed policy returns every row to that role, which
# was checked rather than assumed before this was written. `ALTER TABLE …
# FORCE ROW LEVEL SECURITY` binds a table's *owner*; nothing binds BYPASSRLS.
#
# So `pie_app` is NOSUPERUSER, NOBYPASSRLS, and deliberately not the owner of
# anything. It is the closest thing this harness has to how a correctly
# configured production deployment connects — which is the state
# `docs/postgres.md` describes and `compose.yaml` does not yet reach.
app_url() {
  echo "postgresql+psycopg://pie_app@/pie_verify?host=$SOCKET_DIR&port=$PORT"
}

# Created on every start rather than only on first init: the data directory
# outlives a single run (`start` is a no-op when it exists), so a sandbox left
# over from before this role existed would otherwise never grow one.
#
# The grants are on the *schema*, not on tables — Alembic creates those later,
# and `ALTER DEFAULT PRIVILEGES` makes each one reachable as it appears without
# a second pass after every migration. USAGE on sequences is what lets an
# INSERT reach a serial default.
ensure_app_role() {
  local PGBIN="$1"
  as_pg_owner "'$PGBIN/psql' -h '$SOCKET_DIR' -p $PORT -U pie -d pie_verify -v ON_ERROR_STOP=1 -q -c \"
    DO \\\$\\\$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pie_app') THEN
        CREATE ROLE pie_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
      END IF;
    END
    \\\$\\\$;
    GRANT USAGE ON SCHEMA public TO pie_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pie_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pie_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pie_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      GRANT USAGE, SELECT ON SEQUENCES TO pie_app;
  \" >/dev/null"
}

start() {
  PGBIN="$(find_pg_bin)"
  if [ -z "${PGBIN:-}" ]; then
    echo "pg_sandbox: no PostgreSQL server binaries (initdb) found" >&2
    exit 3
  fi
  mkdir -p "$SOCKET_DIR"
  if [ ! -d "$DATA_DIR" ]; then
    as_pg_owner "'$PGBIN/initdb' -D '$DATA_DIR' -U pie --auth=trust -E UTF8 >/dev/null"
  fi
  if ! as_pg_owner "'$PGBIN/pg_ctl' -D '$DATA_DIR' status >/dev/null 2>&1"; then
    as_pg_owner "'$PGBIN/pg_ctl' -D '$DATA_DIR' -l '$LOG' -w -t 60 -o \"\
      -k '$SOCKET_DIR' -p $PORT -c listen_addresses='' \
      -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
      -c max_connections=200\" start >/dev/null"
  fi
  as_pg_owner "'$PGBIN/createdb' -h '$SOCKET_DIR' -p $PORT -U pie pie_verify 2>/dev/null" || true
  ensure_app_role "$PGBIN"
  url
}

stop() {
  PGBIN="$(find_pg_bin)"
  if [ -n "${PGBIN:-}" ] && [ -d "$DATA_DIR" ]; then
    as_pg_owner "'$PGBIN/pg_ctl' -D '$DATA_DIR' -m immediate stop >/dev/null 2>&1" || true
  fi
  rm -rf "$SANDBOX"
}

case "${1:-}" in
  start) start ;;
  stop)  stop ;;
  url)   url ;;
  app-url) app_url ;;
  bin)   find_pg_bin ;;
  *) echo "usage: $0 start|stop|url|app-url|bin" >&2; exit 2 ;;
esac
