"""Stage C: naming what each capture group holds, and refusing to guess.

Three things are pinned here, and they are different kinds of claim.

**Evidence is a measurement.** :mod:`app.decoding.evidence` reports what the
file put in each group — never what the pattern would allow — and reports it
the same way whatever order the rows arrive in. A reviewer looking at evidence
is looking at their own file.

**The surface binder is derived, not tabulated.** A number written ``3xD`` is a
depth ratio because ``depth_ratio_xd`` is the only slot in the vocabulary
ending ``_xd``; a number written ``5.1mm`` is one of eleven millimetre slots and
so is *declined*, with the eleven listed. That decline is the point of these
tests as much as the bindings are: on the shipped corpus twelve of three
hundred and thirty groups are named by the file's own text, and a step claiming
more than that would be guessing.

**The model can name a slot and cannot state a value.** Its whole output
vocabulary is an existing segment id, an existing group name, a slot from a
list this file narrowed, and one of four types. The gate refuses anything else
by comparing against evidence, so "AI never computes a number" holds by
construction rather than by review — and a provider that fails, stalls or
answers nonsense leaves the deterministic answer standing.
"""
from __future__ import annotations

import csv
import random

import pytest

from app import decoding
from app.ai.provider import ProviderTimeout, ProviderUnavailable
from app.decisions import decoder_binding
from app.decoding import bind, evidence, infer
from app.decoding.schema import CORE_SLOTS, DecoderError, FieldBinding, Segment


def _corpus_table():
    from app.config import settings

    with settings.PIE_CORPUS.open(encoding="utf-8") as fh:
        return [row for row in list(csv.reader(fh))[1:]
                if len(row) >= 3 and row[1]]


requires_pie = pytest.mark.skipif(
    not __import__("app.config", fromlist=["settings"]).settings.PIE_CORPUS.exists(),
    reason="pie-parser corpus not present")


#: A shape with one unambiguous unit (``xD``), one ambiguous one (``mm``), one
#: bare number and one optional word — the four outcomes the binder has.
DRILLS = [f"SC DRILL {d}mm/.{1000 + d}/ {x}xD" + (" COOLANT" if d % 2 else "")
          for d in range(3, 40) for x in (3, 5, 8)]
DRILLS += [f"SC DRILL {d},5mm/.{2000 + d}/ 5xD" for d in (4, 6, 8)]

#: The same shape with a *third* ending, so its optional group holds two
#: different words — the state the shipped corpus is in, where COOLANT and MQL
#: are folded together.
MIXED = ([f"SC DRILL {d}mm/.{1000 + d}/ 5xD" for d in range(3, 20)]
         + [f"SC DRILL {d}mm/.{1000 + d}/ 5xD COOLANT" for d in range(20, 32)]
         + [f"SC DRILL {d}mm/.{1000 + d}/ 5xD MQL" for d in range(32, 44)])

#: Four bare numbers separated by ``x``: nothing in the text says which is the
#: shank and which the overall length. The model step's actual job.
MILLS = [f"GP SC End Mill {f}FL {d}x{d}x{d * 2}x{d * 5}"
         for f in (2, 3, 4) for d in (6, 8, 10, 12, 16, 20)]


def _decoder(rows):
    proposal = infer.propose(rows)
    assert proposal.decoder is not None, proposal.reason
    return proposal.decoder


def _evidence(rows):
    return evidence.gather(_decoder(rows), rows)


def _group(rows, segment_id, group_name):
    for segment in _evidence(rows):
        if segment.segment != segment_id:
            continue
        for group in segment.groups:
            if group.group == group_name:
                return group
    raise AssertionError(f"no group {group_name} in {segment_id}")


def _only_segment(rows):
    segments = _evidence(rows)
    assert len(segments) == 1, [s.segment for s in segments]
    return segments[0]


# ── evidence is a measurement over the file ────────────────────────────────

def test_evidence_reports_what_the_file_put_in_a_group_not_what_the_pattern_allows():
    """The pattern for a number group accepts any number. If the file only ever
    writes one value there, that is the finding — and reading the pattern would
    have hidden it behind "accepts any number"."""
    rows = [f"WIDGET {n} 0mm" for n in range(3, 30)]
    segment = _only_segment(rows)
    second = [g for g in segment.groups if g.right == "mm"][0]
    assert second.distinct == 1
    assert second.samples == ("0",)
    assert second.occurrences == len(rows)


