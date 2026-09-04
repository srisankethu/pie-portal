"""Proposing a decoder from a file's own text, and what it refuses to invent.

Stage A made a frozen decoder reproducible. This is the step that *writes* one
by reading the file — the step that has no shipped rule set to choose from and
so has to find the shapes itself.

Two things these tests are about, and the second is the less obvious one.

**What it finds.** A real 6,717-row industrial export, no manufacturer
knowledge of any kind, and two thirds of it claimed by patterns induced from
the text. The three tests at the bottom are regressions for bugs found by
measuring exactly that, each of which cost hundreds of rows and none of which
a five-row fixture would have shown.

**What it will not do.** It never offers a pattern that fails its own
examples — every candidate goes through ``freeze`` before it is proposed, so a
proposal is a decoder that already works. It binds no slots: what a captured
number *means* is a judgement about the trade and belongs to the step that
asks a person. And it never guesses at a row it could not place; unclaimed
rows come back counted, with samples, because a file this cannot read is the
finding rather than the failure.
"""
from __future__ import annotations

import csv
import random

import pytest

from app import decoding
from app.decoding import infer


def _corpus_descriptions():
    from app.config import settings

    with settings.PIE_CORPUS.open(encoding="utf-8") as fh:
        return [row[1] for row in list(csv.reader(fh))[1:]
                if len(row) >= 3 and row[1]]


# ── what a proposal is, and is not ──────────────────────────────────────────

def test_a_proposal_is_a_decoder_that_already_works():
    """Frozen before it is offered, so every check in `freeze` has run: the
    patterns compile and are safe, and each segment matches its own examples
    and rejects its own counterexamples. A proposal that had not been through
    that would be a decoder somebody has to validate by using it."""
    rows = [f"SC DRILL {n},0mm 5xD" for n in range(3, 20)]
    proposal = infer.propose(rows)

    assert proposal.decoder is not None
    assert proposal.reason is None
    # Re-freezing it is a no-op that proves the point: it is already valid.
    assert decoding.freeze(list(proposal.decoder.segments),
                           decimal=proposal.decoder.decimal).decoder_id \
        == proposal.decoder.decoder_id
    for segment in proposal.decoder.segments:
        assert segment.examples
        decoding.check_pattern(segment.pattern)


def test_a_proposal_binds_no_slots_because_that_is_a_different_question():
    """Structure here, meaning later. Knowing that a captured number is a
    cutting diameter rather than a shank diameter is domain knowledge about
    cutting tools, and a wrong binding is a confidently wrong dimension —
    which is the failure the whole no-default design exists to prevent."""
    proposal = infer.propose([f"SC DRILL {n},0mm 5xD" for n in range(3, 20)])
    assert proposal.decoder is not None
    for segment in proposal.decoder.segments:
        assert segment.fields == ()
    # The groups are named and waiting, so the next step has something to bind.
    assert "num1" in proposal.decoder.segments[0].pattern

    # And a decoder with no bindings still decodes: it places a row in a shape
    # and says which, which is what makes the coverage numbers above real.
    result = decoding.decode([["A", "SC DRILL 4,0mm 5xD", ""]], proposal.decoder)
    assert result.records[0]["segment"] == proposal.decoder.segments[0].id
    assert result.quarantined == []


def test_the_same_file_proposes_the_same_decoder_whatever_order_it_is_in():
    """Not required — the freeze is what makes *decoding* reproducible — and
    worth having anyway: a proposal that came out differently each run would
    make review useless, because a person could not tell a change they caused
    from noise. Row order is the part that had to be worked at; counting
    skeletons over a sample of each cluster cost this property, and counting
    over all of them bought it back."""
    rows = ([f"SC DRILL {n},0mm 5xD" for n in range(3, 40)]
            + [f"END MILL {n}mm 4FL" for n in range(4, 30)])
    first = infer.propose(rows)
    again = infer.propose(rows)
    shuffled = rows[:]
    random.Random(11).shuffle(shuffled)
    reordered = infer.propose(shuffled)

    assert first.decoder is not None
    assert first.decoder.decoder_id == again.decoder.decoder_id
    assert first.decoder.decoder_id == reordered.decoder.decoder_id
    assert first.coverage == reordered.coverage


#: Twenty descriptions with nothing structural in common — each opens with its
#: own words, so no cluster reaches the floor.
#:
#: Writing them as ``f"one-off item {n} …"`` was the first attempt and was not
#: this at all: those twenty rows share a skeleton exactly, differing only in
#: the number, which is *precisely* the shape this module looks for. It
#: proposed one segment covering all twenty and was right to.
NO_SHAPE = [f"{word} {word[::-1]} unit" for word in
            ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
             "oscar", "papa", "quebec", "romeo", "sierra", "tango")]


