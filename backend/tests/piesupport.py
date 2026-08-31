"""One decoded catalogue, lent to whichever company a test needs it for.

Catalogues are per company now: ``pie_service`` resolves against
``data/catalogues/<connection_id>/products.jsonl`` and against nothing else, so
a test that wants a real resolution has to give its company a real catalogue.
Decoding the shipped corpus takes ~1.7s, and the suite creates a fresh company
per test — several hundred builds — so this builds it **once per worker** and
links that file into each company's directory.

Linking rather than faking: ``run_parse`` is deterministic in its inputs, so
the file every company here gets is byte-for-byte the file its own build would
have produced from the same corpus and pack. What is shared is the cost, not
the answer.

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

_built: Optional[Path] = None


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


def give_company_a_catalogue(connection_id: str) -> Path:
    """Put this worker's decoded catalogue where company ``connection_id`` reads.

    Hard-linked where the filesystem allows it (13 MB a company otherwise), and
    copied where it does not. Drops whatever ``pie_service`` has memoised for
    that company, including a memoised *absence* — a company asked about before
    its catalogue existed is remembered as having none.
    """
    from app import catalog
    from app.pie_service import pie_service

    source = shared_catalogue()
    target = catalog.company_catalog_path(connection_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.samefile(source):
        pie_service.reload(connection_id)
        return target
    # Renamed into place rather than unlinked and re-created: a reader that
    # opens the path between those two steps gets no file at all, which is a
    # company that has no catalogue for one unlucky test.
    staged = target.with_suffix(f".{os.getpid()}.tmp")
    try:
        os.link(source, staged)
    except OSError:
        shutil.copyfile(source, staged)
    os.replace(staged, target)
    pie_service.reload(connection_id)
    return target


def forget_company_catalogue(connection_id: str) -> None:
    """Take a company's catalogue away again, memo included."""
    from app import catalog
    from app.pie_service import pie_service

    catalog.company_catalog_path(connection_id).unlink(missing_ok=True)
    pie_service.reload(connection_id)
