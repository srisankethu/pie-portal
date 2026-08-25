#!/usr/bin/env bash
#
# Take a database dump, verify it, put it where the health check looks, and
# prune what has aged out.
#
# `scripts/restore_drill.py` proves on every `make verify` that the procedure in
# docs/hosting.md round-trips this schema. It says nothing about a backup
# existing — it dumps a database it created seconds earlier. This is the other
# half. Between them: the procedure is tested, and the artefact is present.
#
# Three things here are load-bearing and none of them are obvious.
#
# **pipefail.** A shell pipeline reports its *last* command's status, so without
# it a `pg_dump` that dies halfway still exits 0 and leaves a perfectly valid
# gzip archive of nothing. docs/hosting.md has said this since restore_drill.py
# was written; this script is where it stops being advice.
#
# **The rename.** `pipefail` catches a dump that *fails*. It does not catch one
# that is interrupted — a kill, a full disk, a container stopped mid-write —
# which leaves a partial file whose mtime is fresh and whose name is a backup.
# So the dump is written to `.part`, tested with `gzip -t`, size-checked, and
# only then moved into place. `mv` within one directory is atomic, so the
# backup directory only ever contains dumps that completed. That is what lets
# the health check trust an mtime instead of guessing.
#
# **The newest file is never pruned, whatever the cutoff.** `BACKUP_RETAIN_DAYS=0`
# is the natural way to spell "keep only the latest" and must not come out as
# "keep none". Two things make sure of that and they are deliberately not one:
# the cutoff is fixed before the dump runs, so the file this run writes can
# never fall outside its own window; and the head of the list is skipped
# unconditionally, so a directory would still keep one dump under a cutoff that
# swept everything. The second is redundant while this script only ever prunes
# after a dump it took itself — and redundant is the point. It is the whole
# safety margin the day this grows a prune-only mode, or somebody points it at
# a directory the dump did not write to.
#
# Timestamps are UTC and to the second. The procedure in docs/hosting.md used
# `date +%F`, so a second run on one day silently overwrote the first — and the
# day you take an extra dump is the day you are about to do something risky.
#
# Environment:
#
#   BACKUP_DIR          where dumps land. Required; no default, because a
#                       default would put them somewhere nobody is watching.
#   BACKUP_RETAIN_DAYS  how long to keep them (default 14). Matches
#                       `settings.BACKUP_RETAIN_DAYS`.
#   BACKUP_MIN_BYTES    below this a file is not a database (default 1024).
#                       Matches `settings.BACKUP_MIN_BYTES`.
#   BACKUP_DUMP_CMD     the command that writes a plain SQL dump to stdout.
#                       Defaults to the compose form in docs/hosting.md.
#                       Override it for a deployment that is not compose, or
#                       to exercise this script without a database.
#   COMPOSE_ENV_FILE    default .env.production
#   BACKUP_DB_SERVICE   compose service name, default db
#   BACKUP_DB_USER      default pie_portal
#   BACKUP_DB_NAME      default pie_portal
#
# Exit codes, which are the whole interface to cron:
#
#   0  a dump completed, was verified, and is in place
#   2  BACKUP_DIR is not set, or is not a writable directory
#   3  the dump command failed, or produced something that is not a valid,
#      plausibly-sized gzip. Nothing was moved into place.
#   4  the dump succeeded but pruning did not. The backup is good; something
#      about the directory is not.
set -Eeuo pipefail

readonly RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-14}"
readonly MIN_BYTES="${BACKUP_MIN_BYTES:-1024}"
readonly PREFIX="pie-portal-"

die() { printf 'backup: %s\n' "$1" >&2; exit "$2"; }

if [ -z "${BACKUP_DIR:-}" ]; then
  die "BACKUP_DIR is not set. There is no default: a dump written somewhere nobody watches is not a backup." 2
fi
[ -d "$BACKUP_DIR" ] || die "BACKUP_DIR $BACKUP_DIR is not a directory" 2
[ -w "$BACKUP_DIR" ] || die "BACKUP_DIR $BACKUP_DIR is not writable" 2

# The dump command. Kept as one string so a deployment can replace the whole
# pipeline — a managed Postgres reached over the network has no compose service
# to exec into.
DUMP_CMD="${BACKUP_DUMP_CMD:-docker compose --env-file ${COMPOSE_ENV_FILE:-.env.production} exec -T ${BACKUP_DB_SERVICE:-db} pg_dump -U ${BACKUP_DB_USER:-pie_portal} ${BACKUP_DB_NAME:-pie_portal}}"

# A free name, never an occupied one. `mv` over an existing file destroys it
# without a word, and a stamp to the second is only *finer* than the `date +%F`
# this replaces — not different in kind. Two runs inside one second is exactly
# the hand-run-before-something-risky case, which is the one time the dump
# being overwritten is the dump that mattered.
# The retention cutoff is fixed *before* the dump, not after it. Taken
# afterwards it is a later instant than the file's own mtime, so with
# `BACKUP_RETAIN_DAYS=0` a second ticking over between the rename and this
# arithmetic puts the dump this run just wrote on the wrong side of its own
# window. Rare, and rare is the worst kind: it would delete a backup roughly
# one run in a thousand and never on the run anybody was watching.
cutoff=$(( $(date -u +%s) - RETAIN_DAYS * 86400 ))

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/${PREFIX}${stamp}.sql.gz"
suffix=1
while [ -e "$final" ]; do
  suffix=$((suffix + 1))
  final="$BACKUP_DIR/${PREFIX}${stamp}-${suffix}.sql.gz"
done
part="$final.part"

# The .part file must not survive a failure — a half-written dump left lying
# around is the thing the next operator has to reason about at three in the
# morning.
cleanup() { rm -f "$part"; }
trap cleanup EXIT

if ! eval "$DUMP_CMD" | gzip > "$part"; then
  die "the dump command failed; nothing was moved into place" 3
fi
if ! gzip -t "$part" 2>/dev/null; then
  die "the dump is not a readable gzip archive; nothing was moved into place" 3
fi

size="$(wc -c < "$part" | tr -d ' ')"
if [ "$size" -lt "$MIN_BYTES" ]; then
  die "the dump is ${size} bytes, below the ${MIN_BYTES}-byte floor — that is a file, not a database" 3
fi

mv "$part" "$final"
trap - EXIT
printf 'backup: wrote %s (%s bytes)\n' "$final" "$size"

# ── prune ────────────────────────────────────────────────────────────────────
# Newest first, so index 0 is the one that is never touched whatever its age.
prune_failed=0
first=1
while IFS= read -r path; do
  [ -n "$path" ] || continue
  if [ "$first" = 1 ]; then
    first=0
    continue
  fi
  mtime="$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null || echo 0)"
  if [ "$mtime" -lt "$cutoff" ]; then
    if rm -f "$path"; then
      printf 'backup: pruned %s\n' "$path"
    else
      printf 'backup: could not prune %s\n' "$path" >&2
      prune_failed=1
    fi
  fi
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${PREFIX}*.sql.gz" -printf '%T@ %p\n' 2>/dev/null \
         | sort -rn | cut -d' ' -f2-)

[ "$prune_failed" = 0 ] || exit 4