def test_evidence_does_not_depend_on_the_order_rows_were_read_in():
    """Two people looking at the same file must be looking at the same
    evidence, or a review is an argument about noise."""
    shuffled = list(DRILLS)
    random.Random(20260904).shuffle(shuffled)
    decoder = _decoder(DRILLS)
    assert (tuple(s.to_dict() for s in evidence.gather(decoder, DRILLS))
            == tuple(s.to_dict() for s in evidence.gather(decoder, shuffled)))


def test_a_group_no_row_uses_is_reported_with_no_occurrences():
    """Inference can fold in an optional part that the base skeleton then makes
    unreachable. Twenty-eight groups of the shipped corpus are like that, and
    the honest report is a zero rather than an absence."""
    segment = Segment(
        id="s1", pattern=r"^ITEM (?P<num1>\d+)(?P<opt1>ZZZ)?$",
        examples=("ITEM 4",))
    decoder = decoding.freeze([segment])
    gathered = evidence.gather(decoder, ["ITEM 4", "ITEM 5"])
    unused = [g for g in gathered[0].groups if g.group == "opt1"][0]
    assert unused.occurrences == 0
    assert unused.samples == ()
    assert unused.rows_matched == 2


def test_the_adjacent_token_is_folded_to_the_case_the_pattern_matches():
    """The corpus writes ``11,1mm`` and ``11,1MM``, and the pattern matches
    both because inference folded the case. Reporting them as two tokens made
    ``_unanimous`` answer "no unit" about a file that writes one on every
    row — measured at 1,175 against 64."""
    rows = [f"SC DRILL {n}mm 5xD" for n in range(3, 20)]
    rows += [f"SC DRILL {n}MM 5XD" for n in range(20, 30)]
    group = [g for g in _only_segment(rows).groups if g.group == "num1"][0]
    assert group.right == "mm"
    assert group.occurrences == len(rows)


def test_a_group_whose_neighbours_disagree_has_no_unit():
    """Unanimity, not the most common answer. A most-common token would assert
    a unit for a group that has two, which is the one thing evidence must not
    do — it is the input to a binding."""
    segment = Segment(id="s1", pattern=r"^ITEM (?P<num1>\d+)(?:mm|kg)$",
                      examples=("ITEM 4mm",))
    decoder = decoding.freeze([segment])
    gathered = evidence.gather(decoder, ["ITEM 4mm", "ITEM 9kg"])
    assert gathered[0].groups[0].right == ""


def test_evidence_counts_the_rows_a_decode_would_give_a_segment():
    """First-match-wins, exactly as the executor applies it. Counting what each
    pattern *could* reach would make a group's occurrences disagree with the
    decode that follows, and the coverage numbers unusable."""
    first = Segment(id="s1", pattern=r"^ITEM (?P<num1>\d+)", examples=("ITEM 4",))
    second = Segment(id="s2", pattern=r"^ITEM (?P<num2>\d+)", examples=("ITEM 4",))
    decoder = decoding.freeze([first, second])
    gathered = evidence.gather(decoder, ["ITEM 4", "ITEM 5"])
    assert gathered[0].rows_matched == 2
    assert gathered[1].rows_matched == 0
    assert gathered[1].groups[0].occurrences == 0


def test_bound_slots_reports_what_a_segment_already_binds():
    """A decoder coming back for a second review must show its existing
    answers rather than have them silently re-proposed."""
    segment = Segment(
        id="s1", pattern=r"^ITEM (?P<num1>\d+)xD$",
        fields=(FieldBinding(group="num1", slot="depth_ratio_xd",
                             type="integer"),),
        examples=("ITEM 4xD",))
    assert evidence.bound_slots(segment) == {"num1": "depth_ratio_xd"}
    assert evidence.bound_slots(Segment(id="s2", pattern="^x$")) == {}


# ── the surface binder: derived, and honest about what it cannot name ──────

def test_a_token_reaching_exactly_one_slot_names_it():
    """``xD`` binds because the vocabulary has exactly one ``_xd`` slot. Not
    because a table says so — see the derivation test below."""
    suggestion = bind.suggest_group(_group(DRILLS, "s1-sc-drill", "num3"))
    assert suggestion.slot == "depth_ratio_xd"
    assert suggestion.type == "integer"
    assert suggestion.source == "surface"
    assert suggestion.reason == bind.FROM_UNIT


