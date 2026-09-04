"""Propose a decoder for a file, from that file and nothing else.

This is the step that reads an unseen price list and works out what shapes of
description it contains — and it is **not** the step that decides what the
numbers in them mean. It proposes *structure*: here are six shapes, here is a
pattern for each, here are the parts that vary. Binding a varying part to
``cutting_dia_mm`` is a judgement about the trade, and it is Stage C.

**Deterministic, which is not required and is worth having.** The freeze
(:mod:`app.decoding.schema`) is what makes decoding reproducible, so nothing
here *has* to be — a proposal is reviewed before it becomes a decoder. But a
proposal that came out differently each run would make review useless: a person
could not tell a change they caused from noise, and two people looking at the
same file would be arguing about different things. So every ordering here is
explicit, every tie is broken by a stated rule, and the same file always yields
the same proposal.

**What it will not do.** It never proposes a pattern that fails its own
examples: every candidate goes through :func:`~app.decoding.schema.freeze`
before it is offered, so a proposal is a decoder that already works or it is
not a proposal. It computes no score for a segment and ranks nothing by
quality — segments come back in coverage order, which is a count, and whether
that coverage is good enough is a judgement it leaves alone. And it never
guesses at a row it could not place: unclaimed rows are reported as unclaimed,
with samples, because a file this cannot read is the useful finding.

The shape of the algorithm, and why each part:

1. **Tokenise** each description into atoms — runs of letters, runs that are a
   number, and single characters otherwise. A number is what varies between two
   rows of the same shape, so it is the thing worth finding.
2. **Cluster** by the leading two tokens with numbers masked. On the shipped
   corpus that yields ``SC DRILL`` (1,273 rows), ``ANSI/ISO Turning`` (1,016),
   ``GP SC`` (282) — the shapes a person would name looking at the file.
3. **Align** within a cluster against its most common skeleton, and turn the
   differences into *optional* parts. This is the step that matters: masking
   numbers alone splits the drills into ``SC DRILL #mm/.#/ #xD``,
   ``…#xD COOLANT`` and ``SC DRILL KU …`` as three unrelated shapes, when they
   are one shape with two optional pieces.
4. **Validate** the result over every row of the file, not the sample it was
   induced from — the overfitting check, and the reason a proposal carries
   coverage rather than a promise.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .schema import Decoder, DecoderError, Segment, freeze

#: How many leading tokens name a cluster. Two, measured against the shipped
#: corpus: one collapses every ``SC …`` product into a cluster nothing can
#: align, three splits ``SC DRILL KU …`` away from ``SC DRILL …`` before the
#: alignment step has a chance to notice that ``KU`` is optional.
PREFIX_TOKENS = 2

#: How many distinct skeletons in one cluster are folded into its pattern.
#: Each one contributes at most a few optional parts, and a pattern built from
#: forty of them is a pattern that matches nearly anything — which is the
#: overfitting failure running in the other direction.
MAX_VARIANTS = 12

#: A cluster smaller than this is not proposed as a segment. Not a judgement
#: about the rows — they are reported as unclaimed, which is honest — but a
#: pattern induced from two examples is a pattern about those two examples.
MIN_CLUSTER_ROWS = 5

#: How many real rows are carried on a segment as examples, and how many rows
#: from *other* clusters as counterexamples. Small: they are checked at freeze
#: on every load, and their job is to catch a pattern that stopped doing what
#: its author believed rather than to document the file.
EXAMPLES = 3
COUNTEREXAMPLES = 3

#: How many unclaimed rows a proposal carries as samples. Enough to see what
#: kind of thing was missed; not the whole tail, which can be thousands.
UNCLAIMED_SAMPLES = 12

#: A number, as this module recognises one: digits with an optional decimal
#: separator, or a bare fraction like ``.1181`` — which the shipped corpus
#: writes for the inch half of a dual-unit dimension.
_NUMBER = r"\d+(?:[.,]\d+)?|[.,]\d+"
_ATOM = re.compile(rf"(?P<num>{_NUMBER})|(?P<word>[A-Za-z]+)|(?P<other>.)")

#: What a number becomes in a skeleton. Not a character that can appear in a
#: description, so a skeleton can never be mistaken for one.
_MASK = "\x00"


@dataclass(frozen=True)
class Cluster:
    """One shape of description, and the rows that have it."""

    key: str
    rows: Tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.rows)


@dataclass
class Proposal:
    """What inference found: a decoder to review, and what it does to the file.

    The decoder is ``None`` when nothing could be proposed — a file of one-off
    descriptions with no shape in it. That is a real answer about the file and
    is reported as one, never as an empty decoder that would freeze and decode
    nothing.
    """

    decoder: Optional[Decoder]
    #: Rows claimed per segment id, measured over **every** row of the file.
    coverage: Dict[str, int] = field(default_factory=dict)
    rows_read: int = 0
    claimed: int = 0
    #: Rows no proposed segment matched, as a count and a handful of samples.
    #: The most useful part of the output on a file this does not understand.
    unclaimed: int = 0
    unclaimed_samples: Tuple[str, ...] = ()
    #: Segments whose rows another segment would also have claimed, had it come
    #: first. Not an error — first-match-wins makes it well defined — but it is
    #: what says two shapes are really one, or that the order matters more than
    #: it looks.
    overlaps: Tuple[Dict[str, Any], ...] = ()
    #: Why there is no decoder, when there is none.
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decoder": self.decoder.to_dict() if self.decoder else None,
            "decoder_id": self.decoder.decoder_id if self.decoder else None,
            "rows_read": self.rows_read,
            "claimed": self.claimed,
            "unclaimed": self.unclaimed,
            "unclaimed_samples": list(self.unclaimed_samples),
            "coverage": dict(self.coverage),
            "overlaps": [dict(o) for o in self.overlaps],
            "reason": self.reason,
        }


# ── 1. tokenising ───────────────────────────────────────────────────────────


def _atoms(text: str) -> Tuple[Tuple[str, str], ...]:
    """One description as ``(kind, text)`` atoms, kind being num/word/other.

    Numbers first in the alternation, so ``.1181`` is one atom and not a full
    stop followed by an integer — the inch half of ``3mm/.1181/`` would
    otherwise align against the wrong thing.
    """
    out: List[Tuple[str, str]] = []
    for match in _ATOM.finditer(text):
        kind = match.lastgroup or "other"
        out.append((kind, match.group()))
    return tuple(out)


def _skeleton(atoms: Sequence[Tuple[str, str]]) -> Tuple[str, ...]:
    """The atoms with every number masked and every word folded to lower case.

    Case is folded because a file that writes ``3mm`` in one row and ``1,8MM``
    in the next — the shipped corpus does — is writing one shape, and treating
    them as two would propose the same pattern twice and align neither against
    the other. The pattern built from a folded skeleton matches
    case-insensitively at exactly the atoms that were folded, so nothing else
    loosens (see :func:`_literal`).
    """
    return tuple(_MASK if kind == "num" else
                 (text.lower() if kind == "word" else text)
                 for kind, text in atoms)


# ── 2. clustering ───────────────────────────────────────────────────────────


def cluster(descriptions: Iterable[str]) -> List[Cluster]:
    """Group descriptions by their leading tokens, largest cluster first.

    Ordered by size and then by key, so the order is a property of the file
    rather than of the order it was read in — which is what lets two people
    looking at the same file discuss the same first segment.
    """
    groups: Dict[str, List[str]] = {}
    for description in descriptions:
        text = (description or "").strip()
        if not text:
            continue
        groups.setdefault(_cluster_key(text), []).append(text)
    return [Cluster(key=key, rows=tuple(rows))
            for key, rows in sorted(groups.items(),
                                    key=lambda kv: (-len(kv[1]), kv[0]))]


def _cluster_key(text: str) -> str:
    """The first :data:`PREFIX_TOKENS` whitespace tokens, numbers masked.

    Upper-cased, because a file that writes ``SC DRILL`` and ``SC Drill`` is
    writing one shape twice and clustering them apart would propose the same
    pattern twice.
    """
    masked = re.sub(_NUMBER, "#", text)
    return " ".join(masked.split()[:PREFIX_TOKENS]).upper()


# ── 3. aligning one cluster into a pattern ─────────────────────────────────


def _variants(rows: Sequence[str]) -> List[Tuple[Tuple[str, ...], int]]:
    """The distinct skeletons of a cluster, most common first.

    Counted over **every** row of the cluster rather than a sample of it, so
    the answer does not depend on the order the file was written in. Sampling
    the first N rows was the first implementation and cost order-invariance: a
    price list re-exported with its rows sorted differently proposed a
    different decoder, which would show up in review as a change nobody made.
    The expense here is the pairwise alignment further down, and that is bounded
    by :data:`MAX_VARIANTS`, not by the row count — counting skeletons is one
    cheap pass.

    Ties broken by the skeleton itself, so a file with two shapes of equal
    frequency proposes them in the same order every run.
    """
    counts = Counter(_skeleton(_atoms(row)) for row in rows)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_VARIANTS]


def _optional_inserts(base: Sequence[str],
                      variant: Sequence[str]) -> Optional[Dict[int, Tuple[str, ...]]]:
    """What ``variant`` adds to ``base``, or None if it also removes something.

    Only pure insertions are folded in, which is what makes the choice of base
    matter: measured against the *most common* skeleton, a shorter variant is a
    deletion and cannot fold, so the drill shape lost its own plainest form.
    :func:`_choose_base` picks the skeleton the most other rows are insertions
    of, for exactly that reason.

    A variant that genuinely drops part of the base is still refused. Making
    the base's own atoms optional, repeatedly, is how a pattern ends up
    matching everything — so such a variant is left to be its own segment, or
    to be unclaimed and reported.
    """
    inserts: Dict[int, Tuple[str, ...]] = {}
    matcher = difflib.SequenceMatcher(a=list(base), b=list(variant),
                                      autojunk=False)
    for tag, i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "insert":
            return None
        inserts[i1] = inserts.get(i1, ()) + tuple(variant[j1:j2])
    return inserts


def _choose_base(variants: Sequence[Tuple[Tuple[str, ...], int]]
                 ) -> Tuple[str, ...]:
    """The skeleton the rest of the cluster is best expressed as additions to.

    Scored by how many *rows* fold into it, not how many variants — a shape
    that lets in one 300-row variant is worth more than one that lets in three
    of five rows each. Ties break on the shorter skeleton and then
    lexicographically, so the answer is a property of the cluster and not of
    the order it was counted in.

    Choosing by frequency instead was the first implementation and was wrong in
    a way worth recording: the most common drill skeleton includes ``COOLANT``,
    so every drill *without* coolant became a deletion, could not fold, and the
    segment claimed a third of the rows it should have.
    """
    best: Optional[Tuple[Tuple[int, int, Tuple[str, ...]], Tuple[str, ...]]] = None
    for candidate, _count in variants:
        folded = sum(count for skeleton, count in variants
                     if _optional_inserts(candidate, skeleton) is not None)
        rank = (-folded, len(candidate), candidate)
        if best is None or rank < best[0]:
            best = (rank, candidate)
    assert best is not None  # variants is never empty here
    return best[1]


def _pattern_for(base: Sequence[str],
                 inserts: Dict[int, Sequence[Tuple[str, ...]]]) -> Tuple[str, int]:
    """A regex for one shape: literals escaped, numbers captured, extras optional.

    Whitespace in the base becomes ``\\s+`` rather than a literal space, because
    a file that writes one space in one row and two in another is writing the
    same shape and should not need two segments to say so.

    Several different runs at one position become an **alternation inside the
    optional group** — ``(?:\\s+KU|\\s+HP|\\s+HPR)?`` for a drill line that
    names one of several tool families there. Keeping only the first was the
    first implementation, and it silently dropped every row carrying one of the
    others. This is safe where ``safety`` refuses alternation: what that check
    forbids is an alternation *inside an unbounded repeat*, and an optional
    group is not one.

    Longest alternative first, so ``HP`` cannot claim the ``HP`` of ``HPR`` and
    leave the rest of the row unmatched. The engine would backtrack out of that
    anyway; ordering it away is cheaper than relying on it.
    """
    parts: List[str] = ["^"]
    numbers = 0
    optionals = 0
    for index in range(len(base) + 1):
        if index in inserts:
            optionals += 1
            runs = sorted(set(inserts[index]),
                          key=lambda run: (-len("".join(run)), run))
            bodies = ["".join(_literal(atom) for atom in run) for run in runs]
            body = bodies[0] if len(bodies) == 1 else "(?:" + "|".join(bodies) + ")"
            parts.append(f"(?P<opt{optionals}>{body})?")
        if index == len(base):
            break
        atom = base[index]
        if atom == _MASK:
            numbers += 1
            parts.append(f"(?P<num{numbers}>{_NUMBER})")
        else:
            parts.append(_literal(atom))
    return "".join(parts), numbers


def _literal(atom: str) -> str:
    """One skeleton atom as pattern text.

    Runs of whitespace are flexible, and a word matches either case — the two
    loosenings the skeleton already made when it folded them, applied here so
    the pattern matches what the skeleton said it would. Nothing else loosens:
    punctuation and separators are escaped exactly, because ``/`` between two
    numbers is structure and not decoration.
    """
    if atom.strip() == "":
        return r"\s+"
    if atom.isalpha():
        return f"(?i:{re.escape(atom)})"
    return re.escape(atom)


def _segment_for(index: int, group: Cluster,
                 others: Sequence[str]) -> Optional[Segment]:
    """One cluster as a candidate segment, or None if it will not hold.

    Returns None rather than raising when the induced pattern fails its own
    checks: a shape this could not express is a shape whose rows are better
    reported unclaimed than covered by a pattern nobody validated.
    """
    variants = _variants(group.rows)
    if not variants:
        return None
    base = _choose_base(variants)

    inserts: Dict[int, List[Tuple[str, ...]]] = {}
    for skeleton, _count in variants:
        if skeleton == base:
            continue
        found = _optional_inserts(base, skeleton)
        if found is None:
            continue
        for position, atoms in sorted(found.items()):
            runs = inserts.setdefault(position, [])
            if atoms not in runs:
                runs.append(atoms)

    pattern, numbers = _pattern_for(base, inserts)
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None

    matching = [row for row in group.rows if compiled.search(row)]
    if not matching:
        return None
    non_matching = [row for row in others if not compiled.search(row)]

    segment = Segment(
        id=f"s{index}-{_slug(group.key)}",
        pattern=pattern,
        # No bindings. This step proposes *structure*; what a captured number
        # means is a judgement about the trade and belongs to the step that
        # asks a person.
        fields=(),
        examples=tuple(sorted(matching)[:EXAMPLES]),
        counterexamples=tuple(sorted(non_matching)[:COUNTEREXAMPLES]),
        label=None,
    )
    if numbers == 0 and not inserts:
        # A pattern with nothing variable in it is a literal, and a literal is
        # not a shape — it is one description repeated. Still proposed, because
        # a file really can hold four hundred identical descriptions, but it is
        # worth knowing that is what happened.
        pass
    return segment


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:32] or "rows"


# ── 4. the proposal, validated over the whole file ─────────────────────────


def propose(descriptions: Sequence[str], *, decimal: str = "either") -> Proposal:
    """Read a file's descriptions and propose a decoder for them.

    ``decimal`` is the one thing this does not infer. Whether ``11,1`` is
    eleven point one or eleven thousand one hundred is not answerable from the
    text — both readings parse — and the default here is the permissive one
    because these are dimensions, with the consequence stated in
    :data:`~app.decoding.schema.DECIMAL_CONVENTIONS`. A person changes it, and
    the artifact records what they chose.

    The returned decoder is frozen, so it has already passed every check in
    :func:`~app.decoding.schema.freeze` — including that each segment matches
    its own examples and rejects its own counterexamples. What it has *not*
    passed is anybody's judgement about whether these are the right shapes,
    which is what the coverage numbers are for.
    """
    rows = [(d or "").strip() for d in descriptions]
    populated = [row for row in rows if row]
    if not populated:
        return Proposal(decoder=None, rows_read=len(rows),
                        reason="This file has no descriptions to read.")

    clusters = cluster(populated)
    big = [c for c in clusters if c.size >= MIN_CLUSTER_ROWS]
    if not big:
        return Proposal(
            decoder=None, rows_read=len(rows), unclaimed=len(populated),
            unclaimed_samples=tuple(sorted(populated)[:UNCLAIMED_SAMPLES]),
            reason=(f"No shape in this file repeats {MIN_CLUSTER_ROWS} times. "
                    f"Every description looks different from every other, so "
                    f"there is no pattern to propose — this file needs reading "
                    f"by hand, or it is not a price list."))

    segments: List[Segment] = []
    for index, group in enumerate(big, start=1):
        # Sorted, so which rows become counterexamples is a property of the
        # file and not of the order it arrived in.
        others = [row for other in big if other.key != group.key
                  for row in sorted(other.rows)[:COUNTEREXAMPLES]]
        segment = _segment_for(index, group, others)
        if segment is None:
            continue
        try:
            freeze([segment], decimal=decimal)
        except DecoderError:
            # A candidate that will not freeze is never offered. The rows it
            # would have covered are reported unclaimed, which is the honest
            # answer, and the alternative — offering it anyway — is how a
            # decoder that never worked reaches a catalogue.
            continue
        segments.append(segment)

    if not segments:
        return Proposal(
            decoder=None, rows_read=len(rows), unclaimed=len(populated),
            unclaimed_samples=tuple(sorted(populated)[:UNCLAIMED_SAMPLES]),
            reason=("Shapes repeat in this file, but none of them could be "
                    "expressed as a pattern that matches its own rows."))

    decoder = freeze(segments, decimal=decimal)
    return _measure(decoder, rows, populated)


def _measure(decoder: Decoder, rows: Sequence[str],
             populated: Sequence[str]) -> Proposal:
    """Run the proposal over **every** row, not the sample it came from.

    This is the overfitting check and the reason a proposal carries numbers
    rather than a promise. A pattern induced from 400 rows that fails at row
    8,000 looks like success right up until a rebuild, and the only thing that
    catches it is measuring the whole file.
    """
    compiled = [(segment.id, re.compile(segment.pattern))
                for segment in decoder.segments]
    coverage: Dict[str, int] = {segment.id: 0 for segment in decoder.segments}
    also_matched: Dict[Tuple[str, str], int] = {}
    unclaimed: List[str] = []

    for row in populated:
        winner: Optional[str] = None
        for segment_id, pattern in compiled:
            if pattern.search(row) is None:
                continue
            if winner is None:
                winner = segment_id
                coverage[segment_id] += 1
            else:
                # A later segment that would also have claimed this row. Not an
                # error — first-match-wins is well defined — but two shapes that
                # overlap this much are usually one shape.
                key = (winner, segment_id)
                also_matched[key] = also_matched.get(key, 0) + 1
        if winner is None:
            unclaimed.append(row)

    overlaps = tuple(
        {"claimed_by": first, "also_matched": second, "rows": count}
        for (first, second), count in sorted(also_matched.items(),
                                             key=lambda kv: (-kv[1], kv[0])))
    return Proposal(
        decoder=decoder,
        coverage=coverage,
        rows_read=len(rows),
        claimed=len(populated) - len(unclaimed),
        unclaimed=len(unclaimed),
        unclaimed_samples=tuple(sorted(unclaimed)[:UNCLAIMED_SAMPLES]),
        overlaps=overlaps,
    )
