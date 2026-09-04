"""Naming what a capture group holds — deterministically, where the file says so.

Stage B proposes structure, :mod:`app.decoding.evidence` measures what each
group caught, and this module answers the part of "what does it mean" that the
text itself settles. It is deliberately a small answer. A number followed by
``xD`` is a depth ratio because the vocabulary has exactly one ``_xd`` slot;
a number followed by ``mm`` is *some* millimetre dimension and the vocabulary
has eleven, so this module narrows to those eleven and stops. Choosing among
them needs to know that in ``16x16x56x110`` the first is the shank and the
third is the overall length, which is knowledge about the trade and not about
the file — the model step in ``decisions/decoder_binding.py``, confirmed by a
person.

**There is no shipped rule set here, and the distinction is worth being exact
about.** :data:`UNIT_TOKENS` is nine entries long and every one of them is a
*unit of measure or a unit-like counter*. It says what ``mm`` is, not how to
read a price list. The mapping from a token to a slot is then **derived from
:data:`~app.decoding.schema.CORE_SLOTS` itself** — ``xd`` binds because
``depth_ratio_xd`` is the only slot ending in ``_xd``, and if the vocabulary
grew a second one tomorrow this module would stop binding it and start
narrowing instead. Nothing here encodes a manufacturer, a family or a file
format, and there is no default to fall back to: a group this cannot name is
returned unnamed, with its candidates listed.

**A group that captured nothing is never bound.** Not with a plausible slot,
not with a low-confidence one. Stage B can propose an optional part that no row
in the file actually uses — measured: three of the eleven groups in the shipped
corpus' end-mill segment never participate in a match — and a binding on one of
those would be a claim with no evidence under it, which is the failure
``CLAUDE.md`` names as the benign default. It comes back with
:data:`NO_OCCURRENCES` and the reviewer sees why.

Every suggestion is unconfirmed. The surface layer is right often enough to be
worth having and wrong often enough that a person confirms it, exactly as
``ingestion.item_master.suggest_mapping`` guesses a column mapping and is named
as a guess everywhere it surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .evidence import GroupEvidence, SegmentEvidence
from .schema import CORE_SLOTS, SLOT_TYPES, Decoder, DecoderError, Segment, freeze

#: Tokens that are a unit of measure, or a counter written like one, together
#: with the vocabulary suffix each normalises to. Every entry is a spelling
#: this corpus actually uses — ``5.1mm``, ``.1181``\ ``in``, ``3xD``, ``4FL``,
#: ``140°`` — and nothing here is a product, a family or a format.
#:
#: The suffix is all this table contributes. Which slot a suffix reaches is
#: derived from :data:`~app.decoding.schema.CORE_SLOTS`, so the vocabulary
#: stays the single statement of what a decoded record may hold and this stays
#: a list of ways files spell a unit.
UNIT_TOKENS: Dict[str, str] = {
    "mm": "mm",
    "in": "inch",
    "inch": "inch",
    '"': "inch",
    "deg": "deg",
    "°": "deg",
    "xd": "xd",
    "fl": "flute_count",     # a slot, not a suffix: see _slots_for
    "fls": "flute_count",
}

#: Why a group has no suggestion. Stable strings, because a screen groups by
#: them and a reviewer learns to read them.
NO_OCCURRENCES = "NO_OCCURRENCES"
NO_UNIT = "NO_UNIT"
AMBIGUOUS_UNIT = "AMBIGUOUS_UNIT"
MIXED_VALUES = "MIXED_VALUES"
UNKNOWN_WORD = "UNKNOWN_WORD"
SLOT_TAKEN = "SLOT_TAKEN"

#: Why a group has one. Also stable.
FROM_UNIT = "FROM_UNIT"
FROM_WORD = "FROM_WORD"


@dataclass(frozen=True)
class Suggestion:
    """One group, and what is proposed for it — including nothing.

    ``slot`` is ``None`` for a group this step could not name, and that is a
    first-class outcome rather than a gap: ``reason`` says which of the five
    ways it declined, and ``candidates`` says what a person or the model step
    has to choose between. A suggestion is never confirmed here; confirming is
    what a person does, and :func:`apply_bindings` is what they confirm into.
    """

    segment: str
    group: str
    slot: Optional[str]
    type: Optional[str]
    #: ``surface`` for a suggestion from this module, ``model`` for one that
    #: came through the seam in ``decisions/``, ``none`` for a decline. Carried
    #: onto the review so a reviewer knows which answers a machine judged.
    source: str
    candidates: Tuple[str, ...]
    reason: str
    evidence: Optional[GroupEvidence] = None
    #: A sentence adding to ``reason`` where the code alone does not locate the
    #: problem — which other group took the slot, say.
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "segment": self.segment,
            "group": self.group,
            "slot": self.slot,
            "type": self.type,
            "source": self.source,
            "candidates": list(self.candidates),
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.evidence is not None:
            out["evidence"] = self.evidence.to_dict()
        return out


def suggest(segments: Sequence[SegmentEvidence]) -> Tuple[Suggestion, ...]:
    """A suggestion for every group of every segment, in evidence order.

    Every group appears, named or not. A review that listed only the groups
    this could name would be a review a person could complete without ever
    seeing the ones nobody has decided about.

    One slot is claimed at most once per segment, first in evidence order —
    which is left to right through the description. :func:`suggest_group` reads
    one group and cannot see that another has already taken its slot, so this
    is where that invariant lives, and it is not a formality: a set with two
    groups on one slot is one :func:`~app.decoding.schema.freeze` refuses, so
    without this a reviewer would be shown an error instead of a review.
    """
    out: List[Suggestion] = []
    for segment in segments:
        taken: Dict[str, str] = {}
        for group in segment.groups:
            suggestion = suggest_group(group)
            if suggestion.slot and suggestion.slot in taken:
                out.append(_decline(
                    group, SLOT_TAKEN, (suggestion.slot,),
                    detail=(f"group {taken[suggestion.slot]} of this segment "
                            f"already reads {suggestion.slot}")))
                continue
            if suggestion.slot:
                taken[suggestion.slot] = group.group
            out.append(suggestion)
    return tuple(out)


def suggest_group(group: GroupEvidence) -> Suggestion:
    """What the file's own text says this group holds, or why it says nothing."""
    if group.occurrences == 0:
        return _decline(group, NO_OCCURRENCES, ())
    if group.kind == "optional":
        return _from_word(group)
    return _from_unit(group)


