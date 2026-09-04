"""The guarantee: a frozen decoder decodes the same way forever.

Inferring how a file should be read is a judgement and is not reproducible.
Decoding is, and these are what make that a fact rather than an intention.
Each test pins one of the properties ``app/decoding/__init__.py`` claims, and
the claim is worthless without the test — "deterministic" is exactly the kind
of property that stays true right up until a dict lands in the wrong place.

The refusal tests matter as much as the replay ones. Every check that could
reject a decoder happens at freeze, so a stored decoder is one already proven
to compile, to match its own examples and to reject its own counterexamples.
A freeze that let any of those through would be a decoder that fails on the
one file nobody re-ran.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import decoding
from app.decoding import FieldBinding, Segment

#: One shape of the shipped corpus, written by hand. Stage A ships no inference
#: — the point of the artifact is that it is data, so it can be authored — and
#: this is the artifact a person would write for these rows.
DRILL = Segment(
    id="sc-drill",
    pattern=(r"^SC DRILL (?P<dia>[0-9]+[.,][0-9]+)MM"
             r"(?: (?P<xd>[0-9]+)XD)?(?P<coolant> COOLANT)?"),
    fields=(FieldBinding("dia", "cutting_dia_mm", "number"),
            FieldBinding("xd", "depth_ratio_xd", "integer"),
            FieldBinding("coolant", "through_coolant", "flag")),
    examples=("SC DRILL 11,1MM 5XD COOLANT", "SC DRILL 8,0MM"),
    counterexamples=("M3X11 SCREW", "REAMER 12,00MM"),
    label="solid_carbide_drill",
)

INSERT = Segment(
    id="iso-insert",
    pattern=(r"^(?P<shape>[A-Z])(?P<clearance>[A-Z])(?P<tolerance>[A-Z])"
             r"(?P<fixing>[A-Z]) (?P<size>[0-9]{6})"),
    fields=(FieldBinding("shape", "iso_shape", "text"),
            FieldBinding("clearance", "iso_clearance_letter", "text"),
            FieldBinding("tolerance", "iso_tolerance", "text"),
            FieldBinding("fixing", "iso_fixing", "text")),
    examples=("CNMG 120408-49 - TN2000",),
    counterexamples=("SC DRILL 8,0MM",),
    label="turning_insert",
)

ROWS = [
    ["A1", "SC DRILL 11,1MM 5XD COOLANT", "KC7315"],
    ["A2", "SC DRILL 8,0MM", "KC7315"],
    ["A3", "CNMG 120408-49 - TN2000", "TN2000"],
    ["A4", "M3X11 SCREW", ""],
    ["A5", "", "KC7315"],
]


@pytest.fixture()
def decoder():
    return decoding.freeze([DRILL, INSERT], decimal="comma")


# ── the replay guarantee ────────────────────────────────────────────────────

def test_decoding_the_same_rows_twice_gives_identical_bytes(decoder):
    """The whole claim, at its smallest. Two decodes, one comparison of bytes
    rather than of parsed structures: a difference in key order or in a float's
    repr is a difference a structural comparison would forgive and a stored
    catalogue would not."""
    first = decoding.to_jsonl(decoding.decode(ROWS, decoder, source_sha256="s").records)
    second = decoding.to_jsonl(decoding.decode(ROWS, decoder, source_sha256="s").records)
    assert first == second
    assert first  # and it decoded something, so this is not vacuously true


def test_a_decoder_rebuilt_from_its_stored_json_is_the_same_decoder(decoder):
    """The round trip a redeploy makes: the artifact is stored as JSON and read
    back, and the decoder that comes out must have the same id and produce the
    same bytes. An id that moved across a round trip would make every record
    stamped before it unattributable."""
    stored = json.loads(json.dumps({**decoder.to_dict(),
                                    "decoder_id": decoder.decoder_id}))
    reloaded = decoding.from_dict(stored)
    assert reloaded.decoder_id == decoder.decoder_id
    assert (decoding.to_jsonl(decoding.decode(ROWS, reloaded).records)
            == decoding.to_jsonl(decoding.decode(ROWS, decoder).records))


def test_the_decoder_id_is_the_width_the_other_stamps_are(decoder):
    """The id stands beside `ruleset_checksum` and `run_id` on a record and
    replaces them, so it is the width they are. A stamp that looked like a
    different kind of thing from its neighbours would read as one."""
    assert len(decoder.decoder_id) == decoding.ID_WIDTH == 16
    assert decoder.decoder_id.isalnum()


def test_the_decoder_id_is_the_content_and_nothing_else(decoder):
    """Two decoders with the same rules are the same decoder, and any change to
    the rules is a different one. That is what lets a record name its decoder
    in one field: the id is the rules."""
    same = decoding.freeze([DRILL, INSERT], decimal="comma")
    assert same.decoder_id == decoder.decoder_id

    # Order is part of the decoder: first-match-wins, so swapping two segments
    # can change which one claims a row.
    reordered = decoding.freeze([INSERT, DRILL], decimal="comma")
    assert reordered.decoder_id != decoder.decoder_id

    # So is the decimal convention, and this is the one that would otherwise
    # look like a cosmetic setting: it decides whether 11,1 is eleven point one.
    dotted = decoding.freeze(
        [Segment(id=DRILL.id, pattern=DRILL.pattern, fields=DRILL.fields,
                 examples=DRILL.examples, counterexamples=DRILL.counterexamples,
                 label=DRILL.label), INSERT], decimal="dot")
    assert dotted.decoder_id != decoder.decoder_id


def test_every_record_names_the_decoder_and_the_file_that_produced_it(decoder):
    """Provenance on the row, not in a side table. Months later "why does this
    part number decode like this" is answerable from the record alone."""
    result = decoding.decode(ROWS, decoder, source_sha256="deadbeef")
    assert result.records
    for record in result.records:
        assert record["decoder_id"] == decoder.decoder_id
        assert record["source_sha256"] == "deadbeef"
        assert record["schema_version"] == decoding.SCHEMA_VERSION
        assert record["segment"] in {"sc-drill", "iso-insert"}


