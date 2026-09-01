"""Locate, build and describe each company's decoded PIE product catalogue.

A catalogue is ``products.jsonl`` produced by pie-parser's ``run_parser`` over
one company's item-master export. It is large (~13 MB) and deterministic, so it
is gitignored and rebuilt from the corpus rather than committed.

**One catalogue per connected company, and no deployment-wide default.** Each
company decodes its own uploaded export through its own chosen org-layer pack,
into ``data/catalogues/<connection_id>/``. A company that has uploaded nothing
has no catalogue and resolves nothing — which is the honest answer, and the one
thing that could be worse is answering from another company's.

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
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import clock
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
# seed corpus is present. Everything below belongs to one connected company.


#: What an uploaded corpus may weigh. The shipped corpus is 6,717 rows and a
#: couple of megabytes; this is generous against that and still small enough
#: that the row, the parse and the request are all bounded. Rejected *before*
#: the bytes are read into memory, not after.
MAX_CORPUS_BYTES = 32 * 1024 * 1024

#: How many files one company may keep as sources at once. A ceiling rather
#: than a limit anyone should reach: a build reads every one of them on every
#: rebuild, and a company with fifty price lists has a data problem the
#: catalogue cannot fix. Refused by name, so the answer says what to do
#: (replace a source, or remove one) instead of failing at build time.
MAX_SOURCES = 20


def company_catalog_path(connection_id: str) -> Path:
    """Where one company's decoded JSONL lives.

    Derived and rebuildable: the corpus row is the source of truth, so losing
    this file costs a rebuild rather than the data. That is the property §1.1
    of the design doc exists to preserve.
    """
    return settings.PIE_CATALOG.parent / "catalogues" / connection_id / "products.jsonl"


def available_packs() -> List[Dict[str, str]]:
    """The org-layer packs the pinned engine ships, for a company to choose from.

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


def pack_for(connection: Any) -> Optional[Path]:
    """The pack this company decodes through, or None if it has not chosen one.

    Stored on ``ZohoConnection.config["pie_pack"]`` as an *identifier*, resolved
    to a path here against what the engine ships. An id naming a pack this
    engine does not have resolves to None rather than to a guess: the pin can
    move under a stored choice, and answering with a different pack than the one
    recorded would make the stamp a lie.
    """
    chosen = ((connection.config or {}).get("pie_pack") or "").strip()
    if not chosen:
        return None
    for pack in available_packs():
        if pack["id"] == chosen:
            return Path(pack["path"])
    return None


