#!/usr/bin/env bash
# SessionStart hook: make the gate runnable before anything is asked of it.
#
# The single most effective cause of a skipped check is an environment that
# cannot run it. An agent that meets `No module named pytest` does not stop and
# fix the toolchain — it reasons about the diff and writes "tests should pass",
# which reads exactly like a real result.
#
# This repository has two traps a fresh container falls into, and neither
# announces itself:
#
#   * no `cffi`     -> Debian's `cryptography` binds to Rust bindings that import
#                      `_cffi_backend`. Without it, `import app.main` dies with
#                      `pyo3_runtime.PanicException: Python API call failed` and
#                      takes 22 test modules with it at collection.
#   * no pie-parser -> `FileNotFoundError: PIE corpus not found`, which reads
#                      like a data problem and is a missing clone.
#
# Quiet and fast on the common path.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
REPO="$PWD"

need_install=0
python3 -c "import fastapi, sqlalchemy, alembic, pytest, xdist" 2>/dev/null || need_install=1
python3 -m ruff --version >/dev/null 2>&1 || need_install=1
# Imported rather than assumed: the failure this guards against is an *import*
# panic, which a version check would not see.
python3 -c "from cryptography.fernet import Fernet" 2>/dev/null || need_install=1

if [ "$need_install" = "1" ]; then
  echo "pie-portal: installing pinned dev tooling (backend/requirements-dev.txt)…"
  python3 -m pip install -q -r backend/requirements-dev.txt 2>&1 | tail -2 || true
fi

# Locate pie-parser. A sibling checkout is the common shape in a session that
# has both repositories attached, and it is not where the app looks by default.
if [ ! -f "$REPO/pie-parser/corpora/kmt_zcnc_2026-07_nomenclature.csv" ]; then
  for cand in "${PIE_PARSER_ROOT:-}" "$REPO/../pie-parser" "$HOME/pie-parser"; do
    if [ -n "$cand" ] && [ -f "$cand/corpora/kmt_zcnc_2026-07_nomenclature.csv" ]; then
      echo "pie-portal: pie-parser found at $cand"
      echo "            export PIE_PARSER_ROOT=$cand   # needed by the tests"
      break
    fi
  done
fi

WARN=""
python3 -c "from cryptography.fernet import Fernet" 2>/dev/null \
  || WARN="  cryptography still fails to import — the suite cannot run; say so rather than reporting tests as passing."

echo "pie-portal ready — the gate is 'make verify' (~4m) or 'make verify-fast' (~2.5m)."
[ -n "$WARN" ] && echo "$WARN"
exit 0