def test_the_decimal_convention_is_the_artifacts_not_the_machines(decoder):
    """`11,1` is eleven point one here because the artifact says so. Under the
    other convention the same text is not a number at all and the row is
    quarantined — refused rather than guessed, because guessing per row would
    make the decode depend on the rows already seen."""
    assert decoding.decode([ROWS[0]], decoder).records[0]["cutting_dia_mm"] == 11.1

    dotted = decoding.freeze([DRILL], decimal="dot")
    result = decoding.decode([ROWS[0]], dotted)
    assert result.records == []
    assert result.quarantined[0].reason == decoding.BAD_VALUE


# ── what a decode reports, and what it refuses to invent ────────────────────

def test_an_unclaimed_row_is_quarantined_by_name_and_never_half_decoded(decoder):
    """There is no fallback to fall back to, and that is the design. A row no
    segment claims is named as such; a partial decode would be the benign
    default §1 forbids."""
    result = decoding.decode(ROWS, decoder)
    reasons = {q.record_id: q.reason for q in result.quarantined}
    assert reasons == {"A4": decoding.NO_SEGMENT, "A5": decoding.NO_DESCRIPTION}
    assert {r["record_id"] for r in result.records} == {"A1", "A2", "A3"}
    assert result.summary()["by_segment"] == {"sc-drill": 2, "iso-insert": 1}


def test_a_slot_the_pattern_did_not_fill_is_absent_rather_than_null(decoder):
    """"Decoded and found nothing" and "never decoded" are different claims,
    and a null would say the first about the second — the distinction
    `pie_service._attributes_of` keeps for the same reason."""
    by_id = {r["record_id"]: r for r in decoding.decode(ROWS, decoder).records}
    assert by_id["A1"]["through_coolant"] is True
    assert by_id["A1"]["depth_ratio_xd"] == 5
    # A2 says nothing about coolant or depth, so the record says nothing.
    assert "through_coolant" not in by_id["A2"]
    assert "depth_ratio_xd" not in by_id["A2"]


def test_a_segment_that_claims_a_row_it_cannot_read_is_a_defect_not_a_fallthrough():
    """A binding that matches and will not convert is the decoder being wrong,
    so the row is quarantined by name. Falling through to a later segment would
    decode it through a shape nobody chose and hide the broken binding."""
    broken = decoding.freeze([
        Segment(id="broken", pattern=r"^SC DRILL (?P<n>[A-Z]+)",
                fields=(FieldBinding("n", "flute_count", "integer"),),
                examples=("SC DRILL ABC",)),
        DRILL,
    ], decimal="comma")
    result = decoding.decode([["A", "SC DRILL ABC", ""]], broken)
    assert result.records == []
    assert result.quarantined[0].reason == decoding.BAD_VALUE
    assert "flute_count" in result.quarantined[0].detail


