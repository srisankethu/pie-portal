"""Locate, build and describe each company's decoded PIE product catalogue.

A catalogue is ``products.jsonl`` produced by pie-parser's ``run_parser`` over
one company's item-master export. It is large (~13 MB) and deterministic, so it
is gitignored and rebuilt from the corpus rather than committed.

**Catalogues belong to a connected company, and there is no deployment-wide
default.** A company keeps one catalogue *per manufacturer it sells* —
Kennametal's price lists decoded through Kennametal's pack, YG-1's through
YG-1's — each into ``data/catalogues/<connection_id>/<catalogue_key>/``, and it
resolves against the **union** of them, written beside those files under
``_union/``. A company that has uploaded nothing has no catalogue and resolves
nothing — which is the honest answer, and the one thing that could be worse is
answering from another company's.

The corpus that ships inside the pinned submodule (``settings.PIE_CORPUS``,
``settings.PIE_PACK``) is the **seed**, not a runtime fallback: on start-up
:func:`seed_company_catalogues` gives it to each organization's first company
where that company has no export of its own, so a deployment that has been
resolving against the old shared catalogue keeps resolving after the cutover.
Nothing reads it once a company has its own.

``docs/per-company-catalogues.md`` is the design and the sequencing.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import clock, retrieval
from .config import settings

log = logging.getLogger("pie_portal.catalog")

#: One build at a time. Two rebuild clicks must queue, not interleave — the
#: second waits ~2s and rebuilds again, which is harmless because the output is
#: deterministic.
_build_lock = threading.Lock()

#: The provenance every emitted record carries (see pie-parser's ``StampInfo``).
#: Uniform across a build — ``run_id`` derives from the input bytes plus the
#: ruleset checksum — so the first record speaks for all of them.
STAMP_FIELDS = (
    "pack_id", "pack_version", "org_id", "org_version",
    "ruleset_checksum", "run_id", "engine_version", "schema_version",
)


def catalog_stamp(path: Path) -> Dict[str, Any]:
    """The stamp on the catalogue's rows, read from the first record.

    Read rather than recomputed: the checksum belongs to the run that built the
    file, and deriving our own would be a second answer to a question the
    parser has already answered. Empty when the file is missing or unreadable.
    """
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    return {k: rec.get(k) for k in STAMP_FIELDS
                            if rec.get(k) is not None}
    except (OSError, ValueError):
        log.warning("could not read a stamp from %s", path)
    return {}


def source_state() -> Dict[str, Any]:
    """Whether the shipped seed corpus is here, and if not, exactly why.

    This is the corpus a *first* company inherits (see
    :func:`seed_company_catalogues`), not a catalogue anything resolves
    against. Its absence costs a deployment nothing once every company has
    uploaded its own export; it costs an existing deployment its seed.

    The two absences are different fixes and must not share one message: an
    uninitialised private submodule needs credentials and a fetch, a missing
    corpus inside a present engine needs PIE_CORPUS pointed at the right file.
    """
    reason = None
    if not (settings.PIE_PARSER_ROOT / "engine").is_dir():
        reason = (
            f"pie-parser is not present at {settings.PIE_PARSER_ROOT} — the "
            "private submodule is not initialised. Fetch it with "
            "./scripts/setup_pie_parser.sh (or set PIE_PARSER_ROOT)."
        )
    elif not settings.PIE_CORPUS.exists():
        reason = (
            f"PIE corpus not found at {settings.PIE_CORPUS}. The engine is "
            "present but this corpus file is not — check PIE_CORPUS."
        )
    return {
        "available": reason is None,
        "reason": reason,
        "pie_parser_root": str(settings.PIE_PARSER_ROOT),
        "corpus": str(settings.PIE_CORPUS),
        "pack": str(settings.PIE_PACK),
    }


def pack_columns(pack_path: Path) -> Dict[str, str]:
    """What this pack calls the three columns the pipeline reads.

    Which headers a corpus uses is the *pack's* to say — it is a fact about one
    organisation's export, and pie-parser's org layer declares it. Asked here
    rather than restated at each caller: these three literals were once
    duplicated between this file and pie-parser's ``tools/run_parser.py``, so a
    second distributor's corpus needed the same edit made twice, in two
    repositories, and one of them would eventually be missed. There are two
    callers again now — the parse, and the normalisation that writes the corpus
    the parse reads — and they must agree exactly or the parse finds no columns.

    ``getattr`` rather than a plain attribute read, and the same defaults: the
    two repositories version independently and ``PIE_PARSER_ROOT`` is a pinned
    checkout, so a portal that crashed on a slightly older engine would be a
    worse failure than one that falls back to the values that engine was using
    anyway.
    """
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine.pack import load_pack  # noqa: PLC0415

    return getattr(load_pack(pack_path), "columns", None) or {
        "record_id": "MM#", "description": "Material Description",
        "grade": "Grade"}


def run_parse(corpus: Path, pack_path: Path, out: Path) -> Dict[str, Any]:
    """Decode one corpus through one pack into one JSONL. The single parse.

    Every build reaches the pipeline through here, for the reason CLAUDE.md §2
    gives: a second invocation of the same pipeline is the semantic duplication
    that drifts, and the two would eventually disagree about the payload gate
    or the atomic write. It is deliberately company-blind — it takes paths, not
    a connection — so the one place that knows about companies is
    :func:`build_for_company`.

    Returns the counts, the parser's own report and the stamp taken off an
    emitted record. Never returns a parse rate it computed itself.
    """
    # Import pie-parser's parser pipeline in-process (no subprocess).
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine import configio  # noqa: E402
    from engine.pack import load_pack  # noqa: E402
    from engine.pipeline import (  # noqa: E402
        ColumnMapping,
        CsvAdapter,
        ParserPipeline,
        RunProfile,
    )

    log.info("Building PIE catalogue from %s ...", corpus)
    started = time.perf_counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    pack = load_pack(pack_path)
    # Which headers the corpus uses is the *pack's* to say, and `pack_columns`
    # is where that is asked — the same answer the normalisation that produced
    # this corpus was written against. Loading the pack twice costs milliseconds
    # against a parse measured in seconds; two copies of the fallback literals
    # would cost a corpus that normalises to headers the parse then cannot find.
    columns = pack_columns(pack_path)
    mapping = ColumnMapping(
        record_id=columns["record_id"], description=columns["description"],
        grade=columns["grade"]
    )
    records = CsvAdapter(corpus, mapping).read()
    # The default profile excludes the payload — the corpus's opaque
    # commercial columns — which is what keeps price out of nomenclature.
    profile = RunProfile()
    fingerprint = configio.checksum_bytes(corpus.read_bytes())
    pipeline = ParserPipeline(pack, profile)
    products, report, quarantine = pipeline.run(records, input_fingerprint=fingerprint)

    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in products:
            fh.write(configio.dump_stable_json(rec) + "\n")
    os.replace(tmp, out)

    # Quarantined rows are kept, not dropped — "unknown means unknown" is
    # pie-parser's contract and losing the rows here would break it.
    quarantine_out = out.with_suffix(".quarantine.jsonl")
    with quarantine_out.open("w", encoding="utf-8") as fh:
        for rec in quarantine:
            fh.write(configio.dump_stable_json(rec) + "\n")

    # The stamp, taken off an emitted record rather than re-derived. The build
    # time is carried beside the artifact, never in it — products.jsonl stays
    # byte-identical for identical input, which is what determinism means here.
    stamped = products[0] if products else (quarantine[0] if quarantine else {})
    log.info("Wrote %d products to %s (%d quarantined)",
             len(products), out, len(quarantine))
    return {
        "report": report.to_dict(),
        "stamp": {k: stamped.get(k) for k in STAMP_FIELDS
                  if stamped.get(k) is not None},
        "records": len(products),
        "quarantined": len(quarantine),
        "rows_read": report.total,
        "duration_s": round(time.perf_counter() - started, 2),
        "corpus_fingerprint": fingerprint,
    }


# ── per connected company ────────────────────────────────────────────────────
#
# Everything above this line is generic: a parse, a stamp, and whether the
# seed corpus is present. Everything below belongs to one connected company —
# and, within it, to one of that company's catalogues.
#
# **A company keeps a catalogue per manufacturer.** A distributor sells
# Kennametal and YG-1 and a dozen more, and each manufacturer's price lists
# are decoded through that manufacturer's own pack. So a catalogue is the unit
# of *building* — its own source files, its own pack, its own
# ``products.jsonl`` — and the **union** of a company's built catalogues is
# what that company *resolves against*. The union is derived from the members
# and rebuilt whenever one of them changes; nothing is stored for it beyond
# the file and a manifest saying which builds it was made from.


#: What an uploaded corpus may weigh. The shipped corpus is 6,717 rows and a
#: couple of megabytes; this is generous against that and still small enough
#: that the row, the parse and the request are all bounded. Rejected *before*
#: the bytes are read into memory, not after.
MAX_CORPUS_BYTES = 32 * 1024 * 1024

#: How many files one catalogue may keep as sources at once. A ceiling rather
#: than a limit anyone should reach: a build reads every one of them on every
#: rebuild, and a catalogue with fifty price lists has a data problem the
#: build cannot fix. Refused by name, so the answer says what to do
#: (replace a source, or remove one) instead of failing at build time.
MAX_SOURCES = 20

#: How many catalogues one company may keep. Each is a manufacturer whose
#: price lists this company decodes, and every one of them is read into the
#: union the company resolves against — so the ceiling is a memory bound on
#: that union, not a judgement about how many lines a distributor carries.
MAX_CATALOGUES = 10

#: The key of the one catalogue a company had before it could have several.
#: Every corpus row and catalogue row written before then carries it.
DEFAULT_CATALOGUE = "default"

#: The directory beside a company's catalogues that holds their union. Named
#: so it cannot collide with a catalogue key, which never starts with an
#: underscore (see :func:`catalogue_key_for`).
UNION_DIR = "_union"

#: The name a company's built catalogue carries beside its file, so the union
#: can be assembled from the disk alone — at start-up, before any session, and
#: in the resolver, which has none.
CATALOGUE_SIDECAR = "catalogue.json"

_KEY_STRIP = re.compile(r"[^a-z0-9]+")


class CatalogueError(ValueError):
    """A catalogue could not be created or removed, with the sentence saying why.

    ``status`` is the HTTP status the router answers with, decided here where
    the reason is known rather than re-derived from the message: a name that
    yields no key is the caller's input (422), a key already in use or a
    company at its ceiling is a conflict (409), a pack the engine does not ship
    is a bad choice (400), a catalogue that is not there is not there (404).
    """

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def catalogue_key_for(name: str) -> str:
    """The key a catalogue is created under, from the name a person gave it.

    A slug rather than the name itself because the key is a directory on disk
    and a path segment on the wire — ``Kennametal (2026)`` becomes
    ``kennametal-2026`` — and it is fixed at creation: the directory and every
    corpus row carry it, so a later rename must not move them. Refused when
    nothing is left of the name, and when it would collide with the union's
    own directory.
    """
    key = _KEY_STRIP.sub("-", (name or "").strip().lower()).strip("-")[:64].strip("-")
    if not key:
        raise CatalogueError(
            "A catalogue needs a name with at least one letter or digit in it — "
            "usually the manufacturer's, like Kennametal or YG-1.", status=422)
    if key == UNION_DIR.strip("_") or key.startswith("_"):
        raise CatalogueError(f"{name!r} is not a name a catalogue can have.",
                             status=422)
    return key


def catalogue_dir(connection_id: str, catalogue_key: str = DEFAULT_CATALOGUE) -> Path:
    """Where one of a company's catalogues keeps its decoded files."""
    return settings.PIE_CATALOG.parent / "catalogues" / connection_id / catalogue_key


