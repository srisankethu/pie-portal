"""Locate, build and describe each company's decoded PIE product catalogue.

A catalogue is ``products.jsonl`` produced by pie-parser's ``run_parser`` over
one company's item-master export. It is large (~13 MB) and deterministic, so it
is gitignored and rebuilt from the corpus rather than committed.

**Catalogues belong to a connected company, every price list carries its own
decoding config, and there is no default decoder.** A company keeps one
catalogue *per manufacturer it sells*, each into
``data/catalogues/<connection_id>/<catalogue_key>/``; each price list uploaded
into a catalogue is analysed on its own to find how it is read and which of
the engine's shipped rule sets decodes it, a person saves that decoding
config, and the build decodes each file through its own. The company resolves
against the **union** of its built catalogues, written beside them under
``_union/``. A company that has uploaded nothing has no catalogue and resolves
nothing — which is the honest answer, and the one thing that could be worse is
answering from another company's.

The corpus that ships inside the pinned submodule (``settings.PIE_CORPUS``) is
the **seed**, not a runtime fallback: on start-up :func:`seed_company_catalogues`
gives it to each organization's first company where that company has no export
of its own, with the decoding config it ships with (``settings.PIE_PACK`` is
the rule set written against exactly that file). Nothing else reads either.

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
        # The rule set the seed is decoded through — the one written against
        # that file. Not a default for anything uploaded.
        "seed_rule_set": str(settings.PIE_PACK),
    }


def rule_set_columns(rule_set: Path) -> Dict[str, str]:
    """What column names this rule set's own pipeline expects.

    These are the headers the normalisation writes, not the headers an uploaded
    file has: a file keeps its own, and its decoding config says which of them
    fills each role. Asked here rather than restated at each caller: these three literals were once
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

    return getattr(load_pack(rule_set), "columns", None) or {
        "record_id": "MM#", "description": "Material Description",
        "grade": "Grade"}


def run_parse(corpus: Path, rule_set: Path, out: Path) -> Dict[str, Any]:
    """Decode one corpus through one rule set into one JSONL. The single parse.

    Every build reaches the pipeline through here, for the reason CLAUDE.md §2
    gives: a second invocation of the same pipeline is the semantic duplication
    that drifts, and the two would eventually disagree about the payload gate
    or the atomic write. It is deliberately company- and file-blind — it takes
    paths, not a connection and not a corpus row — so the one place that knows
    whose file this is and which config decodes it is
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
    pack = load_pack(rule_set)
    # Which headers this corpus uses is the rule set's to say, and
    # `rule_set_columns` is where that is asked — the same answer the
    # normalisation that produced this corpus was written against. Loading the
    # rule set twice costs milliseconds against a parse measured in seconds;
    # two copies of the fallback literals would cost a corpus that normalises
    # to headers the parse then cannot find.
    columns = rule_set_columns(rule_set)
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
# and, within it, to one of that company's catalogues, and within that to one
# uploaded price list.
#
# **A company keeps a catalogue per manufacturer, and every price list carries
# its own decoding config.** A catalogue is a manufacturer's product universe:
# a set of uploaded price lists built into its own ``products.jsonl``. It owns
# no decoder. Each price list is read and decoded through a *decoding config*
# of its own — which of its columns hold the part number, description and
# grade, and which of the engine's shipped rule sets decodes its descriptions
# — proposed by analysing that file and saved by a person. There is no default
# rule set: a file without a saved config is not decoded, and a build says
# which file is missing one. A build decodes each file through its own config
# and merges the results; the **union** of a company's built catalogues is
# what that company resolves against.


#: What an uploaded corpus may weigh. The shipped corpus is 6,717 rows and a
#: couple of megabytes; this is generous against that and still small enough
#: that the row, the parse and the request are all bounded. Rejected *before*
#: the bytes are read into memory, not after.
MAX_CORPUS_BYTES = 32 * 1024 * 1024

#: How many files one catalogue may keep as sources at once. A ceiling rather
#: than a limit anyone should reach: a build decodes every one of them on
#: every rebuild, and a catalogue with fifty price lists has a data problem the
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
    """A catalogue could not be created, built or removed, with the sentence
    saying why.

    ``status`` is the HTTP status the router answers with, decided here where
    the reason is known rather than re-derived from the message: a name that
    yields no key is the caller's input (422), a key already in use, a company
    at its ceiling or a file with no decoding config is a conflict (409), a
    rule set the engine does not ship is a bad choice (400), a catalogue that
    is not there is not there (404).
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


# ── rule sets: what the engine ships, never what a tenant uploads ────────────


