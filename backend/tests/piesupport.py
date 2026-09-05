"""One decoded catalogue, lent to whichever company a test needs it for.

Catalogues are per company now: ``pie_service`` resolves against the union of
``data/catalogues/<connection_id>/<catalogue_key>/products.jsonl`` and against
nothing else, so a test that wants a real resolution has to give its company a
real catalogue. Decoding the shipped corpus takes ~1.7s, and the suite creates
a fresh company per test — several hundred builds — so this builds it **once
per worker** and links that file into each company's ``default`` catalogue.

Linking rather than faking: ``run_parse`` is deterministic in its inputs, so
the file every company here gets is byte-for-byte the file its own build would
have produced from the same corpus and pack. What is shared is the cost, not
the answer. The union a company resolves against is then assembled from that
file exactly as it would be from a real build, and — being deterministic in
its one member — its retrieval index is built once per worker too and linked
beside each company's union.

The corresponding *product* behaviour — a company inheriting the shipped corpus
— is ``catalog.seed_company_catalogues``, and it is a different thing: it
writes a corpus row the company then owns. Tests that exercise the seed call
that; tests that merely need resolution to work call :func:`give_company_a_catalogue`.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

#: Whether the engine is actually present. The orchestration entry point is the
#: thing ``pie_service`` loads, so its absence is exactly what "no engine"
#: means — a stale directory left by an interrupted fetch is not an engine.
#:
#: Defined HERE rather than in ``tests/conftest.py``, which imports it from
#: here. Both ``tests/`` and ``tests/incentive_engine/`` hold a conftest.py and
#: neither is a package, so pytest puts both directories on ``sys.path`` and
#: the top-level module name ``conftest`` resolves to whichever went in first.
#: ``from conftest import PIE_AVAILABLE`` therefore read
#: tests/incentive_engine/conftest.py under a whole-suite ``pytest tests`` run
#: and raised ImportError while collecting — for every test in the suite, which
#: is the always-red check CLAUDE.md §6 has the incident about. ``piesupport``
#: is one file with one name, so importing from it cannot land somewhere else.
#:
#: Making tests/incentive_engine/ a package instead would not do:
#: ``incentive_engine`` is pie-parser's own top-level module — ``commercial/
#: floor.py`` imports ``incentive_engine.floor`` — and a test package of that
#: name would shadow the real one.
PIE_AVAILABLE = (Path(os.environ["PIE_PARSER_ROOT"]) / "tools" / "resolve_rfq.py").exists()

_SKIP_REASON = (
    "pie-parser is not checked out, so there is no engine to resolve against. "
    "Fetch it with ./scripts/setup_pie_parser.sh, or set PIE_PARSER_ROOT."
)

_built: Optional[Path] = None
_union_index: Optional[Path] = None


def worker() -> str:
    """This xdist worker's name, or "solo" for a serial run."""
    return os.environ.get("PYTEST_XDIST_WORKER", "solo")


def company_id(name: str) -> str:
    """A company id no other worker will touch.

    A catalogue is a *file*, and the filesystem is the one thing xdist workers
    share: with ``--dist load`` two workers run two tests of the same module at
    the same time, so a fixed id means one worker unlinking the path another is
    about to read. That failure is intermittent, lands on whichever test was
    unlucky, and looks nothing like its cause — which is the worst kind of test
    to leave in a suite. The database is already per worker, so the suffix
    costs nothing there.
    """
    return f"{name}-{worker()}"


def shared_catalogue() -> Path:
    """The shipped corpus, decoded once for this worker."""
    global _built
    if _built is not None and _built.exists():
        return _built

    _require_engine()

    from app import catalog
    from app.config import settings

    # Per worker: `run_parse` writes `<out>.tmp` and renames it into place, so
    # two xdist workers building the same path would share one temporary file.
    out = (settings.PIE_CATALOG.parent / "test-catalogues" / worker()
           / "products.jsonl")
    if not out.exists():
        catalog.run_parse(settings.PIE_CORPUS, settings.PIE_PACK, out)
    _built = out
    return out