def test_a_bare_millimetre_narrows_to_eleven_and_names_none_of_them():
    """The honest gap, and the reason there is a model step at all. ``5.1mm``
    is *a* millimetre dimension and the vocabulary has eleven; choosing needs
    to know that in ``16x16x56x110`` the third number is the length of cut,
    which is knowledge of the trade and not of the file."""
    suggestion = bind.suggest_group(_group(DRILLS, "s1-sc-drill", "num1"))
    assert suggestion.slot is None
    assert suggestion.reason == bind.AMBIGUOUS_UNIT
    assert len(suggestion.candidates) == 11
    assert "cutting_dia_mm" in suggestion.candidates
    assert all(slot.endswith("_mm") for slot in suggestion.candidates)


def test_the_slot_a_token_reaches_is_derived_from_the_vocabulary():
    """Not tabulated. ``UNIT_TOKENS`` says only what ``mm`` *is*; which slots
    that reaches comes out of ``CORE_SLOTS``, so growing a second ``_xd`` slot
    would make this stop binding ``xD`` and start narrowing — the correct new
    behaviour, with no edit to the binder."""
    for token, target in bind.UNIT_TOKENS.items():
        reached = bind._slots_for(target)
        assert reached, f"{token!r} reaches no slot"
        if target in CORE_SLOTS:
            assert reached == (target,)
        else:
            assert set(reached) == {slot for slot in CORE_SLOTS
                                    if slot.rsplit("_", 1)[-1] == target
                                    and "_" in slot}


def test_every_unit_token_is_a_unit_and_not_trade_knowledge():
    """The table is nine entries and each is a unit of measure or a counter
    written like one. A manufacturer, a family or a file format appearing here
    would be a shipped rule set wearing a smaller name."""
    assert set(bind.UNIT_TOKENS) == {"mm", "in", "inch", '"', "deg", "°",
                                     "xd", "fl", "fls"}


def test_a_group_that_captured_nothing_is_never_bound():
    """Absence of evidence is not a pass. A plausible slot on a group no row
    uses is a claim with nothing under it — and it would decode to nothing
    while reading, on a screen, as a decoded attribute."""
    segment = Segment(id="s1", pattern=r"^ITEM (?P<num1>\d+)(?P<opt1>\s*xD)?$",
                      examples=("ITEM 4",))
    decoder = decoding.freeze([segment])
    gathered = evidence.gather(decoder, ["ITEM 4", "ITEM 5"])
    unused = [g for g in gathered[0].groups if g.group == "opt1"][0]
    suggestion = bind.suggest_group(unused)
    assert suggestion.slot is None
    assert suggestion.reason == bind.NO_OCCURRENCES
    assert bind.candidates_for(unused) == ()


def test_a_unit_before_a_number_is_not_that_numbers_unit():
    """The regression. ``GP SCEM 2FL 20x20x75x150`` — the token before ``20``
    is ``FL``, and an earlier version read the shank diameter as a flute count
    on seven segments of the shipped corpus. Every token in the table is a
    suffix; looking left only ever found the previous field's."""
    shank = _group(MILLS, "s1-gp-sc", "num2")
    assert shank.left == "fl"
    suggestion = bind.suggest_group(shank)
    assert suggestion.slot is None
    assert suggestion.reason == bind.NO_UNIT


def test_one_slot_is_claimed_once_per_segment():
    """``suggest_group`` reads one group and cannot see that another took its
    slot, so ``suggest`` holds this. Without it a reviewer would be handed a
    set ``freeze`` refuses — an error where a review should be."""
    segment = Segment(
        id="s1", pattern=r"^ITEM (?P<num1>\d+)xD (?P<num2>\d+)xD$",
        examples=("ITEM 3xD 5xD",))
    decoder = decoding.freeze([segment])
    gathered = evidence.gather(decoder, ["ITEM 3xD 5xD", "ITEM 4xD 8xD"])
    first, second = bind.suggest(gathered)
    assert first.slot == "depth_ratio_xd"
    assert second.slot is None
    assert second.reason == bind.SLOT_TAKEN
    assert "num1" in second.detail
    # And therefore the set it produced is one that can actually be applied.
    decoding.apply_bindings(decoder, [
        {"segment": s.segment, "group": s.group, "slot": s.slot, "type": s.type}
        for s in (first, second) if s.slot])