def available_rule_sets() -> List[Dict[str, str]]:
    """The rule sets the pinned engine ships, for a decoding config to name.

    A rule set is what pie-parser keeps as an org-layer pack under
    ``packs/org/<id>``: the routing, grammars, patterns and repairs that read
    how one kind of export phrases a description, over the shared nomenclature
    layer it names. In this codebase it is only ever *the decoder for a file*,
    which is why it is called that here rather than "pack".

    Shipped, never uploaded. A rule set is grammars and regexes the engine
    compiles and runs over every row of a corpus, so accepting one from a
    tenant means executing tenant-supplied patterns — a catastrophic-
    backtracking expression is then a denial of service somebody can upload for
    themselves, against a build that is a synchronous request. Listing what
    ships keeps that surface at zero. `docs/per-company-catalogues.md` §1.2
    has the argument and what an uploaded bundle would have to carry.
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


def rule_set_path(rule_set_id: Optional[str]) -> Optional[Path]:
    """A rule set identifier as a path, against what the engine ships — or None.

    An id naming a rule set this engine does not have resolves to None rather
    than to a guess: the pin can move under a stored choice, and answering
    with a different decoder than the one recorded would make the stamp a lie.
    """
    chosen = (rule_set_id or "").strip()
    if not chosen:
        return None
    for rule_set in available_rule_sets():
        if rule_set["id"] == chosen:
            return Path(rule_set["path"])
    return None


# ── the decoding config: how one price list is read and decoded ─────────────


def decoding_config(source: Any) -> Dict[str, Any]:
    """One price list's decoding config as the screen and the API see it.

    Two halves and a status. ``columns`` says which of the file's own columns
    are read; ``rule_set`` says which shipped rule set decodes the
    descriptions; ``analysis`` is the evidence the proposal rested on, kept so
    a person's choice can be checked against what they saw. ``ready`` is the
    one question a build asks: saved, with a rule set the engine still ships.
    A proposal that was never saved is not ready, however good it looked.
    """
    confirmed = getattr(source, "decoding_confirmed_at", None)
    rule_set = getattr(source, "rule_set", None) or None
    return {
        "columns": getattr(source, "mapping", None),
        "rule_set": rule_set,
        "rule_set_resolved": rule_set_path(rule_set) is not None,
        "analysis": getattr(source, "analysis", None),
        "confirmed_at": clock.iso(clock.aware(confirmed)) if confirmed else None,
        "confirmed_by": getattr(source, "decoding_confirmed_by", None) if confirmed else None,
        "ready": decoding_ready(source),
    }


def decoding_ready(source: Any) -> bool:
    """Whether this file can be decoded: a saved config naming a rule set the
    engine ships. Nothing else counts — not a proposal, not a rule set the
    pin no longer has."""
    return (getattr(source, "decoding_confirmed_at", None) is not None
            and getattr(source, "mapping", None) is not None
            and rule_set_path(getattr(source, "rule_set", None)) is not None)


def analyze_source(raw: bytes, filename: str = "", content_type: str = "",
                   columns: Optional[Dict[str, Any]] = None,
                   sample_rows: int = 0) -> Dict[str, Any]:
    """Work out how one uploaded file should be decoded, from the file alone.

    Every upload starts as an unknown format, so this is the discovery step:
    read the file as a table, settle which of its columns hold the part
    number, description and grade (suggested from the headers, or as given),
    and then run **every rule set the engine ships** over the first rows and
    report the parser's own counts for each — classified, quarantined, the
    per-family census. Nothing here is inherited from the company, the
    catalogue or a deployment default; a second file from the same
    manufacturer is analysed afresh, because its headers may not match and
    its phrasing may not either.

    **What it proposes, and what it refuses to.** A rule set is proposed only
    when exactly one of them classified any sampled row: that is the file's
    own evidence choosing. Where several read the file the counts are shown
    and a person chooses — ranking them by a number this module invented
    would be the second parse-rate calculation ``run_parse`` refuses to have.
    Where none reads it, the proposal is empty and the reason says so: the
    file needs a rule set nobody has written yet, and no amount of choosing
    from the menu fixes that. A proposal is never a config: it becomes one
    when a person saves it (``confirm_decoding``).

    Raises ``ingestion.item_master.ItemMasterError`` when the file cannot be
    read as a table or the columns cannot be identified, with the sentence
    written for the person who uploaded it.
    """
    import tempfile

    from .ingestion import item_master

    table = item_master.read_table(raw, filename, content_type)
    settled = dict(columns) if columns else item_master.suggest_mapping(table)
    item_master.check_mapping(table, settled)
    ingest = item_master.describe(table, settled)
    sample_rows = sample_rows or item_master.SAMPLE_ROWS

    rule_sets = available_rule_sets()
    candidates: List[Dict[str, Any]] = []
    if rule_sets:
        probe = _Probe(raw, filename, content_type, settled)
        with _build_lock:
            tmpdir = Path(tempfile.mkdtemp(prefix="pie-analyse-"))
            try:
                for rule_set in rule_sets:
                    path = Path(rule_set["path"])
                    try:
                        normalised_corpus(probe, path, tmpdir / "sample.csv",
                                          limit=sample_rows)
                        result = run_parse(tmpdir / "sample.csv", path,
                                           tmpdir / f"{rule_set['id']}.jsonl")
                    except Exception as e:  # noqa: BLE001 — one rule set failing is a result
                        # Reported against that rule set rather than raised: a
                        # rule set the engine ships but cannot run on this file
                        # is precisely what the analysis is for, and it must
                        # not hide the others.
                        log.info("rule set %s could not decode %s: %s",
                                 rule_set["id"], filename, e)
                        candidates.append({"rule_set": rule_set["id"],
                                           "error": f"{type(e).__name__}: {e}"})
                        continue
                    candidates.append({
                        "rule_set": rule_set["id"],
                        "rows_read": result["rows_read"],
                        "classified": result["records"],
                        "quarantined": result["quarantined"],
                        "report": result["report"],
                    })
            finally:
                # Nothing here is a catalogue: an analysis that left a
                # products.jsonl behind would be a company resolving against a
                # rule set nobody chose.
                for leftover in tmpdir.glob("*"):
                    leftover.unlink(missing_ok=True)
                tmpdir.rmdir()

    readers = [c["rule_set"] for c in candidates if c.get("classified")]
    if not rule_sets:
        reason = source_state()["reason"] or "This engine ships no rule sets."
    elif not readers:
        reason = ("No rule set this engine ships reads this file: none of them "
                  "classified a single sampled row. Its manufacturer needs a rule "
                  "set written before it can be decoded.")
    elif len(readers) > 1:
        reason = ("More than one rule set reads this file. Choose one on the "
                  "counts above; nothing here ranks them.")
    else:
        reason = None
    return {
        "columns": settled,
        "ingest": ingest,
        "sample_rows": sample_rows,
        "candidates": candidates,
        "proposed": readers[0] if len(readers) == 1 else None,
        "reason": reason,
    }


class _Probe:
    """An in-memory stand-in for a corpus row, so :func:`normalised_corpus`
    can read a file that has not been stored yet — the analysis runs before
    the row is written, on purpose, so a file that cannot be read is refused
    rather than stored and unusable."""

    def __init__(self, content: bytes, filename: str, content_type: str,
                 mapping: Dict[str, Any]) -> None:
        self.content = content
        self.filename = filename
        self.content_type = content_type
        self.mapping = mapping
        self.source_key = filename
        self.corpus_id = ""
        self.sha256 = hashlib.sha256(content).hexdigest()


def confirm_decoding(session: Any, source: Any, columns: Dict[str, Any],
                     rule_set_id: Optional[str], actor: Optional[str]) -> Any:
    """Save one file's decoding config: the columns a person confirmed and the
    rule set they chose. The step between "shown" and "decoded".

    Re-reads the stored bytes with the columns given and refuses one naming a
    column the file does not have, so a config that cannot build is never
    stored; refuses a rule set the engine does not ship for the same reason.
    ``rule_set_id`` may be empty — the honest state for a file no shipped rule
    set reads — and the config is then saved but not *ready*: the file's
    columns are on record, and the build names it as waiting for a rule set.

    The bytes are not touched and the source is not superseded: this changes
    how a file is *read*, and superseding it would say a different file had
    arrived.
    """
    from .ingestion import item_master

    table = item_master.read_table(source.content, source.filename,
                                   source.content_type or "")
    settled = dict(columns)
    item_master.check_mapping(table, settled)
    if rule_set_id and rule_set_path(rule_set_id) is None:
        known = ", ".join(r["id"] for r in available_rule_sets()) or "none"
        raise CatalogueError(
            f"No rule set called {rule_set_id!r} ships with this engine. "
            f"Available: {known}.", status=400)
    source.mapping = settled
    source.ingest = item_master.describe(table, settled)
    source.rule_set = rule_set_id or None
    source.decoding_confirmed_at = clock.now()
    source.decoding_confirmed_by = actor
    _release(source)
    session.flush()
    return source


def rule_sets_in_use(session: Any, org: str) -> List[str]:
    """Every rule set a saved decoding config in this organization names,
    across every company and catalogue. The vocabulary a margin policy is
    validated against is the union of what these declare."""
    from sqlalchemy import select

    from .domain import models

    rows = session.scalars(
        select(models.CompanyCorpus.rule_set)
        .where(models.CompanyCorpus.organization_id == org,
               models.CompanyCorpus.superseded_at.is_(None),
               models.CompanyCorpus.decoding_confirmed_at.is_not(None),
               models.CompanyCorpus.rule_set.is_not(None))
        .distinct())
    return sorted({r for r in rows if r})


# ── catalogues: one per manufacturer ─────────────────────────────────────────


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

    return session.get(models.CompanyCatalogue, (org, connection_id, catalogue_key))


def create_catalogue(session: Any, org: str, connection_id: str, name: str) -> Any:
    """Define a new catalogue for this company: a manufacturer, by name.

    Nothing is uploaded or built here, and nothing about decoding is decided:
    a catalogue is the manufacturer's product universe, and each price list
    uploaded into it brings its own decoding config. Refuses a name that
    yields no key, a key the company already uses, and a company at its
    ceiling — each with the sentence a person can act on.
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
    row = models.CompanyCatalogue(
        organization_id=org, connection_id=connection_id, catalogue_key=key,
        name=(name or "").strip()[:255])
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
        write_sidecar(connection_id, catalogue_key, row)
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


