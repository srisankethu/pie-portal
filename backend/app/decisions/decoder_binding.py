"""Asking a model what a capture group means — the one interpreted step, gated.

This is the seam for the decoder pipeline, and it sits in ``decisions/`` for
the reason ``CLAUDE.md`` §3 gives: ``decoding/`` is a deterministic package and
must never import ``ai/``. Everything on the other side of this module —
freezing, safety, execution, evidence, the surface binder — is arithmetic and
regular expressions. This module is the only place a model is consulted about a
decoder, and nothing it returns is applied without a person.

**Why a model can be trusted here at all, given "AI never computes a number".**
It cannot compute one. Its entire output vocabulary is four fields, and none of
them can hold a value:

* ``segment`` — an id of a segment that already exists in the decoder;
* ``group`` — a name of a capture group that segment already declares;
* ``slot`` — a name from :data:`~app.decoding.schema.CORE_SLOTS`, and in fact
  from the narrowed candidate list this module supplies per group;
* ``type`` — one of the four the executor implements.

There is no field through which a measurement could arrive, so a model that
tried to assert that a drill is 5.1 mm has nowhere to put it. What it decides
is that *the number the file already contains at this position* is a cutting
diameter — a naming, and the exact judgement ``commercial/`` could not make and
a person should not have to make three hundred times. The number itself is read
out of the file by :mod:`app.decoding.executor` applying a frozen regular
expression, and it would be the same number under any binding.

The gate below enforces that structurally rather than by inspection, and it is
deterministic in the way ``ai/contract.validate_output`` is: every rejection is
a comparison against something computed from the file.

**Never raises, and the floor is the deterministic answer.** A provider that is
missing, misconfigured, slow or wrong degrades to the surface suggestions from
:mod:`app.decoding.bind` — which are fewer and honest — with a stated reason.
That is the same contract ``ai/provider.select_provider`` is built for: a
caller that gets a degraded answer got something; a caller that got an
exception got nothing.

**Nothing here is confirmed.** The output is a review. A person reads it,
changes what they disagree with, and confirms; only then does
:func:`app.decoding.bind.apply_bindings` freeze a new artifact.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from ..ai.provider import AIProvider, ProviderError, select_provider
from ..decoding import evidence as evidence_mod
from ..decoding import bind
from ..decoding.bind import Suggestion
from ..decoding.schema import Decoder

log = logging.getLogger("pie_portal.decisions.decoder_binding")

#: How many groups are put to the model in one call. A decoder for a real file
#: has a few hundred; asking about all of them in one prompt is a prompt whose
#: middle nothing reads. Batched by segment so a segment's groups are always
#: judged together — which number is the shank depends on which the others are.
MAX_GROUPS_PER_CALL = 40

#: Why an entry of the reply was refused. Counted and reported, never silent.
UNKNOWN_TARGET = "UNKNOWN_TARGET"
SLOT_NOT_A_CANDIDATE = "SLOT_NOT_A_CANDIDATE"
TYPE_NOT_SUPPORTED = "TYPE_NOT_SUPPORTED"
DUPLICATE_SLOT = "DUPLICATE_SLOT"

#: Why the model was not consulted, or its answer not used.
NOT_ASKED = "NOTHING_LEFT_TO_ASK"
PROVIDER_FAILED = "PROVIDER_FAILED"
UNREADABLE_REPLY = "UNREADABLE_REPLY"


@dataclass
class BindingReview:
    """Everything a person needs to confirm a binding set, and nothing applied.

    The suggestions cover **every** group, including the ones neither step
    could name — a review listing only the answered groups is one a person can
    finish without seeing what nobody decided.
    """

    decoder_id: str
    segments: Tuple[Any, ...] = ()
    suggestions: Tuple[Suggestion, ...] = ()
    #: How many suggestions came from the file's own text and how many from the
    #: model. Shown, because "a machine judged this" is what a reviewer is
    #: being asked to check.
    from_surface: int = 0
    from_model: int = 0
    unnamed: int = 0
    #: Entries of the model's reply the gate refused, as ``reason -> count``.
    #: A reply that is mostly refused is a finding about the prompt.
    refused: Dict[str, int] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    #: Why there is no model contribution, when there is none.
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decoder_id": self.decoder_id,
            "segments": [s.to_dict() for s in self.segments],
            "suggestions": [s.to_dict() for s in self.suggestions],
            "from_surface": self.from_surface,
            "from_model": self.from_model,
            "unnamed": self.unnamed,
            "refused": dict(self.refused),
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
        }


def review(decoder: Decoder, descriptions: Sequence[str], *,
           session: Optional[Session] = None,
           organization_id: Optional[str] = None,
           provider: Optional[AIProvider] = None) -> BindingReview:
    """Gather evidence, name what the file names, ask about the rest.

    In that order, and the order is the design: the deterministic answer is
    computed first and is never overridden. A group the file itself settles —
    a number written ``3xD`` — is bound from the text, and the model is asked
    only about groups where the text does not say. A model that disagreed with
    the file would be a model overruling evidence with judgement, so it is not
    given the chance.
    """
    segments = evidence_mod.gather(decoder, descriptions)
    surface = list(bind.suggest(segments))
    by_key = {(s.segment, s.group): index for index, s in enumerate(surface)}
    groups = {(g.segment, g.group): g
              for segment in segments for g in segment.groups}

    # Only groups something could actually be bound to. A group with no
    # candidates is one the gate would refuse every answer for, so asking about
    # it spends a prompt to be told nothing — see `bind.candidates_for`.
    asking = [s for s in surface
              if s.slot is None and bind.candidates_for(groups[(s.segment, s.group)])]
    result = BindingReview(decoder_id=decoder.decoder_id, segments=segments)
    if not asking:
        result.reason = NOT_ASKED
    else:
        chosen = provider or select_provider(session, organization_id)
        result.provider = getattr(chosen, "name", "")
        result.model = getattr(chosen, "model", "")
        accepted, refused, reason = _ask(chosen, segments, asking, groups)
        result.refused = refused
        result.reason = reason
        taken = {(s.segment, s.slot) for s in surface if s.slot}
        for suggestion in accepted:
            if (suggestion.segment, suggestion.slot) in taken:
                refused[DUPLICATE_SLOT] = refused.get(DUPLICATE_SLOT, 0) + 1
                continue
            taken.add((suggestion.segment, suggestion.slot))
            surface[by_key[(suggestion.segment, suggestion.group)]] = suggestion

    result.suggestions = tuple(surface)
    result.from_surface = sum(1 for s in surface if s.source == "surface")
    result.from_model = sum(1 for s in surface if s.source == "model")
    result.unnamed = sum(1 for s in surface if s.slot is None)
    return result


def _ask(provider: AIProvider, segments: Sequence[Any],
         asking: Sequence[Suggestion],
         groups: Dict[Tuple[str, str], Any],
         ) -> Tuple[List[Suggestion], Dict[str, int], Optional[str]]:
    """One or more provider calls, each answer gated before it is believed."""
    accepted: List[Suggestion] = []
    refused: Dict[str, int] = {}
    reason: Optional[str] = None
    examples = {s.segment: s.examples for s in segments}
    patterns = {s.segment: s.pattern for s in segments}

    for batch in _batches(asking):
        user = build_user(batch, groups, examples, patterns)
        try:
            raw = provider.complete(SYSTEM, user)
        except ProviderError as e:
            log.warning("Binding suggestions degraded to the file's own text: "
                        "the provider failed (%s).", e)
            reason = reason or PROVIDER_FAILED
            continue
        except Exception as e:  # noqa: BLE001 — a review must not 500 a screen
            log.warning("Binding suggestions degraded to the file's own text: "
                        "the provider raised %s.", e)
            reason = reason or PROVIDER_FAILED
            continue
        entries = _entries(raw)
        if entries is None:
            reason = reason or UNREADABLE_REPLY
            continue
        batch_accepted, batch_refused = gate(entries, batch, groups)
        accepted.extend(batch_accepted)
        for key, count in batch_refused.items():
            refused[key] = refused.get(key, 0) + count
    return accepted, refused, reason


def gate(entries: Sequence[Any], asked: Sequence[Suggestion],
         groups: Dict[Tuple[str, str], Any],
         ) -> Tuple[List[Suggestion], Dict[str, int]]:
    """Keep the entries the file's own evidence supports; count the rest.

    Deterministic, and every check is a comparison against something computed
    from the file rather than a judgement about the reply:

    * the entry names a segment and group that were **asked about** — not
      merely ones that exist, so a reply cannot rebind a group the surface
      layer already settled from the text;
    * the slot is in that group's candidate list, which
      :func:`app.decoding.bind.candidates_for` narrowed from the unit written
      in the file — so a group followed by ``mm`` cannot be typed as an inch
      dimension however confident the reply is;
    * the type is one the group's own values survive, so a group holding
      ``5.1`` cannot be made an integer and quarantine a thousand rows at
      decode time;
    * no two entries fill one slot of one segment. The first in evidence order
      wins, because that order is a property of the file.

    Entries are dropped individually rather than failing the batch: a reply
    that names forty groups and gets two wrong should still leave a reviewer
    thirty-eight. The counts are what makes a mostly-wrong reply visible.
    """
    allowed = {(s.segment, s.group) for s in asked}
    order = {(s.segment, s.group): index for index, s in enumerate(asked)}
    accepted: Dict[Tuple[str, str], Suggestion] = {}
    refused: Dict[str, int] = {}
    taken: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def deny(code: str) -> None:
        refused[code] = refused.get(code, 0) + 1

    for entry in entries:
        if not isinstance(entry, dict):
            deny(UNKNOWN_TARGET)
            continue
        key = (str(entry.get("segment") or ""), str(entry.get("group") or ""))
        if key not in allowed:
            deny(UNKNOWN_TARGET)
            continue
        group = groups[key]
        slot = str(entry.get("slot") or "")
        if slot not in bind.candidates_for(group):
            deny(SLOT_NOT_A_CANDIDATE)
            continue
        slot_type = str(entry.get("type") or "")
        if slot_type not in bind.types_for(group):
            deny(TYPE_NOT_SUPPORTED)
            continue
        slot_key = (key[0], slot)
        if slot_key in taken:
            if order[key] >= order[taken[slot_key]]:
                deny(DUPLICATE_SLOT)
                continue
            accepted.pop(taken[slot_key], None)
            deny(DUPLICATE_SLOT)
        taken[slot_key] = key
        accepted[key] = Suggestion(
            segment=key[0], group=key[1], slot=slot, type=slot_type,
            source="model", candidates=bind.candidates_for(group),
            reason="FROM_MODEL", evidence=group)

    return [accepted[key] for key in sorted(accepted, key=lambda k: order[k])], refused


def _entries(raw: str) -> Optional[List[Any]]:
    """The reply's ``bindings`` list, or None if there is not one.

    Tolerant of a fenced code block and of leading prose, because those are the
    two things every provider does and neither changes what was said. Not
    tolerant of anything else: a reply this cannot read is reported unreadable
    rather than repaired into something nobody sent.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start = text.find("{")
    if start > 0:
        text = text[start:]
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    entries = payload.get("bindings")
    return entries if isinstance(entries, list) else None