def test_an_optional_group_holding_one_word_is_a_flag():
    """Presence is the value: the executor's ``flag`` is True when the group
    participated and **absent** otherwise, never False — because a pattern can
    establish that a file said COOLANT and never that it said the tool has
    none."""
    suggestion = bind.suggest_group(_group(DRILLS, "s1-sc-drill", "opt1"))
    assert suggestion.slot == "through_coolant"
    assert suggestion.type == "flag"
    assert suggestion.reason == bind.FROM_WORD


def test_an_optional_group_holding_two_words_declines_rather_than_picking_one():
    """The shipped corpus folds COOLANT and MQL into one optional group, and
    those are two slots. One binding cannot express both, so this declines —
    which makes a limitation of the inference step visible instead of resolving
    it by picking the more common word."""
    group = _group(MIXED, "s1-sc-drill", "opt1")
    assert {v.strip().lower() for v in group.samples} == {"coolant", "mql"}
    suggestion = bind.suggest_group(group)
    assert suggestion.slot is None
    assert suggestion.reason == bind.MIXED_VALUES
    # And nothing may be bound there: an answer would be wrong by
    # construction, so the seam does not ask and the gate accepts nothing.
    assert bind.candidates_for(group) == ()


def test_the_type_is_the_narrowest_the_files_own_values_survive():
    """A group holding 5 a thousand times and 5.1 once is a number. Typing it
    from the thousand would quarantine the one at decode time, which is a row
    silently lost to a decision made here."""
    integers = _group([f"ITEM {n}xD" for n in range(3, 30)], "s1-item-xd", "num1")
    assert bind.suggest_group(integers).type == "integer"
    assert bind.types_for(integers) == ("integer", "number", "text")

    mixed = _group([f"ITEM {n}xD" for n in range(3, 30)] + ["ITEM 5.1xD"],
                   "s1-item-xd", "num1")
    assert bind.suggest_group(mixed).type == "number"
    assert bind.types_for(mixed) == ("number", "text")


def test_a_decline_carries_the_slots_a_person_may_still_choose_from():
    """A decline is an open question, not a dead end. The suggestion a reviewer
    reads must say what may go there — an empty list is reserved for the two
    cases where nothing may (no occurrences, two different words)."""
    open_group = bind.suggest_group(_group(MILLS, "s1-gp-sc", "num2"))
    assert open_group.slot is None and open_group.candidates == CORE_SLOTS
    narrowed = bind.suggest_group(_group(DRILLS, "s1-sc-drill", "num1"))
    assert narrowed.slot is None and len(narrowed.candidates) == 11


def test_candidates_are_the_whole_vocabulary_when_nothing_narrows_it():
    """An honest "could be anything" rather than a short list the binder
    invented. It is what the model is allowed to choose from, so a narrower
    answer here would be this module quietly deciding."""
    assert bind.candidates_for(_group(MILLS, "s1-gp-sc", "num2")) == CORE_SLOTS


def test_every_group_appears_in_a_suggestion_named_or_not():
    """A review listing only the answered groups is one a person can finish
    without ever seeing what nobody decided about."""
    gathered = _evidence(DRILLS)
    assert (len(bind.suggest(gathered))
            == sum(len(segment.groups) for segment in gathered))


# ── applying a confirmed set: a new artifact, never an edit ────────────────

def test_binding_produces_a_new_artifact_and_leaves_the_old_one_alone():
    """Rows already decoded were stamped with the id of a decoder that did not
    have these bindings. Changing what that id means is the one thing the
    freeze exists to prevent."""
    decoder = _decoder(DRILLS)
    bound = decoding.apply_bindings(decoder, [
        {"segment": "s1-sc-drill", "group": "num3",
         "slot": "depth_ratio_xd", "type": "integer"}])
    assert bound.decoder_id != decoder.decoder_id
    assert decoder.segments[0].fields == ()
    assert bound.segments[0].fields[0].slot == "depth_ratio_xd"


def test_the_id_is_a_function_of_the_bindings():
    """Same set, same id; a different slot, a different id. What makes "which
    decoder produced this row" answerable from the row."""
    decoder = _decoder(DRILLS)
    one = {"segment": "s1-sc-drill", "group": "num1",
           "slot": "cutting_dia_mm", "type": "number"}
    other = dict(one, slot="shank_dia_mm")
    assert (decoding.apply_bindings(decoder, [one]).decoder_id
            == decoding.apply_bindings(decoder, [dict(one)]).decoder_id)
    assert (decoding.apply_bindings(decoder, [one]).decoder_id
            != decoding.apply_bindings(decoder, [other]).decoder_id)


