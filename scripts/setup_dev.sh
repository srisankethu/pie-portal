#!/usr/bin/env bash
# Everything needed before `make verify` can run, from a bare clone.
#
# This exists because the previous answer was four steps spread over three
# documents, and the two that are easiest to miss are the two that produce the
# most confusing failures:
#
#   * no `cffi`  -> `pyo3_runtime.PanicException: Python API call failed` at
#                   import of `cryptography`, killing 22 test modules at
#                   collection. Nothing in the traceback says "install cffi".
#   * no pie-parser -> `FileNotFoundError: PIE corpus not found`, which reads
#                   like a data problem and is actually a missing clone.
#
# Idempotent: safe to re-run, cheap when everything is already in place.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"

echo "── pie-parser (pinned clone) ──────────────────────────────────────────"
if [ -n "${PIE_PARSER_ROOT:-}" ] && [ -d "$PIE_PARSER_ROOT" ]; then
  echo "using PIE_PARSER_ROOT=$PIE_PARSER_ROOT"
elif [ -f "$REPO/pie-parser/corpora/kmt_zcnc_2026-07_nomenclature.csv" ]; then
  echo "already present at ./pie-parser"
else
  ./scripts/setup_pie_parser.sh || {
    echo "pie-parser could not be fetched. It is a private repo — see the script's"
    echo "own message for the auth options, or set PIE_PARSER_ROOT to a checkout."
    exit 1
  }
fi

echo
echo "── backend (pinned dev tooling) ───────────────────────────────────────"
python3 -m pip install -q -r backend/requirements-dev.txt

echo
echo "── frontend ───────────────────────────────────────────────────────────"
if [ -d frontend/node_modules ]; then
  echo "node_modules present; run 'cd frontend && npm ci' to refresh"
else
  # The browsers are not needed to typecheck or build, and fetching them is
  # minutes for nothing.
  (cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci)
fi

echo
echo "── database ───────────────────────────────────────────────────────────"
(cd backend && python3 -m app.bootstrap 2>&1 | tail -6)

echo
echo "Ready. The gate is 'make verify' (~4 min) or 'make verify-fast' (~2.5 min)."