def _from_unit(group: GroupEvidence) -> Suggestion:
    """A number, named by the unit written immediately after it.

    **After, and only after.** Every token in :data:`UNIT_TOKENS` is a suffix
    in this trade — ``5.1mm``, ``3xD``, ``4FL``, ``140°`` — and an earlier
    version also looked at the left-hand token, on the theory that a file might
    write ``dia 5.1``. It never caught one of those, because ``dia`` is not a
    unit and so was never in the table. What it caught instead was the *next*
    field's suffix: in ``GP SCEM 2FL 20x20x75x150`` the token before ``20`` is
    ``FL``, so the shank diameter was bound to ``flute_count`` — on seven
    segments of the shipped corpus, each a wrong slot with a real stamp on it.

    A file that writes a prefix unit is a convention this does not read, and
    that is a decline rather than a guess.
    """
    suffix = UNIT_TOKENS.get(group.right)
    if suffix is None:
        return _decline(group, NO_UNIT, CORE_SLOTS)
    candidates = _slots_for(suffix)
    if len(candidates) == 1:
        slot = candidates[0]
        return Suggestion(
            segment=group.segment, group=group.group, slot=slot,
            type=_type_for(group), source="surface", candidates=candidates,
            reason=FROM_UNIT, evidence=group)
    return _decline(group, AMBIGUOUS_UNIT, candidates)


def _from_word(group: GroupEvidence) -> Suggestion:
    """An optional part, named by the word it contains.

    An optional group is present or absent, so what it can carry is a flag —
    the executor's ``flag`` type, which is ``True`` when the group participated
    and **absent** otherwise, never ``False``. That asymmetry is the whole
    reason this is safe: a pattern can establish that a file said ``COOLANT``
    and can never establish that it said the tool has no through-coolant.

    Declines when the group's values are not one word. The shipped corpus folds
    ``COOLANT`` and ``MQL`` into a single optional group, and those are two
    different slots; one binding cannot express both, so this returns
    :data:`MIXED_VALUES` rather than picking the more common one. Splitting
    that group is inference's job, not this module's, and the decline is what
    makes it visible.
    """
    words = {value.strip().lower() for value in group.samples}
    words.discard("")
    if len(words) != 1:
        return _decline(group, MIXED_VALUES, ())
    word = next(iter(words))
    candidates = _slots_for(word)
    if len(candidates) == 1:
        return Suggestion(
            segment=group.segment, group=group.group, slot=candidates[0],
            type="flag", source="surface", candidates=candidates,
            reason=FROM_WORD, evidence=group)
    if candidates:
        return _decline(group, AMBIGUOUS_UNIT, candidates)
    return _decline(group, UNKNOWN_WORD, CORE_SLOTS)


def _slots_for(suffix: str) -> Tuple[str, ...]:
    """The vocabulary slots this token could be, derived from the vocabulary.

    A whole slot name matches itself — ``mql`` is a slot, so an optional group
    reading ``MQL`` reaches it directly. Otherwise the token is a trailing
    word: ``mm`` reaches all eleven ``*_mm`` slots and ``xd`` reaches the one
    ``*_xd`` slot. Derived rather than tabulated so a vocabulary change moves
    this with it — grow a second ``_xd`` slot and ``xD`` starts narrowing
    instead of binding, which is the correct new behaviour and needs no edit
    here.
    """
    if suffix in CORE_SLOTS:
        return (suffix,)
    return tuple(slot for slot in CORE_SLOTS
                 if slot.rsplit("_", 1)[-1] == suffix and "_" in slot)