def current_corpora(session: Any, org: str, connection_id: str) -> List[Any]:
    """Every file this company's catalogue would be built from, oldest first.

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
               models.CompanyCorpus.superseded_at.is_(None))
        .order_by(models.CompanyCorpus.uploaded_at.asc(),
                  models.CompanyCorpus.corpus_id.asc())))


def current_corpus(session: Any, org: str, connection_id: str) -> Any:
    """The newest of a company's sources, or None if it has uploaded none.

    Still meaningful with several: it is the file whose arrival a screen dates
    the export by, and the one a collision resolves in favour of. What it is
    *not* any more is the whole of what the catalogue was built from — that is
    :func:`current_corpora`, and the digest over it.
    """
    sources = current_corpora(session, org, connection_id)
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
    "out of date" answerable once a company has several files: adding a source,
    replacing one and removing one all move this hash, and none of the three is
    visible in a single ``corpus_id``.
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
    column names this pack declares — so a company can build from an item
    master calling the part number ``MM#`` and a price list calling it
    ``Part No`` without either file being edited.

    *Streaming*: each source is read row by row and written straight to ``out``.
    Materialising them cost 394 MB of resident memory for one 33 MB file (see
    ``ingestion.item_master.Table``), and a company may keep twenty.

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


def company_catalog_state(session: Any, org: str, connection_id: str) -> Dict[str, Any]:
    """One company's catalogue: what is on disk, what built it, and what is missing.

    ``exists: False`` is its own state and ``records`` stays ``None`` — a
    company with no catalogue says nothing about coverage, and rendering that
    as a zero is the benign default §1 forbids.

    ``stale`` is the one genuinely new fact: a catalogue built from a corpus
    that has since been superseded is still a real catalogue with a real stamp,
    but it is no longer built from what the company last uploaded. Saying so is
    the difference between "out of date" and "wrong", and only one of them is
    urgent.

    With several sources, staleness is a comparison of *digests over the set* —
    a source added or removed changes what a build would read while leaving the
    newest ``corpus_id`` untouched, so the old single-id comparison would have
    called that catalogue current. It is kept as the fallback for a row built
    before the digest column existed, where it is still the honest answer.
    """
    from .domain import models

    row = session.get(models.CompanyCatalogue, (org, connection_id))
    sources = current_corpora(session, org, connection_id)
    corpus = sources[-1] if sources else None
    path = company_catalog_path(connection_id)
    on_disk = path.exists()

    if not row or not sources:
        stale = False
    elif row.corpus_digest:
        stale = row.corpus_digest != sources_digest(sources)
    else:
        stale = row.corpus_id != corpus.corpus_id

    return {
        "connection_id": connection_id,
        "scope": "company",
        "exists": bool(row) and on_disk,
        # A row without its file is a rebuild waiting to happen, not a
        # catalogue. Named rather than silently treated as absent, because the
        # two have different fixes and only this one is free.
        "built_but_missing_on_disk": bool(row) and not on_disk,
        "records": row.records if (row and on_disk) else None,
        "rows_read": row.rows_read if row else None,
        "quarantined": row.quarantined if row else None,
        "duration_s": row.duration_s if row else None,
        "built_at": clock.iso(clock.aware(row.built_at)) if row else None,
        "built_by": row.built_by if row else None,
        "pack": row.pack if row else None,
        "report": row.report if row else None,
        "stamp": ({k: getattr(row, k) for k in STAMP_FIELDS
                   if getattr(row, k, None) is not None} if row else {}),
        # What merging the sources did, and — for a built catalogue — which
        # files it read. Kept out of `report`, which is the parser's own dict
        # served verbatim and must not gain fields the parser did not write.
        "ingest": row.ingest if row else None,
        "built_from": row.sources if row else None,
        # The newest source, kept under its original name: a screen dates a
        # company's export by the file that arrived last, and one caller
        # (`master_health`) still asks for one file rather than the set.
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


def build_for_company(session: Any, org: str, connection_id: str,
                      pack_path: Path, actor: Optional[str] = None) -> Dict[str, Any]:
    """Decode every file this company has uploaded, through its chosen pack.

    The merged corpus is written to a temporary file for the parse and removed
    after: pie-parser's ``CsvAdapter`` reads a path, and the durable copies are
    the rows — materialising them beside the output would put a second source
    of truth on the disk that §1.1 of the design says cannot be trusted to
    survive.

    Stores the run report and the stamp as a row rather than a sidecar file,
    for the same reason. Raises ``FileNotFoundError`` when the company has
    uploaded nothing, which is a state to report rather than an error to log,
    and ``ingestion.item_master.ItemMasterError`` when one of its files can no
    longer be read as a table — named per file, because "the build failed" over
    six sources is not something a person can act on.
    """
    import tempfile

    from .domain import models

    sources = current_corpora(session, org, connection_id)
    if not sources:
        raise FileNotFoundError(
            "This company has no item-master export on file. Upload one "
            "before building its catalogue.")

    out = company_catalog_path(connection_id)
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

    row = session.get(models.CompanyCatalogue, (org, connection_id))
    if row is None:
        row = models.CompanyCatalogue(organization_id=org,
                                      connection_id=connection_id)
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
    return company_catalog_state(session, org, connection_id)


def prepare_source(raw: bytes, filename: str = "", content_type: str = "",
                   mapping: Optional[Dict[str, Any]] = None,
                   ) -> tuple[Dict[str, Optional[str]], Dict[str, Any]]:
    """Read an upload as a table, settle its column mapping, and say what it holds.

    Called *before* the row is written rather than at build time: an upload that
    is accepted and then fails to build leaves the company holding a file it
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
             sample_rows: int = 0) -> Dict[str, Any]:
    """Try every shipped pack against a sample of this company's files.

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
    sources = current_corpora(session, org, connection_id)
    if not packs:
        return {"available": False, "reason": source_state()["reason"] or (
            "This engine ships no org-layer packs."), "packs": []}
    if not sources:
        return {"available": False, "reason": (
            "This company has no item-master export on file yet, so there is "
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
                    log.info("pack %s could not decode %s: %s",
                             pack["id"], connection_id, e)
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
    becomes the first company's corpus, once, and the pack it was decoded
    through becomes that company's pack.

    Deliberately conservative, because it writes on somebody's behalf:

    * only an organization that has **no corpus row at all** is seeded, so a
      company that has uploaded its own export is never handed a different
      manufacturer's item master;
    * only its **first enabled** company — ``list_connections`` orders oldest
      first — because which of three legal entities sells this catalogue's
      product is a question this function cannot answer, and guessing three
      times is worse than guessing once;
    * an existing ``pie_pack`` choice is left alone;
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
        # The pack the shipped corpus is decoded through, and only where the
        # company has not chosen one. `pack_for` resolves an *identifier*
        # against what this engine ships, so the id is what is stored.
        if pack_for(first) is None:
            config = dict(first.config or {})
            config["pie_pack"] = settings.PIE_PACK.name
            first.config = config
        session.flush()
        log.info("seeded the shipped corpus to company %s of organization %s",
                 first.connection_id, org)
        out.append({"organization_id": org, "connection_id": first.connection_id})
    return out


def ensure_company_catalogues(session: Any,
                              actor: str = "auto-build") -> List[Dict[str, Any]]:
    """Build any company catalogue whose corpus is on record but whose file is not.

    The catalogue is derived: the corpus row is durable and the JSONL is not,
    so a redeploy onto a container with a fresh disk arrives with every company
    holding a corpus and no catalogue. Rebuilding here is what makes that a
    two-second start-up cost rather than an administrator noticing, days later,
    that resolution has quietly stopped.

    Honours ``AUTO_BUILD_CATALOG``: with it off, a company reports NOT BUILT
    and somebody builds it from the screen. Never falls back to another
    company's catalogue — that is the one outcome worse than an empty screen.

    **Commits per company**, which is unusual for a function taking a session
    and is CLAUDE.md §4's rule rather than an exception to it: this is a
    long-running write, a company is its natural boundary, and holding one
    transaction across several two-second parses is exactly the shape that made
    ``/api/health`` answer "database is locked" during a sync. It also means a
    company that fails to build does not roll back the ones that succeeded.
    """
    from .domain import models

    from sqlalchemy import select

    if not settings.AUTO_BUILD_CATALOG:
        return []

    out: List[Dict[str, Any]] = []
    for org, connection_id in session.execute(
            select(models.CompanyCorpus.organization_id,
                   models.CompanyCorpus.connection_id)
            .where(models.CompanyCorpus.superseded_at.is_(None))
            .distinct()):
        if company_catalog_path(connection_id).exists():
            continue
        connection = session.get(models.ZohoConnection, connection_id)
        pack = pack_for(connection) if connection is not None else None
        if pack is None:
            log.info("company %s has a corpus but no pack this engine ships; "
                     "its catalogue is NOT BUILT until one is chosen",
                     connection_id)
            continue
        try:
            build_for_company(session, org, connection_id, pack, actor=actor)
            session.commit()
        except Exception:  # noqa: BLE001 — one company must not stop the boot
            log.exception("could not build the catalogue for company %s",
                          connection_id)
            session.rollback()
            continue
        log.info("built the catalogue for company %s", connection_id)
        out.append({"organization_id": org, "connection_id": connection_id})
    return out
