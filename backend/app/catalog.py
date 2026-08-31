"""Locate, build and describe the decoded PIE product catalogue.

The catalogue is ``products.jsonl`` produced by pie-parser's ``run_parser`` over
the Kennametal/WIDIA nomenclature corpus. It is large (~13 MB) and deterministic,
so it is gitignored and (re)built from the pinned pie-parser submodule instead
of being committed.

**Two catalogues live here, and only one of them answers resolution today.**

The *deployment-wide* one — ``settings.PIE_CATALOG``, built from the corpus that
ships in the pinned submodule — is what every organization still resolves
against. It is unchanged.

The *per-company* one is the half below the divider: each connected company
decodes its own uploaded item-master export through its own chosen org-layer
pack, into ``data/catalogues/<connection_id>/``. Nothing resolves against it
yet. The cutover — a quote naming its company, the resolution API gaining its
refusal, and the deployment default being removed — is a separate change, so
that this one cannot regress a running deployment.

``docs/per-company-catalogues.md`` is the design and the sequencing.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
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


def report_path(catalog: Optional[Path] = None) -> Path:
    """Where a build's run report lives, beside the catalogue it describes."""
    return (catalog or settings.PIE_CATALOG).with_suffix(".run_report.json")


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
    """Whether a catalogue *could* be built here, and if not, exactly why.

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


def catalog_state() -> Dict[str, Any]:
    """The catalogue as it sits on disk — existence, provenance, and the
    parser's own run report. Everything here is read, never computed: the
    censuses and parse rates come from the report ``build_catalog`` stored,
    and the stamp from the records themselves.

    ``exists: False`` is its own state, not a zero — a missing catalogue says
    nothing about coverage, which is the distinction
    ``pie_service.catalog_available`` exists to keep.
    """
    out = settings.PIE_CATALOG
    state: Dict[str, Any] = {
        "path": str(out),
        "scope": "deployment",
        "source": source_state(),
        "exists": out.exists(),
        "records": None,
        "built_at": None,
        "size_bytes": None,
        "stamp": {},
        "build": None,
        "report": None,
        "report_missing": None,
    }
    if not state["exists"]:
        return state

    state["size_bytes"] = out.stat().st_size
    state["stamp"] = catalog_stamp(out)

    sidecar = report_path(out)
    meta: Optional[Dict[str, Any]] = None
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("run report at %s is unreadable", sidecar)
    if meta:
        build = meta.get("build") or {}
        state["build"] = build
        state["report"] = meta.get("report")
        state["records"] = build.get("emitted")
        state["built_at"] = build.get("built_at")
    else:
        # A catalogue from before the report was kept beside it. The counters
        # cannot be reconstructed without re-parsing — which would be a second
        # parse-rate calculation — so say what is missing instead of guessing.
        state["report_missing"] = (
            "This catalogue was built before its run report was kept beside "
            "it, so rows read, parse rates and new tokens are not available. "
            "Rebuild to produce them."
        )
        with out.open("r", encoding="utf-8") as fh:
            state["records"] = sum(1 for line in fh if line.strip())
        state["built_at"] = clock.iso(
            datetime.fromtimestamp(out.stat().st_mtime, tz=timezone.utc))
    return state


def build_catalog(force: bool = False) -> Path:
    """Build products.jsonl from the pie-parser corpus. Returns its path.

    Also writes the parser's ``RunReport`` (censuses, parse rates, flags, new
    tokens) plus build metadata to :func:`report_path` — surfaced, never
    recomputed, by :func:`catalog_state`. The catalogue itself is written to a
    temporary file and renamed into place, so a process reading it mid-rebuild
    sees the old complete file, never a truncated one.

    Synchronous by measurement, not assumption: the full 6,717-row corpus
    parses and writes in under two seconds in-process, so there is no job to
    watch and no database transaction to keep short (§4 concerns writes the
    database serves during; this writes one file).
    """
    out = settings.PIE_CATALOG
    with _build_lock:
        if out.exists() and not force:
            return out
        source = source_state()
        if not source["available"]:
            raise FileNotFoundError(source["reason"])

        result = run_parse(settings.PIE_CORPUS, settings.PIE_PACK, out)
        sidecar = {
            "report": result["report"],
            "stamp": result["stamp"],
            "build": {
                "built_at": clock.iso(clock.now()),
                "duration_s": result["duration_s"],
                "rows_read": result["rows_read"],
                "emitted": result["records"],
                "quarantined": result["quarantined"],
                "corpus": str(settings.PIE_CORPUS),
                "corpus_fingerprint": result["corpus_fingerprint"],
                "pack": str(settings.PIE_PACK),
            },
        }
        report_path(out).write_text(
            json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")
        return out


def run_parse(corpus: Path, pack_path: Path, out: Path) -> Dict[str, Any]:
    """Decode one corpus through one pack into one JSONL. The single parse.

    Both callers reach the pipeline through here — the deployment-wide
    :func:`build_catalog` and the per-company :func:`build_for_company` — for
    the reason CLAUDE.md §2 gives: a second invocation of the same pipeline is
    the semantic duplication that drifts, and the two would eventually disagree
    about the payload gate or the atomic write.

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


def ensure_catalog() -> Path:
    """Return the catalogue path, building it if allowed and missing."""
    out = settings.PIE_CATALOG
    if out.exists():
        return out
    if settings.AUTO_BUILD_CATALOG:
        return build_catalog()
    raise FileNotFoundError(
        f"PIE catalogue not found at {out} and AUTO_BUILD_CATALOG is off. "
        "Run scripts/build_catalog.py."
    )


# ── per connected company ────────────────────────────────────────────────────
#
# Everything above this line is the deployment-wide catalogue, which still
# answers resolution. Everything below is the per-company one, which does not
# yet — the cutover is its own change, so nothing here can regress a running
# deployment. `docs/per-company-catalogues.md` §8 is the sequencing.


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
    as a zero is the benign default §1 forbids. The same rule the
    deployment-wide state already follows, per company.

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
