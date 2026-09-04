"""Run one frozen decoder over one file's rows. The deterministic half.

``decode(rows, decoder, …)`` is a pure function of its arguments. Given the
same rows and the same :class:`~app.decoding.schema.Decoder` it returns the
same records, in the same order, byte-identical when serialised — this year,
next year, on any machine. Everything that could make that untrue has been
removed rather than mitigated:

* **no clock.** Nothing here reads the time. When a decode happened is carried
  beside the output by the caller, never inside it, exactly as
  ``catalog.run_parse`` keeps build time out of ``products.jsonl``.
* **no randomness, and no iteration over a set or a dict.** Segments are an
  ordered tuple and are tried in order; bindings are a tuple; the record is
  serialised with sorted keys.
* **no locale.** The decimal convention is the artifact's, so ``11,1`` reads
  the same everywhere.
* **no timeout.** A wall-clock limit would make a loaded machine produce
  different records from an idle one, which is the guarantee this module
  exists to hold. Pathological patterns are refused at freeze instead
  (:mod:`app.decoding.safety`), and rows are bounded before matching.
* **no fallback.** A row no segment claims is quarantined by name. There is
  nothing to fall back *to* — that is the point of the design — and inventing
  a partial decode for an unclaimed row would be the benign default CLAUDE.md
  §1 forbids, wearing a different hat.

The executor refuses a decoder frozen against a different schema version
rather than decoding it. An executor that quietly did its best with an older
artifact would produce records that disagree with the ones already stamped
with that decoder's id, and nothing downstream could tell the two apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .safety import MAX_INPUT_LENGTH
from .schema import SCHEMA_VERSION, Decoder, Segment

#: Why a row was not decoded. A closed set, because "not decoded" is several
#: different facts and a screen that showed one word for all of them would be
#: hiding the one that is actionable.
NO_SEGMENT = "NO_SEGMENT"          # no segment's pattern claimed this row
NO_DESCRIPTION = "NO_DESCRIPTION"  # nothing to match against
BAD_VALUE = "BAD_VALUE"            # a group matched but would not convert


class DecoderVersionError(ValueError):
    """A decoder frozen against a different executor. Never decoded anyway."""


@dataclass(frozen=True)
class Quarantined:
    """One row that was not decoded, and which of the reasons it was."""

    record_id: str
    description: str
    reason: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {"record_id": self.record_id, "description_raw": self.description,
               "reason": self.reason}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class DecodeResult:
    """What one decode produced. Counts, not rates.

    No parse rate is computed here and none should be: a rate is a judgement
    about whether a decoder is good enough, it depends on what the file is,
    and a number this module invented would be a second answer to a question
    the counts already answer exactly.
    """

    records: List[Dict[str, Any]] = field(default_factory=list)
    quarantined: List[Quarantined] = field(default_factory=list)
    #: Rows claimed, per segment id, in the decoder's own order. A segment that
    #: claims nothing is worth seeing — it is either dead weight or shadowed by
    #: an earlier one.
    by_segment: Dict[str, int] = field(default_factory=dict)
    rows_read: int = 0
    #: Rows whose description was longer than :data:`MAX_INPUT_LENGTH` and was
    #: truncated before matching. Reported rather than silent: a truncated row
    #: may decode from its first 512 characters and still be missing something
    #: its tail said.
    truncated: int = 0

    def summary(self) -> Dict[str, Any]:
        return {
            "rows_read": self.rows_read,
            "records": len(self.records),
            "quarantined": len(self.quarantined),
            "truncated": self.truncated,
            "by_segment": dict(self.by_segment),
            "by_reason": {reason: sum(1 for q in self.quarantined
                                      if q.reason == reason)
                          for reason in sorted({q.reason for q in self.quarantined})},
        }


def decode(rows: Iterable[Sequence[str]], decoder: Decoder, *,
           source_sha256: str = "") -> DecodeResult:
    """Decode ``[record_id, description, grade]`` rows through one decoder.

    The row shape is ``ingestion.item_master.emit_rows``', so a file's own
    columns have already been resolved by its decoding config and what arrives
    here is the text to read. ``grade`` is carried onto the record as-is when
    the file has that column: it is a value the file states rather than one
    anything decodes, and a decoder that re-derived it from the description
    would be second-guessing the file.

    ``source_sha256`` is stamped on every record beside the decoder's id, so a
    record names both halves of what produced it. It is not read for anything
    else — passing the wrong one produces a wrongly labelled record rather than
    a different one.
    """
    if decoder.schema_version != SCHEMA_VERSION:
        raise DecoderVersionError(
            f"This decoder was frozen against executor schema "
            f"{decoder.schema_version} and this build implements "
            f"{SCHEMA_VERSION}. Decoding it here could differ from the decode "
            f"its records were stamped with, so it is refused rather than "
            f"attempted.")

    compiled: Tuple[Tuple[Segment, "re.Pattern[str]"], ...] = tuple(
        (segment, re.compile(segment.pattern)) for segment in decoder.segments)

    result = DecodeResult()
    result.by_segment = {segment.id: 0 for segment in decoder.segments}
    for row in rows:
        result.rows_read += 1
        record_id = _cell(row, 0)
        description = _cell(row, 1)
        grade = _cell(row, 2)

        if not description:
            result.quarantined.append(
                Quarantined(record_id, description, NO_DESCRIPTION))
            continue

        text = description
        if len(text) > MAX_INPUT_LENGTH:
            text = text[:MAX_INPUT_LENGTH]
            result.truncated += 1

        record = _decode_row(record_id, description, text, grade, compiled,
                             decoder, source_sha256, result)
        if record is not None:
            result.records.append(record)
    return result


def _decode_row(record_id: str, description: str, text: str, grade: str,
                compiled: Sequence[Tuple[Segment, "re.Pattern[str]"]],
                decoder: Decoder, source_sha256: str,
                result: DecodeResult) -> Optional[Dict[str, Any]]:
    """The first segment that claims this row decodes it. First, not best.

    First-match-wins rather than a score, because a score would be a second
    opinion about which shape a row is and there is nothing to break the tie
    with. The order is the author's statement of specificity, which is the same
    contract pie-parser's routing ladder has.
    """
    for segment, pattern in compiled:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            slots = _slots(segment, match, decoder.decimal)
        except _BadValue as e:
            # The segment claimed the row and then could not read it. That is a
            # defect in the decoder, not in the file, and it is quarantined by
            # name rather than falling through to a later segment — falling
            # through would decode the row through a shape its author did not
            # choose and hide the broken binding.
            result.quarantined.append(
                Quarantined(record_id, description, BAD_VALUE, str(e)))
            return None
        result.by_segment[segment.id] += 1
        record: Dict[str, Any] = {
            "record_id": record_id,
            "description_raw": description,
            "segment": segment.id,
            "decoder_id": decoder.decoder_id,
            "source_sha256": source_sha256,
            "schema_version": decoder.schema_version,
        }
        if segment.label is not None:
            record["label"] = segment.label
        if grade:
            record["grade"] = grade
        record.update(slots)
        return record

    result.quarantined.append(Quarantined(record_id, description, NO_SEGMENT))
    return None


class _BadValue(ValueError):
    """A group matched text its slot type cannot hold."""


def _slots(segment: Segment, match: "re.Match[str]",
           decimal: str) -> Dict[str, Any]:
    """Convert this match's groups into slot values.

    A group that did not participate in the match contributes nothing: the slot
    is **absent**, never null. A key whose value is null reads as "this was
    decoded and found empty", which is a different claim from "this was never
    decoded" — the same distinction ``pie_service._attributes_of`` keeps.
    """
    out: Dict[str, Any] = {}
    for binding in segment.fields:
        raw = match.group(binding.group)
        if binding.type == "flag":
            # Presence is the value. A flag slot is True when its group
            # participated and absent otherwise — never False, because "this
            # file does not say" and "this file says no" are different facts
            # and a pattern can only ever establish the first.
            if raw is not None:
                out[binding.slot] = True
            continue
        if raw is None:
            continue
        value = _convert(binding.type, raw, decimal)
        if value is None:
            raise _BadValue(
                f"group {binding.group!r} matched {raw!r}, which is not a "
                f"{binding.type} for slot {binding.slot!r}")
        out[binding.slot] = value
    return out


def _convert(slot_type: str, raw: str, decimal: str) -> Any:
    """One matched string as its slot's type, or None if it is not one.

    Numbers go through :class:`~decimal.Decimal` and come out as floats. The
    Decimal is what reads the artifact's decimal convention exactly; the float
    is what the record carries, because that is what every consumer of a
    decoded record already expects. Both steps are deterministic — Python's
    float repr is — so the same text always yields the same bytes.
    """
    text = raw.strip()
    if not text:
        return None
    if slot_type == "text":
        return text
    if slot_type == "integer":
        try:
            return int(text, 10)
        except ValueError:
            return None
    if slot_type == "number":
        if decimal == "dot" and "," in text:
            # A comma in a dot-convention file is a thousands separator or a
            # stray, and either way this module will not guess which. A file
            # that writes both declares `either`.
            return None
        normalised = (text.replace(",", ".")
                      if decimal in ("comma", "either") else text)
        if normalised.count(".") > 1:
            # Two separators is a grouped number, and reading it as a decimal
            # would invent a value. Refused, and the row is quarantined by name.
            return None
        try:
            return float(Decimal(normalised))
        except (InvalidOperation, ValueError, ArithmeticError):
            return None
    return None  # pragma: no cover — freeze() refuses an unknown type


def to_jsonl(records: Sequence[Dict[str, Any]]) -> bytes:
    """Decoded records as the bytes a catalogue file holds.

    The serialisation is part of the determinism claim, not an afterthought:
    sorted keys, no insignificant whitespace, one record per line, UTF-8, in
    the order they were decoded. Two decodes of the same file through the same
    decoder produce identical bytes, which is what makes the replay check in
    the gate a comparison of hashes rather than of parsed structures.
    """
    import json  # noqa: PLC0415 — kept local, as the only json use here

    return "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
        for record in records
    ).encode("utf-8")


def _cell(row: Sequence[str], index: int) -> str:
    try:
        return str(row[index]).strip()
    except IndexError:
        return ""