def test_a_binding_set_is_the_whole_answer_for_the_segments_it_names():
    """Not a patch. Which groups of a segment are unbound is a property of the
    set a person confirmed, not of the order things were confirmed in."""
    decoder = _decoder(DRILLS)
    both = decoding.apply_bindings(decoder, [
        {"segment": "s1-sc-drill", "group": "num1",
         "slot": "cutting_dia_mm", "type": "number"},
        {"segment": "s1-sc-drill", "group": "num3",
         "slot": "depth_ratio_xd", "type": "integer"}])
    assert len(both.segments[0].fields) == 2
    fewer = decoding.apply_bindings(both, [
        {"segment": "s1-sc-drill", "group": "num3",
         "slot": "depth_ratio_xd", "type": "integer"}])
    assert [f.slot for f in fewer.segments[0].fields] == ["depth_ratio_xd"]


def test_apply_refuses_a_segment_this_decoder_does_not_have():
    """The one check ``freeze`` cannot make, because it sees a segment and not
    a decoder."""
    decoder = _decoder(DRILLS)
    with pytest.raises(DecoderError) as caught:
        decoding.apply_bindings(decoder, [
            {"segment": "nope", "group": "num1", "slot": "loc_mm",
             "type": "number"}])
    assert "no segment 'nope'" in str(caught.value)


@pytest.mark.parametrize("binding, expected", [
    ({"group": "num9", "slot": "loc_mm", "type": "number"}, "no group"),
    ({"group": "num1", "slot": "cutting_diameter", "type": "number"},
     "not a slot"),
    ({"group": "num1", "slot": "loc_mm", "type": "decimal"},
     "not a slot type"),
])
def test_apply_refuses_a_binding_that_would_not_decode(binding, expected):
    """Every rejection is at freeze, so a stored decoder is one already proven
    to compile and to name things the vocabulary knows."""
    decoder = _decoder(DRILLS)
    with pytest.raises(DecoderError) as caught:
        decoding.apply_bindings(decoder, [dict(binding, segment="s1-sc-drill")])
    assert expected in str(caught.value)


def test_apply_refuses_two_bindings_on_one_slot():
    decoder = _decoder(DRILLS)
    with pytest.raises(DecoderError) as caught:
        decoding.apply_bindings(decoder, [
            {"segment": "s1-sc-drill", "group": "num1",
             "slot": "loc_mm", "type": "number"},
            {"segment": "s1-sc-drill", "group": "num2",
             "slot": "loc_mm", "type": "number"}])
    assert "two groups both fill 'loc_mm'" in str(caught.value)


def test_a_bound_decoder_decodes_the_slots_and_replays_identically():
    """The two halves joined: a binding changes what a record holds, and
    nothing about whether it holds it reproducibly."""
    decoder = _decoder(DRILLS)
    bound = decoding.apply_bindings(decoder, [
        {"segment": "s1-sc-drill", "group": "num1",
         "slot": "cutting_dia_mm", "type": "number"},
        {"segment": "s1-sc-drill", "group": "num3",
         "slot": "depth_ratio_xd", "type": "integer"}])
    table = [[str(index), text, ""] for index, text in enumerate(DRILLS)]
    first = decoding.decode(table, bound, source_sha256="x")
    second = decoding.decode(table, bound, source_sha256="x")
    assert decoding.to_jsonl(first.records) == decoding.to_jsonl(second.records)
    assert all(record["cutting_dia_mm"] > 0 for record in first.records)
    assert {record["decoder_id"] for record in first.records} == {bound.decoder_id}
    # The unbound decoder over the same rows carries no attribute at all.
    plain = decoding.decode(table, decoder, source_sha256="x")
    assert not any("cutting_dia_mm" in record for record in plain.records)


# ── the seam: what a model may say, and what happens when it says else ────

class _Reply:
    """A provider that answers with a fixed binding list, whatever it is asked."""

    name = "stub"
    model = "stub-1"

    def __init__(self, *entries, envelope=None):
        self.entries = list(entries)
        self.envelope = envelope
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        import json

        self.prompts.append(user)
        if self.envelope is not None:
            return self.envelope
        return json.dumps({"bindings": self.entries})


class _Raises:
    name = "stub"
    model = "stub-1"

    def __init__(self, error):
        self.error = error

    def complete(self, system: str, user: str) -> str:
        raise self.error


