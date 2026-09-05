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

Both of those layers ask "does this package obey the rule". Neither asks
"is this package covered by the rule", and for a long time the answer for
eight of them was no — ``DETERMINISTIC`` named six packages and the rest of
``app/`` was unconstrained, so a new package importing ``ai/`` would have gone
green (Decision 020). The membership tests are the third layer: every package
under ``app/`` is deterministic or is exempt with a written reason, and a new
one fails until somebody chooses. That is the same principle as the two above
— an unchecked thing reads exactly like a clean one — applied to the list
itself rather than to the code it screens.
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
#:
#: ``enquiry/`` persists no number at all, and is here for the other half
#: of the rule: it holds ``raw_text``, a customer's own words about their
#: project, their volumes and their urgency. A model that could import it
#: could be handed one tenant's commercial intelligence wholesale, which is
#: a worse outcome than a number nobody can reproduce.
#:
#: The other seven arrived together, because naming six packages made the
#: invariant *opt-in*: everything not listed was unconstrained, so a new
#: package importing ``ai/`` passed the gate in silence — the failure mode
#: this codebase has documented twice (CLAUDE.md §6: a check that does not run
#: reads exactly like a check that passes). Decision 020 asks that a new
#: deterministic package be added here in the commit that creates it; that
#: only helps once the list is complete, and it was not. Each of the seven was
#: walked against the real import graph before being added — direct imports
#: and the transitive closure below — rather than assumed clean from its name:
#:
#: ``context/``       assembles the ``ContextBundle`` that is the only thing a
#:                    model ever sees, and drops RESTRICTED facts on the way.
#:                    ``ai/`` imports it, which is legal; the reverse edge
#:                    would put the redactor downstream of what it redacts
#:                    for, and is the exact hole this module's own docstring
#:                    describes.
#: ``domain/``        models and enums, imported by ``ai/`` (``ai.byok``). The
#:                    reverse would make the schema depend on the interpreter.
#: ``identity/``      links records and never merges them. §1's confirmation
#:                    gate exists because an asserted identity becomes an exact
#:                    reference the equivalence engine derives a requirement
#:                    from — so a model reachable from here is how two
#:                    tolerance bands get composed into a wrong part.
#: ``master_health/`` measures one ERP's item-master export offline; its own
#:                    docstring lists the refusals that keep it offline.
#: ``messaging/``     the queue broker and its worker, which "knows nothing
#:                    about syncs, or Zoho, or what any payload means". A queue
#:                    that could interpret is a queue that decides what to run.
#: ``observability/`` metrics, health and capacity. Numbers about the platform
#:                    are still numbers, and they are still arithmetic.
#: ``trust/``         keys, vault, pseudonyms, disclosure, erasure.
#:                    ``disclosure`` records exactly what reaches a model and
#:                    ``pseudonym`` is what keeps a real customer name from
#:                    being it. CLAUDE.md §3 already asserts this package
#:                    "imports neither commercial/ nor ai/" — it was prose, and
#:                    nothing checked it.
#: ``retrieval/``     nearest-neighbour search over a decoded catalogue, the
#:                    candidate generator beneath the rule engine's ranking.
#:                    Its embedding is a hashed n-gram model and not a hosted
#:                    one *because* of this list: ``catalog`` and
#:                    ``pie_service`` import it, and every deterministic
#:                    package imports those, so a retrieval layer that reached
#:                    ``ai/`` would carry the whole of ``commercial/`` with it.
#:                    A neural embedder belongs behind the same protocol,
#:                    injected from a layer that may reach interpretation.
#: ``monetization/`` PIE's own pricing model — what the platform charges,
#:                    priced from rows and versioned. The only package here
#:                    that is not about a tenant. A model that could reach it
#:                    could be asked to justify a price, and a justification
#:                    produced by a model is not an audit trail.
#: ``sso/``          ID-token verification: the security boundary past which
#:                    the caller *is* whoever the token says. It reaches
#:                    ``clock`` and ``ingestion.url_safety`` and nothing else
#:                    in this codebase. Deciding who is signed in from anything
#:                    a model produced is the whole of what this package must
#:                    never do.
DETERMINISTIC = ("attribution", "commercial", "context", "decoding", "domain",
                 "enquiry", "identity", "ingestion", "master_health",
                 "messaging", "monetization", "observability", "retrieval",
                 "signals", "sso", "state", "trust")

#: The packages deliberately *outside* ``DETERMINISTIC``, each with the reason
#: it is out. A package silently omitted from an opt-in list is the same defect
#: as the list not existing, so the omissions are written down and
#: ``test_every_package_under_app_is_classified`` makes a new package a
#: decision somebody has to record rather than a default nobody notices.
_MAY_REACH_INTERPRETATION = {
    "ai": "the interpretation layer itself.",
    "decisions": (
        "the seam. §1: the single place a deterministic fact meets an "
        "interpretation of it, so it imports ai/ by design — preflight.py, "
        "quote_support.py and service.py all do. Adding it here would delete "
        "the seam, and the closure tests below depend on it existing."),
    "routers": (
        "checked rather than assumed, and it does genuinely reach "
        "interpretation: ai_settings.py is the BYOK provider-configuration "
        "endpoint, quote.py imports ai.reading and select_provider, and "
        "internal.py reports ai.metrics. Constraining routers/ would mean "
        "relocating the provider admin surface, which is a product decision "
        "and not this file's to make. What holds the line instead is that "
        "every layer a router computes through is constrained above, plus "
        "CLAUDE.md §3 — a router does HTTP mapping and role scoping and no "
        "money arithmetic. This is the weakest point in the invariant and it "
        "is named here so it is argued with rather than discovered."),
}

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
    for package in (*DETERMINISTIC, *COMPUTING, "ai", "decisions",
                    *_MAY_REACH_INTERPRETATION):
        assert (_APP / package).is_dir(), package