def test_a_row_longer_than_the_bound_is_truncated_and_counted(decoder):
    """The bound is half the safety argument, so its use is reported. A row
    that decoded from its first 512 characters may still be missing what its
    tail said, and a silent truncation would be that loss unrecorded."""
    long_row = ["A9", "SC DRILL 9,0MM" + " X" * decoding.MAX_INPUT_LENGTH, ""]
    result = decoding.decode([long_row], decoder)
    assert result.truncated == 1
    assert result.records[0]["cutting_dia_mm"] == 9.0
    # The record keeps the whole description; only matching was bounded.
    assert result.records[0]["description_raw"] == long_row[1]


# ── what freeze refuses, so a decode never has to ───────────────────────────

def test_a_decoder_that_does_not_match_its_own_example_is_refused():
    with pytest.raises(decoding.DecoderError, match="own example"):
        decoding.freeze([Segment(id="s", pattern=r"^DRILL",
                                 examples=("MILL 12MM",))])


def test_a_decoder_that_matches_its_own_counterexample_is_refused():
    with pytest.raises(decoding.DecoderError, match="told not to"):
        decoding.freeze([Segment(id="s", pattern=r"DRILL",
                                 examples=("SC DRILL 8,0MM",),
                                 counterexamples=("DRILL TIP",))])


def test_a_binding_naming_a_group_or_a_slot_that_does_not_exist_is_refused():
    with pytest.raises(decoding.DecoderError, match="no group named"):
        decoding.freeze([Segment(id="s", pattern=r"^(?P<a>X)",
                                 fields=(FieldBinding("b", "grade", "text"),))])
    with pytest.raises(decoding.DecoderError, match="not a slot"):
        decoding.freeze([Segment(id="s", pattern=r"^(?P<a>X)",
                                 fields=(FieldBinding("a", "cuting_dia_mm",
                                                      "number"),))])
    # An `ext:` field is the way to keep a fact the vocabulary has no name for,
    # and it is still checked for shape rather than waved through.
    assert decoding.freeze([Segment(id="s", pattern=r"^(?P<a>X)",
                                    fields=(FieldBinding("a", "ext:kmt.style",
                                                         "text"),))])
    with pytest.raises(decoding.DecoderError, match="not a slot"):
        decoding.freeze([Segment(id="s", pattern=r"^(?P<a>X)",
                                 fields=(FieldBinding("a", "ext:nodot", "text"),))])


def test_two_bindings_cannot_fill_one_slot():
    """One attribute, one value. Two would be a record nobody can read, and the
    winner would be whichever binding came last."""
    with pytest.raises(decoding.DecoderError, match="two groups both fill"):
        decoding.freeze([Segment(
            id="s", pattern=r"^(?P<a>[0-9]+)x(?P<b>[0-9]+)",
            fields=(FieldBinding("a", "cutting_dia_mm", "number"),
                    FieldBinding("b", "cutting_dia_mm", "number")),
            examples=("12x8",))])


def test_two_segments_cannot_share_an_id():
    with pytest.raises(decoding.DecoderError, match="share the id"):
        decoding.freeze([Segment(id="s", pattern="A", examples=("A",)),
                         Segment(id="s", pattern="B", examples=("B",))])


def test_a_decoder_with_no_segments_is_refused():
    with pytest.raises(decoding.DecoderError, match="at least one segment"):
        decoding.freeze([])


def test_an_unstated_decimal_convention_is_refused():
    with pytest.raises(decoding.DecoderError, match="decimal convention"):
        decoding.freeze([DRILL], decimal="european")


def test_an_edited_artifact_is_refused_rather_than_decoded(decoder):
    """A stored artifact whose contents no longer hash to its id was changed
    after it was frozen, so the records stamped with that id were not decoded
    by this. Refused rather than silently re-identified.

    The edit here is the one every other check would wave through: flipping the
    decimal convention leaves every pattern compiling, every example matching
    and every binding valid, and changes eleven point one into a row that will
    not decode. Nothing structural can catch that. The content hash can, and
    that is what it is for.
    """
    tampered = {**decoder.to_dict(), "decoder_id": decoder.decoder_id}
    tampered["decimal"] = "dot"
    with pytest.raises(decoding.DecoderError, match="changed since it was frozen"):
        decoding.from_dict(tampered)

    # And without the claimed id it loads as what it now is — a different
    # decoder, with a different id, which is the honest reading of edited data.
    reframed = decoding.from_dict({k: v for k, v in tampered.items()
                                   if k != "decoder_id"})
    assert reframed.decoder_id != decoder.decoder_id