def _require_engine() -> None:
    """Refuse to decode without the engine, and name the missing marker.

    Reaching here without pie-parser means an unmarked test asked for a real
    catalogue: `conftest.pytest_collection_modifyitems` skips the tests marked
    ``requires_pie`` when the engine is absent, so a test that runs this far is
    one nobody marked. That used to surface as ``ModuleNotFoundError: No module
    named 'engine'`` raised from inside ``app/catalog.run_parse`` during fixture
    setup — a traceback about an import three layers down, naming neither the
    test's real requirement nor the one-line fix. Thirty-one tests across ten
    files were added that way in two commits before anyone read it as "you
    forgot the marker".

    Deliberately an error and not ``pytest.skip``: skipping here would make the
    test pass quietly on a checkout with no engine *and* leave it unselected by
    ``pytest -m requires_pie``, so the `pie-contract` job would not run it
    either. That is a test covering nothing, anywhere — the always-green check
    CLAUDE.md §6 has the incident about. The marker is the only mechanism that
    both skips it here and runs it there, so this insists on the marker rather
    than standing in for it.
    """
    if PIE_AVAILABLE:
        return
    raise RuntimeError(
        f"This test needs a decoded catalogue, but {_SKIP_REASON}\n"
        "Mark it `@pytest.mark.requires_pie` (or set a module-level "
        "`pytestmark`, where the fixture that needs the engine is autouse) so "
        "it skips here and still runs in the `pie-contract` job."
    )


def give_company_a_catalogue(connection_id: str) -> Path:
    """Put this worker's decoded catalogue where company ``connection_id`` reads.

    Hard-linked where the filesystem allows it (13 MB a company otherwise), and
    copied where it does not. Drops whatever ``pie_service`` has memoised for
    that company, including a memoised *absence* — a company asked about before
    its catalogue existed is remembered as having none.
    """
    from app import catalog, retrieval
    from app.pie_service import pie_service

    source = shared_catalogue()
    target = catalog.company_catalog_path(connection_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target.exists() and target.samefile(source)):
        # Renamed into place rather than unlinked and re-created: a reader that
        # opens the path between those two steps gets no file at all, which is
        # a company that has no catalogue for one unlucky test.
        _link(source, target)
        # What a build writes beside its file, so the union can be assembled
        # from the disk alone the way it is for a catalogue a person built.
        catalog.write_sidecar(connection_id, catalog.DEFAULT_CATALOGUE,
                              _row_for(source))
    union = catalog.union_catalogue(connection_id)
    assert union is not None
    # The retrieval index too, built once per worker over this one-member
    # union: it is derived from the file, and the company would otherwise
    # rebuild the same 11 MB on its first requirement line, once per company
    # per worker. Identical members make identical union bytes, which is what
    # lets one index describe every company's union.
    index = retrieval.index_path_for(union.path)
    if not index.exists():
        shared = _shared_union_index(union.path)
        if shared is not None:
            _link(shared, index)
    pie_service.reload(connection_id)
    return target


class _row_for:
    """What ``write_sidecar`` reads off a built catalogue's row, for a
    catalogue that was linked into place rather than built.

    The stamp and the run id are read from the linked file's own records, so
    the union assembled from it carries the same version a real build's would
    — which is the point of linking rather than faking: the file is
    byte-for-byte what this company's own build would have produced.
    """

    def __init__(self, decoded: Path) -> None:
        from app import catalog
        from app.config import settings

        stamp = catalog.catalog_stamp(decoded)
        self.name = ""
        self.built_at = None
        self.records = 0
        self.sources = [{"source_key": settings.PIE_CORPUS.name,
                         "rule_set": settings.PIE_PACK.name, "stamp": stamp}]
        for field in catalog.STAMP_FIELDS:
            setattr(self, field, stamp.get(field))


def _shared_union_index(union_path: Path) -> Optional[Path]:
    global _union_index
    from app import retrieval

    if _union_index is None or not _union_index.exists():
        staging = union_path.parent.parent.parent / "test-catalogues" / worker()
        staging = staging / "union" / "products.jsonl"
        staging.parent.mkdir(parents=True, exist_ok=True)
        if not staging.exists():
            _link(union_path, staging)
        retrieval.ensure_index(staging)
        _union_index = retrieval.index_path_for(staging)
    return _union_index


def _link(source: Path, target: Path) -> None:
    staged = target.with_suffix(f".{os.getpid()}.tmp")
    try:
        os.link(source, staged)
    except OSError:
        shutil.copyfile(source, staged)
    os.replace(staged, target)


def forget_company_catalogue(connection_id: str) -> None:
    """Take a company's catalogues away again, memo included."""
    from app import catalog
    from app.pie_service import pie_service

    shutil.rmtree(catalog.catalogue_dir(connection_id).parent, ignore_errors=True)
    pie_service.reload(connection_id)
