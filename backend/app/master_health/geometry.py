"""Decode item names through pie-parser, and admit only a full ISO slot fill.

The gate is the whole of this module's value, so it is stated before the code.

``docs/concepts/01-application-engineering.md`` §3 measured what happens when
the engine is run over a whole item master and the family route is believed:
every row scores ``row_confidence`` 0.00 with ``GRADE_MISSING`` and
``MANUFACTURER_UNKNOWN``; 30.3% of rows route to a named ``product_family``;
and **11.6% of those routed rows carry a named non-Kennametal manufacturer**.
That 11.6% is a *contamination* rate over routed rows — the share of routed
rows whose own master says a maker this pack does not cover — not a measured
misroute rate. Read either way it says the same thing about the route: an EMUGE
screwdriver and an ``M3X11`` screw both route to ``turning_insert``, so a
family route is a guess the engine itself declines to vouch for.

Restricted to a **full ISO slot fill** — shape *and* edge length *and* corner
radius all decoded — the same measurement found definite misroutes at 1 row in
2,057. ISO slot fill validates itself; a family route does not. So:

    a row is geometry-decoded here only when all three slots filled.

The published census in that document used a two-slot definition (shape + edge,
2,577 rows, 17.1%) and the three-slot gate is a strictly smaller set (2,057
rows). Both are reported, separately and labelled, by :mod:`.analysis` — the
gated figure is the one this report vouches for, and the published one is
carried so a reader can line the two censuses up instead of guessing why they
differ.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..config import settings
from .source import MasterRow

log = logging.getLogger("pie_portal.master_health.geometry")

#: The three ISO slots the gate requires. Named here rather than inline so the
#: gate has exactly one definition and a test can assert against it.
GATED_SLOTS: tuple[str, ...] = ("iso_shape", "edge_length_mm", "corner_radius_mm")

#: The two-slot definition the 2026-08-09 census published. Kept only for
#: comparability with that document; never used as this report's gate.
PUBLISHED_SLOTS: tuple[str, ...] = ("iso_shape", "edge_length_mm")


@dataclass(frozen=True)
class DecodeOutcome:
    """What the engine made of one item name.

    ``slots`` holds only the ISO slots that actually decoded, so a caller
    cannot mistake a null for a decoded absence.
    """

    row_number: int
    routed_family: Optional[str]
    slots: Dict[str, Any]

    def fills(self, required: Sequence[str]) -> bool:
        return all(self.slots.get(s) is not None for s in required)

    @property
    def gated(self) -> bool:
        """Full ISO slot fill — the only decode this report will act on."""
        return self.fills(GATED_SLOTS)


@dataclass(frozen=True)
class DecodeRun:
    """Every row's decode, or a stated reason there is none.

    ``unavailable_reason`` being set is *not* a run of zeroes. A report that
    could not load the engine has an UNKNOWN geometry coverage, and the
    difference between "the pack decoded nothing" and "nobody asked the pack"
    is the difference between evidence and silence.
    """

    outcomes: Dict[int, DecodeOutcome]
    pack_id: str = ""
    pack_version: str = ""
    ruleset_checksum: str = ""
    #: Manufacturers and brands the loaded pack claims to cover, read from the
    #: pack's own claim signatures rather than named here — this repository
    #: holds no manufacturer literal for the same reason pie-parser's engine
    #: does not.
    covered_brands: tuple[str, ...] = ()
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason


def _load_pack(pack_path: Path) -> Any:
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine.pack import load_pack  # noqa: PLC0415
    return load_pack(pack_path)


def _covered_brands(pack: Any) -> tuple[str, ...]:
    names: set[str] = set()
    for claim in getattr(pack, "claims", ()) or ():
        for attr in ("brand", "manufacturer"):
            value = getattr(claim, attr, None)
            if value:
                names.add(str(value))
    return tuple(sorted(names))


def decode_names(rows: Sequence[MasterRow],
                 pack_path: Optional[Path] = None) -> DecodeRun:
    """Run every row's name through the pack, once, and gate the results.

    ``pack_path`` is the rule set this export is decoded through — the decoder
    half of what a stored file's decoding config would name. It is **required**
    and there is no fallback: measuring one export through another's grammars
    would report *that* rule set's coverage of *this* file and call it a
    finding, and decoding through a shipped default is the same mistake with
    nobody to blame. Absent, nothing is decoded and geometry coverage is
    UNKNOWN, which is the honest answer and the one this module gives
    everywhere else.

    One batch through one ``ParserPipeline``, the same way ``app/catalog.py``
    builds the catalogue — not a per-row call, which would recompile the
    grammars fifteen thousand times.

    ``record_id`` is the export's row number rather than its SKU on purpose:
    a master holds blank SKUs and duplicate SKUs (both are findings this report
    counts), and keying the decode on a value that is neither present nor
    unique would drop or collide exactly the rows under measurement.

    Never raises. A missing engine, an unreadable pack or a pipeline that
    refuses the batch all come back as ``unavailable_reason``, because a
    diagnostic that dies on its optional half tells you nothing about the half
    that worked.
    """
    if pack_path is None:
        return DecodeRun({}, unavailable_reason=(
            "no rule set was named, so no name was decoded. Which rule set "
            "reads an export is a fact about that file and there is no default "
            "one — pass --rule-set with a shipped id or a path. Geometry "
            "coverage is UNKNOWN, not zero."))
    if not (Path(settings.PIE_PARSER_ROOT) / "engine" / "pipeline.py").exists():
        return DecodeRun({}, unavailable_reason=(
            f"pie-parser is not checked out at {settings.PIE_PARSER_ROOT}, so no "
            f"name was decoded. Fetch it with ./scripts/setup_pie_parser.sh (or set "
            f"PIE_PARSER_ROOT). Geometry coverage is UNKNOWN, not zero."))
    try:
        pack = _load_pack(pack_path)
        from engine import configio  # noqa: PLC0415
        from engine.model import RawRecord  # noqa: PLC0415
        from engine.pipeline import ParserPipeline, RunProfile  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — an absent pack must not kill a report
        log.warning("master-health: pack unreadable", exc_info=True)
        return DecodeRun({}, unavailable_reason=(
            f"the rule set at {pack_path} could not be loaded "
            f"({exc}), so no "
            f"name was decoded. Geometry coverage is UNKNOWN, not zero."))

    records = [
        RawRecord(
            record_id=f"row-{r.row_number}",
            description=r.name or "",
            # No grade column is mapped, and there is no role for one: an item
            # master does not carry a grade (measured — 1.4% of names contain a
            # grade token). Passing None is the honest input, and the engine's
            # abstention on it is correct rather than a failure. See
            # docs/concepts/13-confidence-and-input-completeness.md.
            grade=None,
            payload={},
            source_file="item-master-export",
            source_sheet="-",
            source_row=r.row_number,
        )
        for r in rows
    ]
    fingerprint = configio.checksum_bytes(
        "|".join(f"{r.record_id}\x1f{r.description}" for r in records).encode("utf-8"))
    try:
        products, _report, quarantine = ParserPipeline(pack, RunProfile()).run(
            records, input_fingerprint=fingerprint)
    except Exception as exc:  # noqa: BLE001
        log.warning("master-health: decode batch failed", exc_info=True)
        return DecodeRun({}, unavailable_reason=(
            f"the decode batch failed ({exc}). Geometry coverage is UNKNOWN, not zero."))

    # Quarantined rows are merged in rather than dropped: a row the router could
    # not place is still a row this census must account for, and losing it would
    # narrow the denominator by whichever rows decoded worst.
    outcomes: Dict[int, DecodeOutcome] = {}
    emitted: List[dict] = list(products) + list(quarantine)
    for row in emitted:
        rid = str(row.get("record_id") or "")
        if not rid.startswith("row-"):
            continue
        try:
            number = int(rid[4:])
        except ValueError:
            continue
        slots = {s: row.get(s) for s in set(GATED_SLOTS) | set(PUBLISHED_SLOTS)
                 if row.get(s) is not None}
        outcomes[number] = DecodeOutcome(
            row_number=number,
            routed_family=row.get("product_family"),
            slots=slots,
        )
    return DecodeRun(
        outcomes=outcomes,
        pack_id=str(getattr(pack, "pack_id", "") or ""),
        pack_version=str(getattr(pack, "version", "") or ""),
        ruleset_checksum=str(getattr(pack, "checksum", "") or ""),
        covered_brands=_covered_brands(pack),
    )