def _review(rows, provider):
    return decoder_binding.review(_decoder(rows), rows, provider=provider)


def test_the_gate_refuses_a_slot_the_file_rules_out():
    """A group followed by ``mm`` cannot be an inch dimension however confident
    the reply is. The candidate list was narrowed from the file, so this is a
    comparison against evidence rather than a judgement about the answer."""
    result = _review(DRILLS, _Reply(
        {"segment": "s1-sc-drill", "group": "num1",
         "slot": "cutting_dia_inch", "type": "number"}))
    assert result.from_model == 0
    assert result.refused == {decoder_binding.SLOT_NOT_A_CANDIDATE: 1}


def test_the_gate_refuses_a_type_the_values_would_quarantine():
    """``5.1`` typed as an integer is a thousand rows lost at decode time to a
    decision made in a prompt."""
    result = _review(DRILLS, _Reply(
        {"segment": "s1-sc-drill", "group": "num1",
         "slot": "cutting_dia_mm", "type": "integer"}))
    assert result.from_model == 0
    assert result.refused == {decoder_binding.TYPE_NOT_SUPPORTED: 1}


def test_the_gate_refuses_a_group_the_file_already_settled():
    """Only groups that were *asked about* may be answered — so a model cannot
    overrule the file's own text with a judgement. ``num3`` reads ``3xD`` and
    the surface binder named it before any prompt was built."""
    result = _review(DRILLS, _Reply(
        {"segment": "s1-sc-drill", "group": "num3",
         "slot": "loc_mm", "type": "number"}))
    assert result.refused == {decoder_binding.UNKNOWN_TARGET: 1}
    named = {s.group: s for s in result.suggestions if s.slot}
    assert named["num3"].slot == "depth_ratio_xd"
    assert named["num3"].source == "surface"


@pytest.mark.parametrize("entry", [
    {"segment": "nope", "group": "num1", "slot": "loc_mm", "type": "number"},
    {"segment": "s1-sc-drill", "group": "num99", "slot": "loc_mm",
     "type": "number"},
    "not even an object",
])
def test_the_gate_refuses_an_entry_naming_nothing_real(entry):
    result = _review(DRILLS, _Reply(entry))
    assert result.from_model == 0
    assert result.refused == {decoder_binding.UNKNOWN_TARGET: 1}


def test_the_gate_keeps_the_good_entries_of_a_partly_wrong_reply():
    """A reply that names two groups and gets one wrong should still leave a
    reviewer the other. The counts are what make a mostly-wrong reply visible
    rather than a quietly thin review."""
    result = _review(DRILLS, _Reply(
        {"segment": "s1-sc-drill", "group": "num1",
         "slot": "cutting_dia_mm", "type": "number"},
        {"segment": "s1-sc-drill", "group": "num2",
         "slot": "cutting_dia_inch", "type": "integer"}))
    assert result.from_model == 1
    assert result.refused == {decoder_binding.TYPE_NOT_SUPPORTED: 1}
    named = {s.group: s for s in result.suggestions if s.slot}
    assert named["num1"].slot == "cutting_dia_mm"
    assert named["num1"].source == "model"


def test_the_gate_refuses_two_entries_on_one_slot():
    """One attribute cannot hold two values, and ``freeze`` would refuse the
    whole set — so it is refused here, where the reviewer still gets the rest.
    Evidence order breaks the tie, because that order is a property of the
    file."""
    result = _review(DRILLS, _Reply(
        {"segment": "s1-sc-drill", "group": "num2",
         "slot": "cutting_dia_mm", "type": "number"},
        {"segment": "s1-sc-drill", "group": "num1",
         "slot": "cutting_dia_mm", "type": "number"}))
    assert result.from_model == 1
    assert result.refused == {decoder_binding.DUPLICATE_SLOT: 1}
    named = {s.group: s for s in result.suggestions if s.source == "model"}
    assert set(named) == {"num1"}