def company_catalog_path(connection_id: str,
                         catalogue_key: str = DEFAULT_CATALOGUE) -> Path:
    """Where one of a company's catalogues has its decoded JSONL.

    Derived and rebuildable: the corpus rows are the source of truth, so losing
    this file costs a rebuild rather than the data. That is the property §1.1
    of the design doc exists to preserve. One directory per catalogue, so a
    company's Kennametal and YG-1 files sit beside each other and the union
    beside both.
    """
    return catalogue_dir(connection_id, catalogue_key) / "products.jsonl"


def union_catalog_path(connection_id: str) -> Path:
    """The one file a company resolves against: every built catalogue, merged."""
    return catalogue_dir(connection_id, UNION_DIR) / "products.jsonl"


def available_packs() -> List[Dict[str, str]]:
    """The org-layer packs the pinned engine ships, for a catalogue to choose from.

    Chosen, never uploaded. A pack is grammars and regexes the engine compiles
    and runs over every row of a corpus, so accepting one from a tenant means
    executing tenant-supplied patterns — a catastrophic-backtracking expression
    is then a denial of service somebody can upload for themselves, against a
    build that is a synchronous request. Listing what ships keeps that surface
    at zero. `docs/per-company-catalogues.md` §1.2 has the argument and what an
    uploaded bundle would have to carry.
    """
    root = settings.PIE_PARSER_ROOT / "packs" / "org"
    if not root.is_dir():
        return []
    out: List[Dict[str, str]] = []
    for entry in sorted(root.iterdir()):
        if not (entry / "manifest.yaml").exists():
            continue
        out.append({"id": entry.name, "path": str(entry)})
    return out


def resolve_pack(pack_id: Optional[str]) -> Optional[Path]:
    """A pack identifier as a path, against what the engine ships — or None.

    An id naming a pack this engine does not have resolves to None rather than
    to a guess: the pin can move under a stored choice, and answering with a
    different pack than the one recorded would make the stamp a lie.
    """
    chosen = (pack_id or "").strip()
    if not chosen:
        return None
    for pack in available_packs():
        if pack["id"] == chosen:
            return Path(pack["path"])
    return None


