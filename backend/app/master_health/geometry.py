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

The gate is three slots. What this module *keeps* is not, and the two were the
same line of code until they were separated here. The engine decodes 37 distinct
fact fields over this corpus read as an item master (44 with a grade column
supplied), and ``DecodeOutcome.slots`` used to keep three of them — the gate's
own — so every other decoded fact was dropped before any caller could see it.
The gate's question ("is this row's geometry trustworthy enough to count?") is
not the question an attribute store asks ("what did the pack say about this
row?"), and answering only the first threw away the answer to the second.

Keeping more cannot move the gate, and that is structural rather than lucky:
:meth:`DecodeOutcome.fills` takes the slots it requires as an argument, so the
census names ``GATED_SLOTS`` and gets the same answer whatever else rode along.
``test_widening_the_kept_fields_did_not_move_the_gate`` asserts it rather than
trusting it, and the census over the 6,717-row corpus was run either side of the
widening and compared byte for byte.
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

#: Everything the engine emits that is **not** a decoded fact about the product.
#: :data:`DecodeOutcome.slots` keeps every other non-null field, so this set is
#: the whole of the difference between an ISO-slot triple and an attribute
#: store. Five groups, each out for its own reason:
#:
#: * **Where the row came from** — ``record_id``, ``source_file``,
#:   ``source_sheet``, ``source_row``. This module supplied all four itself and
#:   already holds the only one that identifies anything, as ``row_number``.
#: * **What was read** — ``description_raw``, ``description_norm``. The item
#:   master's own name column is where that text lives; echoing it back as an
#:   attribute stores the question beside the answer.
#: * **Which code and which pack answered** — the run and ruleset stamps and the
#:   ids of the rules that fired. Run provenance, carried once on
#:   :class:`DecodeRun` rather than repeated on every row of the master, which is
#:   where a writer should read it from.
#: * **How the read went** — ``row_confidence``, ``field_meta``, ``flags``,
#:   ``validations``, ``text_ambiguous``, ``unresolved_tokens``,
#:   ``dual_unit_check``. Statements about the *reading*, not about the product.
#:   ``field_meta`` is per-field provenance, confidence and span — what a store
#:   row carries in its own columns — and ``ProductAttributeValue``'s docstring
#:   is explicit that confidence says how well a value was READ and is never a
#:   score to rank on. A confidence that arrived as an attribute row keyed
#:   ``row_confidence`` is one join away from being ranked on anyway.
#: * **Not one fact** — ``attributes_ext``, a namespaced bag of extras
#:   (``iso.shape_name``, ``kmt.series_brand``) under a key that is not itself a
#:   fact about anything. Whether to unpack it into rows is a decision for
#:   whoever writes the store, taken deliberately; it is not this dict's to take
#:   by flattening.
#:
#: ``product_family`` is out for a different reason: :class:`DecodeOutcome`
#: already carries it as ``routed_family``. One value under two names inside one
#: object is exactly the drift the capability search in ``CLAUDE.md`` §2 exists
#: to prevent.
NON_FACT_FIELDS: frozenset[str] = frozenset({
    "record_id", "source_file", "source_sheet", "source_row",
    "description_raw", "description_norm",
    "run_id", "engine_version", "schema_version", "ruleset_checksum",
    "pack_id", "pack_version", "org_id", "org_version",
    "family_rule_id", "grammar_id",
    "row_confidence", "field_meta", "flags", "validations",
    "text_ambiguous", "dual_unit_check", "unresolved_tokens",
    "attributes_ext",
    "product_family",
})


def _decoded_facts(row: Dict[str, Any]) -> Dict[str, Any]:
    """Every fact the engine decoded for one row, metadata and nulls removed.

    **A denylist, and the direction of that bet is the point.** An allowlist —
    the shape ``pie_service.ATTRIBUTE_FIELDS`` uses — looks like the safer
    choice and is the wrong one here, because the two fail in opposite
    directions and only one failure is visible. An allowlist nobody updated
    drops a newly decoded field in silence: no report line, no store row,
    nothing anywhere saying a fact was thrown away. Decision 002's exit
    criterion is published coverage per category, so a silent drop holds that
    number flat while the pack is getting better — the "absence of evidence is
    not a pass" failure, in the one measurement meant to catch it. A denylist
    nobody updated lets a new *metadata* field through, where it lands as an
    attribute named after a stamp and holding a checksum: wrong, but wrong in
    the output, on the first read, and one entry here to fix.

    Which of the two sets grows differs too. pie-parser's engine holds no
    manufacturer knowledge — families, notations, grades and dimensions all
    arrive as pack data (its ``CLAUDE.md`` §3) — so the fact set grows with pack
    releases and no portal change, while the metadata set moves only when the
    engine's own record schema does, which is a coordinated change with a
    version bump attached.

    Not reused from ``pie_service._attributes_of``, although the shape matches
    almost exactly: that projection is the portal's statement of what a *line on
    a screen* may show, deliberately fourteen fields and deliberately narrow so
    that a pack change cannot silently widen a screen. This is measurement and
    storage, where a pack change widening what is kept is the entire point. Two
    functions, two opposite bets, and sharing one would settle the wrong one.

    Keys are sorted rather than left in the engine's emission order: this
    mapping is now an input to something that writes and serialises rows, dict
    order is byte order once it is serialised, and a rerun over one export must
    produce identical bytes. Sorting makes that order a property of this
    function instead of the engine's dict construction, which nothing here
    controls.
    """
    return {k: row[k] for k in sorted(row)
            if k not in NON_FACT_FIELDS and row[k] is not None}


@dataclass(frozen=True)
class DecodeOutcome:
    """What the engine made of one item name.

    ``slots`` holds every fact that actually decoded — not only the gated three
    — and drops the nulls, so a caller still cannot mistake a null for a decoded
    absence. What it leaves out, and why, is :data:`NON_FACT_FIELDS`.
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


def _load_pack() -> Any:
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine.pack import load_pack  # noqa: PLC0415
    return load_pack(settings.PIE_PACK)


def _covered_brands(pack: Any) -> tuple[str, ...]:
    names: set[str] = set()
    for claim in getattr(pack, "claims", ()) or ():
        for attr in ("brand", "manufacturer"):
            value = getattr(claim, attr, None)
            if value:
                names.add(str(value))
    return tuple(sorted(names))


def decode_names(rows: Sequence[MasterRow]) -> DecodeRun:
    """Run every row's name through the pack, once, and gate the results.

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
    if not (Path(settings.PIE_PARSER_ROOT) / "engine" / "pipeline.py").exists():
        return DecodeRun({}, unavailable_reason=(
            f"pie-parser is not checked out at {settings.PIE_PARSER_ROOT}, so no "
            f"name was decoded. Fetch it with ./scripts/setup_pie_parser.sh (or set "
            f"PIE_PARSER_ROOT). Geometry coverage is UNKNOWN, not zero."))
    try:
        pack = _load_pack()
        from engine import configio  # noqa: PLC0415
        from engine.model import RawRecord  # noqa: PLC0415
        from engine.pipeline import ParserPipeline, RunProfile  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — an absent pack must not kill a report
        log.warning("master-health: pack unreadable", exc_info=True)
        return DecodeRun({}, unavailable_reason=(
            f"the pack at {settings.PIE_PACK} could not be loaded ({exc}), so no "
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
        outcomes[number] = DecodeOutcome(
            row_number=number,
            routed_family=row.get("product_family"),
            slots=_decoded_facts(row),
        )
    return DecodeRun(
        outcomes=outcomes,
        pack_id=str(getattr(pack, "pack_id", "") or ""),
        pack_version=str(getattr(pack, "version", "") or ""),
        ruleset_checksum=str(getattr(pack, "checksum", "") or ""),
        covered_brands=_covered_brands(pack),
    )
