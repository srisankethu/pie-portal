"""Locate, build and describe the decoded PIE product catalogue.

The catalogue is ``products.jsonl`` produced by pie-parser's ``run_parser`` over
the Kennametal/WIDIA nomenclature corpus. It is large (~13 MB) and deterministic,
so it is gitignored and (re)built from the pinned pie-parser submodule instead
of being committed.

**Deployment-wide, not per-organization** — ``settings.PIE_CATALOG`` is one
path and ``pie_service`` builds one index per process, so every organization on
a deployment resolves against the same catalogue. When that changes, this
module is where the organization enters: these settings-derived paths become
per-org lookups, and the router already holds a principal to pass. Until then
the API says ``scope: "deployment"`` rather than pretending otherwise.
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
from typing import Any, Dict, Optional

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
        out.parent.mkdir(parents=True, exist_ok=True)

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

        log.info("Building PIE catalogue from %s ...", settings.PIE_CORPUS)
        started = time.perf_counter()
        pack = load_pack(settings.PIE_PACK)
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
        records = CsvAdapter(settings.PIE_CORPUS, mapping).read()
        # The default profile excludes the payload — the corpus's opaque
        # commercial columns — which is what keeps price out of nomenclature.
        profile = RunProfile()
        fingerprint = configio.checksum_bytes(settings.PIE_CORPUS.read_bytes())
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

        # The stamp, taken off an emitted record rather than re-derived. The
        # sidecar carries when the build *happened*, which the deterministic
        # artifact deliberately does not — products.jsonl stays byte-identical
        # for identical input.
        stamped = products[0] if products else (quarantine[0] if quarantine else {})
        sidecar = {
            "report": report.to_dict(),
            "stamp": {k: stamped.get(k) for k in STAMP_FIELDS
                      if stamped.get(k) is not None},
            "build": {
                "built_at": clock.iso(clock.now()),
                "duration_s": round(time.perf_counter() - started, 2),
                "rows_read": report.total,
                "emitted": len(products),
                "quarantined": len(quarantine),
                "corpus": str(settings.PIE_CORPUS),
                "corpus_fingerprint": fingerprint,
                "pack": str(settings.PIE_PACK),
            },
        }
        report_path(out).write_text(
            json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")
        log.info("Wrote %d products to %s (%d quarantined)",
                 len(products), out, len(quarantine))
        return out


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
