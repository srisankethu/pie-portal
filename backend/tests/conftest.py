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
    """Build the catalogue once (idempotent) so resolution tests have data.

    A no-op without the engine: the tests that would read it are already
    skipped, and raising here would take the rest of the suite with it.
    """
    if not PIE_AVAILABLE:
        return
    from app.catalog import ensure_catalog
    ensure_catalog()


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
    """
    from app.bootstrap import bootstrap
    bootstrap()
