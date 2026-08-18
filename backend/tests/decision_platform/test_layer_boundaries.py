"""The §1 invariant, as a test rather than a grep somebody remembers to run.

**AI never computes a number.** Mechanically that is an import rule: the
deterministic layers must not reach into ``ai/``, and ``ai/`` must not reach
into ``commercial/``. ``decisions/`` is the one seam where a deterministic fact
meets an interpretation of it.

CLAUDE.md states this as two ripgreps. A grep is only run by someone who
remembers to; this fails the build.

Two layers of test, on purpose. The direct-import tests fail with the exact
file that broke the rule, so they stay. Behind them sits a transitive check,
because a direct rule has a hole a direct test cannot see: ``ai/`` legally
imports ``context/``, so one ``commercial`` import added to a bundle module
would hand the model the computing layer while every direct assertion stayed
green. The closure tests walk the real import graph and fail with the whole
chain.
"""
from __future__ import annotations

import ast
import functools
import pathlib
from collections import deque

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


def _imported_module_candidates(path: pathlib.Path) -> set[tuple[str, ...]]:
    """Every module an import in this file could load, as dotted-path parts.

    Parsed rather than grepped, so a package named in a docstring or a comment
    cannot fail the build — the historical false-positive with this kind of
    check. Relative imports are resolved against ``app/``; an absolute
    ``app.x`` is read as ``x``. For ``from X import name`` both ``X`` and
    ``X.name`` are offered, because ``name`` may be a submodule
    (``from ..domain import models``) or an attribute
    (``from ..config import settings``), and only the file tree can say which
    — offering both is also what lets ``from .. import ai`` be seen at all,
    which the module-path-only reading missed.
    """
    tree = ast.parse(path.read_text())
    package = path.relative_to(_APP).parts[:-1]
    found: set[tuple[str, ...]] = set()

    def _in_app(parts: list[str]) -> tuple[str, ...]:
        return tuple(parts[1:] if parts[:1] == ["app"] else parts)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(_in_app(alias.name.split(".")))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = _in_app([p for p in (node.module or "").split(".") if p])
            else:
                # `from ..state import events` inside app/ingestion/sync.py:
                # level 2 climbs to app/, and what follows is the target.
                base = tuple(package[: len(package) - (node.level - 1)])
                base += tuple(p for p in (node.module or "").split(".") if p)
            found.add(base)
            for alias in node.names:
                found.add(base + (alias.name,))
    found.discard(())
    return found


def _imported_top_levels(path: pathlib.Path) -> set[str]:
    """The first component of every module this file imports."""
    return {parts[0] for parts in _imported_module_candidates(path)}


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


# ── the transitive closure behind the direct rules ──────────────────────────
#
# The tests above catch the file that imports a forbidden package by name.
# They cannot catch a route: ``ai/`` importing ``context/`` is legal,
# ``context/`` importing ``signals/`` is legal, and each new hop is judged on
# its own — so one import added to a module ``ai/`` already loads would open
# a path to ``commercial/`` with every direct test still green. The tests
# below walk the module-level import graph (what an ``import`` statement
# actually executes, nested function-level imports included) and assert the
# forbidden packages stay unreachable, printing the whole chain on failure.

#: The one sanctioned crossing. §1: ``decisions/`` is the single seam where a
#: deterministic fact meets an interpretation of it — ``commercial/`` and
#: ``ingestion/`` deliberately call it (``quote_service.resolve_customer`` is
#: CLAUDE.md's own reuse example), and it imports ``ai/``. A route from a
#: deterministic layer to ``ai/`` *through* the seam is the design; the
#: closure asserts there is no route around it. The exemption is one-way:
#: ``ai/`` gets no such pass, because ``ai/`` importing the seam would hand
#: the model the computing layers the seam exists to keep from it.
_SEAM = "decisions"


def _module_files() -> dict[tuple[str, ...], pathlib.Path]:
    """Dotted-path parts -> file, for every module under ``app/``."""
    files: dict[tuple[str, ...], pathlib.Path] = {}
    for path in _APP.rglob("*.py"):
        parts = path.relative_to(_APP).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            files[parts] = path
    return files


@functools.lru_cache(maxsize=None)
def _import_graph(exclude_top: tuple[str, ...] = ()) -> dict[str, frozenset[str]]:
    """module -> the app modules importing it loads.

    A candidate resolves to its longest prefix that names a real module
    (``context.bundle.ContextBundle`` -> ``context.bundle``), plus every
    package ``__init__`` on the way, because Python executes those too.
    Imports outside ``app/`` resolve to nothing and drop out.
    """
    files = _module_files()
    graph: dict[str, frozenset[str]] = {}
    for parts, path in files.items():
        if parts[0] in exclude_top:
            continue
        targets: set[tuple[str, ...]] = set()
        for candidate in _imported_module_candidates(path):
            for depth in range(len(candidate), 0, -1):
                if candidate[:depth] in files:
                    targets.update(candidate[:d] for d in range(1, depth + 1)
                                   if candidate[:d] in files)
                    break
        graph[".".join(parts)] = frozenset(
            ".".join(t) for t in targets
            if t != parts and t[0] not in exclude_top)
    return graph


def _route(graph: dict[str, frozenset[str]], source: str, target: str) -> list[str] | None:
    """The first import chain from a module of ``source`` to one of ``target``,
    or None. Breadth-first with sorted neighbours, so the answer — and any
    failure message — is deterministic."""
    parents: dict[str, str | None] = {
        m: None for m in sorted(graph) if m.split(".")[0] == source}
    queue = deque(parents)
    while queue:
        module = queue.popleft()
        if module.split(".")[0] == target:
            chain = [module]
            while parents[chain[-1]] is not None:
                chain.append(parents[chain[-1]])
            return chain[::-1]
        for imported in sorted(graph.get(module, ())):
            if imported not in parents:
                parents[imported] = module
                queue.append(imported)
    return None


def _rendered(chain: list[str]) -> str:
    files = _module_files()
    return "\n      -> ".join(
        f"{m}  ({files[tuple(m.split('.'))].relative_to(_APP)})" for m in chain)


@pytest.mark.parametrize("package", COMPUTING)
def test_the_interpreted_layer_cannot_reach_a_computing_one_even_transitively(package):
    chain = _route(_import_graph(), "ai", package)
    assert chain is None, (
        f"ai/ reaches {package}/ — each module below imports the next, so a "
        "model call loads the computing layer it must only be handed facts "
        f"from. Break any link in the chain:\n         {_rendered(chain)}")


@pytest.mark.parametrize("package", DETERMINISTIC)
def test_a_deterministic_layer_cannot_reach_ai_except_through_the_seam(package):
    chain = _route(_import_graph(exclude_top=(_SEAM,)), package, "ai")
    assert chain is None, (
        f"{package}/ reaches ai/ without passing through {_SEAM}/ — each "
        "module below imports the next. Interpretation is asked for at the "
        f"seam or not at all; break any link in the chain:\n         "
        f"{_rendered(chain)}")


def test_the_import_graph_actually_sees_imports():
    """A parser regression returning empty edge sets would turn the closure
    tests into a silent pass — the §1 failure mode this file exists to close.
    Every package under test imports *something* in ``app/`` (``domain/`` or
    ``config`` at the very least), so an empty picture is a broken parser,
    never a clean architecture."""
    graph = _import_graph()
    for package in (*DETERMINISTIC, "ai"):
        assert any(module.split(".")[0] == package and graph[module]
                   for module in graph), (
            f"no intra-app imports found anywhere under {package}/")