def pack_for(catalogue: Any) -> Optional[Path]:
    """The pack one catalogue decodes through, or None if it has not chosen one.

    Stored on ``CompanyCatalogue.pack_choice`` as an *identifier*, resolved to
    a path here. It used to live on the company (``ZohoConnection.config``)
    while a company had one catalogue; it is a fact about the catalogue, and
    ``m1cats`` moved it.
    """
    if catalogue is None:
        return None
    return resolve_pack(getattr(catalogue, "pack_choice", None))


def catalogue_rows(session: Any, org: str, connection_id: str) -> List[Any]:
    """Every catalogue this company has defined, the default first, then by key.

    Scoped to the organization in the query rather than checked after it: a
    connection id from another tenant must read as "no catalogues" rather than
    as a permission error that confirms one exists.
    """
    from sqlalchemy import select

    from .domain import models

    rows = list(session.scalars(
        select(models.CompanyCatalogue)
        .where(models.CompanyCatalogue.organization_id == org,
               models.CompanyCatalogue.connection_id == connection_id)))
    rows.sort(key=lambda r: (r.catalogue_key != DEFAULT_CATALOGUE, r.catalogue_key))
    return rows


def catalogue_row(session: Any, org: str, connection_id: str,
                  catalogue_key: str) -> Any:
    """One catalogue's row, or None. Organization-scoped, like the list."""
    from .domain import models

    row = session.get(models.CompanyCatalogue, (org, connection_id, catalogue_key))
    return row


def catalogue_rows_for_org(session: Any, org: str) -> List[Any]:
    """Every catalogue of every company in this organization."""
    from sqlalchemy import select

    from .domain import models

    return list(session.scalars(
        select(models.CompanyCatalogue)
        .where(models.CompanyCatalogue.organization_id == org)
        .order_by(models.CompanyCatalogue.connection_id,
                  models.CompanyCatalogue.catalogue_key)))


def create_catalogue(session: Any, org: str, connection_id: str, name: str,
                     pack_id: Optional[str] = None) -> Any:
    """Define a new catalogue for this company: a name, and optionally a pack.

    Nothing is built and nothing is uploaded here; the row is what the files
    and the build then belong to. Refuses a name that yields no key, a key the
    company already uses, and a pack the engine does not ship — each with the
    sentence a person can act on, because a stored definition that cannot
    build would leave them with a catalogue and no statement of why.
    """
    from .domain import models

    key = catalogue_key_for(name)
    if catalogue_row(session, org, connection_id, key) is not None:
        raise CatalogueError(
            f"This company already has a catalogue keyed {key!r}. Add files to "
            f"that one, or choose a different name.")
    existing = catalogue_rows(session, org, connection_id)
    if len(existing) >= MAX_CATALOGUES:
        raise CatalogueError(
            f"This company already has {len(existing)} catalogues, which is the "
            f"limit of {MAX_CATALOGUES}. Remove one first.")
    if pack_id and resolve_pack(pack_id) is None:
        known = ", ".join(p["id"] for p in available_packs()) or "none"
        raise CatalogueError(
            f"No pack called {pack_id!r} ships with this engine. Available: {known}.",
            status=400)
    row = models.CompanyCatalogue(
        organization_id=org, connection_id=connection_id, catalogue_key=key,
        name=(name or "").strip()[:255], pack_choice=(pack_id or None))
    session.add(row)
    session.flush()
    return row


def rename_catalogue(session: Any, org: str, connection_id: str,
                     catalogue_key: str, name: str) -> Any:
    """Give a catalogue a name a person will recognise — the migrated one
    arrives as ``default`` with none. The key stays: it is the directory and
    every corpus row's address, and a rename must not move them."""
    row = catalogue_row(session, org, connection_id, catalogue_key)
    if row is None:
        raise CatalogueError("This company has no catalogue by that key.",
                             status=404)
    cleaned = (name or "").strip()
    if not cleaned:
        raise CatalogueError("A catalogue needs a name.", status=422)
    row.name = cleaned[:255]
    session.flush()
    if row.built_at is not None:
        write_sidecar(connection_id, catalogue_key, row.name, row.pack_choice,
                      row.built_at, {k: getattr(row, k) for k in STAMP_FIELDS
                                     if getattr(row, k, None) is not None},
                      row.records)
        refresh_union(connection_id)
    return row


def delete_catalogue(session: Any, org: str, connection_id: str,
                     catalogue_key: str) -> None:
    """Remove one of a company's catalogues: its definition, its files, and its
    place in the union.

    The source rows are **superseded, not deleted**, on the same reasoning as
    every other change to a corpus: a build that happened keeps a real record
    of what it was built from, and the bytes are the one thing that cannot be
    recreated. The decoded files go — they are derived — and the union is
    refreshed so the company stops resolving against this manufacturer at once
    rather than at the next rebuild.
    """
    import shutil

    row = catalogue_row(session, org, connection_id, catalogue_key)
    if row is None:
        raise CatalogueError("This company has no catalogue by that key.",
                             status=404)
    now = clock.now()
    for source in current_corpora(session, org, connection_id, catalogue_key):
        source.superseded_at = now
    session.delete(row)
    session.flush()
    shutil.rmtree(catalogue_dir(connection_id, catalogue_key), ignore_errors=True)
    refresh_union(connection_id)


def current_corpora(session: Any, org: str, connection_id: str,
                    catalogue_key: str = DEFAULT_CATALOGUE) -> List[Any]:
    """Every file one catalogue would be built from, oldest first.

    Oldest first because that is the order :func:`combined_corpus` resolves
    collisions in: the newest source wins, so it must be written last.

    Scoped to the organization in the query rather than checked after it, like
    ``_skip_rows`` in the data router: a connection id from another tenant must
    read as "no sources" rather than as a permission error that confirms one
    exists.
    """
    from sqlalchemy import select

    from .domain import models

    return list(session.scalars(
        select(models.CompanyCorpus)
        .where(models.CompanyCorpus.organization_id == org,
               models.CompanyCorpus.connection_id == connection_id,
               models.CompanyCorpus.catalogue_key == catalogue_key,
               models.CompanyCorpus.superseded_at.is_(None))
        .order_by(models.CompanyCorpus.uploaded_at.asc(),
                  models.CompanyCorpus.corpus_id.asc())))


def current_corpus(session: Any, org: str, connection_id: str,
                   catalogue_key: str = DEFAULT_CATALOGUE) -> Any:
    """The newest of a catalogue's sources, or None if it has none.

    Still meaningful with several: it is the file whose arrival a screen dates
    the export by, and the one a collision resolves in favour of. What it is
    *not* any more is the whole of what the catalogue was built from — that is
    :func:`current_corpora`, and the digest over it.
    """
    sources = current_corpora(session, org, connection_id, catalogue_key)
    return sources[-1] if sources else None


def source_key_of(row: Any) -> str:
    """Which source a corpus row is, for a row written before keys existed.

    Falls back to the filename and then to the id, so every row has a key to be
    replaced or removed by. Never empty: an unkeyed row that could not be
    addressed would be a source a person can see and not delete.
    """
    return (getattr(row, "source_key", None) or row.filename
            or row.corpus_id)


