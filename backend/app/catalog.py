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
    # Which headers the corpus uses is the *pack's* to say — it is a fact about
    # one organisation's export, and pie-parser's org layer declares it. These
    # three literals were duplicated here and in `tools/run_parser.py`, so a
    # second distributor's corpus needed the same edit made twice, in two
    # repositories, and one of them would eventually be missed.
    #
    # `getattr` rather than a plain attribute read: the two repositories version
    # independently, `PIE_PARSER_ROOT` is a pinned checkout, and a portal that
    # crashed on a slightly older engine would be a worse failure than one that
    # falls back to the values that engine was using anyway.
    columns = getattr(pack, "columns", None) or {
        "record_id": "MM#", "description": "Material Description",
        "grade": "Grade"}
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


def current_corpus(session: Any, org: str, connection_id: str) -> Any:
    """The corpus a company would build from, or None if it has uploaded none.

    The newest row that has not been superseded. Scoped to the organization in
    the query rather than checked after it, like ``_skip_rows`` in the data
    router: a connection id from another tenant must read as "no corpus" rather
    than as a permission error that confirms one exists.
    """
    from sqlalchemy import select

    from .domain import models

    return session.scalar(
        select(models.CompanyCorpus)
        .where(models.CompanyCorpus.organization_id == org,
               models.CompanyCorpus.connection_id == connection_id,
               models.CompanyCorpus.superseded_at.is_(None))
        .order_by(models.CompanyCorpus.uploaded_at.desc())
        .limit(1))


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
    """
    from .domain import models

    row = session.get(models.CompanyCatalogue, (org, connection_id))
    corpus = current_corpus(session, org, connection_id)
    path = company_catalog_path(connection_id)
    on_disk = path.exists()

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
        "corpus": ({
            "corpus_id": corpus.corpus_id,
            "filename": corpus.filename,
            "size_bytes": corpus.size_bytes,
            "sha256": corpus.sha256,
            "uploaded_at": clock.iso(clock.aware(corpus.uploaded_at)),
            "uploaded_by": corpus.uploaded_by,
        } if corpus else None),
        "stale": bool(row and corpus and row.corpus_id != corpus.corpus_id),
    }


def build_for_company(session: Any, org: str, connection_id: str,
                      pack_path: Path, actor: Optional[str] = None) -> Dict[str, Any]:
    """Decode this company's uploaded corpus through its chosen pack.

    The corpus is written to a temporary file for the parse and removed after:
    pie-parser's ``CsvAdapter`` reads a path, and the durable copy is the row —
    materialising it beside the output would put a second source of truth on
    the disk that §1.1 of the design says cannot be trusted to survive.

    Stores the run report and the stamp as a row rather than a sidecar file,
    for the same reason. Raises ``FileNotFoundError`` when the company has
    uploaded nothing, which is a state to report rather than an error to log.
    """
    import tempfile

    from .domain import models

    corpus = current_corpus(session, org, connection_id)
    if corpus is None:
        raise FileNotFoundError(
            "This company has no item-master export on file. Upload one "
            "before building its catalogue.")

    out = company_catalog_path(connection_id)
    with _build_lock:
        tmpdir = tempfile.mkdtemp(prefix="pie-corpus-")
        tmp_corpus = Path(tmpdir) / (corpus.filename or "corpus.csv")
        try:
            tmp_corpus.write_bytes(corpus.content)
            result = run_parse(tmp_corpus, pack_path, out)
        finally:
            # The bytes are in the row; nothing is lost by removing them here,
            # and leaving a tenant's item master in /tmp is a disclosure.
            tmp_corpus.unlink(missing_ok=True)
            Path(tmpdir).rmdir()

    row = session.get(models.CompanyCatalogue, (org, connection_id))
    if row is None:
        row = models.CompanyCatalogue(organization_id=org,
                                      connection_id=connection_id)
        session.add(row)
    row.corpus_id = corpus.corpus_id
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


def validate_corpus(raw: bytes, pack_path: Optional[Path]) -> Optional[str]:
    """Why these bytes are not a usable item-master export, or None.

    Checked here rather than in the router, which stays a mapping layer (§3),
    and *before* the row is written rather than at build time: an upload that
    is accepted and then fails to build leaves the company holding a corpus it
    cannot use and no message saying why.

    Three things, in the order they stop being cheap:

    * it decodes as UTF-8 — a mojibaked master is unusable and the failure is
      otherwise a confusing parse error thousands of rows in;
    * it has a header row at all;
    * where the company has already chosen a pack, that pack's mapped columns
      are present, and the message names the missing one. The pack declares
      which headers it reads, so this asks the pack rather than restating the
      three column names it happens to use today.
    """
    if not raw.strip():
        return "The file is empty."
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ("The file is not UTF-8 text. Export it as UTF-8 CSV — an "
                "item master in another encoding decodes to the wrong "
                "characters rather than failing outright.")

    import csv as _csv
    import io as _io

    reader = _csv.reader(_io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return "The file has no header row."
    if not any(header):
        return "The first row is empty, so there are no column names to read."

    if pack_path is None:
        return None
    try:
        root = str(settings.PIE_PARSER_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from engine.pack import load_pack  # noqa: PLC0415

        columns = getattr(load_pack(pack_path), "columns", None) or {}
    except Exception:  # noqa: BLE001 — an unreadable pack is not the file's fault
        log.warning("could not read columns from %s; accepting the upload "
                    "without a column check", pack_path, exc_info=True)
        return None

    for role in ("record_id", "description"):
        wanted = columns.get(role)
        if wanted and wanted not in header:
            return (f"This pack reads the {role.replace('_', ' ')} from a "
                    f"column called {wanted!r}, which this file does not have. "
                    f"Its columns are: {', '.join(h for h in header if h)}.")
    return None


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