def _packages() -> set[str]:
    """Every package directory under ``app/``.

    Any directory holding a ``.py`` file *anywhere beneath it*, not only one
    holding an ``__init__.py``: a namespace package imports perfectly well
    without one, and ``_modules()`` — which is what the rules above actually
    walk — globs the tree rather than reading ``__init__``. Keying this on
    ``__init__.py`` would have let a new package skip the census and still be
    unscreened, which is the hole being closed, one layer down.

    ``rglob`` rather than ``glob``, and that is not a detail. The first
    version of this used a non-recursive ``glob("*.py")``, which leaves
    precisely the hole the paragraph above says it closes: a package whose
    modules all sit in subdirectories — the shape ``ingestion/erp/`` already
    has — holds no top-level ``.py``, so it would drop out of the census
    entirely and be neither classified nor screened. A check that argues for
    its own thoroughness in a docstring and then does the shallow thing is
    worse than one that never claimed it. ``__pycache__`` holds ``.pyc`` and
    drops out on the same test either way.
    """
    return {d.name for d in _APP.iterdir()
            if d.is_dir() and any(d.rglob("*.py"))}


def test_every_package_under_app_is_classified():
    """The rule was opt-in; this is the part that closes it.

    Extending ``DETERMINISTIC`` fixes the packages that exist today and
    nothing about the next one. Decision 020 asks each new deterministic
    package to be added in the commit that creates it — a convention, and
    conventions are kept by whoever read the document. So membership is now
    exhaustive instead: a package is deterministic, or it is in
    ``_MAY_REACH_INTERPRETATION`` with a written reason, and a new directory
    under ``app/`` fails here until somebody says which. The failure is loud
    and it is at the moment of creation, which is the only moment the answer
    is obvious.

    Both directions matter. An unclassified package is the silent hole; a name
    in either list that no longer exists is the screen with nothing in it, and
    reports clean for the same reason.
    """
    classified = set(DETERMINISTIC) | set(_MAY_REACH_INTERPRETATION)
    packages = _packages()

    assert packages, "no packages found under app/ — this check screens nothing"
    assert packages - classified == set(), (
        f"unclassified package(s) under app/: {sorted(packages - classified)}. "
        "Add each to DETERMINISTIC if it computes or persists facts — which is "
        "the default — or to _MAY_REACH_INTERPRETATION with the reason it may "
        "reach ai/. Do not leave it out: an unlisted package is not checked, "
        "and an unchecked package reads exactly like a clean one.")
    assert classified - packages == set(), (
        f"listed but not a package: {sorted(classified - packages)}. A name "
        "here that names nothing screens nothing.")

    # The exemption reasons are the point of the mapping, not decoration: the
    # thing being prevented is a package quietly leaving the invariant, and a
    # blank reason is exactly that with a key in front of it.
    unexplained = sorted(k for k, why in _MAY_REACH_INTERPRETATION.items()
                         if not (why or "").strip())
    assert unexplained == [], (
        f"exempt with no reason given: {unexplained}. Say why the package may "
        "reach ai/, so the next reader can disagree with it.")


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


def test_no_top_level_module_reaches_ai_outside_the_sanctioned_consumers():
    """The other half of Decision 020: ``pie_service.py``, ``store.py`` and
    ``resolution.py`` are not packages, so ``DETERMINISTIC`` cannot hold them
    and the census above cannot see them.

    ``routers/`` is excluded alongside the seam here, not waved through: it is
    a sanctioned consumer of ``ai/`` (see ``_MAY_REACH_INTERPRETATION``), and
    ``main.py`` mounts every router, so every route from ``main`` runs through
    it. Excluding both leaves the question this test is actually asking — does
    a loose module at the top of ``app/`` reach the model by some other path —
    and today the answer is none at all, including ``store.py`` and
    ``resolution.py``, which sit directly on the identity and equivalence
    surfaces §1 cares most about.
    """
    graph = _import_graph(exclude_top=(_SEAM, "routers"))
    modules = sorted(p.stem for p in _APP.glob("*.py") if p.stem != "__init__")

    assert modules, "no top-level modules found under app/"
    offenders = {m: _route(graph, m, "ai") for m in modules}
    offenders = {m: chain for m, chain in offenders.items() if chain}
    assert offenders == {}, (
        "a top-level module reaches ai/ without passing through decisions/ or "
        "routers/. It is not covered by DETERMINISTIC — a module is not a "
        "package — so this is the only thing standing in front of it:\n         "
        + "\n\n         ".join(_rendered(c) for c in offenders.values()))


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
