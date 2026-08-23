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
  bin)   find_pg_bin ;;
  *) echo "usage: $0 start|stop|url|bin" >&2; exit 2 ;;
esac