def test_a_decoder_from_another_executor_version_is_refused_not_attempted(decoder):
    """The failure that would destroy the guarantee quietly: a newer executor
    doing its best with an older artifact and producing records that disagree
    with the ones already stamped with that id."""
    from dataclasses import replace

    other = replace(decoder, schema_version=decoding.SCHEMA_VERSION + 1)
    with pytest.raises(decoding.DecoderVersionError, match="refused"):
        decoding.decode(ROWS, other)
    with pytest.raises(decoding.DecoderError, match="schema version"):
        decoding.freeze([DRILL], schema_version=decoding.SCHEMA_VERSION + 1)


# ── the pattern safety check, which is static because determinism needs it ──

@pytest.mark.parametrize("pattern", [
    r"(a+)+",                 # the classic
    r"(?:(?:a+))+",           # the same, written to defeat a substring search
    r"(a|aa)+b",              # ambiguous alternation under a repeat
    r"(?:x(?:a|aa))+",        # …nested one level down
    r"(?P<x>a)(?P=x)",        # a backreference
    r"(\d+\s*)*",             # two unbounded repeats, one inside the other
])
def test_a_pattern_that_could_backtrack_exponentially_is_never_stored(pattern):
    """Refused at freeze rather than timed out at decode, and the reason is
    determinism rather than taste: a wall-clock limit would make the same file
    decode differently on a loaded machine than on an idle one."""
    with pytest.raises(decoding.UnsafePattern):
        decoding.check_pattern(pattern)
    with pytest.raises(decoding.DecoderError):
        decoding.freeze([Segment(id="s", pattern=pattern)])


@pytest.mark.parametrize("pattern", [
    r"^SC DRILL (?P<d>[0-9,.]+)MM",
    r"^(?P<a>[A-Z]{4}) ?(?P<n>[0-9]{6})",
    r"(?:INS|INSERT)\.? (?P<c>[A-Z]{4})",
    r"^(?P<x>[A-Z0-9-]+)\s+(?P<y>.+)$",
])
def test_the_patterns_a_decoder_actually_needs_are_allowed(pattern):
    """The refusal is conservative, and this is the other half of that trade:
    it has to still permit the patterns a real price list needs, or the check
    would be a ban rather than a guard."""
    decoding.check_pattern(pattern)


def test_the_safety_check_fails_closed_when_it_cannot_check(monkeypatch):
    """A safety check that silently stops checking is worse than one that stops
    the build. It walks Python's own parse tree through a private name, so the
    day that name moves this must refuse everything and say so."""
    from app.decoding import safety

    monkeypatch.setattr(safety, "_parser", lambda: None)
    with pytest.raises(decoding.UnsafePattern, match="refused rather than run"):
        safety.check_pattern(r"^SC DRILL")


# ── the one thing that is not per file ──────────────────────────────────────

@pytest.mark.requires_pie
def test_the_slot_vocabulary_is_the_engines():
    """Declared in the portal, on `pie_service.ATTRIBUTE_FIELDS`' reasoning —
    it is the portal's statement of what a decoded record may hold, and
    importing the engine's copy would let a pinned-submodule bump silently
    widen it. Identical today, and this is what says so: a slot the engine does
    not have would be a name nothing downstream compares."""
    import sys

    from app.config import settings

    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine.model import CORE_SLOTS as ENGINE_SLOTS

    assert set(decoding.CORE_SLOTS) <= set(ENGINE_SLOTS), (
        "a slot the engine does not know is one nothing can compare: "
        f"{sorted(set(decoding.CORE_SLOTS) - set(ENGINE_SLOTS))}")


# ── the same guarantee, on the real file, at its real size ─────────────────

#: The shipped corpus's drill rows, decoded by one hand-authored segment.
#: Stage A ships no inference: the artifact is data, so it can be written, and
#: this is what a person would write having looked at the file.
#:
#: Both decimal spellings are in the pattern because both are in the file —
#: ``SC DRILL 5.1mm`` and ``SC DRILL 11,1mm``, same column, same export. That
#: is the case ``either`` exists for, and the test below measures what it buys.
CORPUS_DRILL = Segment(
    id="sc-drill",
    pattern=(r"^SC DRILL (?:(?P<line>[A-Z]{1,3}) )?"
             r"(?P<dia_mm>[0-9]+(?:[.,][0-9]+)?)[mM]{2}"
             r"/(?P<dia_in>\.[0-9]+)/ ?(?P<xd>[0-9]+)[xX][dD]"
             r"(?P<coolant> COOLANT)?"),
    fields=(FieldBinding("line", "line_code", "text"),
            FieldBinding("dia_mm", "cutting_dia_mm", "number"),
            FieldBinding("dia_in", "cutting_dia_inch", "number"),
            FieldBinding("xd", "depth_ratio_xd", "integer"),
            FieldBinding("coolant", "through_coolant", "flag")),
    examples=("SC DRILL 3mm/.1181/ 5xD",
              "SC DRILL 11,1mm/.4370/ 5xD COOLANT",
              "SC DRILL KU 7mm/.2756/ 5xD COOLANT"),
    counterexamples=("SC DRILL SLOT ENDMILL A D=1.5 Z=3 30°",
                     "END MILL 6mm"),
    label="solid_carbide_drill",
)


