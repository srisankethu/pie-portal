"""Every refusal says what *kind* of refusal it is.

The point of ``insight/absence.py`` is that a reader can tell a limit from a
task. That only holds if it holds for *every* entry — one untyped refusal in a
list of five renders as an unclassified box and the reader goes back to treating
them all the same.

So there are two tests, and the second is the one that will actually fire.

The first calls every producer and checks the kinds are real. The second parses
the source and finds refusal-shaped dict literals that carry no ``kind`` at all,
including ones written months from now in a module that does not exist yet. It
is AST-based rather than a grep for the same reason
``test_layer_boundaries`` is: a key named in a docstring or a comment must not
be able to pass or fail it.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.commercial.insight import (absence, adoption, bonds, dependency,
                                    mix, outcomes, schemes)
from app.commercial.insight import simulate as sim
from app.commercial.insight import stock as _stock
from app.commercial.insight import supply as _supply

#: The key pairs that identify a refusal, rather than an ordinary dict that
#: happens to have one of these keys. ``{"series": [...]}`` is a chart series
#: and appears several times; ``{"series": ..., "reason": ...}`` is only ever a
#: refusal. Requiring both halves is what keeps this test quiet.
REFUSAL_SHAPES = (("series", "reason"), ("what", "why"), ("scenario", "why"))

SOURCES = ("backend/app/commercial/insight", "backend/app/routers/insight.py")


def _repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "backend" / "app").is_dir():
            return parent
    raise AssertionError("could not locate the repository root from the test file")


def _python_files() -> list[pathlib.Path]:
    root = _repo_root()
    out: list[pathlib.Path] = []
    for entry in SOURCES:
        path = root / entry
        out.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return out


def _literal_keys(node: ast.Dict) -> set[str]:
    return {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


# ── the producers, called for real ──────────────────────────────────────────
def _every_produced_entry() -> list[tuple[str, dict]]:
    """One flat list of (where it came from, the entry) across the package."""
    produced: list[tuple[str, dict]] = []

    def add(label: str, entries) -> None:
        produced.extend((label, e) for e in entries)

    # Both branches of stock's, including the permission-withheld column.
    add("stock", _stock._unavailable())
    add("stock(no reorder policy)", _stock._unavailable(3, 10))
    add("stock(drain withheld)", _stock._unavailable(drain_withheld=True))
    add("stock(too new to band)", _stock._unavailable(too_new=4, dead_days=365))
    # Both branches of supply's: none promised, and some promised.
    add("supply(none promised)", _supply._unavailable(0, 5))
    add("supply(some promised)", _supply._unavailable(2, 5))
    add("dependency", dependency.unavailable())
    add("mix", mix.unavailable())
    add("adoption", adoption.unavailable())
    add("bonds(customer)", bonds.unavailable(bonds.CUSTOMER, has_reliability=False))
    add("bonds(vendor)", bonds.unavailable(bonds.VENDOR, has_reliability=False))
    add("bonds(vendor, reliable)", bonds.unavailable(bonds.VENDOR,
                                                     has_reliability=True))
    add("outcomes(win rate against a competitor)",
        [outcomes.win_rate_against_unavailable()])
    add("simulate", list(sim.UNAVAILABLE))
    add("schemes", list(schemes.REFUSALS.values()))
    return produced


def test_every_refusal_carries_a_kind_from_the_closed_set():
    entries = _every_produced_entry()
    # A guard on the guard: if a refactor renames the producers, this test must
    # fail loudly rather than pass over an empty list.
    assert len(entries) >= 12, f"only found {len(entries)} refusals — did a producer move?"

    for where, entry in entries:
        assert "kind" in entry, f"{where}: refusal carries no kind — {entry}"
        assert entry["kind"] in absence.KINDS, \
            f"{where}: {entry['kind']!r} is not one of {sorted(absence.KINDS)}"


def test_the_distinction_that_pays_for_the_module_is_actually_drawn():
    """At least one PERMANENT and one COLLECTABLE, or the field says nothing.

    A taxonomy where everything lands in one bucket is a constant with extra
    steps. This is the test that would fail if a later change classified the
    lot as PERMANENT to make the worklist go away.
    """
    kinds = {entry["kind"] for _, entry in _every_produced_entry()}
    assert absence.PERMANENT in kinds
    assert absence.COLLECTABLE in kinds


# ── and the one that catches an entry written later ─────────────────────────
@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_refusal_shaped_literal_is_left_untyped(path: pathlib.Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = _literal_keys(node)
        if any(a in keys and b in keys for a, b in REFUSAL_SHAPES):
            if "kind" not in keys:
                offenders.append(node.lineno)
    assert not offenders, (
        f"{path}: refusal literal(s) at line(s) {offenders} carry no 'kind'. "
        f"Pick one of {sorted(absence.KINDS)} — see commercial/insight/absence.py.")