def sources_digest(sources: List[Any]) -> str:
    """A hash over the set of files a build would read.

    Over each source's key and content digest, sorted, so it is a property of
    the *set* rather than of the order it was assembled in. This is what makes
    "out of date" answerable once a catalogue has several files: adding a
    source, replacing one and removing one all move this hash, and none of the
    three is visible in a single ``corpus_id``.
    """
    parts = sorted(f"{source_key_of(s)}:{s.sha256}" for s in sources)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _release(source: Any) -> None:
    """Forget one source's bytes, so a merge holds one file rather than all.

    Best effort by design: a detached row, or one built by hand in a test, has
    no session to expire it against and needs none — it was never the case this
    protects.
    """
    from sqlalchemy import inspect as sa_inspect

    try:
        state = sa_inspect(source)
        if state.session is not None:
            state.session.expire(source, ["content"])
    except Exception:  # noqa: BLE001 — releasing memory must not fail a build
        log.debug("could not expire the content of a corpus row", exc_info=True)


def combined_corpus(sources: List[Any], pack_path: Path, out: Path,
                    limit: Optional[int] = None) -> Dict[str, Any]:
    """Write every source, normalised to the pack's columns, as one CSV.

    Three things happen here, and the third is the one with teeth.

    *Normalisation* is per source, through the mapping stored with it, into the
    column names this pack declares — so a catalogue can build from an item
    master calling the part number ``MM#`` and a price list calling it
    ``Part No`` without either file being edited.

    *Streaming*: each source is read row by row and written straight to ``out``.
    Materialising them cost 394 MB of resident memory for one 33 MB file (see
    ``ingestion.item_master.Table``), and a catalogue may keep twenty.

    *De-duplication* is by record id, and the newest source wins. This is not
    tidying. pie-parser's ``AuthoritativeIndex`` indexes identifiers per
    namespace and treats a duplicate inside one namespace as a collision that
    **never resolves** — so emitting the same part number from two files would
    not give a wrong answer, it would silently stop that part number resolving
    at all, which is a defect nobody would find by looking at record counts.
    Newest-wins is a policy and it is stated as one: a later file is a later
    statement about the same product. Every collision is counted and the first
    of them named, because the same part number in two price lists is a real
    disagreement and the answer is to tell somebody, not to pick quietly.

    The sources are therefore read **newest first**, so the row that wins is
    the first one seen and only the keys have to be remembered rather than the
    rows. The output is in that order; nothing downstream depends on the order
    of a corpus, and a given set of files always produces the same bytes.

    ``limit`` reads only the first rows of each source, for the pack-fit trial.
    """
    from .ingestion import item_master

    headers = pack_columns(pack_path)
    order = [headers.get(role) or role for role in item_master.ROLES]

    seen: set = set()
    collisions: Dict[str, str] = {}
    per_source: List[Dict[str, Any]] = []

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(order)
        for source in reversed(sources):
            # Named per file. "The build failed" over six sources is not
            # something a person can act on, and the reason a file has stopped
            # being readable is usually specific to that file — a mapping whose
            # column was renamed in a re-upload, a workbook saved in an older
            # format.
            report: Dict[str, Any] = {}
            emitted = 0
            try:
                table = item_master.read_table(
                    source.content, source.filename, source.content_type or "")
                mapping = (getattr(source, "mapping", None)
                           or item_master.suggest_mapping(table))
                report = item_master.ingest_report(table, mapping)
                # Streamed, not collected: `list()` around this generator would
                # put the whole file back in memory, which is the one thing this
                # function exists to avoid.
                for row in item_master.emit_rows(table, mapping, report,
                                                 limit=limit):
                    if row[0] in seen:
                        # An older file restating a part number a newer one
                        # already gave. Recorded against the file that lost,
                        # which is the one somebody would go and look at.
                        collisions.setdefault(row[0], source_key_of(source))
                        continue
                    seen.add(row[0])
                    writer.writerow(row)
                    emitted += 1
            except item_master.ItemMasterError as e:
                raise item_master.ItemMasterError(
                    f"{source.filename or source_key_of(source)}: {e}") from e
            report["rows_emitted"] = emitted
            report["source_key"] = source_key_of(source)
            report["filename"] = source.filename
            report["sha256"] = source.sha256
            per_source.append(report)
            # Released as soon as this file has been written out. `content` is
            # deferred, so it arrived on demand a moment ago; without this the
            # session would hold every source's bytes at once by the end of the
            # loop, which is the profile deferring it was for.
            _release(source)

    return {
        # Oldest first, matching how the screen lists them; the reading order
        # above is an implementation detail of the collision rule.
        "sources": list(reversed(per_source)),
        "rows_kept": len(seen),
        "rows_in": sum(r["rows_read"] for r in per_source),
        "rows_skipped_blank_key": sum(r["rows_skipped_blank_key"]
                                      for r in per_source),
        "collisions": len(collisions),
        # A handful, named. The full list would be unbounded and the count is
        # what says whether this is a stray or a structural overlap.
        "collision_examples": sorted(collisions)[:10],
        "sampled": any(r["sampled"] for r in per_source),
    }


