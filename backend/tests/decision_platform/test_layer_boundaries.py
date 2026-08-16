"""The §1 invariant, as a test rather than a grep somebody remembers to run.

**AI never computes a number.** Mechanically that is an import rule: the
deterministic layers must not reach into ``ai/``, and ``ai/`` must not reach
into ``commercial/``. ``decisions/`` is the one seam where a deterministic fact
meets an interpretation of it.

CLAUDE.md states this as two ripgreps. A grep is only run by someone who
remembers to; this fails the build.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Packages that compute or persist facts. None of them may import ``ai/`` —
#: a number that an interpretation layer could reach is a number nobody can
#: reproduce.
DETERMINISTIC = ("attribution", "commercial", "signals", "ingestion", "state")

#: Packages ``ai/`` must not import, which is the same rule read from the other
#: side. ``attribution`` is here as well as in ``DETERMINISTIC`` because it is
#: the ledger a renewal is argued from: a model that could reach it could compute
#: what the platform claims to be worth, which is the one number on this surface
#: that must be arithmetic over rows and nothing else.
COMPUTING = ("commercial", "attribution")


def _imported_top_levels(path: pathlib.Path) -> set[str]:
    """The first component of every module this file imports, relative imports
    resolved against ``app/``.

    Parsed rather than grepped, so a package named in a docstring or a comment
    cannot fail the build — the historical false-positive with this kind of
    check.
    """
    tree = ast.parse(path.read_text())
    package = path.relative_to(_APP).parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add((node.module or "").split(".")[0])
                continue
            # `from ..state import events` inside app/ingestion/sync.py:
            # level 2 climbs to app/, and the module's first part is the target.
            base = list(package[: len(package) - (node.level - 1)])
            parts = base + (node.module or "").split(".")
            found.add(next((p for p in parts if p), ""))
    return found


def _modules(package: str) -> list[pathlib.Path]:
    return sorted((_APP / package).rglob("*.py"))


@pytest.mark.parametrize("package", DETERMINISTIC)
def test_a_deterministic_layer_never_imports_the_interpreted_one(package):
    offenders = [f"{p.relative_to(_APP)}" for p in _modules(package)
                 if "ai" in _imported_top_levels(p)]
    assert offenders == [], (
        f"{package}/ imports ai/. Prices, margins and priorities are computed "
        "here and only read there; move the interpretation into decisions/.")


@pytest.mark.parametrize("package", COMPUTING)
def test_the_interpreted_layer_never_imports_the_computing_one(package):
    offenders = [f"{p.relative_to(_APP)}" for p in _modules("ai")
                 if package in _imported_top_levels(p)]
    assert offenders == [], (
        f"ai/ imports {package}/. It receives facts; it must never be able to "
        "compute one.")


def test_the_deterministic_packages_listed_here_all_exist():
    """A typo in the list above would silently check nothing."""
    for package in (*DETERMINISTIC, *COMPUTING, "ai", "decisions"):
        assert (_APP / package).is_dir(), package