# ── price lists: the files a catalogue is built from ─────────────────────────


def current_corpora(session: Any, org: str, connection_id: str,
                    catalogue_key: str = DEFAULT_CATALOGUE) -> List[Any]:
    """Every file one catalogue would be built from, oldest first.

    Oldest first because that is the order collisions are resolved in: the
    newest source wins, so it is decoded last and read first by the merge.

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
    """A hash over the set of files a build would read, and how each is decoded.

    Over each source's key, content digest and rule set, sorted, so it is a
    property of the *set* rather than of the order it was assembled in. This
    is what makes "out of date" answerable once a catalogue has several files:
    adding a source, replacing one, removing one and changing one's rule set
    all move this hash, and none of the four is visible in a single
    ``corpus_id``.
    """
    parts = sorted(f"{source_key_of(s)}:{s.sha256}:{getattr(s, 'rule_set', '') or ''}"
                   for s in sources)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _release(source: Any) -> None:
    """Forget one source's bytes, so a build holds one file rather than all.

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


def normalised_corpus(source: Any, rule_set: Path, out: Path,
                      limit: Optional[int] = None) -> Dict[str, Any]:
    """Write one file, read through its own columns, as the CSV its rule set parses.

    *Normalisation* is through the mapping stored with the file, into the
    column names this rule set declares — so a price list calling the part
    number ``Part No`` decodes without being edited. *Streaming*: the file is
    read row by row and written straight to ``out``; materialising it cost
    394 MB of resident memory for one 33 MB file (see
    ``ingestion.item_master.Table``).

    One file, deliberately. A catalogue used to be merged as CSV first and
    parsed once through one rule set; now each file is decoded through its own
    rule set, so the merge happens *after* the parse, on decoded records
    (:func:`_merge_decoded`), where the collision rule lives once for files
    and for catalogues alike.

    ``limit`` reads only the first rows, for the analysis.
    """
    from .ingestion import item_master

    headers = rule_set_columns(rule_set)
    order = [headers.get(role) or role for role in item_master.ROLES]

    emitted = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(order)
        # Named per file. "The build failed" over six sources is not something
        # a person can act on, and the reason a file has stopped being readable
        # is usually specific to that file — a mapping whose column was renamed
        # in a re-upload, a workbook saved in an older format.
        try:
            table = item_master.read_table(
                source.content, source.filename, source.content_type or "")
            mapping = (getattr(source, "mapping", None)
                       or item_master.suggest_mapping(table))
            report = item_master.ingest_report(table, mapping)
            # Streamed, not collected: `list()` around this generator would
            # put the whole file back in memory, which is the one thing this
            # function exists to avoid.
            for row in item_master.emit_rows(table, mapping, report, limit=limit):
                writer.writerow(row)
                emitted += 1
        except item_master.ItemMasterError as e:
            raise item_master.ItemMasterError(
                f"{source.filename or source_key_of(source)}: {e}") from e
    report["rows_emitted"] = emitted
    report["source_key"] = source_key_of(source)
    report["filename"] = source.filename
    report["sha256"] = source.sha256
    # Released as soon as this file has been written out. `content` is
    # deferred, so it arrived on demand a moment ago; without this a session
    # would hold every source's bytes at once by the end of a build.
    _release(source)
    return report