def company_catalog_state(session: Any, org: str, connection_id: str,
                          catalogue_key: str = DEFAULT_CATALOGUE) -> Dict[str, Any]:
    """One catalogue: what is on disk, what built it, and what is missing.

    ``exists: False`` is its own state and ``records`` stays ``None`` — a
    catalogue with no build says nothing about coverage, and rendering that
    as a zero is the benign default §1 forbids. A row that has never been
    built (``built_at`` null) is a *definition*: it reports its name and its
    pack, and nothing a build would have stamped.

    ``stale`` is the one genuinely new fact: a catalogue built from a corpus
    that has since been superseded is still a real catalogue with a real stamp,
    but it is no longer built from what was last uploaded. Saying so is the
    difference between "out of date" and "wrong", and only one of them is
    urgent.

    With several sources, staleness is a comparison of *digests over the set* —
    a source added or removed changes what a build would read while leaving the
    newest ``corpus_id`` untouched, so the old single-id comparison would have
    called that catalogue current. It is kept as the fallback for a row built
    before the digest column existed, where it is still the honest answer.
    """
    row = catalogue_row(session, org, connection_id, catalogue_key)
    built = row is not None and row.built_at is not None
    sources = current_corpora(session, org, connection_id, catalogue_key)
    corpus = sources[-1] if sources else None
    path = company_catalog_path(connection_id, catalogue_key)
    on_disk = path.exists()
    pack = pack_for(row)

    if not built or not sources:
        stale = False
    elif row.corpus_digest:
        stale = row.corpus_digest != sources_digest(sources)
    else:
        stale = row.corpus_id != corpus.corpus_id

    return {
        "connection_id": connection_id,
        "catalogue_key": catalogue_key,
        "name": (row.name if row else "") or "",
        "scope": "catalogue",
        # The pack *chosen* for this catalogue, and whether the pinned engine
        # still ships it. A stored id the engine no longer has resolves to
        # nothing rather than to a guess — the pin can move under a stored
        # choice, and answering from a different pack would make the stamp
        # lie. `stamp.pack_id` below is the engine's own record of what a
        # build actually decoded through.
        "pack_id": (row.pack_choice if row else None) or None,
        "pack_resolved": pack is not None,
        "exists": built and on_disk,
        # A row without its file is a rebuild waiting to happen, not a
        # catalogue. Named rather than silently treated as absent, because the
        # two have different fixes and only this one is free.
        "built_but_missing_on_disk": built and not on_disk,
        "records": row.records if (built and on_disk) else None,
        "rows_read": row.rows_read if built else None,
        "quarantined": row.quarantined if built else None,
        "duration_s": row.duration_s if built else None,
        "built_at": clock.iso(clock.aware(row.built_at)) if built else None,
        "built_by": row.built_by if built else None,
        "pack": row.pack if built else None,
        "report": row.report if built else None,
        "stamp": ({k: getattr(row, k) for k in STAMP_FIELDS
                   if getattr(row, k, None) is not None} if built else {}),
        # What merging the sources did, and — for a built catalogue — which
        # files it read. Kept out of `report`, which is the parser's own dict
        # served verbatim and must not gain fields the parser did not write.
        "ingest": row.ingest if built else None,
        "built_from": row.sources if built else None,
        # The newest source, kept under its original name: a screen dates a
        # catalogue's export by the file that arrived last.
        "corpus": ({
            "corpus_id": corpus.corpus_id,
            "filename": corpus.filename,
            "size_bytes": corpus.size_bytes,
            "sha256": corpus.sha256,
            "uploaded_at": clock.iso(clock.aware(corpus.uploaded_at)),
            "uploaded_by": corpus.uploaded_by,
        } if corpus else None),
        "sources": [{
            "source_key": source_key_of(s),
            "corpus_id": s.corpus_id,
            "filename": s.filename,
            "content_type": s.content_type,
            "size_bytes": s.size_bytes,
            "sha256": s.sha256,
            "uploaded_at": clock.iso(clock.aware(s.uploaded_at)),
            "uploaded_by": s.uploaded_by,
            "mapping": getattr(s, "mapping", None),
            "ingest": getattr(s, "ingest", None),
        } for s in sources],
        "stale": stale,
    }


def company_state(session: Any, org: str, connection_id: str) -> Dict[str, Any]:
    """Every catalogue this company has defined, and the union it resolves against.

    The union is described from its manifest — which builds it holds, how
    many records, how many part numbers two catalogues both claimed — and
    ``None`` when nothing is built, which is the honest shape for a company
    that resolves nothing.
    """
    return {
        "catalogues": [company_catalog_state(session, org, connection_id,
                                             row.catalogue_key)
                       for row in catalogue_rows(session, org, connection_id)],
        "union": union_state(connection_id),
    }


def _sidecar_path(connection_id: str, catalogue_key: str) -> Path:
    return catalogue_dir(connection_id, catalogue_key) / CATALOGUE_SIDECAR


