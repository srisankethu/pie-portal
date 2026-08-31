"""Test fixtures: point the app at the pinned pie-parser submodule and build a
small catalogue once for the whole session.

pie-parser is a *private* submodule, so a checkout without access to it — CI's
default token cannot fetch it — has no engine and no corpus to decode. That
must cost only the tests that actually resolve a product code. It used to cost
the entire suite: this fixture is session-scoped and autouse, so building the
catalogue unconditionally meant a missing engine raised during setup for every
one of the ~1180 tests, including the migration and schema tests whose own
conftest says in as many words that they do not load pie-parser.

So: the catalogue is built when the engine is there, and tests that need it are
marked ``requires_pie`` and skip when it is not. Skipped, never silently
passed — the `pie-contract` gate job fetches the engine and runs them for real.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# Ensure the app uses the vendored submodule + a test catalogue location.
os.environ.setdefault("PIE_PARSER_ROOT", str(REPO / "pie-parser"))
os.environ.setdefault("PIE_CATALOG", str(BACKEND / "data" / "products.jsonl"))

# The seeder does not hand these fixtures accounts that must change their password
# first. Fifty-five test modules seed the demo org to exercise something that is
# not the credential lifecycle, and `authz.current_principal` refuses a flagged
# account everything but the change itself — correctly, which is why it cannot be
# worked around per test. The gate is not configurable and is tested directly, by
# setting the flag on a user; only the *seeder* reads this.
os.environ.setdefault("ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD", "0")

# pie-parser's own packages (`identity`, `engine`, `resolver`) must be importable
# by name, because a few tests import them directly rather than through
# `app.pie_service`.
#
# They used to arrive by side effect: both `app/catalog.py` and `app/pie_service.py`
# insert this path immediately before their own `from engine import ...`. That
# made the result depend on execution order *and* on a generated file —
# `ensure_catalog()` returns early when `products.jsonl` already exists, several
# lines before it touches `sys.path`. So with the engine present but the
# catalogue already built, `test_confirmed_mappings` still failed with
# `ModuleNotFoundError: No module named 'identity'`.
#
# The `requires_pie` marker below is about the engine being *absent*; this is the
# separate case where it is present and merely unimportable. Both are needed.
# Done at import time because a fixture cannot help — the failing import is
# inside a test body, but nothing guarantees another test ran first.
_PIE_ROOT = os.environ["PIE_PARSER_ROOT"]
if _PIE_ROOT not in sys.path:
    sys.path.insert(0, _PIE_ROOT)

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
# Serial runs are untouched, so `pytest tests` behaves exactly as before; only
# `-n` opts into the isolated path. The migration suite passes its own explicit
# URLs and is unaffected either way.
#
# With PIE_TEST_DATABASE_URL set, the same isolation happens on a Postgres
# server instead — the production dialect: each worker gets `<base>_app_<id>`,
# created on first use and migrated by the bootstrap fixture below through the
# real Alembic chain. Serial runs are isolated too (worker "main"), because in
# this mode there is no SQLite file to fall back to. `tests/dbsupport.py` is
# the other half: the per-test fixture databases.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
_PG_TEST = os.environ.get("PIE_TEST_DATABASE_URL")
if _PG_TEST:
    from sqlalchemy.engine import make_url

    _u = make_url(_PG_TEST)
    os.environ["DATABASE_URL"] = str(
        _u.set(database=f"{_u.database}_app_{_WORKER or 'main'}"))
elif _WORKER:
    _worker_db = BACKEND / "data" / f"test_{_WORKER}.db"
    _worker_db.parent.mkdir(parents=True, exist_ok=True)
    # Not setdefault: an inherited DATABASE_URL would put every worker back on
    # one file, which is the failure this exists to prevent.
    os.environ["DATABASE_URL"] = f"sqlite:///{_worker_db}"

#: Whether the engine is actually present. The orchestration entry point is the
#: thing ``pie_service`` loads, so its absence is exactly what "no engine"
#: means — a stale directory left by an interrupted fetch is not an engine.
PIE_AVAILABLE = (Path(os.environ["PIE_PARSER_ROOT"]) / "tools" / "resolve_rfq.py").exists()

_SKIP_REASON = (
    "pie-parser is not checked out, so there is no engine to resolve against. "
    "Fetch it with ./scripts/setup_pie_parser.sh, or set PIE_PARSER_ROOT."
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_pie: needs the pie-parser engine; skipped when it is absent.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if PIE_AVAILABLE:
        return
    skip = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        if "requires_pie" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _catalog():
    """Decode the shipped corpus once, so resolution tests have data to link.

    Catalogues are per company: a test gives its own company this file with
    ``piesupport.give_company_a_catalogue``. Built here rather than lazily so
    the cost lands in session setup instead of in whichever test happens to
    resolve first.

    A no-op without the engine: the tests that would read it are already
    skipped, and raising here would take the rest of the suite with it.
    """
    if not PIE_AVAILABLE:
        return
    import piesupport
    piesupport.shared_catalogue()


@pytest.fixture(scope="session", autouse=True)
def _platform_database():
    """Create and seed the real database once, for the tests that drive the
    real app rather than an in-memory fixture.

    `test_quote_flow` and `test_db_concurrency` go through `app.main`, which
    binds the configured engine at import and reads `backend/data/` — a
    directory that is gitignored and therefore absent on any fresh checkout.
    Nothing in the suite created it, so those tests passed only where somebody
    had run `make bootstrap` by hand and failed everywhere else, CI included,
    with `unable to open database file`.

    The subtler one: without a database the approval gate has nothing to check
    against, so `POST /estimate` answered 200 where the test demands 403. That
    test exists because sending without a platform identity would otherwise be
    the way around every approval in the product — it must never be able to
    pass or fail for an incidental reason.

    Alembic-only and idempotent, so this is `make bootstrap` rather than a
    second schema path (CLAUDE.md §4 — never `create_all` outside a fixture,
    and this is not one of those either).

    On Postgres (PIE_TEST_DATABASE_URL set) the worker's application database
    is created and emptied first, so every run migrates from nothing — a
    leftover schema from another checkout would otherwise make this worker's
    results depend on what ran here last.
    """
    if _PG_TEST:
        import dbsupport

        dbsupport.ensure_database(os.environ["DATABASE_URL"])
        dbsupport.wipe_schema(os.environ["DATABASE_URL"])
    from app.bootstrap import bootstrap
    bootstrap()


@pytest.fixture(autouse=True)
def _no_leaked_zoho_token():
    """A Zoho access token must not survive from one test into the next.

    The client caches it per *credential* and deliberately outlives the source
    object, because a sync builds one source per window and Zoho meters the
    refresh quota per credential rather than per object. Every test in the
    suite builds its source from the same fake credential, so without this a
    token minted by one test is served to the next — and the tests that assert
    the token endpoint *refuses* would never reach the token endpoint at all.

    Looked up by name rather than imported, so this stays a no-op for the runs
    that never touch ``zoho_client``: an unimported module has cached nothing.
    """
    from app import cache

    def drop() -> None:
        entry = cache.get_cache("zoho_access_token")
        if entry is not None:
            entry.clear()

    drop()
    yield
    drop()