def _type_for(group: GroupEvidence) -> str:
    """The narrowest executor type the group's own values support.

    ``integer`` only when every captured value in the file is one — a group
    that holds ``5`` in a thousand rows and ``5.1`` in one is a number, and
    typing it from the thousand would quarantine the one. ``text`` when the
    values are not numeric at all, which for a group Stage B built around a
    number position means the pattern is catching something else.
    """
    if group.all_integer:
        return "integer"
    if group.all_numeric:
        return "number"
    return "text"


def types_for(group: GroupEvidence) -> Tuple[str, ...]:
    """Every type this group's values would survive, narrowest first.

    What the seam validates a model's answer against: a reply typing a group
    ``integer`` when the file writes ``5.1`` in it is refused here rather than
    quarantining a thousand rows at decode time.
    """
    out: List[str] = []
    if group.all_integer:
        out.append("integer")
    if group.all_numeric:
        out.append("number")
    out.append("text")
    if group.kind == "optional":
        out.append("flag")
    return tuple(out)


def candidates_for(group: GroupEvidence) -> Tuple[str, ...]:
    """Every slot this group could plausibly fill, for the seam to choose in.

    The narrowed set when a unit narrows it, and the whole vocabulary when
    nothing does — an honest "could be anything" rather than a short list this
    module invented.

    **Empty means nothing may be bound here**, and there are two such cases.
    A group no row uses has no evidence to bind on. A group whose values are
    two different words cannot be one binding whatever slot is chosen, so
    offering candidates would invite an answer that is wrong by construction;
    the fix is for inference to split the group, and the empty list is what
    keeps that visible rather than papered over.
    """
    suggestion = suggest_group(group)
    if suggestion.candidates:
        return suggestion.candidates
    return (suggestion.slot,) if suggestion.slot else ()


def apply_bindings(decoder: Decoder,
                   bindings: Sequence[Mapping[str, str]]) -> Decoder:
    """Attach a confirmed binding set to a decoder, producing a **new** one.

    A new artifact, always, with its own :attr:`~Decoder.decoder_id` — never an
    edit in place. That is the freeze doing its job: rows already decoded were
    stamped with the id of a decoder that did not have these bindings, and
    changing what that id means is the one thing the whole design exists to
    prevent.

    A binding set is the **whole** answer for the segments it names, not a
    patch. Each named segment's fields are replaced, so which of its groups are
    unbound is a property of the set a person confirmed rather than of the
    order things were confirmed in. Segments the set does not name keep what
    they have.

    Everything else is :func:`~app.decoding.schema.freeze`'s to refuse — an
    unknown group, an unknown slot, a type this executor has no conversion
    for, two bindings on one slot, or a pattern that stops matching its own
    examples. This adds one check freeze cannot make, because freeze sees a
    segment and not a decoder: that every named segment exists.
    """
    known = {segment.id: segment for segment in decoder.segments}
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for binding in bindings:
        segment_id = str(binding.get("segment") or "")
        if segment_id not in known:
            raise DecoderError(
                f"This decoder has no segment {segment_id!r} to bind. Its "
                f"segments are: {', '.join(sorted(known)) or '(none)'}.")
        slot = str(binding.get("slot") or "")
        slot_type = str(binding.get("type") or "")
        if slot_type not in SLOT_TYPES:
            raise DecoderError(
                f"Segment {segment_id}: {slot_type!r} is not a slot type. Use "
                f"one of {', '.join(SLOT_TYPES)}.")
        grouped.setdefault(segment_id, []).append({
            "group": str(binding.get("group") or ""),
            "slot": slot,
            "type": slot_type,
        })

    rebuilt: List[Segment] = []
    for segment in decoder.segments:
        chosen = grouped.get(segment.id)
        if chosen is None:
            rebuilt.append(segment)
            continue
        rebuilt.append(Segment(
            id=segment.id,
            pattern=segment.pattern,
            fields=tuple(_binding(entry) for entry in chosen),
            examples=segment.examples,
            counterexamples=segment.counterexamples,
            label=segment.label,
        ))
    return freeze(rebuilt, decimal=decoder.decimal,
                  schema_version=decoder.schema_version)


def _binding(entry: Mapping[str, str]):
    from .schema import FieldBinding

    return FieldBinding(group=entry["group"], slot=entry["slot"],
                        type=entry["type"])


def _decline(group: GroupEvidence, reason: str, candidates: Tuple[str, ...],
             detail: str = "") -> Suggestion:
    return Suggestion(segment=group.segment, group=group.group, slot=None,
                      type=None, source="none", candidates=candidates,
                      reason=reason, evidence=group, detail=detail)