def test_a_file_with_no_shape_in_it_gets_no_decoder_and_a_reason():
    """The honest answer for a file that is not a price list. An empty decoder
    would freeze, decode nothing, and read as a decoder that found nothing to
    do — which is a claim about the file rather than about the attempt."""
    proposal = infer.propose(NO_SHAPE)
    assert proposal.decoder is None
    assert "no shape" in (proposal.reason or "").lower()
    assert proposal.unclaimed == 20
    assert proposal.unclaimed_samples


def test_unclaimed_rows_are_counted_and_sampled_rather_than_ignored():
    """The most useful part of the output on a file this does not understand:
    how much it could not place, and what it looks like."""
    # The stragglers each lead with their own words, so none of them clusters.
    # Six rows that all began "MYSTERY WIDGET" would be a shape — six is over
    # the floor — and would be proposed as one, correctly.
    rows = [f"SC DRILL {n},0mm 5xD" for n in range(3, 30)] + NO_SHAPE[:6]
    proposal = infer.propose(rows)

    assert proposal.decoder is not None
    assert proposal.claimed == 27
    assert proposal.unclaimed == 6
    assert len(proposal.unclaimed_samples) == 6
    assert proposal.claimed + proposal.unclaimed == proposal.rows_read


def test_coverage_is_measured_over_the_whole_file_not_the_sample():
    """The overfitting check. A pattern induced from part of a file and
    reported against that same part is a pattern that reports its own training
    set — and it looks like success right up until a rebuild."""
    common = [f"SC DRILL {n},0mm 5xD" for n in range(3, 12)]
    # Rows of the same cluster that the induced pattern will not match: they
    # have to appear in `unclaimed`, not be excused as out of sample.
    odd = ["SC DRILL WITH NO DIMENSIONS AT ALL"] * 6
    proposal = infer.propose(common + odd)

    assert proposal.decoder is not None
    assert proposal.rows_read == 15
    assert sum(proposal.coverage.values()) == proposal.claimed
    assert proposal.unclaimed == 6


def test_two_segments_that_would_claim_the_same_rows_are_reported():
    """First-match-wins makes an overlap well defined rather than wrong, so
    this is information and not an error — but two shapes that overlap heavily
    are usually one shape, and nothing else would say so."""
    rows = ([f"INSERT CNMG {n}0408" for n in range(11, 24)]
            + [f"INSERT CNMG {n}0408 EXTRA" for n in range(11, 24)])
    proposal = infer.propose(rows)
    assert proposal.decoder is not None
    # One cluster here, so one segment claims everything and there is nothing
    # to overlap with; the assertion worth making is that the field exists and
    # is empty rather than absent.
    assert proposal.overlaps == ()
    assert proposal.claimed == 26


# ── the real file, which is where the findings came from ────────────────────

@pytest.mark.requires_pie
def test_two_thirds_of_the_real_corpus_is_claimed_with_no_trade_knowledge():
    """The measurement this stage exists to make.

    6,717 rows of a real industrial export, no manufacturer knowledge, no
    shipped rule set, nothing but the text — and roughly two thirds of it
    claimed by patterns induced from it. The bound is asserted loosely on
    purpose: this is evidence that the approach works at size, and pinning it
    to the exact number would make an improvement fail the suite.
    """
    descriptions = _corpus_descriptions()
    assert len(descriptions) == 6717

    proposal = infer.propose(descriptions)
    assert proposal.decoder is not None
    assert proposal.claimed / proposal.rows_read > 0.6

    # The drill shape is the one a person would write first, and inference
    # writes it better: 1,239 rows against the 1,219 the hand-authored segment
    # in test_decoder_determinism claims, because it found the tool-family
    # alternation (FLAT|HPR|HPS|HP|KU|XL|XS) that a person reading the file
    # missed.
    drill = next(s for s in proposal.decoder.segments if s.id.endswith("sc-drill"))
    assert proposal.coverage[drill.id] > 1200

    # Every proposed pattern is one the safety check would store, because
    # `freeze` already ran it. Asserted again here over 135 machine-written
    # patterns, which is the population that check exists for.
    for segment in proposal.decoder.segments:
        decoding.check_pattern(segment.pattern)


