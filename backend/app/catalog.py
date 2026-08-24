"""Locate (and, if needed, build) the decoded PIE product catalogue.

The catalogue is ``products.jsonl`` produced by pie-parser's ``run_parser`` over
the Kennametal/WIDIA nomenclature corpus. It is large (~13 MB) and deterministic,
so it is gitignored and (re)built from the pinned pie-parser submodule instead
of being committed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import settings

log = logging.getLogger("pie_portal.catalog")


def build_catalog(force: bool = False) -> Path:
    """Build products.jsonl from the pie-parser corpus. Returns its path."""
    out = settings.PIE_CATALOG
    if out.exists() and not force:
        return out
    if not settings.PIE_CORPUS.exists():
        raise FileNotFoundError(
            f"PIE corpus not found at {settings.PIE_CORPUS}. Fetch pie-parser "
            "with ./scripts/setup_pie_parser.sh (or set PIE_PARSER_ROOT)."
        )
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
    profile = RunProfile()
    fingerprint = configio.checksum_bytes(settings.PIE_CORPUS.read_bytes())
    pipeline = ParserPipeline(pack, profile)
    products, _report, _quarantine = pipeline.run(records, input_fingerprint=fingerprint)
    with out.open("w", encoding="utf-8") as fh:
        for rec in products:
            fh.write(configio.dump_stable_json(rec) + "\n")
    log.info("Wrote %d products to %s", len(products), out)
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