def company_catalog_state(session: Any, org: str, connection_id: str,
                          catalogue_key: str = DEFAULT_CATALOGUE) -> Dict[str, Any]:
    """One catalogue: what is on disk, what built it, and what is missing.

    ``exists: False`` is its own state and ``records`` stays ``None`` — a
    catalogue with no build says nothing about coverage, and rendering that
    as a zero is the benign default §1 forbids. A row that has never been
    built (``built_at`` null) is a *definition*: it reports its name and its
    files, and nothing a build would have stamped.

    ``decoding_ready`` is whether every file has a saved decoding config the
    engine can run; ``awaiting_decoding`` names the ones that do not, because
    "not built" is not useful advice to someone whose build would be refused
    by name.

    ``stale`` is the one genuinely new fact: a catalogue built from a corpus
    that has since been superseded is still a real catalogue with a real stamp,
    but it is no longer built from what was last uploaded. Saying so is the
    difference between "out of date" and "wrong", and only one of them is
    urgent. With several sources, staleness is a comparison of *digests over
    the set*; the single-id comparison is kept as the fallback for a row built
    before the digest column existed, where it is still the honest answer.
    """
    row = catalogue_row(session, org, connection_id, catalogue_key)
    built = row is not None and row.built_at is not None
    sources = current_corpora(session, org, connection_id, catalogue_key)
    corpus = sources[-1] if sources else None
    path = company_catalog_path(connection_id, catalogue_key)
    on_disk = path.exists()
    awaiting = [source_key_of(s) for s in sources if not decoding_ready(s)]

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
        "exists": built and on_disk,
        # A row without its file is a rebuild waiting to happen, not a
        # catalogue. Named rather than silently treated as absent, because the
        # two have different fixes and only this one is free.
        "built_but_missing_on_disk": built and not on_disk,
        "decoding_ready": bool(sources) and not awaiting,
        "awaiting_decoding": awaiting,
        "records": row.records if (built and on_disk) else None,
        "rows_read": row.rows_read if built else None,
        "quarantined": row.quarantined if built else None,
        "duration_s": row.duration_s if built else None,
        "built_at": clock.iso(clock.aware(row.built_at)) if built else None,
        "built_by": row.built_by if built else None,
        "report": row.report if built else None,
        "stamp": ({k: getattr(row, k) for k in STAMP_FIELDS
                   if getattr(row, k, None) is not None} if built else {}),
        # What merging the files did, and — for a built catalogue — which
        # files it decoded, each through which rule set, with each file's own
        # stamp, counts and report. Kept out of `report`, which is the
        # parser's own dict served verbatim and must not gain fields the
        # parser did not write.
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
            "ingest": getattr(s, "ingest", None),
            "decoding": decoding_config(s),
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


