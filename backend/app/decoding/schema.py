"""The decoder artifact: everything needed to decode one file, and nothing else.

A decoder is **per file and self-contained**. It carries its own extraction
rules, its own decimal convention and its own executor version, so decoding is
a pure function of (file bytes, artifact) and stays that way for the life of
the artifact. Nothing here reads a shipped rule set, a company setting or a
deployment default — there are none left to read.

**Why a frozen artifact is what makes inference safe.** Working out how a file
should be decoded is a judgement: it looks at the text, proposes structure, and
a person confirms it. That step is not reproducible and does not need to be —
it is a *build* step, exactly as authoring a migration is. What must be
reproducible is the *decode*, and it is, because the artifact is frozen,
content-addressed and stamped on every record it produces. The rule that keeps
this true is one line long and is the whole design: **inference never runs at
decode time.** Re-inference produces a new artifact a person confirms; it never
edits one in place.

Four properties hold the guarantee:

* **content-addressed** — :attr:`Decoder.decoder_id` is the sha256 of the
  artifact's canonical JSON, so "which decoder produced this row" is answerable
  from the row alone, forever;
* **self-contained** — no references out, so nothing can move underneath a
  stored decoder;
* **version-refusing** — the artifact names the executor schema it was frozen
  against and :mod:`app.decoding.executor` refuses a mismatch rather than
  decoding differently. Silent re-interpretation under a new executor is the
  one failure that would destroy the guarantee, so it is an error rather than a
  behaviour;
* **validated at freeze, never at decode** — every check that could reject a
  decoder happens in :func:`freeze`. A decode has no decisions left to take.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .safety import UnsafePattern, check_pattern

#: The executor contract this module and :mod:`app.decoding.executor` implement.
#: Bumped only when a change would make an existing artifact decode
#: *differently*; a change that only adds a capability an old artifact does not
#: use does not move it. An artifact frozen at 1 is decoded by an executor at 1
#: and refused by any other — see the module docstring.
SCHEMA_VERSION = 1

#: What a decoded record may hold: the shared attribute vocabulary, and the one
#: thing in this design that is *not* per file.
#:
#: Every file gets its own extraction rules, but they all extract into these
#: names. That is not a convenience — it is what makes two files comparable.
#: A quote asks "is this drill equivalent to that one", and the answer is a
#: comparison of ``cutting_dia_mm`` against ``cutting_dia_mm``; if each file
#: named its own fields there would be nothing to compare and the equivalence
#: engine would silently return less.
#:
#: **Declared here rather than imported from pie-parser**, on
#: ``pie_service.ATTRIBUTE_FIELDS``' reasoning: this is the *portal's*
#: statement of what a decoded record may hold, and importing the engine's copy
#: would let a pinned-submodule bump silently widen it. The two are deliberately
#: identical today and
#: ``test_decoder_determinism.test_the_slot_vocabulary_is_the_engines`` pins the
#: relationship, so they cannot drift unnoticed.
CORE_SLOTS: Tuple[str, ...] = (
    # classification & nomenclature
    "grade", "grade_system", "applications", "toughness_index", "grade_segment",
    "material_class", "coating", "coating_process", "grade_revision",
    "catalog_number_full", "series", "line_code",
    # dimensions & features
    "cutting_dia_mm", "cutting_dia_inch", "shank_dia_mm", "loc_mm", "reach_mm",
    "oal_mm", "corner_radius_mm", "depth_ratio_xd", "flute_count",
    "point_angle_deg", "through_coolant", "mql", "dual_unit_check",
    # ISO designation
    "iso_shape", "iso_clearance_letter", "iso_clearance_deg", "iso_stub_only",
    "insert_polarity",
    # full ISO designation
    "iso_tolerance", "iso_fixing", "edge_length_mm", "thickness_code",
    "thickness_mm",
    # indexable-insert features
    "chipbreaker", "chipbreaker_meaning", "ic_size_mm", "edge_count", "wiper",
    # solid round tools
    "ball_nose", "corner_form", "chamfer_width_mm",
    # grooving / threading
    "cut_width_mm",
)

#: A slot outside :data:`CORE_SLOTS` must say so in its name. An ``ext:`` field
#: is carried on the record and is **never compared**: nothing else knows what
#: it means, so treating it as evidence of fit would be inventing a comparison.
#: It exists so a decoder can keep a fact the shared vocabulary has no name for
#: rather than discard it, and so that needing one is visible.
EXT_PREFIX = "ext:"

_EXT_NAME = re.compile(r"^ext:[a-z0-9_]+\.[a-z0-9_]+$")

#: How a matched group becomes a value. Four, deliberately.
#:
#: There is no ``mm``/``inch`` type and no unit conversion: the slot name
#: carries the unit (``cutting_dia_mm``, ``cutting_dia_inch``), so a decoder
#: that read an inch value into a millimetre slot would be a wrong number with
#: a real stamp — the class of defect this whole design exists to prevent. A
#: file quoting both gets two bindings, and whether they agree is the engine's
#: ``dual_unit_check`` to make, not this module's.
SLOT_TYPES: Tuple[str, ...] = ("text", "integer", "number", "flag")

#: Which character a decimal fraction is written with in this file. Stated in
#: the artifact rather than sniffed at decode time: a decode that guessed per
#: row would be one whose output depended on which rows it had already seen.
#:
#: ``either`` exists because real exports are not consistent. The shipped
#: corpus writes ``SC DRILL 5.1mm`` and ``SC DRILL 11,1mm`` in the same column
#: of the same file, and forcing one convention on it would quarantine half the
#: drills. It is still a declaration and not a guess — the same text always
#: converts the same way, whatever else the file contains.
#:
#: What ``either`` cannot represent is a **thousands separator**: it reads
#: ``1,234`` as one point two three four. That is the right reading for a
#: dimension and the wrong one for a quantity, so a file that groups digits
#: must declare ``dot`` and accept the rows that costs. The convention is in
#: the artifact precisely so that choice is recorded rather than assumed.
DECIMAL_CONVENTIONS: Tuple[str, ...] = ("dot", "comma", "either")


class DecoderError(ValueError):
    """A decoder could not be frozen, with the sentence saying why.

    Every rejection is at freeze time. There is no decode-time equivalent, and
    that asymmetry is the design: a stored decoder is one that has already been
    proven to compile, to match its own examples and to reject its own
    counterexamples.
    """


@dataclass(frozen=True)
class FieldBinding:
    """One named group of one pattern, and the slot it fills."""

    group: str
    slot: str
    type: str

    def to_dict(self) -> Dict[str, Any]:
        return {"group": self.group, "slot": self.slot, "type": self.type}


@dataclass(frozen=True)
class Segment:
    """One shape of description this file contains, and how to read it.

    A file is not one shape. A price list holds drills phrased one way and
    inserts phrased another, so a decoder is an **ordered** list of these and
    the first whose pattern matches wins. Ordered, not a mapping: which segment
    claims a row is part of the answer, and a set would make it depend on
    iteration order.

    ``examples`` and ``counterexamples`` are real rows from the file this was
    built from. They are not documentation — :func:`freeze` refuses a segment
    whose pattern does not match every example or matches any counterexample,
    so a decoder that does not do what its author believed cannot be stored.
    """

    id: str
    pattern: str
    fields: Tuple[FieldBinding, ...] = ()
    examples: Tuple[str, ...] = ()
    counterexamples: Tuple[str, ...] = ()
    #: A label for what this shape is, carried onto the record. Free text: it
    #: is not the engine's family vocabulary and nothing compares it.
    label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "pattern": self.pattern,
            "fields": [f.to_dict() for f in self.fields],
            "examples": list(self.examples),
            "counterexamples": list(self.counterexamples),
        }
        if self.label is not None:
            out["label"] = self.label
        return out


@dataclass(frozen=True)
class Decoder:
    """A frozen, content-addressed decoder for one file.

    Built only by :func:`freeze`, which is what makes ``decoder_id`` mean
    something: an instance that exists has passed every check, so the id names
    a decoder that compiles and matches its own examples rather than an
    arbitrary blob of JSON.
    """

    schema_version: int
    decimal: str
    segments: Tuple[Segment, ...]
    decoder_id: str

    def to_dict(self) -> Dict[str, Any]:
        """The artifact as plain data, **carrying its own id**.

        The id is a sibling of the hashed content and never part of it —
        :func:`artifact_dict` is what :func:`content_id` is taken over, and it
        stays exactly as it was, so no existing id moves. It is included here
        because this is what gets *stored*, and an artifact that does not carry
        its own checksum is one :func:`from_dict` cannot tell has been edited:
        the tamper check reads ``payload["decoder_id"]``, so a payload without
        the key skipped it silently and re-froze under whatever id the new
        contents hashed to.

        Found by storing one in a database column and then trying to detect an
        edit to it. Before this, every caller wanting the check had to
        remember to attach the id itself, and the two places in the test suite
        that did were the only evidence the check worked at all.
        """
        return {**artifact_dict(self.schema_version, self.decimal, self.segments),
                "decoder_id": self.decoder_id}


def artifact_dict(schema_version: int, decimal: str,
                  segments: Sequence[Segment]) -> Dict[str, Any]:
    """The artifact as plain data, in the shape the id is taken over.

    Deliberately without ``decoder_id``: a hash cannot cover itself. This is
    the hashed content; :meth:`Decoder.to_dict` is that plus the id, and is
    what anything storing or transmitting an artifact should use.
    """
    return {
        "schema_version": schema_version,
        "decimal": decimal,
        "segments": [s.to_dict() for s in segments],
    }


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """The one serialisation the id is taken over.

    Sorted keys, no insignificant whitespace, UTF-8. Written here rather than
    borrowed from pie-parser's ``configio.dump_stable_json``: this package must
    work in a deployment built without the engine, and an id that depended on
    an optional import would be an id that changed when the submodule was
    absent.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