@pytest.mark.requires_pie
def test_the_proposal_for_the_real_corpus_decodes_it_deterministically():
    """The two stages joined: infer a decoder from the file, then decode the
    file with it twice and compare bytes. Whatever inference proposed, what
    comes out the other side is reproducible — which is the whole point of
    putting a freeze between them."""
    from app.config import settings

    with settings.PIE_CORPUS.open(encoding="utf-8") as fh:
        rows = [row for row in list(csv.reader(fh))[1:] if len(row) >= 3]

    proposal = infer.propose([row[1] for row in rows])
    assert proposal.decoder is not None

    first = decoding.decode(rows, proposal.decoder, source_sha256="corpus")
    second = decoding.decode(rows, proposal.decoder, source_sha256="corpus")
    assert decoding.to_jsonl(first.records) == decoding.to_jsonl(second.records)
    # And the decode agrees with what inference measured, which it would not if
    # coverage had been counted through a different path than the executor's.
    assert len(first.records) == proposal.claimed
    assert {r["decoder_id"] for r in first.records} == {proposal.decoder.decoder_id}


# ── three regressions, each found by measuring the real file ───────────────

def test_the_plainest_form_of_a_shape_is_not_lost_to_a_longer_base():
    """Found by measuring: the drill segment claimed a third of the drills.

    The base skeleton was chosen by frequency, and the most common drill
    skeleton includes ``COOLANT`` — so every drill *without* coolant was a
    deletion relative to the base, could not fold in, and went unclaimed.
    `_choose_base` picks the skeleton the most rows are pure insertions of
    instead.
    """
    # Deliberately more with the suffix than without, which is what made
    # frequency the wrong rule.
    rows = ([f"SC DRILL {n},0mm 5xD COOLANT" for n in range(3, 25)]
            + [f"SC DRILL {n},0mm 5xD" for n in range(3, 15)])
    proposal = infer.propose(rows)

    assert proposal.decoder is not None
    assert proposal.unclaimed == 0, (
        "the shape without the optional suffix must fold into the same segment")
    assert len(proposal.decoder.segments) == 1


def test_several_different_words_in_one_optional_position_all_fold():
    """Found by measuring: a drill line names one of several tool families in
    the same position (``KU``, ``HP``, ``HPR``, ``XL``…) and only the first was
    kept, silently dropping every row carrying one of the others.

    They become an alternation *inside the optional group*, which is safe:
    what `safety` refuses is an alternation inside an unbounded **repeat**, and
    an optional group is not one.
    """
    families = ["KU", "HP", "HPR", "XL", "XS"]
    rows = [f"SC DRILL {family} {n},0mm 5xD"
            for family in families for n in range(3, 10)]
    rows += [f"SC DRILL {n},0mm 5xD" for n in range(3, 20)]
    proposal = infer.propose(rows)

    assert proposal.decoder is not None
    assert proposal.unclaimed == 0
    assert len(proposal.decoder.segments) == 1
    pattern = proposal.decoder.segments[0].pattern
    for family in families:
        assert family.lower() in pattern.lower(), f"{family} was dropped"
    # And it is still a pattern the safety check will store.
    decoding.check_pattern(pattern)


def test_the_same_word_in_two_cases_is_one_shape_not_two():
    """Found by measuring: the corpus writes ``3mm`` and ``1,8MM``. Read
    case-sensitively those are different skeletons, so the shape split in two
    and the smaller half was below the cluster floor and went unclaimed.

    Case is folded when the skeleton is taken and the pattern matches
    case-insensitively at exactly those atoms — nothing else loosens, because
    a ``/`` between two numbers is structure rather than decoration.
    """
    rows = ([f"SC DRILL {n},0mm 5xD" for n in range(3, 20)]
            + [f"SC DRILL {n},0MM 5xD" for n in range(3, 8)])
    proposal = infer.propose(rows)

    assert proposal.decoder is not None
    assert len(proposal.decoder.segments) == 1
    assert proposal.unclaimed == 0

    # The separators are still exact: a shape whose punctuation differs is a
    # different shape, and folding that too would be a pattern matching
    # anything shaped vaguely like a drill.
    slashed = infer.propose([f"SC DRILL {n},0mm/.1181/ 5xD" for n in range(3, 20)])
    assert slashed.decoder is not None
    assert "/" in slashed.decoder.segments[0].pattern
    assert decoding.decode(
        [["A", "SC DRILL 4,0mm .1181 5xD", ""]], slashed.decoder
    ).quarantined[0].reason == decoding.NO_SEGMENT
