"""Test fixtures: point the app at the pinned pie-parser clone, and build the
decoded catalogue for the tests that actually resolve a product code.

Why this is opt-in rather than autouse
--------------------------------------
It used to be a session-scoped ``autouse`` fixture, which made a pie-parser
checkout a precondition for *every* test in the suite — including the migration
integrity tests, which state in their own docstring that they do not load
pie-parser.

pie-parser is a separate private repository. CI does not have it, so the fixture
raised ``FileNotFoundError`` during setup and took the entire run down with it:
the `migrations on an empty database` job failed with 25 collection errors that
named a missing CSV, and no test in it had ever run. A precondition that broad
is indistinguishable from a broken suite, and it hid a real gate behind a
misleading error.

Now the catalogue is a fixture a test asks for. A file that resolves product
codes declares::

    pytestmark = pytest.mark.usefixtures("pie_catalog")

and everything else — 1,153 tests covering the database, the commercial engine,
the signal detectors, trust and the HTTP surface — runs with no clone present.
When the clone *is* present (a developer's machine, or CI with
``PIE_PARSER_TOKEN`` set) all 1,182 run, the 29 resolution tests included,
exactly as before.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# Ensure the app uses the vendored clone + a test catalogue location. `setdefault`
# so an explicitly-exported PIE_PARSER_ROOT (a sibling checkout, CI's clone path)
# still wins.
#
# An *empty* value is discarded first. `setdefault` treats "" as set, and CI
# passes `PIE_PARSER_ROOT: ${{ steps.parser.outputs.root }}`, which is the empty
# string on the run where no clone happened — that would resolve the corpus to a
# bare relative path rather than falling back to the vendored location.
if not os.environ.get("PIE_PARSER_ROOT"):
    os.environ.pop("PIE_PARSER_ROOT", None)
os.environ.setdefault("PIE_PARSER_ROOT", str(REPO / "pie-parser"))
os.environ.setdefault("PIE_CATALOG", str(BACKEND / "data" / "products.jsonl"))


@pytest.fixture(scope="session", autouse=True)
def _data_dir():
    """Create ``backend/data/`` before anything opens the app's engine.

    A fresh clone has no ``backend/data/`` — the .db files are gitignored and
    nothing else in the directory is tracked — and SQLite will not create a
    missing directory: it fails with ``unable to open database file``.
    ``bootstrap.ensure_data_dir`` exists precisely for this and runs on a real
    startup, but ``TestClient`` never runs the app's lifespan, so the tests that
    touch ``app.db.engine`` directly (``test_db_concurrency``) never got it.
    They passed on every developer machine, where the directory already exists,
    and failed the first time the suite ran on a clean checkout.

    Autouse here, unlike the catalogue below, and the difference is the point: a
    `mkdir` has no external dependency and cannot fail in a way that says
    something misleading about the code under test. Making a *private repository
    checkout* a session-wide precondition is what took the whole suite down.
    """
    from app.bootstrap import ensure_data_dir

    ensure_data_dir()


@pytest.fixture(scope="session")
def pie_catalog():
    """Build the decoded catalogue once, or skip the tests that need it.

    Skipping is deliberate and loud. The alternative — resolving against an
    empty catalogue — would let a resolution test pass by finding nothing, which
    is worse than not running it.
    """
    from app.catalog import ensure_catalog
    from app.config import settings

    # The *clone* is the precondition, not the catalogue. A prebuilt
    # products.jsonl is not enough: resolution imports pie-parser's engine and
    # `identity` package from PIE_PARSER_ROOT at call time, so with a catalogue
    # and no clone these tests fail with `ModuleNotFoundError: No module named
    # 'identity'` — which is the confusing failure this fixture exists to
    # replace with a sentence.
    if not settings.PIE_PARSER_ROOT.is_dir():
        pytest.skip(
            f"pie-parser not found at {settings.PIE_PARSER_ROOT} — product "
            "resolution tests need the pinned clone. Run "
            "./scripts/setup_pie_parser.sh, or set PIE_PARSER_ROOT."
        )
    if not settings.PIE_CATALOG.exists() and not settings.PIE_CORPUS.exists():
        pytest.skip(
            f"pie-parser is present but its corpus is not, at "
            f"{settings.PIE_CORPUS}. The clone looks incomplete — re-run "
            "./scripts/setup_pie_parser.sh."
        )
    ensure_catalog()


@pytest.fixture(scope="session")
def platform_db():
    """Bring the configured database to head and seed the demo org + users.

    ``TestClient(app)`` built at module scope never runs the app's ``lifespan``,
    so the ``AUTO_BOOTSTRAP`` step that prepares the database on a real startup
    does not happen under test. Anything reaching the platform identity
    endpoints (``/api/v1/auth/login``) therefore hit an unmigrated database and
    failed with ``no such table: users`` — the exact symptom ``bootstrap.py``
    was written to make impossible, reappearing because the test client takes a
    different path into the app than uvicorn does.

    ``bootstrap()`` is idempotent and is the only sanctioned way to build this
    schema (§4: Alembic only, never ``create_all``), so calling it here costs a
    second on an already-migrated database and builds a working one from nothing
    in CI.
    """
    from app.bootstrap import bootstrap

    bootstrap()