def test_a_model_reply_cannot_carry_a_value_at_all():
    """The structural half of "AI never computes a number". The reply's whole
    vocabulary is a segment id, a group name, a slot and a type; extra keys are
    not sanitised, they are simply never read. So a model asserting that this
    drill is 9.99 mm has nowhere to put it — and the number the record ends up
    with is the one the file already contained, read by a frozen pattern."""
    result = _review(DRILLS, _Reply(
        {"segment": "s1-sc-drill", "group": "num1", "slot": "cutting_dia_mm",
         "type": "number", "value": 9.99, "cutting_dia_mm": 9.99,
         "confidence": 0.99, "pattern": "^.*$"}))
    assert result.from_model == 1
    suggestion = [s for s in result.suggestions if s.source == "model"][0]
    assert suggestion.to_dict()["slot"] == "cutting_dia_mm"
    assert "9.99" not in repr(suggestion.to_dict())

    bound = decoding.apply_bindings(_decoder(DRILLS), [
        {"segment": s.segment, "group": s.group, "slot": s.slot, "type": s.type}
        for s in result.suggestions if s.slot])
    assert "9.99" not in repr(bound.to_dict())
    assert bound.segments[0].pattern == _decoder(DRILLS).segments[0].pattern
    table = [[str(i), text, ""] for i, text in enumerate(DRILLS)]
    values = {r["cutting_dia_mm"]
              for r in decoding.decode(table, bound, source_sha256="x").records}
    assert 9.99 not in values


@pytest.mark.parametrize("error", [
    ProviderTimeout("slow"), ProviderUnavailable("down"),
    RuntimeError("something else entirely"),
])
def test_a_provider_that_fails_leaves_the_deterministic_answer_standing(error):
    """The floor is the file's own text, not an error page. A caller that gets
    a degraded review got something; one that got an exception got nothing —
    and this runs behind a screen."""
    result = _review(DRILLS, _Raises(error))
    assert result.reason == decoder_binding.PROVIDER_FAILED
    assert result.from_model == 0
    assert result.from_surface > 0
    assert {s.slot for s in result.suggestions if s.slot} == {"depth_ratio_xd",
                                                              "through_coolant"}


@pytest.mark.parametrize("envelope", [
    "this is not json {", "[]", '{"bindings": "num1"}', '{"other": []}', "",
])
def test_an_unreadable_reply_is_reported_unreadable_and_not_repaired(envelope):
    """A reply this cannot read is reported rather than parsed into something
    nobody sent."""
    result = _review(DRILLS, _Reply(envelope=envelope))
    assert result.reason == decoder_binding.UNREADABLE_REPLY
    assert result.from_model == 0


@pytest.mark.parametrize("envelope", [
    '```json\n{"bindings": [{"segment": "s1-sc-drill", "group": "num1",'
    ' "slot": "cutting_dia_mm", "type": "number"}]}\n```',
    'Here is the answer:\n{"bindings": [{"segment": "s1-sc-drill",'
    ' "group": "num1", "slot": "cutting_dia_mm", "type": "number"}]}',
])
def test_a_fenced_or_prefaced_reply_is_still_read(envelope):
    """The two things every provider does, neither of which changes what was
    said. Tolerant of exactly those and nothing else."""
    result = _review(DRILLS, _Reply(envelope=envelope))
    assert result.from_model == 1


def test_the_model_is_not_called_when_the_file_settles_everything():
    """Cost and honesty both: there is nothing to ask, so nothing is asked and
    the review says so."""
    rows = [f"ITEM {n}xD" for n in range(3, 40)]
    provider = _Reply()
    result = decoder_binding.review(_decoder(rows), rows, provider=provider)
    assert provider.prompts == []
    assert result.reason == decoder_binding.NOT_ASKED
    assert result.from_surface == 1


def test_the_prompt_carries_the_evidence_and_the_allowed_answers():
    """A group is judged from the shape it sits in — ``16x16x56x110`` is four
    bare numbers and one shape — so the segment's examples and pattern go with
    its groups, and the candidate slots go with each group."""
    provider = _Reply()
    decoder_binding.review(_decoder(MILLS), MILLS, provider=provider)
    prompt = provider.prompts[0]
    assert "## segment s1-gp-sc" in prompt
    # The lexicographically smallest claimed rows, so the prompt is a property
    # of the file rather than of the order it was read in.
    assert "example: GP SC End Mill 2FL 10x10x20x50" in prompt
    assert "candidate slots:" in prompt
    assert "cutting_dia_mm" in prompt
    assert "types: integer, number, text" in prompt


