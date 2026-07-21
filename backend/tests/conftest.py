"""Test fixtures: point the app at the pinned pie-parser submodule and build a
small catalogue once for the whole session.
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


@pytest.fixture(scope="session", autouse=True)
def _catalog():
    """Build the catalogue once (idempotent) so resolution tests have data."""
    from app.catalog import ensure_catalog
    ensure_catalog()