# ── the sidecar, and the merge shared by catalogues and the union ────────────


def _sidecar_path(connection_id: str, catalogue_key: str) -> Path:
    return catalogue_dir(connection_id, catalogue_key) / CATALOGUE_SIDECAR


def write_sidecar(connection_id: str, catalogue_key: str, row: Any) -> None:
    """What a built catalogue says about itself beside its file.

    The union is assembled from the disk alone — in the resolver, which has no
    session, and at start-up before any request — so the facts it needs
    (which catalogue this is, when it was built, what its files were decoded
    through and stamped with) travel with the file rather than only in the
    row.
    """
    payload = {
        "catalogue_key": catalogue_key,
        "name": row.name or "",
        "built_at": clock.iso(clock.aware(row.built_at)) if row.built_at else None,
        "stamp": {k: getattr(row, k) for k in STAMP_FIELDS
                  if getattr(row, k, None) is not None},
        "records": row.records or 0,
        "rule_sets": sorted({s.get("rule_set") for s in (row.sources or [])
                             if s.get("rule_set")}),
        # Every file's run id, so the union's version can move when any one
        # file of any catalogue is rebuilt from different bytes.
        "run_ids": sorted(f"{s.get('source_key')}:{(s.get('stamp') or {}).get('run_id') or ''}"
                          for s in (row.sources or [])),
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
        stamp = dict(meta.get("stamp") or catalog_stamp(products))
        out.append({
            "catalogue_key": entry.name,
            "name": str(meta.get("name") or ""),
            "built_at": meta.get("built_at") or clock.iso(
                clock.aware(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc))),
            "stamp": stamp,
            "rule_sets": list(meta.get("rule_sets") or []),
            "run_ids": list(meta.get("run_ids") or (
                [f":{stamp['run_id']}"] if stamp.get("run_id") else [])),
            "path": str(products),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    return out


def _merge_decoded(members: List[Dict[str, Any]], out: Path,
                   tag: str) -> Dict[str, Any]:
    """Merge decoded JSONL files into one, newest first, one record per part
    number per namespace — the collision rule, stated once.

    pie-parser's ``AuthoritativeIndex`` indexes identifiers per namespace and
    treats a duplicate inside one namespace as a collision that **never
    resolves** — so keeping the same part number from two files would not
    give a wrong answer, it would silently stop that part number resolving at
    all, which is a defect nobody would find by looking at record counts.
    ``members`` arrive newest first, so the first record seen for a key is the
    one kept: a later file, or a later build, is a later statement about the
    same product. Every collision is counted and the first of them named,
    because two documents disagreeing about one product is a real
    disagreement and the answer is to tell somebody, not to pick quietly.

    Each kept record is tagged with ``tag`` (``source_key`` for a catalogue's
    files, ``catalogue_key`` for a company's catalogues) — the portal's own
    provenance field on a decoded record, so a resolution can say where a
    record came from without a second map to keep in step with the file.
    Deterministic in the members, so an identical merge produces identical
    bytes.
    """
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from identity.store import record_namespace  # noqa: PLC0415
    except ImportError:  # the engine is absent: the same rule, stated once more
        def record_namespace(rec: Dict[str, Any]) -> str:  # type: ignore[misc]
            return str(rec.get("org_id") or rec.get("pack_id")
                       or rec.get("manufacturer") or rec.get("brand") or "")

    seen: set = set()
    duplicates: Dict[str, str] = {}
    kept_per_member: List[int] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for member in members:
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
                        duplicates.setdefault(key[1], member["key"])
                        continue
                    seen.add(key)
                    rec[tag] = member["key"]
                    fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":"))
                             + "\n")
                    kept += 1
            kept_per_member.append(kept)
    os.replace(tmp, out)
    return {"records": sum(kept_per_member), "kept": kept_per_member,
            "duplicates": len(duplicates),
            # A handful, named. The full list would be unbounded and the count
            # is what says whether this is a stray or a structural overlap.
            "duplicate_examples": sorted(duplicates)[:10]}