def test_the_review_covers_every_group_whatever_the_model_said():
    provider = _Reply({"segment": "s1-gp-sc", "group": "num2",
                       "slot": "shank_dia_mm", "type": "integer"})
    decoder = _decoder(MILLS)
    result = decoder_binding.review(decoder, MILLS, provider=provider)
    groups = sum(len(s.groups) for s in evidence.gather(decoder, MILLS))
    assert len(result.suggestions) == groups
    assert result.from_surface + result.from_model + result.unnamed == groups


def test_the_offline_default_contributes_nothing_invalid():
    """``select_provider`` returns the mock where no live provider is
    configured, and the mock answers about decisions rather than decoders. It
    must therefore contribute no bindings — and must not break the review."""
    result = decoder_binding.review(_decoder(DRILLS), DRILLS)
    assert result.from_model == 0
    assert result.from_surface > 0
    assert result.provider == "mock"


def test_a_declined_group_is_still_offered_with_its_candidates():
    """What a reviewer needs in order to answer it themselves."""
    result = _review(MILLS, _Raises(ProviderTimeout("slow")))
    unnamed = [s for s in result.suggestions if s.group == "num2"][0]
    assert unnamed.slot is None
    assert unnamed.candidates == CORE_SLOTS
    assert unnamed.evidence is not None
    assert unnamed.to_dict()["evidence"]["samples"]


# ── the layer boundary this whole arrangement exists to keep ───────────────

def test_the_decoding_package_names_no_model_anywhere():
    """``decoding/`` is deterministic by contract (``CLAUDE.md`` §1) and the
    binder is the module most tempted to reach for a model, so it is worth
    asserting here as well as in the layer-boundary test — this one names the
    file that would break it."""
    import pathlib

    import app.decoding as package

    for path in pathlib.Path(package.__file__).parent.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from ..ai" not in source, path
        assert "from app.ai" not in source, path
        assert "import ai" not in source, path


# ── the real file ─────────────────────────────────────────────────────────

@requires_pie
def test_the_real_corpus_is_named_only_where_its_own_text_says_so():
    """Measured, and the number is small on purpose: twelve of three hundred
    and thirty groups. A step claiming more than the text supports would be
    guessing, and the declines are grouped by reason so a reviewer can see
    *why* three hundred and eighteen are open."""
    from collections import Counter

    table = _corpus_table()
    descriptions = [row[1] for row in table]
    decoder = _decoder(descriptions)
    gathered = evidence.gather(decoder, descriptions)
    suggestions = bind.suggest(gathered)

    assert len(suggestions) == 330
    named = [s for s in suggestions if s.slot]
    assert len(named) == 12
    assert Counter(s.slot for s in named) == {
        "flute_count": 8, "depth_ratio_xd": 2, "cutting_dia_inch": 1,
        "wiper": 1}
    reasons = Counter(s.reason for s in suggestions)
    assert reasons[bind.AMBIGUOUS_UNIT] == 16      # a bare mm, eleven ways
    assert reasons[bind.NO_OCCURRENCES] == 28      # groups no row uses
    assert reasons[bind.NO_UNIT] > 200             # the model step's work
    # Every suggestion the binder made is one that can actually be applied.
    bound = decoding.apply_bindings(decoder, [
        {"segment": s.segment, "group": s.group, "slot": s.slot,
         "type": s.type} for s in named])
    assert bound.decoder_id != decoder.decoder_id


@requires_pie
def test_the_real_corpus_decodes_into_slots_and_replays_identically():
    """The whole pipeline on the real file: infer a decoder from it, name what
    its text names, freeze, decode, and get the same bytes twice."""
    table = _corpus_table()
    descriptions = [row[1] for row in table]
    decoder = _decoder(descriptions)
    suggestions = bind.suggest(evidence.gather(decoder, descriptions))
    bound = decoding.apply_bindings(decoder, [
        {"segment": s.segment, "group": s.group, "slot": s.slot,
         "type": s.type} for s in suggestions if s.slot])

    first = decoding.decode(table, bound, source_sha256="corpus")
    second = decoding.decode(table, bound, source_sha256="corpus")
    assert decoding.to_jsonl(first.records) == decoding.to_jsonl(second.records)
    assert len(first.records) > 4000
    filled = sum(1 for record in first.records
                 if "depth_ratio_xd" in record or "flute_count" in record)
    assert filled > 1600
    # No row was lost to a type the binder chose: the executor quarantines a
    # BAD_VALUE, so an unchanged count is the assertion that none was.
    plain = decoding.decode(table, decoder, source_sha256="corpus")
    assert len(first.records) == len(plain.records)
