#!/usr/bin/env bash
# Stop hook: do not let a turn end on unverified source edits.
#
# This is the mechanical answer to "the agent skipped a step". CLAUDE.md §6 has
# always listed the gate; a document cannot enforce itself, and the failure mode
# is not defiance but plausibility — the change looks obviously correct, so the
# suite feels redundant. It is not. The checks that catch things here are the
# empty-database migration run and the layer invariants, and neither is visible
# by reading a diff.
#
# Deliberately cheap. It does NOT run the suite — that would add four minutes to
# every turn, including ones that only answered a question. It compares a content
# signature against the stamp scripts/verify.sh writes when it passes, and asks
# for a run only when they differ.
#
# Exit 0 allows the turn to end. Exit 2 blocks it and feeds stderr back.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0

# A missing helper must never wedge the session — degrade to allowing the stop.
[ -x scripts/source_signature.sh ] || exit 0

SIG="$(./scripts/source_signature.sh 2>/dev/null)" || exit 0
# Truncated the same way source_signature.sh truncates, which is the whole
# point: it cuts to 32 hex characters so the stamp is not credential-shaped
# (test_no_live_secret_is_committed scans for long hex runs). Comparing against
# the untruncated 64-character hash meant this branch could never be true, so a
# committed, fully-verified tree still asked for a run that could not change
# anything — the check nagged hardest exactly when there was nothing to check.
CLEAN="$(printf '' | sha256sum | cut -d' ' -f1 | cut -c1-32)"

# Nothing uncommitted: there is nothing this hook could usefully ask for.
[ "$SIG" = "$CLEAN" ] && exit 0

STAMP="$(cat .verify-stamp 2>/dev/null || echo none)"
[ "$SIG" = "$STAMP" ] && exit 0

# Loop-breaker. If the gate is genuinely failing, the agent needs to be able to
# stop and report that rather than being held in a retry it cannot win, so a
# second consecutive block is allowed through with the state named.
GUARD=".claude/.verify-nag"
if [ -f "$GUARD" ] && [ "$(cat "$GUARD" 2>/dev/null)" = "$SIG" ]; then
  rm -f "$GUARD"
  exit 0
fi
mkdir -p .claude && printf '%s' "$SIG" > "$GUARD"

# Name the migration case specifically. It is the check most often skipped and
# the one with the worst failure mode: an already-migrated local database cannot
# exercise the empty case, and production only ever runs the empty case.
MIGRATION_NOTE=""
if ! git diff HEAD --quiet -- backend/app/domain backend/alembic 2>/dev/null; then
  MIGRATION_NOTE="
This change touches models or migrations, so the empty-database run in step 5 is
the one that matters. Your own database is already migrated and cannot exercise
it. CLAUDE.md §4 is an account of what happens when nobody checks."
fi

cat >&2 <<EOF
Source has changed since the last green \`make verify\`, so this turn is not done.

Run it now:

    make verify          full gate (~4m: lint, §1 invariants, 1174 backend
                         tests, frontend build, migrations on an EMPTY database)
    make verify-fast     inner loop only — does NOT stamp, does NOT count
$MIGRATION_NOTE

Then report the real numbers: how many tests passed, and what each check said.
CLAUDE.md §6 asks for the gate to be filled in against real tool output, not
from memory.

If the gate is failing and you cannot fix it, say so plainly with the output and
stop — do not describe the change as done. Stopping again will be allowed.
EOF
exit 2