# ── the union: what a company resolves against ───────────────────────────────


@dataclass
class UnionCatalogue:
    """A company's built catalogues merged into the one file it resolves against."""

    path: Path
    #: What a resolution is stamped with: a hash over every member's key and
    #: the run ids of the files it was built from. A run id is pie-parser's
    #: own fingerprint of one decode — the input bytes plus the rule set's
    #: checksum — so this moves when any file of any catalogue is rebuilt from
    #: different bytes or through a different rule set, and stays put across
    #: an identical rebuild. The ruleset checksum alone would not do: it is the
    #: *rule set's* hash, the same for two companies decoding different item
    #: masters through ``zcnc``, and a resolution cache keyed on it would hand
    #: one company the other's answer. Empty when any member's run ids could
    #: not be read — and an empty version is never cached, which is the safe
    #: direction.
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
    parts: List[str] = []
    for m in members:
        run_ids = [r for r in m.get("run_ids") or [] if r and not r.endswith(":")]
        if not run_ids:
            return ""
        parts.extend(f"{m['catalogue_key']}:{r}" for r in run_ids)
    if not parts:
        return ""
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


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

    De-duplicated across catalogues by :func:`_merge_decoded`'s rule, the
    most recently *built* catalogue winning, with each record tagged
    ``catalogue_key``. A part number two manufacturers both use is not a
    duplicate: their records sit in different namespaces and the engine
    reports a bare lookup of it as ambiguous, which is the right answer.

    Rebuilt only when a member's file has changed (size or modification time),
    which the manifest beside the union records.
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
    # Newest build first, so the first record seen for a key is the one kept.
    # The key breaks a tie between two builds stamped the same instant, so the
    # outcome is a function of the members and never of directory order.
    ordered = sorted(members, key=lambda m: (m["built_at"] or "", m["catalogue_key"]),
                     reverse=True)
    merged = _merge_decoded(
        [{"key": m["catalogue_key"], "path": m["path"]} for m in ordered],
        out, tag="catalogue_key")
    per_catalogue = [{
        "catalogue_key": m["catalogue_key"],
        "name": m["name"],
        "rule_sets": m["rule_sets"],
        "built_at": m["built_at"],
        "records": kept,
        **{k: m["stamp"].get(k) for k in
           ("ruleset_checksum", "run_id", "pack_version", "org_id")
           if m["stamp"].get(k) is not None},
    } for m, kept in zip(ordered, merged["kept"])]
    per_catalogue.sort(key=lambda c: (c["catalogue_key"] != DEFAULT_CATALOGUE,
                                      c["catalogue_key"]))
    union = UnionCatalogue(
        path=out, version=_union_version(members), records=merged["records"],
        duplicates=merged["duplicates"],
        duplicate_examples=merged["duplicate_examples"],
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
             "%d duplicated across them", connection_id, union.records,
             len(members), union.duplicates)
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


# ── the build: each file through its own config, then merged ────────────────


