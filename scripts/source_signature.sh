#!/usr/bin/env bash
# A content signature for everything the gate actually checks.
#
# Two callers need to agree on "has the source changed since it was last
# verified?": scripts/verify.sh stamps this value when it passes, and the Claude
# Code stop-hook compares against that stamp. Written once, here — two
# definitions of "source" would drift and the hook would then either nag about
# nothing or stay quiet about something real.
#
# It covers uncommitted work only: tracked modifications plus untracked files. A
# clean tree signs as the empty string, which is what makes "nothing to verify"
# distinguishable from "verified".
set -uo pipefail
cd "$(dirname "$0")/.."

# What the gate reads. docs/ is deliberately absent — editing architecture.md
# cannot change whether the suite passes, and nagging about it is how a check
# gets muted. backend/data/ is absent because it is generated.
PATHS=(backend/app backend/tests backend/alembic frontend/src scripts
       ruff.toml backend/requirements.txt backend/requirements-dev.txt
       frontend/package.json frontend/package-lock.json backend/pytest.ini)

{
  git diff HEAD -- "${PATHS[@]}" 2>/dev/null
  # Untracked files are hashed by content, not just named: a new module that
  # keeps its name while its body changes must still invalidate the stamp.
  git ls-files --others --exclude-standard -- "${PATHS[@]}" 2>/dev/null \
    | sort | while read -r f; do
        [ -f "$f" ] && printf '%s ' "$f" && sha256sum "$f" | cut -d' ' -f1
      done
# Truncated to 32 hex characters — 128 bits, which is far more than enough to
# answer "did the source change". The full 64 is deliberately avoided: a bare
# long hex run is exactly the shape
# tests/decision_platform/test_crypto.py::test_no_live_secret_is_committed
# scans for, and it flagged .verify-stamp as a possible Zoho client secret. The
# right fix is a stamp that is not credential-shaped, not a looser scanner —
# that test exists because a real refresh token was once committed.
} | sha256sum | cut -d' ' -f1 | cut -c1-32