def _corpus_rows():
    import csv

    from app.config import settings

    with settings.PIE_CORPUS.open(encoding="utf-8") as fh:
        return [row for row in list(csv.reader(fh))[1:] if len(row) >= 3]


@pytest.mark.requires_pie
def test_the_guarantee_holds_over_the_whole_shipped_corpus():
    """6,717 real rows, decoded twice, compared as bytes.

    Every other test here runs on five hand-made rows, which is enough to pin
    the rules and not enough to be evidence about a real file. This one is the
    evidence: one hand-authored segment over the corpus that actually ships,
    claiming a real share of it, deterministic at that size.
    """
    rows = _corpus_rows()
    assert len(rows) == 6717

    decoder = decoding.freeze([CORPUS_DRILL], decimal="either")
    first = decoding.decode(rows, decoder, source_sha256="corpus")
    second = decoding.decode(rows, decoder, source_sha256="corpus")
    assert decoding.to_jsonl(first.records) == decoding.to_jsonl(second.records)

    # One segment, and it claims nearly every drill in the file. The rest are
    # other shapes — `SC DRILL Flat …`, `SC DRILL SLOT ENDMILL …` — which are
    # their own segments, not this one stretched to cover them.
    drills = sum(1 for r in rows if r[1].startswith("SC DRILL"))
    assert drills == 1273
    assert len(first.records) >= 1200
    assert first.summary()["by_segment"] == {"sc-drill": len(first.records)}

    # Everything it did not claim is named, not lost.
    assert (len(first.records) + len(first.quarantined)) == first.rows_read
    assert {q.reason for q in first.quarantined} == {decoding.NO_SEGMENT}

    # And a spot check that the values are the file's, not something rounded
    # into shape on the way through.
    by_id = {r["record_id"]: r for r in first.records}
    assert by_id["4150668"]["cutting_dia_mm"] == 5.1
    assert by_id["4150668"]["depth_ratio_xd"] == 5
    assert "through_coolant" not in by_id["4150668"]
    assert by_id["1913578"]["line_code"] == "KU"
    assert by_id["1913578"]["through_coolant"] is True


@pytest.mark.requires_pie
def test_the_mixed_decimal_convention_is_what_the_real_file_needs():
    """Why `either` exists, measured rather than argued.

    The same export writes `5.1mm` and `11,1mm`. Under `dot` the comma rows
    are refused — correctly, since a comma there could be a thousands
    separator and this module will not guess — and the cost of that correctness
    is hundreds of drills. The artifact declaring `either` is how a person says
    "in this file it is a decimal point", once, on the record.
    """
    rows = _corpus_rows()
    mixed = decoding.decode(
        rows, decoding.freeze([CORPUS_DRILL], decimal="either")).records
    dotted = decoding.decode(
        rows, decoding.freeze([CORPUS_DRILL], decimal="dot"))

    assert len(mixed) > len(dotted.records) + 300
    # Refused by name rather than decoded to a wrong number.
    assert any(q.reason == decoding.BAD_VALUE for q in dotted.quarantined)


# ── the golden file: bytes, committed ───────────────────────────────────────

GOLDEN = Path(__file__).parent / "fixtures" / "decoded_golden.jsonl"


def test_the_decode_still_produces_the_bytes_it_produced_when_this_landed(decoder):
    """The replay check, against bytes committed to the repository rather than
    computed here. Every other test in this file compares a decode to another
    decode in the same process, which cannot catch a change that moves both.
    This one can: if it fails, output that somebody's stored catalogue depends
    on has changed, and either the decoder id must change with it or the change
    must not ship."""
    produced = decoding.to_jsonl(
        decoding.decode(ROWS, decoder, source_sha256="fixture").records)
    assert produced == GOLDEN.read_bytes(), (
        "the decode changed. If that was deliberate, SCHEMA_VERSION has to "
        "move with it — a stored decoder must never decode differently under "
        "the same id — and this fixture is regenerated as part of that.")
    # And the decoder that produced them is named, so the fixture is not just
    # bytes: a change to the rules that left the output alone still moves this.
    assert decoder.decoder_id == json.loads(
        GOLDEN.read_bytes().splitlines()[0])["decoder_id"]