def build_for_company(session: Any, org: str, connection_id: str,
                      actor: Optional[str] = None,
                      catalogue_key: str = DEFAULT_CATALOGUE) -> Dict[str, Any]:
    """Decode every file one catalogue holds, each through its own decoding
    config, merge the results, and refresh the union the company resolves
    against.

    **File + its decoding config → decoded records**, and nothing else. A file
    without a saved config is refused by name before anything is decoded —
    there is no default rule set to fall back to, and a build that quietly
    decoded a YG-1 price list through Kennametal's grammars would be a wrong
    catalogue with a real stamp.

    Each file is normalised to its rule set's columns in a temporary file and
    parsed on its own, so its stamp — ``run_id`` over its bytes and its rule
    set — is its own; the merge then keeps one record per part number
    (:func:`_merge_decoded`). The temporary files are removed after: the
    durable copies are the rows, and a tenant's item master left in /tmp is a
    disclosure.

    Stores the run report and the stamp as a row rather than a sidecar file.
    Raises :class:`CatalogueError` when the catalogue has no files or one of
    them has no saved decoding config, and
    ``ingestion.item_master.ItemMasterError`` when a file can no longer be
    read as a table — named per file, because "the build failed" over six
    sources is not something a person can act on.
    """
    import shutil
    import tempfile

    from .domain import models

    sources = current_corpora(session, org, connection_id, catalogue_key)
    if not sources:
        raise CatalogueError(
            "This catalogue has no price list or item-master export on file. "
            "Upload one before building it.")
    waiting = [s for s in sources if not decoding_ready(s)]
    if waiting:
        names = ", ".join(s.filename or source_key_of(s) for s in waiting)
        raise CatalogueError(
            f"Not decoded: {names} "
            f"{'has' if len(waiting) == 1 else 'have'} no saved decoding config. "
            f"Open the file's decoding, check the columns and the rule set the "
            f"analysis proposed, and save it — nothing is decoded through a "
            f"default.")

    out = company_catalog_path(connection_id, catalogue_key)
    started = time.perf_counter()
    per_file: List[Dict[str, Any]] = []
    with _build_lock:
        tmpdir = Path(tempfile.mkdtemp(prefix="pie-build-"))
        try:
            decoded: List[Dict[str, Any]] = []
            for source in sources:
                rule_set = rule_set_path(source.rule_set)
                target = tmpdir / f"{len(decoded)}.jsonl"
                ingest = normalised_corpus(source, rule_set, tmpdir / "corpus.csv")
                result = run_parse(tmpdir / "corpus.csv", rule_set, target)
                per_file.append({
                    "source_key": source_key_of(source),
                    "corpus_id": source.corpus_id,
                    "filename": source.filename,
                    "sha256": source.sha256,
                    "rule_set": source.rule_set,
                    "stamp": result["stamp"],
                    "records": result["records"],
                    "rows_read": result["rows_read"],
                    "quarantined": result["quarantined"],
                    "report": result["report"],
                    **{k: ingest[k] for k in ("rows_read", "rows_kept",
                                              "rows_skipped_blank_key", "sampled")
                       if k in ingest},
                    "rows_emitted": ingest["rows_emitted"],
                })
                decoded.append({"key": source_key_of(source), "path": str(target),
                                "quarantine": str(target.with_suffix(".quarantine.jsonl"))})
            # Newest file first: a later file is a later statement.
            merged = _merge_decoded(list(reversed(decoded)), out, tag="source_key")
            with out.with_suffix(".quarantine.jsonl").open("w", encoding="utf-8") as q:
                for d in decoded:
                    qpath = Path(d["quarantine"])
                    if qpath.exists():
                        q.write(qpath.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    row = catalogue_row(session, org, connection_id, catalogue_key)
    if row is None:
        row = models.CompanyCatalogue(organization_id=org,
                                      connection_id=connection_id,
                                      catalogue_key=catalogue_key)
        session.add(row)
    row.corpus_id = sources[-1].corpus_id
    row.corpus_digest = sources_digest(sources)
    row.sources = per_file
    row.ingest = {
        "sources": per_file,
        "rows_in": sum(f["rows_read"] for f in per_file),
        "rows_kept": merged["records"],
        "rows_skipped_blank_key": sum(f.get("rows_skipped_blank_key", 0)
                                      for f in per_file),
        "collisions": merged["duplicates"],
        "collision_examples": merged["duplicate_examples"],
        "sampled": False,
    }
    row.records = merged["records"]
    row.rows_read = sum(f["rows_read"] for f in per_file)
    row.quarantined = sum(f["quarantined"] for f in per_file)
    row.duration_s = round(time.perf_counter() - started, 2)
    # The parser's report, verbatim, where there is one; with several files
    # each has its own in `sources`, and summing censuses here would be the
    # recomputation the parser has already made unnecessary.
    row.report = per_file[0]["report"] if len(per_file) == 1 else None
    for field in STAMP_FIELDS:
        values = {f["stamp"].get(field) for f in per_file}
        setattr(row, field, values.pop() if len(values) == 1 else None)
    row.built_by = actor
    row.built_at = clock.now()
    session.flush()
    write_sidecar(connection_id, catalogue_key, row)
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


# ── the seed: what a deployment that predates per-company catalogues gets ────


def seed_company_catalogues(session: Any,
                            actor: str = "shipped-corpus") -> List[Dict[str, Any]]:
    """Give each organization's first company the corpus that ships in the image.

    Every deployment that ran before this change resolved against one shared
    catalogue built from ``settings.PIE_CORPUS``. Removing that default without
    putting it somewhere turns every quote line UNRESOLVED on deploy — a
    regression that looks exactly like the engine being down. So the corpus
    becomes the first company's *default* catalogue's one price list, once.

    **Its decoding config is the one it ships with**, not a default. The
    shipped corpus and ``settings.PIE_PACK`` are one pairing — the rule set
    was written against exactly this file — so the seed's config names that
    rule set and is confirmed by the seed itself, on the same footing as a
    config a person saved. No other file ever reads ``PIE_PACK``.

    Deliberately conservative, because it writes on somebody's behalf:

    * only an organization that has **no corpus row at all** is seeded, so a
      company that has uploaded its own export is never handed a different
      manufacturer's item master;
    * only its **first enabled** company — ``list_connections`` orders oldest
      first — because which of three legal entities sells this catalogue's
      product is a question this function cannot answer, and guessing three
      times is worse than guessing once;
    * a missing corpus file seeds nothing and is not an error. The engine is
      optional in ``deploy/backend.Dockerfile``, so an image built without it
      has no seed to give — and that deployment was not resolving before this
      change either.

    Idempotent, and re-runnable: it is called on every boot rather than once in
    a migration, so a deployment that later gains the submodule (or its first
    connection) is seeded on the next start rather than never.
    """
    from .ingestion import item_master
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
        table = item_master.read_table(raw, settings.PIE_CORPUS.name, "text/csv")
        columns = item_master.suggest_mapping(table)
        session.add(models.CompanyCorpus(
            organization_id=org,
            connection_id=first.connection_id,
            catalogue_key=DEFAULT_CATALOGUE,
            # Keyed by its filename like any other source, so the company can
            # replace or remove the seed from the screen once it has its own
            # export.
            source_key=settings.PIE_CORPUS.name,
            filename=settings.PIE_CORPUS.name,
            content_type="text/csv",
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            content=raw,
            mapping=columns,
            ingest=item_master.describe(table, columns),
            rule_set=settings.PIE_PACK.name,
            decoding_confirmed_at=clock.now(),
            decoding_confirmed_by=actor,
            uploaded_by=actor,
        ))
        if catalogue_row(session, org, first.connection_id, DEFAULT_CATALOGUE) is None:
            session.add(models.CompanyCatalogue(
                organization_id=org, connection_id=first.connection_id,
                catalogue_key=DEFAULT_CATALOGUE, name=""))
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
        write_sidecar(connection_id, DEFAULT_CATALOGUE, row)
    log.info("adopted the pre-catalogue file for company %s into %s",
             connection_id, target.parent)


def ensure_company_catalogues(session: Any,
                              actor: str = "auto-build") -> List[Dict[str, Any]]:
    """Build any catalogue whose files are on record but whose output is not.

    The catalogue is derived: the corpus rows are durable and the JSONL is not,
    so a redeploy onto a container with a fresh disk arrives with every company
    holding a corpus and no catalogue. Rebuilding here is what makes that a
    two-second start-up cost per catalogue rather than an administrator
    noticing, days later, that resolution has quietly stopped.

    Honours ``AUTO_BUILD_CATALOG``: with it off, a catalogue reports NOT BUILT
    and somebody builds it from the screen. A catalogue with a file awaiting
    its decoding config is left NOT BUILT and logged by file — never decoded
    through a default. Never falls back to another company's catalogue.

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
        try:
            build_for_company(session, org, connection_id, actor=actor,
                              catalogue_key=catalogue_key)
            session.commit()
        except CatalogueError as e:
            # A file awaiting its decoding config: a state, not a failure.
            log.info("catalogue %s of company %s is NOT BUILT: %s",
                     catalogue_key, connection_id, e)
            session.rollback()
            continue
        except Exception:  # noqa: BLE001 — one catalogue must not stop the boot
            log.exception("could not build catalogue %s for company %s",
                          catalogue_key, connection_id)
            session.rollback()
            continue
        log.info("built catalogue %s for company %s", catalogue_key, connection_id)
        out.append({"organization_id": org, "connection_id": connection_id,
                    "catalogue_key": catalogue_key})
    return out
