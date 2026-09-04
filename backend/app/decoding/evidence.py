"""What each capture group of a decoder actually captured, over the whole file.

Stage B proposes *structure*: this shape has three varying numbers and an
optional ``COOLANT``. It says nothing about what those numbers mean, and it is
right not to — ``5.1`` in ``SC DRILL 5.1mm/.1181/ 3xD`` is a cutting diameter
because of what the trade calls that position, which is not a fact about the
text. Naming it is the binding step, and this module is what the binding step
reads.

**Evidence is gathered from matches, never from the pattern.** The pattern says
what its author intended a group to catch; the matches say what the file
actually put there. Those differ exactly where it matters — a group meant for a
diameter that in one file always captures ``0`` is the finding, and reading the
pattern would hide it. So every field here is a measurement over every row.

Nothing in this module decides anything. It computes no score, ranks no
candidate and proposes no slot: it reports occurrences, distinct values, the
literal text on either side, and whether every value was an integer. Those are
counts and strings a person can check against their own file, which is the
standard ``ingestion.item_master.ingest_report`` sets for the same reason —
a count is not something a person can check, but a named value is.

Deterministic, and it has to be: this is what a reviewer looks at, and evidence
that came out differently each run would make a review an argument about noise.
Every ordering is explicit; samples are the most frequent values, ties broken by
the value itself.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .schema import Decoder, Segment

#: How many distinct captured values are carried per group. Enough to see the
#: spread of a dimension; not so many that a review is reading the file.
SAMPLES = 8

#: How much text on either side of a captured span is looked at for the
#: adjacent token. A unit suffix is one or two characters (``mm``, ``xD``,
#: ``FL``); eight is generous and bounds the work.
CONTEXT = 8

#: How many real rows are carried per segment, to show what the shape looks
#: like. The lexicographically smallest, so they are a property of the file and
#: not of the order it was read in.
EXAMPLES = 3

#: The adjacent token, as this module recognises one: a run of letters (``mm``,
#: ``xD``), or the single character otherwise (``/``, ``°``, ``"``). Anything
#: longer is structure rather than a suffix and is not what this is for.
_TOKEN = re.compile(r"^[A-Za-z]+|^.")


@dataclass(frozen=True)
class GroupEvidence:
    """One capture group of one segment, and what the file put in it.

    ``left`` and ``right`` are the adjacent tokens **only when every occurrence
    agrees**. A group whose right-hand neighbour is ``mm`` in some rows and
    ``xD`` in others has no unit, and an empty string says exactly that — where
    a most-common answer would have quietly asserted one.
    """

    segment: str
    group: str
    #: ``number`` for a group Stage B built around a numeric position,
    #: ``optional`` for one built around a part that is sometimes absent. The
    #: distinction matters to binding: an optional part that is present or not
    #: is the shape of a flag, and a number never is.
    kind: str
    #: Rows the segment claimed. The denominator for everything below.
    rows_matched: int
    #: Rows where this group participated in the match. Equal to
    #: ``rows_matched`` for a required group; less for an optional one, and the
    #: gap is what says how optional it really is.
    occurrences: int
    distinct: int
    #: The most frequent distinct values, as they appear in the file.
    samples: Tuple[str, ...] = ()
    left: str = ""
    right: str = ""
    #: Whether every captured value is a base-ten integer, and whether every
    #: one is a number at all under some decimal convention. Both are facts
    #: about the text; neither chooses a type.
    all_integer: bool = False
    all_numeric: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment": self.segment,
            "group": self.group,
            "kind": self.kind,
            "rows_matched": self.rows_matched,
            "occurrences": self.occurrences,
            "distinct": self.distinct,
            "samples": list(self.samples),
            "left": self.left,
            "right": self.right,
            "all_integer": self.all_integer,
            "all_numeric": self.all_numeric,
        }


@dataclass(frozen=True)
class SegmentEvidence:
    """One segment's groups, in the order the pattern declares them."""

    segment: str
    pattern: str
    rows_matched: int
    groups: Tuple[GroupEvidence, ...]
    examples: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment": self.segment,
            "pattern": self.pattern,
            "rows_matched": self.rows_matched,
            "groups": [g.to_dict() for g in self.groups],
            "examples": list(self.examples),
        }