def _batches(asking: Sequence[Suggestion]) -> List[List[Suggestion]]:
    """Groups in batches, never splitting a segment across two calls."""
    out: List[List[Suggestion]] = []
    current: List[Suggestion] = []
    for suggestion in asking:
        if (current and len(current) >= MAX_GROUPS_PER_CALL
                and suggestion.segment != current[-1].segment):
            out.append(current)
            current = []
        current.append(suggestion)
    if current:
        out.append(current)
    return out


SYSTEM = """\
You name parts of a product description for a cutting-tool catalogue.

You are given shapes of description found in one price list. Each shape has a \
pattern with named capture groups, and for each group you are told what that \
group actually captured across the whole file: how often, how many distinct \
values, a sample of them, and the text written immediately before and after.

For each group, say which attribute slot it fills. Choose only from the \
candidate slots listed for that group and only from the types listed for it. \
If you cannot tell which slot a group fills, leave it out — an omission is a \
correct answer and a guess is not.

You never report a measurement. You are not asked what size anything is; the \
file already contains the values and they are read from it deterministically. \
You are asked only which named slot each position corresponds to.

Reply with JSON and nothing else:

{"bindings": [{"segment": "...", "group": "...", "slot": "...", "type": "..."}]}
"""


def build_user(batch: Sequence[Suggestion], groups: Dict[Tuple[str, str], Any],
               examples: Dict[str, Tuple[str, ...]],
               patterns: Dict[str, str]) -> str:
    """The evidence for one batch, as the text the provider is handed.

    Grouped by segment with its examples and pattern at the top, because which
    number is the shank diameter is answerable only from the others around it —
    ``16x16x56x110`` is four bare numbers and one shape.
    """
    lines: List[str] = []
    current = None
    for suggestion in batch:
        if suggestion.segment != current:
            current = suggestion.segment
            lines.append(f"\n## segment {current}")
            lines.append(f"pattern: {patterns.get(current, '')}")
            for example in examples.get(current, ())[:3]:
                lines.append(f"example: {example}")
        group = groups[(suggestion.segment, suggestion.group)]
        lines.append(
            f"- group {group.group} ({group.kind}): captured in "
            f"{group.occurrences} of {group.rows_matched} rows, "
            f"{group.distinct} distinct")
        lines.append(f"  before: {group.left!r}   after: {group.right!r}")
        lines.append(f"  values: {', '.join(group.samples)}")
        lines.append(f"  candidate slots: {', '.join(bind.candidates_for(group))}")
        lines.append(f"  types: {', '.join(bind.types_for(group))}")
    return "\n".join(lines).strip()