def write_sidecar(connection_id: str, catalogue_key: str, name: str,
                  pack_choice: Optional[str], built_at: Any,
                  stamp: Dict[str, Any], records: int) -> None:
    """What a built catalogue says about itself beside its file.

    The union is assembled from the disk alone — in the resolver, which has no
    session, and at start-up before any request — so the facts it needs
    (which catalogue this is, when it was built, what stamp its rows carry)
    travel with the file rather than only in the row.
    """
    payload = {
        "catalogue_key": catalogue_key,
        "name": name or "",
        "pack_choice": pack_choice,
        "built_at": clock.iso(clock.aware(built_at)) if built_at else None,
        "stamp": dict(stamp),
        "records": records,
    }
    path = _sidecar_path(connection_id, catalogue_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def built_catalogues(connection_id: str) -> List[Dict[str, Any]]:
    """Every catalogue this company has on disk, from the directory alone.

    Each is the sidecar's facts plus the file's size and modification time —
    the two things that say whether the union made from it is still current.
    A directory holding a ``products.jsonl`` without a sidecar (a catalogue
    linked in by hand, or one built before sidecars existed) is still a
    catalogue; its key is its directory and its stamp is read off its rows.
    """
    root = settings.PIE_CATALOG.parent / "catalogues" / connection_id
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if entry.name == UNION_DIR or entry.name.startswith("_") or not entry.is_dir():
            continue
        products = entry / "products.jsonl"
        if not products.is_file():
            continue
        try:
            meta = json.loads((entry / CATALOGUE_SIDECAR).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        stat = products.stat()
        out.append({
            "catalogue_key": entry.name,
            "name": str(meta.get("name") or ""),
            "pack_choice": meta.get("pack_choice"),
            "built_at": meta.get("built_at") or clock.iso(
                clock.aware(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc))),
            "stamp": dict(meta.get("stamp") or catalog_stamp(products)),
            "path": str(products),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    return out


@dataclass
class UnionCatalogue:
    """A company's built catalogues merged into the one file it resolves against."""

    path: Path
    #: What a resolution is stamped with: a hash over every member's key and
    #: ``run_id``. The run id is pie-parser's own fingerprint of a build — the
    #: input bytes plus the ruleset checksum — so this moves when any member
    #: is rebuilt from different files or through a different pack, and stays
    #: put across an identical rebuild. The ruleset checksum alone would not
    #: do: it is the *pack's* hash, the same for two companies decoding
    #: different item masters through ``zcnc``, and a resolution cache keyed
    #: on it would hand one company the other's answer. Empty when any
    #: member's stamp could not be read — and an empty version is never
    #: cached, which is the safe direction.
    version: str
    records: int
    #: Part numbers two catalogues both claimed inside one numbering space.
    duplicates: int
    duplicate_examples: List[str]
    catalogues: List[Dict[str, Any]]


_union_lock = threading.Lock()


def _union_manifest_path(connection_id: str) -> Path:
    return catalogue_dir(connection_id, UNION_DIR) / "union.json"


def _members_fingerprint(members: List[Dict[str, Any]]) -> List[List[Any]]:
    return [[m["catalogue_key"], m["size"], m["mtime_ns"]] for m in members]


def _union_version(members: List[Dict[str, Any]]) -> str:
    """See :attr:`UnionCatalogue.version`. One rule whatever the count, so the
    value means the same thing for a company with one catalogue as for one
    with five."""
    run_ids = [str((m.get("stamp") or {}).get("run_id") or "") for m in members]
    if not members or not all(run_ids):
        return ""
    parts = sorted(f"{m['catalogue_key']}:{r}" for m, r in zip(members, run_ids))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_manifest(connection_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_union_manifest_path(connection_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def union_catalogue(connection_id: str) -> Optional[UnionCatalogue]:
    """The file this company resolves against, made current if it is not.

    Assembled from :func:`built_catalogues` — the disk, not the database — so
    it can be built wherever a catalogue can be read: in the resolver, at
    start-up, in a test that linked a file into place. ``None`` when the
    company has no built catalogue, which is "resolves nothing" and must not
    be an empty file that reads as a catalogue of nothing.

    **De-duplicated across catalogues, and counted.** Two catalogues can claim
    one part number inside the same numbering space — two Kennametal price
    lists kept as two catalogues, say — and pie-parser's ``AuthoritativeIndex``
    treats a duplicate identifier inside one namespace as a collision that
    never resolves, exactly as it does inside one catalogue
    (:func:`combined_corpus`). The most recently *built* catalogue wins, on the
    same reasoning as newest-file-wins there, and the count is reported so a
    person can see that two of their catalogues overlap. A part number two
    manufacturers both use is not a duplicate: their records sit in different
    namespaces and the engine reports a bare lookup of it as ambiguous, which
    is the right answer.

    Each union record carries ``catalogue_key`` — the one portal field added
    to a decoded record — so a resolution can say which manufacturer's
    catalogue answered without a second map to keep in step with the file.

    Rebuilt only when a member's file has changed (size or modification time),
    which the manifest beside the union records. Deterministic in the members,
    so an identical rebuild produces identical bytes.
    """
    members = built_catalogues(connection_id)
    out = union_catalog_path(connection_id)
    with _union_lock:
        if not members:
            if out.exists():
                import shutil
                shutil.rmtree(out.parent, ignore_errors=True)
            return None
        manifest = _read_manifest(connection_id)
        if (manifest and out.exists()
                and manifest.get("members") == _members_fingerprint(members)):
            return UnionCatalogue(
                path=out, version=str(manifest.get("version") or ""),
                records=int(manifest.get("records") or 0),
                duplicates=int(manifest.get("duplicates") or 0),
                duplicate_examples=list(manifest.get("duplicate_examples") or []),
                catalogues=list(manifest.get("catalogues") or []))
        return _write_union(connection_id, members, out)


def _write_union(connection_id: str, members: List[Dict[str, Any]],
                 out: Path) -> UnionCatalogue:
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from identity.store import record_namespace  # noqa: PLC0415
    except ImportError:  # the engine is absent: the same rule, stated once more
        def record_namespace(rec: Dict[str, Any]) -> str:  # type: ignore[misc]
            return str(rec.get("org_id") or rec.get("pack_id")
                       or rec.get("manufacturer") or rec.get("brand") or "")

    # Newest build first, so the first record seen for a key is the one kept.
    # The key breaks a tie between two builds stamped the same instant, so the
    # outcome is a function of the members and never of directory order.
    ordered = sorted(members, key=lambda m: (m["built_at"] or "", m["catalogue_key"]),
                     reverse=True)
    seen: set = set()
    duplicates: Dict[str, str] = {}
    records = 0
    per_catalogue: List[Dict[str, Any]] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for member in ordered:
            kept = 0
            with Path(member["path"]).open("r", encoding="utf-8") as src:
                for line in src:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    key = (str(record_namespace(rec)).strip().upper(),
                           str(rec.get("record_id") or "").strip().upper())
                    if key[1] and key in seen:
                        duplicates.setdefault(key[1], member["catalogue_key"])
                        continue
                    seen.add(key)
                    rec["catalogue_key"] = member["catalogue_key"]
                    fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":"))
                             + "\n")
                    kept += 1
            records += kept
            per_catalogue.append({
                "catalogue_key": member["catalogue_key"],
                "name": member["name"],
                "pack_id": member["pack_choice"],
                "built_at": member["built_at"],
                "records": kept,
                **{k: member["stamp"].get(k) for k in
                   ("ruleset_checksum", "run_id", "pack_version", "org_id")
                   if member["stamp"].get(k) is not None},
            })
    os.replace(tmp, out)
    per_catalogue.sort(key=lambda c: (c["catalogue_key"] != DEFAULT_CATALOGUE,
                                      c["catalogue_key"]))
    union = UnionCatalogue(
        path=out, version=_union_version(members), records=records,
        duplicates=len(duplicates), duplicate_examples=sorted(duplicates)[:10],
        catalogues=per_catalogue)
    manifest = {
        "members": _members_fingerprint(members),
        "version": union.version, "records": union.records,
        "duplicates": union.duplicates,
        "duplicate_examples": union.duplicate_examples,
        "catalogues": union.catalogues,
    }
    mtmp = _union_manifest_path(connection_id).with_suffix(".json.tmp")
    mtmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    os.replace(mtmp, _union_manifest_path(connection_id))
    log.info("union catalogue for connection %s: %d records from %d catalogue(s), "
             "%d duplicated across them", connection_id, records, len(members),
             len(duplicates))
    return union


def refresh_union(connection_id: str) -> Optional[UnionCatalogue]:
    """Bring the union up to date after a build or a removal, and make the
    resolver forget what it had loaded for this company.

    The index beside the union is rebuilt here too rather than on the first
    resolution, for the reason a build used to index its own file: three
    seconds at build time, where a person is already waiting, is better than
    three seconds on the first quote line.
    """
    union = union_catalogue(connection_id)
    if union is not None:
        _index_for_retrieval(union.path)
    # Imported here rather than at the top: `pie_service` imports this module.
    from .pie_service import pie_service  # noqa: PLC0415

    pie_service.reload(connection_id)
    return union


def union_state(connection_id: str) -> Optional[Dict[str, Any]]:
    """The union as the screen reports it, or None when nothing is built."""
    union = union_catalogue(connection_id)
    if union is None:
        return None
    return {
        "records": union.records,
        "version": union.version or None,
        "duplicates": union.duplicates,
        "duplicate_examples": union.duplicate_examples,
        "catalogues": union.catalogues,
        # The nearest-neighbour index beside the union, if one has been built:
        # which model, over how many records, and whether it still describes
        # the file on disk. None when the index could not be written — the
        # union itself is unaffected, and the next resolution builds the index
        # it needs.
        "retrieval": retrieval.describe(union.path),
    }


def build_for_company(session: Any, org: str, connection_id: str,
                      pack_path: Path, actor: Optional[str] = None,
                      catalogue_key: str = DEFAULT_CATALOGUE) -> Dict[str, Any]:
    """Decode every file one catalogue holds, through its pack, and refresh
    the union the company resolves against.

    The merged corpus is written to a temporary file for the parse and removed
    after: pie-parser's ``CsvAdapter`` reads a path, and the durable copies are
    the rows — materialising them beside the output would put a second source
    of truth on the disk that §1.1 of the design says cannot be trusted to
    survive.

    Stores the run report and the stamp as a row rather than a sidecar file,
    for the same reason. Raises ``FileNotFoundError`` when the catalogue has
    no files, which is a state to report rather than an error to log, and
    ``ingestion.item_master.ItemMasterError`` when one of its files can no
    longer be read as a table — named per file, because "the build failed" over
    six sources is not something a person can act on.
    """
    import tempfile

    from .domain import models

    sources = current_corpora(session, org, connection_id, catalogue_key)
    if not sources:
        raise FileNotFoundError(
            "This catalogue has no price list or item-master export on file. "
            "Upload one before building it.")

    out = company_catalog_path(connection_id, catalogue_key)
    with _build_lock:
        tmpdir = tempfile.mkdtemp(prefix="pie-corpus-")
        tmp_corpus = Path(tmpdir) / "corpus.csv"
        try:
            combine = combined_corpus(sources, pack_path, tmp_corpus)
            result = run_parse(tmp_corpus, pack_path, out)
        finally:
            # The bytes are in the rows; nothing is lost by removing them here,
            # and leaving a tenant's item master in /tmp is a disclosure.
            tmp_corpus.unlink(missing_ok=True)
            Path(tmpdir).rmdir()

    row = catalogue_row(session, org, connection_id, catalogue_key)
    if row is None:
        row = models.CompanyCatalogue(organization_id=org,
                                      connection_id=connection_id,
                                      catalogue_key=catalogue_key)
        session.add(row)
    row.corpus_id = sources[-1].corpus_id
    row.corpus_digest = sources_digest(sources)
    row.sources = [{
        "source_key": source_key_of(s),
        "corpus_id": s.corpus_id,
        "filename": s.filename,
        "sha256": s.sha256,
    } for s in sources]
    row.ingest = combine
    row.pack = str(pack_path)
    row.records = result["records"]
    row.rows_read = result["rows_read"]
    row.quarantined = result["quarantined"]
    row.duration_s = result["duration_s"]
    row.report = result["report"]
    for field in STAMP_FIELDS:
        setattr(row, field, result["stamp"].get(field))
    row.built_by = actor
    row.built_at = clock.now()
    session.flush()
    write_sidecar(connection_id, catalogue_key, row.name, row.pack_choice,
                  row.built_at, result["stamp"], result["records"])
    refresh_union(connection_id)
    return company_catalog_state(session, org, connection_id, catalogue_key)


def _index_for_retrieval(out: Path) -> None:
    """Build the union's nearest-neighbour index, and never fail the build.

    The index is derived from the file just written, and ``retrieval`` rebuilds
    a missing or stale one on first use — so a failure here costs the first
    resolution a rebuild, where failing the build would cost the company its
    catalogue. Logged at warning, and visible on the screen as a union with
    no index.
    """
    try:
        retrieval.ensure_index(out)
    except Exception:  # noqa: BLE001 — the catalogue is the deliverable
        log.warning("retrieval index for %s could not be built; it will be "
                    "rebuilt on first use", out, exc_info=True)


def prepare_source(raw: bytes, filename: str = "", content_type: str = "",
                   mapping: Optional[Dict[str, Any]] = None,
                   ) -> tuple[Dict[str, Optional[str]], Dict[str, Any]]:
    """Read an upload as a table, settle its column mapping, and say what it holds.

    Called *before* the row is written rather than at build time: an upload that
    is accepted and then fails to build leaves the catalogue holding a file it
    cannot use and no message saying why. It lives here rather than in the
    router, which stays a mapping layer (§3).

    Returns the mapping to store and the ingest report to store beside it.
    Raises ``ingestion.item_master.ItemMasterError`` with a sentence written for
    the person who uploaded the file.

    **What it no longer checks is the pack's own column names.** It used to
    refuse any file whose headers were not the ones the chosen pack declares,
    which made every export other than the one this platform was written
    against unusable — and the fix a person needed was to rename spreadsheet
    columns to match a pack they cannot see. The mapping replaced that: the file
    keeps its own headers, the mapping says which of them fills each role, and
    `combined_corpus` renames the columns on the way into the parse. So the
    refusal here is narrower and more useful — a file whose part number or
    description cannot be *identified at all*, with its headers listed so the
    person can say which column it is.
    """
    from .ingestion import item_master

    table = item_master.read_table(raw, filename, content_type)
    settled = dict(mapping) if mapping else item_master.suggest_mapping(table)
    item_master.check_mapping(table, settled)
    return settled, item_master.describe(table, settled)


def pack_fit(session: Any, org: str, connection_id: str,
             sample_rows: int = 0,
             catalogue_key: str = DEFAULT_CATALOGUE) -> Dict[str, Any]:
    """Try every shipped pack against a sample of one catalogue's files.

    Which pack decodes an export is not a question a person can answer from a
    dropdown of identifiers — ``zcnc`` says nothing about whether it reads
    their file. So this runs the real pipeline over the first rows of the real
    sources and reports **the parser's own counts** for each pack: classified,
    quarantined, and the per-family census it produced. It computes no score
    and no rate of its own; ranking packs by a number this module invented
    would be exactly the second parse-rate calculation ``run_parse`` refuses.

    Safe by construction, and this is why it can exist at all: it executes only
    the packs the pinned engine ships. No tenant-supplied pattern is compiled
    (see :func:`available_packs`), so the trial adds no execution surface — it
    only spends CPU that the build was going to spend anyway.

    ``available: False`` with a reason when there is nothing to try, never an
    empty result that reads as "no pack fits".
    """
    import tempfile

    from .ingestion import item_master

    sample_rows = sample_rows or item_master.SAMPLE_ROWS
    packs = available_packs()
    sources = current_corpora(session, org, connection_id, catalogue_key)
    if not packs:
        return {"available": False, "reason": source_state()["reason"] or (
            "This engine ships no org-layer packs."), "packs": []}
    if not sources:
        return {"available": False, "reason": (
            "This catalogue has no item-master export on file yet, so there is "
            "nothing to try a pack against."), "packs": []}

    out: List[Dict[str, Any]] = []
    with _build_lock:
        tmpdir = Path(tempfile.mkdtemp(prefix="pie-packfit-"))
        try:
            for pack in packs:
                path = tmpdir / f"{pack['id']}.jsonl"
                try:
                    combine = combined_corpus(sources, Path(pack["path"]),
                                              tmpdir / "corpus.csv",
                                              limit=sample_rows)
                    result = run_parse(tmpdir / "corpus.csv",
                                       Path(pack["path"]), path)
                except Exception as e:  # noqa: BLE001 — one pack failing is a result
                    # Reported against that pack rather than raised: a pack the
                    # engine ships but cannot run on this file is precisely
                    # what the trial is for, and it must not hide the others.
                    log.info("pack %s could not decode %s/%s: %s",
                             pack["id"], connection_id, catalogue_key, e)
                    out.append({"pack_id": pack["id"],
                                "error": f"{type(e).__name__}: {e}"})
                    continue
                out.append({
                    "pack_id": pack["id"],
                    "rows_read": result["rows_read"],
                    "classified": result["records"],
                    "quarantined": result["quarantined"],
                    "report": result["report"],
                    "sampled": combine["sampled"],
                })
        finally:
            # Nothing here is a catalogue: a trial that left a products.jsonl
            # behind would be a company resolving against a pack it never chose.
            for leftover in tmpdir.glob("*"):
                leftover.unlink(missing_ok=True)
            tmpdir.rmdir()

    return {"available": True, "reason": None, "sample_rows": sample_rows,
            "packs": out}


# ── the seed: what a deployment that predates per-company catalogues gets ────


def seed_company_catalogues(session: Any,
                            actor: str = "shipped-corpus") -> List[Dict[str, Any]]:
    """Give each organization's first company the corpus that ships in the image.

    Every deployment that ran before this change resolved against one shared
    catalogue built from ``settings.PIE_CORPUS``. Removing that default without
    putting it somewhere turns every quote line UNRESOLVED on deploy — a
    regression that looks exactly like the engine being down. So the corpus
    becomes the first company's *default* catalogue's corpus, once, and the
    pack it was decoded through becomes that catalogue's pack.

    Deliberately conservative, because it writes on somebody's behalf:

    * only an organization that has **no corpus row at all** is seeded, so a
      company that has uploaded its own export is never handed a different
      manufacturer's item master;
    * only its **first enabled** company — ``list_connections`` orders oldest
      first — because which of three legal entities sells this catalogue's
      product is a question this function cannot answer, and guessing three
      times is worse than guessing once;
    * an existing pack choice on that catalogue is left alone;
    * a missing corpus file seeds nothing and is not an error. The engine is
      optional in ``deploy/backend.Dockerfile``, so an image built without it
      has no seed to give — and that deployment was not resolving before this
      change either.

    Idempotent, and re-runnable: it is called on every boot rather than once in
    a migration, so a deployment that later gains the submodule (or its first
    connection) is seeded on the next start rather than never.
    """
    from .ingestion.connections import list_connections
    from .domain import models

    from sqlalchemy import select

    source = source_state()
    if not source["available"]:
        log.info("no shipped corpus to seed from: %s", source["reason"])
        return []

    seeded_orgs = set(session.scalars(
        select(models.CompanyCorpus.organization_id).distinct()))
    out: List[Dict[str, Any]] = []
    for org in session.scalars(select(models.Organization.organization_id)):
        if org in seeded_orgs:
            continue
        companies = list_connections(session, org, enabled_only=True)
        if not companies:
            continue
        first = companies[0]
        raw = settings.PIE_CORPUS.read_bytes()
        session.add(models.CompanyCorpus(
            organization_id=org,
            connection_id=first.connection_id,
            catalogue_key=DEFAULT_CATALOGUE,
            # Keyed by its filename like any other source, so the company can
            # replace or remove the seed from the screen once it has its own
            # export. No mapping is stored: the shipped corpus uses the shipped
            # pack's own column names, which is exactly the case
            # `suggest_mapping` reads correctly, and inventing a mapping here
            # would state a fact about a file this function did not read.
            source_key=settings.PIE_CORPUS.name,
            filename=settings.PIE_CORPUS.name,
            content_type="text/csv",
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            content=raw,
            uploaded_by=actor,
        ))
        # The catalogue the seed belongs to, defined if it is not, and given the
        # pack the shipped corpus is decoded through where none is chosen.
        # `pack_for` resolves an *identifier* against what this engine ships,
        # so the id is what is stored.
        row = catalogue_row(session, org, first.connection_id, DEFAULT_CATALOGUE)
        if row is None:
            row = models.CompanyCatalogue(
                organization_id=org, connection_id=first.connection_id,
                catalogue_key=DEFAULT_CATALOGUE, name="")
            session.add(row)
        if pack_for(row) is None:
            row.pack_choice = settings.PIE_PACK.name
        session.flush()
        log.info("seeded the shipped corpus to company %s of organization %s",
                 first.connection_id, org)
        out.append({"organization_id": org, "connection_id": first.connection_id})
    return out


def _adopt_legacy_file(connection_id: str, row: Any) -> None:
    """Move a catalogue built before catalogues had directories into place.

    Before a company could keep several, its one file was
    ``catalogues/<connection_id>/products.jsonl``. Moved rather than rebuilt:
    the bytes are the ones the row's stamp describes, a move costs nothing
    where a rebuild costs a parse per company at boot, and the retrieval index
    beside it is derived from those same bytes so it moves too. Only when the
    new place is empty — a file already built there is newer knowledge.
    """
    root = settings.PIE_CATALOG.parent / "catalogues" / connection_id
    legacy = root / "products.jsonl"
    target = company_catalog_path(connection_id, DEFAULT_CATALOGUE)
    if not legacy.is_file() or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    for name in ("products.jsonl", "products.quarantine.jsonl", "retrieval.jsonl"):
        if (root / name).is_file():
            os.replace(root / name, target.parent / name)
    if row.built_at is not None:
        write_sidecar(connection_id, DEFAULT_CATALOGUE, row.name, row.pack_choice,
                      row.built_at, {k: getattr(row, k) for k in STAMP_FIELDS
                                     if getattr(row, k, None) is not None},
                      row.records)
    log.info("adopted the pre-catalogue file for company %s into %s",
             connection_id, target.parent)


def ensure_company_catalogues(session: Any,
                              actor: str = "auto-build") -> List[Dict[str, Any]]:
    """Build any catalogue whose corpus is on record but whose file is not.

    The catalogue is derived: the corpus rows are durable and the JSONL is not,
    so a redeploy onto a container with a fresh disk arrives with every company
    holding a corpus and no catalogue. Rebuilding here is what makes that a
    two-second start-up cost per catalogue rather than an administrator
    noticing, days later, that resolution has quietly stopped.

    Honours ``AUTO_BUILD_CATALOG``: with it off, a catalogue reports NOT BUILT
    and somebody builds it from the screen. Never falls back to another
    company's catalogue — that is the one outcome worse than an empty screen.

    **Commits per catalogue**, which is unusual for a function taking a session
    and is CLAUDE.md §4's rule rather than an exception to it: this is a
    long-running write, a build is its natural boundary, and holding one
    transaction across several two-second parses is exactly the shape that made
    ``/api/health`` answer "database is locked" during a sync. It also means a
    catalogue that fails to build does not roll back the ones that succeeded.
    """
    from .domain import models

    from sqlalchemy import select

    if not settings.AUTO_BUILD_CATALOG:
        return []

    out: List[Dict[str, Any]] = []
    for org, connection_id, catalogue_key in session.execute(
            select(models.CompanyCorpus.organization_id,
                   models.CompanyCorpus.connection_id,
                   models.CompanyCorpus.catalogue_key)
            .where(models.CompanyCorpus.superseded_at.is_(None))
            .distinct()):
        row = catalogue_row(session, org, connection_id, catalogue_key)
        if catalogue_key == DEFAULT_CATALOGUE and row is not None:
            _adopt_legacy_file(connection_id, row)
        if company_catalog_path(connection_id, catalogue_key).exists():
            continue
        pack = pack_for(row)
        if pack is None:
            log.info("catalogue %s of company %s has files but no pack this engine "
                     "ships; it is NOT BUILT until one is chosen",
                     catalogue_key, connection_id)
            continue
        try:
            build_for_company(session, org, connection_id, pack, actor=actor,
                              catalogue_key=catalogue_key)
            session.commit()
        except Exception:  # noqa: BLE001 — one catalogue must not stop the boot
            log.exception("could not build catalogue %s for company %s",
                          catalogue_key, connection_id)
            session.rollback()
            continue
        log.info("built catalogue %s for company %s", catalogue_key, connection_id)
        out.append({"organization_id": org, "connection_id": connection_id,
                    "catalogue_key": catalogue_key})
    return out