def gather(decoder: Decoder, descriptions: Sequence[str]) -> Tuple[SegmentEvidence, ...]:
    """Run the decoder's patterns over the file and report what each group caught.

    First-match-wins, exactly as :func:`app.decoding.executor.decode` applies
    them — so a group's occurrence count here is the count a decode would see,
    not the count the pattern could theoretically reach. A segment that claims
    nothing still comes back, with zeroes: "this shape matched no row" is the
    most useful thing evidence can say about it.
    """
    compiled = [(segment, re.compile(segment.pattern)) for segment in decoder.segments]
    caught: Dict[str, Dict[str, Counter]] = {
        segment.id: {} for segment in decoder.segments}
    context: Dict[str, Dict[str, Tuple[Counter, Counter]]] = {
        segment.id: {} for segment in decoder.segments}
    matched: Counter = Counter()
    claimed: Dict[str, List[str]] = {segment.id: [] for segment in decoder.segments}

    for raw in descriptions:
        text = (raw or "").strip()
        if not text:
            continue
        for segment, pattern in compiled:
            match = pattern.search(text)
            if match is None:
                continue
            matched[segment.id] += 1
            _keep_smallest(claimed[segment.id], text)
            for name in pattern.groupindex:
                value = match.group(name)
                if value is None:
                    continue
                caught[segment.id].setdefault(name, Counter())[value] += 1
                left, right = context[segment.id].setdefault(
                    name, (Counter(), Counter()))
                start, end = match.span(name)
                left[_token(text[max(0, start - CONTEXT):start], before=True)] += 1
                right[_token(text[end:end + CONTEXT], before=False)] += 1
            break

    out: List[SegmentEvidence] = []
    for segment, pattern in compiled:
        rows = matched[segment.id]
        groups: List[GroupEvidence] = []
        for name in _group_order(pattern):
            values = caught[segment.id].get(name, Counter())
            left, right = context[segment.id].get(name, (Counter(), Counter()))
            groups.append(GroupEvidence(
                segment=segment.id,
                group=name,
                kind=_kind(name),
                rows_matched=rows,
                occurrences=sum(values.values()),
                distinct=len(values),
                samples=_frequent(values),
                left=_unanimous(left),
                right=_unanimous(right),
                all_integer=bool(values) and all(
                    _is_integer(v) for v in values),
                all_numeric=bool(values) and all(_is_numeric(v) for v in values),
            ))
        out.append(SegmentEvidence(
            segment=segment.id,
            pattern=segment.pattern,
            rows_matched=rows,
            groups=tuple(groups),
            examples=tuple(claimed[segment.id]),
        ))
    return tuple(out)


def bound_slots(segment: Segment) -> Dict[str, str]:
    """Group name -> slot, for the groups this segment already binds.

    A decoder that has been through the binding step once and is being reviewed
    again needs its existing answers shown, not silently re-proposed.
    """
    return {binding.group: binding.slot for binding in segment.fields}


def _keep_smallest(kept: List[str], text: str) -> None:
    """Hold the lexicographically smallest :data:`EXAMPLES` rows seen so far.

    The examples must not depend on the order the rows arrived in. Taking the
    first three seen was order-dependent and
    ``test_evidence_does_not_depend_on_the_order_rows_were_read_in`` caught
    it — the same defect ``infer._variants`` had, for the same reason, and
    ``infer._segment_for`` fixes it the same way. Smallest-first rather than
    keeping every claimed row and sorting at the end, so a segment claiming
    six thousand rows costs three strings.
    """
    if text in kept:
        return
    if len(kept) < EXAMPLES:
        kept.append(text)
        kept.sort()
        return
    if text < kept[-1]:
        kept[-1] = text
        kept.sort()


def _group_order(pattern: "re.Pattern[str]") -> List[str]:
    """Group names in the order the pattern declares them.

    ``groupindex`` maps name to number, and the number is the declaration
    order — so this is left-to-right through the description, which is the
    order a person reads the row in.
    """
    return [name for name, _ in sorted(pattern.groupindex.items(),
                                       key=lambda kv: kv[1])]


def _kind(name: str) -> str:
    if name.startswith("num"):
        return "number"
    if name.startswith("opt"):
        return "optional"
    return "group"


def _frequent(values: Counter) -> Tuple[str, ...]:
    """The most common distinct values, ties broken by the value itself.

    Most common rather than first-seen or lexicographically first: a reviewer
    asking "what does this group hold" is best served by what it usually holds,
    and frequency is a count over the whole file rather than an artefact of
    where in it somebody looked.
    """
    return tuple(value for value, _ in
                 sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:SAMPLES])


def _unanimous(counter: Counter) -> str:
    """The token when every occurrence agrees, and an empty string otherwise."""
    if len(counter) != 1:
        return ""
    token = next(iter(counter))
    return token


def _token(text: str, *, before: bool) -> str:
    """The adjacent token of a snippet, folded to lower case, or empty.

    Looking backwards, the run nearest the span is at the *end* of the snippet,
    so it is reversed, matched and reversed back — a letter run reads the same
    either way, and a single character is a single character.

    **Case is folded, and it has to be.** The shipped corpus writes ``11,1mm``
    and ``11,1MM`` in the same column, and the pattern Stage B builds matches
    both — :func:`app.decoding.infer._literal` wraps every word atom in
    ``(?i:…)``. So two tokens differing only in case are one token as far as
    the decoder is concerned, and reporting them as two would make
    :func:`_unanimous` answer "this group has no unit" about a file that
    plainly writes one on every row. Measured: unfolded, ``num1`` of the drill
    segment had 1,175 ``mm`` against 64 ``MM`` and therefore no unit at all.

    The cost is that ``right`` shows ``mm`` for a file that wrote ``MM``. That
    is the pattern's own reading of the row rather than the row's spelling,
    which is the right thing for evidence about a decoder to report.
    """
    text = text.strip() if before else text
    if before:
        match = _TOKEN.match(text[::-1])
        token = match.group()[::-1] if match else ""
    else:
        match = _TOKEN.match(text)
        token = match.group() if match else ""
    return token.lower() if token.isalpha() else token


def _is_integer(text: str) -> bool:
    try:
        int(text.strip(), 10)
    except ValueError:
        return False
    return True


def _is_numeric(text: str) -> bool:
    body = text.strip().replace(",", ".", 1)
    if body.count(".") > 1 or not body:
        return False
    try:
        float(body)
    except ValueError:
        return False
    return True