#: How much of the content hash the id is, in hex characters.
#:
#: Sixteen, which is the width pie-parser's own ``ruleset_checksum`` and
#: ``run_id`` use — and this id stands beside those and replaces them, so a
#: different width would make one stamp on a record look like a different kind
#: of thing from its neighbours. Sixty-four bits is ample for what this
#: identifies: a collision needs on the order of five billion distinct decoders
#: before it is an even bet, against a deployment that will hold tens. It is
#: also carried on **every decoded record**, where 48 characters nobody reads,
#: times a few million rows, is real.
ID_WIDTH = 16


def content_id(payload: Mapping[str, Any]) -> str:
    """The decoder's whole identity: :data:`ID_WIDTH` hex of the sha256 of
    :func:`canonical_bytes`."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:ID_WIDTH]


def is_known_slot(slot: str) -> bool:
    """Whether this name is one the shared vocabulary knows, or a well-formed
    ``ext:`` field. Anything else is refused: a typo for ``cutting_dia_mm``
    would otherwise become a field nothing ever compares, silently."""
    return slot in CORE_SLOTS or bool(_EXT_NAME.match(slot))


def freeze(segments: Sequence[Segment], *, decimal: str = "dot",
           schema_version: int = SCHEMA_VERSION) -> Decoder:
    """Validate a candidate decoder and content-address it, or refuse it.

    This is the only door into a :class:`Decoder`, and every check that could
    reject one lives here — so a decode has no decisions left to take and a
    stored decoder cannot be one that never worked. In order:

    * the executor version is one this build implements;
    * the decimal convention is stated and known;
    * there is at least one segment, and no two share an id;
    * every pattern compiles, and passes the static safety check
      (:mod:`app.decoding.safety`) — a generated pattern runs over every row of
      every rebuild, and the reason that check is *static* rather than a
      timeout is itself a determinism requirement: a wall-clock limit would
      make the same file decode differently on a loaded machine;
    * every binding names a group the pattern actually declares, a slot the
      vocabulary knows, and a type this executor implements;
    * no two bindings in one segment fill the same slot — two values for one
      attribute is a decoder that cannot be read;
    * every example matches and every counterexample does not.

    Raises :class:`DecoderError` with the sentence naming the segment.
    """
    if schema_version != SCHEMA_VERSION:
        raise DecoderError(
            f"This build decodes schema version {SCHEMA_VERSION}, and this "
            f"decoder is version {schema_version}. A decoder is frozen against "
            f"one executor on purpose — decoding it here would risk a different "
            f"answer than the one its records were stamped with.")
    if decimal not in DECIMAL_CONVENTIONS:
        raise DecoderError(
            f"{decimal!r} is not a decimal convention. Use one of "
            f"{', '.join(DECIMAL_CONVENTIONS)} — a file writes eleven point one "
            f"as '11.1' or '11,1' or, in the shipped corpus, both, and guessing "
            f"per row would make the decode depend on the rows already seen.")
    if not segments:
        raise DecoderError("A decoder needs at least one segment: a decoder "
                           "that matches nothing decodes nothing.")

    seen_ids: set = set()
    for segment in segments:
        if not segment.id:
            raise DecoderError("Every segment needs an id.")
        if segment.id in seen_ids:
            raise DecoderError(
                f"Two segments share the id {segment.id!r}. Ids are how a row "
                f"says which shape claimed it, so they have to be unique.")
        seen_ids.add(segment.id)
        _check_segment(segment)

    payload = artifact_dict(schema_version, decimal, segments)
    return Decoder(schema_version=schema_version, decimal=decimal,
                   segments=tuple(segments), decoder_id=content_id(payload))


def _check_segment(segment: Segment) -> None:
    try:
        check_pattern(segment.pattern)
    except UnsafePattern as e:
        raise DecoderError(f"segment {segment.id!r}: {e}") from e

    try:
        compiled = re.compile(segment.pattern)
    except re.error as e:  # pragma: no cover — check_pattern compiles first
        raise DecoderError(f"segment {segment.id!r}: {e}") from e

    declared = set(compiled.groupindex)
    filled: set = set()
    for binding in segment.fields:
        if binding.group not in declared:
            raise DecoderError(
                f"segment {segment.id!r}: no group named {binding.group!r} in "
                f"its pattern. Declared: "
                f"{', '.join(sorted(declared)) or 'none'}.")
        if not is_known_slot(binding.slot):
            raise DecoderError(
                f"segment {segment.id!r}: {binding.slot!r} is not a slot this "
                f"platform knows. Use one of the shared attribute names, or "
                f"name it 'ext:<namespace>.<field>' to keep a fact nothing "
                f"else can compare.")
        if binding.type not in SLOT_TYPES:
            raise DecoderError(
                f"segment {segment.id!r}: {binding.type!r} is not a slot type. "
                f"Use one of {', '.join(SLOT_TYPES)}.")
        if binding.slot in filled:
            raise DecoderError(
                f"segment {segment.id!r}: two groups both fill {binding.slot!r}. "
                f"One attribute cannot hold two values.")
        filled.add(binding.slot)

    for example in segment.examples:
        if compiled.search(example) is None:
            raise DecoderError(
                f"segment {segment.id!r} does not match its own example "
                f"{example!r}. A decoder that does not do what its author "
                f"believed is not one worth storing.")
    for counterexample in segment.counterexamples:
        if compiled.search(counterexample) is not None:
            raise DecoderError(
                f"segment {segment.id!r} matches {counterexample!r}, which it "
                f"was told not to. Widen the counterexample or narrow the "
                f"pattern; a segment that claims a row meant for a later one "
                f"decodes it wrongly and silently.")


def from_dict(payload: Mapping[str, Any]) -> Decoder:
    """Rebuild a decoder from stored JSON, re-running every freeze check.

    Re-validated rather than trusted, and the id is recomputed and compared:
    a stored artifact that has been edited by hand, truncated, or migrated
    badly is refused here rather than decoding into records stamped with an id
    that no longer describes it.
    """
    try:
        segments = tuple(
            Segment(
                id=str(s["id"]),
                pattern=str(s["pattern"]),
                fields=tuple(FieldBinding(group=str(f["group"]),
                                          slot=str(f["slot"]),
                                          type=str(f["type"]))
                             for f in s.get("fields", []) or []),
                examples=tuple(str(x) for x in s.get("examples", []) or []),
                counterexamples=tuple(str(x) for x in
                                      s.get("counterexamples", []) or []),
                label=(str(s["label"]) if s.get("label") is not None else None),
            )
            for s in payload["segments"]
        )
        schema_version = int(payload["schema_version"])
        decimal = str(payload["decimal"])
    except (KeyError, TypeError, ValueError) as e:
        raise DecoderError(f"This is not a decoder artifact: {e}") from e

    decoder = freeze(segments, decimal=decimal, schema_version=schema_version)
    stored = payload.get("decoder_id")
    if stored and stored != decoder.decoder_id:
        raise DecoderError(
            f"This artifact says it is {stored} but its contents hash to "
            f"{decoder.decoder_id}. It has been changed since it was frozen, "
            f"and the records stamped with the old id were not decoded by "
            f"this.")
    return decoder
