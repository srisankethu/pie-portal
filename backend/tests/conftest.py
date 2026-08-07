"""Test fixtures: point the app at the pinned pie-parser submodule, bring the
schema up, and build a small catalogue — once for the whole session.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# pie-parser's own packages (`identity`, `engine`, `resolver`) must be importable
# by name, because a few tests import them directly rather than through
# `app.pie_service`.
#
# They used to arrive by side effect: both `app/catalog.py` and `app/pie_service.py`
# insert this path just before their own `from engine import ...`. That made the
# suite's result depend on execution order, and on a *generated file* —
# `ensure_catalog()` returns early when `products.jsonl` already exists, several
# lines before it touches `sys.path`. So on a machine that had built the
# catalogue, `tests/test_confirmed_mappings.py` failed with
# `ModuleNotFoundError: No module named 'identity'`; on a clean one it passed.
# Distributing the suite across xdist workers exposed the same thing.
#
# Done here, once, unconditionally: the fixture below cannot do it, because
# import-time failures in test modules happen before any fixture runs.
_PIE_ROOT = os.environ.get("PIE_PARSER_ROOT") or str(REPO / "pie-parser")
if _PIE_ROOT not in sys.path:
    sys.path.insert(0, _PIE_ROOT)

# Ensure the app uses the vendored submodule + a test catalogue location.
#
# `setdefault`, not assignment: the migration suite and CI both supply their own
# values, and overriding a deliberately-set variable is how a test ends up
# proving something about the wrong database.
os.environ.setdefault("PIE_PARSER_ROOT", str(REPO / "pie-parser"))
os.environ.setdefault("PIE_CATALOG", str(BACKEND / "data" / "products.jsonl"))

# Under pytest-xdist, give every worker its own database.
#
# Without this the workers race to bring up the same SQLite file and lose:
# `sqlite3.OperationalError: table alembic_version already exists`, from two
# processes running `alembic upgrade head` against one path. WAL fixes
# concurrent *readers and writers*; it does not make two concurrent schema
# migrations one migration.
#
# This must happen at import time, before anything reads `app.config` — the
# settings object resolves DATABASE_URL once, on first import, and a later
# assignment would be read by nothing.
#
# Serial runs are untouched, so `pytest tests` behaves exactly as before. Only
# `-n` opts into the isolated path, which is also why the migration suite (which
# passes its own explicit URLs) is unaffected either way.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
if _WORKER:
    _worker_db = BACKEND / "data" / f"test_{_WORKER}.db"
    _worker_db.parent.mkdir(parents=True, exist_ok=True)
    # Not setdefault: an inherited DATABASE_URL would put every worker back on
    # one file, which is the failure this exists to prevent.
    os.environ["DATABASE_URL"] = f"sqlite:///{_worker_db}"


@pytest.fixture(scope="session", autouse=True)
def _schema_and_catalog():
    """Make the suite runnable from a fresh clone.

    Two session-scoped preconditions, in order. The catalogue was already here;
    the schema was not, and its absence made the suite depend on whether somebody
    had happened to run ``python -m app.bootstrap`` earlier. On a clean checkout
    the two end-to-end quote tests failed with ``no such table: users`` — the
    exact failure ``app/bootstrap.py`` was written to make impossible, reached by
    the one path that never called it.

    ``TestClient(app)`` is constructed at module import time in several test
    files rather than used as a context manager, so the app's lifespan never
    runs and never bootstraps. Doing it here instead of changing those files
    keeps the fix in one place.

    ``bootstrap()`` is idempotent, so this is safe to re-run, and cheap when the
    schema is already at head.
    """
    from app.bootstrap import bootstrap
    from app.catalog import ensure_catalog

    bootstrap()
    ensure_catalog()
