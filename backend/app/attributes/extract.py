"""Decoded facts to attribute claims. Pure — no session, no engine, no file.

The split this module exists for: *extraction* decides what a decoded fact means
as a stored value, and *provenance* is stamped by the writer. So an
``AttributeClaim`` carries no ``source_kind``, no ``source_ref`` and no
``decoder_version``: those are properties of the run that produced it, they are
identical for every claim in one batch, and putting them on each claim would
invite two claims in one batch disagreeing about where they came from.

**What counts as a decoded fact field is not decided here.**
``master_health.geometry.NON_FACT_FIELDS`` is the one definition — every key the
engine emits that is *not* a fact about the product: the run stamps, the source
coordinates, the text that was read, and the statements about how the read went.
Both source kinds below project through that same set, which is the point: a
name decode and a catalogue record are the same engine emission read at
different times, and two field lists would eventually disagree about one product
with no way to tell which was right. :data:`ROUTE_FIELDS` is refused on top of
it, and is the one thing this module decides for itself — see the note there.

``pie_service.ATTRIBUTE_FIELDS`` was the near-match and is deliberately not
reused. It is a curated fourteen-field projection of *what a quote line may
show* — a presentation decision, deliberately narrow so a pack change cannot
silently widen a screen. Borrowing it here would have capped Phase 1 at a third
of what the engine decodes, and the engine decodes 44 distinct fact fields over
the 6,717-row catalogue: ``corner_radius_mm`` on 14.5% of rows, ``flute_count``
on 9.2%, ``coating`` on 4.8%. ``geometry._decoded_facts`` states the same
argument from the other side.

**Nothing decodable means nothing stored** (CLAUDE.md §1: absence of evidence is
not a pass). A null, a blank string, a value that will not fit its column — none
of them become a row. They are counted and returned in ``refused`` rather than
dropped, because a silent drop is how a coverage number ends up measuring the
dropper.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..master_health.geometry import NON_FACT_FIELDS

#: The two ``source_kind`` values this package writes. The vocabulary itself is
#: defined on ``ProductAttributeValue`` — four kinds, of which ``SOURCE_FILE``
#: (an imported PIM export) and ``HUMAN`` belong to writers that do not exist
#: yet. These are the strings this package stamps, not a second definition of
#: what they mean.
#:
#: The distinction is why the partial unique index keys on ``source_kind``:
#: reading ``CNMG 120408`` out of an item's own name and inheriting the same
#: field from the manufacturer catalogue record the item *is* are two different
#: claims — the second is the maker's own data and the first is a guess at it —
#: and which one wins is a read-time policy question. Collapsing them at write
#: time would throw away the disagreement, which is the only interesting thing
#: the pair can tell you.
DECODED_NAME = "DECODED_NAME"
CATALOGUE_LINK = "CATALOGUE_LINK"

#: Column widths on ``ProductAttributeValue``. Restated here so extraction can
#: refuse a value it cannot store, instead of handing the writer something that
#: raises on PostgreSQL and is silently truncated on SQLite — the two dialects
#: this repository runs on, disagreeing about data loss.
MAX_KEY_CHARS = 64
MAX_TEXT_CHARS = 255
MAX_UNIT_CHARS = 16

#: The route, refused on top of ``NON_FACT_FIELDS`` — a decision about what
#: belongs in this *store*, not a second opinion about which of the engine's
#: fields are facts.
#:
#: ``geometry`` already drops ``product_family``, on the narrower ground that
#: ``DecodeOutcome`` carries it as ``routed_family``. ``product_subfamily`` is
#: the other half of the same route and is not dropped, and it is emitted for
#: **every routed row**: "MISC BRACKET ASSEMBLY 4 OFF" and "OFFICE CHAIR" both
#: come back ``other_tooling`` / ``general``, which is precisely the misroute
#: ``geometry``'s own docstring measures — an EMUGE screwdriver and an ``M3X11``
#: screw both route to ``turning_insert``, and 11.6% of routed rows name a
#: manufacturer the pack does not cover.
#:
#: Storing it would do two things, both bad. A route is a classification the
#: engine declines to vouch for, and this table exists to hold facts a later
#: compatibility rule can gate on. And because it lands on essentially every
#: product, decision 002's exit criterion — published coverage — would read as
#: complete on the first run, with a bracket counted as a decorated product.
#: That is the "do not weaken a rule to make output appear" failure arriving
#: from the other direction: not a loosened threshold, a padded numerator.
#:
#: ``product_family`` is named here too although ``NON_FACT_FIELDS`` already
#: removes it, so that this rule is complete on its own terms rather than
#: correct only while another module's list stays as it is.
ROUTE_FIELDS: frozenset = frozenset({"product_family", "product_subfamily"})

#: The unit is already *in* the engine's field name — ``corner_radius_mm``,
#: ``cutting_dia_inch``, ``point_angle_deg``. This column is a projection of
#: that suffix and never a second source of truth: where the two could disagree
#: the key wins, and a key with no unit suffix stores NULL rather than a guess.
#: It is worth filling because a retrieval layer filtering ``value_num BETWEEN
#: 0.4 AND 0.8`` should be able to say what the 0.4 is in.
_UNIT_SUFFIXES: Tuple[Tuple[str, str], ...] = (
    ("_mm", "mm"), ("_inch", "inch"), ("_deg", "deg"),
)


@dataclass(frozen=True)
class AttributeClaim:
    """One storable fact about one product, before anyone says where it came from.

    ``value_text`` is always set and ``value_num`` only when the value is a
    number, which is the model's own contract: a retrieval layer cannot filter a
    range over text, and one that casts on every row cannot use an index.
    """

    attribute_key: str
    value_text: str
    value_num: Optional[float] = None
    #: What the source actually said, where the source said anything — the
    #: engine's ``field_meta[key]["raw"]``. ``None`` for a field the engine
    #: derived rather than read (``insert_polarity`` is DERIVED from the shape
    #: code, and no substring of the description spells it), and ``None`` for
    #: the whole DECODED_NAME path, which sees the values without their meta.
    #: Storing ``str(value)`` instead would claim the description contained
    #: "0.8" when what it contained was "08".
    original_value: Optional[str] = None
    unit: Optional[str] = None
    #: How well the value was READ. Never a ranking signal — the model docstring
    #: says so, and decision 008 exists because the confusion is easy to make.
    confidence: Optional[float] = None


@dataclass(frozen=True)
class Extraction:
    """The claims from one source, and every field that did not become one.

    ``refused`` is ``(attribute_key, reason)`` pairs. It exists so a field the
    extractor declined is visible in a report rather than inferred from a
    coverage number that came out lower than somebody expected.
    """

    claims: Tuple[AttributeClaim, ...] = ()
    refused: Tuple[Tuple[str, str], ...] = ()


def unit_for(attribute_key: str) -> Optional[str]:
    """The unit named by the field's own suffix, or ``None``."""
    for suffix, unit in _UNIT_SUFFIXES:
        if attribute_key.endswith(suffix):
            return unit if len(unit) <= MAX_UNIT_CHARS else None
    return None


def decoded_facts(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Every fact in one engine emission, metadata and nulls removed.

    The *knowledge* about which keys are metadata is entirely
    ``geometry.NON_FACT_FIELDS``; what is left is the filter, and it is a line
    long. ``geometry._decoded_facts`` does the same projection for the decode
    path and is private, so this is the same one line rather than a second
    opinion about which fields are facts. :data:`ROUTE_FIELDS` is this module's
    own and is applied to both paths, so the two source kinds stay describable
    by one sentence.

    Sorted, for the reason it is sorted there: this feeds something that writes
    and serialises rows, and dict order is byte order once it is serialised.
    """
    return {k: record[k] for k in sorted(record)
            if k not in NON_FACT_FIELDS and k not in ROUTE_FIELDS
            and record[k] is not None}


def _text_and_number(value: Any) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """``(value_text, value_num, refusal)`` for one decoded value.

    ``bool`` is tested before the numeric types on purpose: ``isinstance(True,
    int)`` is true in Python, so a boolean otherwise falls through into
    ``value_num`` as 1.0 and ``through_coolant`` — a flag on 11.7% of the
    catalogue — becomes a number a range filter happily matches. Booleans are
    text, lower-cased, the way JSON writes them and the way a caller would type
    them.

    A list or a dict is not a value. ``flags``, ``validations`` and
    ``unresolved_tokens`` are structures the engine emits beside the facts, and
    one row per fact cannot hold one; ``NON_FACT_FIELDS`` already excludes the
    ones that exist today, and this is what happens to the next one. Refused
    rather than stringified — a stringified list is a value nothing can query
    and everything must special-case.
    """
    if value is None:
        return None, None, "no value"
    if isinstance(value, bool):
        return ("true" if value else "false"), None, None
    if isinstance(value, (int, float, Decimal)):
        return str(value), float(value), None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            # An empty string is the empty-string row §1 forbids: on a screen it
            # reads as "the engine looked and found nothing", which is a claim
            # nobody made.
            return None, None, "blank"
        return stripped, None, None
    return None, None, f"not a scalar ({type(value).__name__})"


def _claim(attribute_key: str, value: Any, *, original: Any = None,
           confidence: Any = None) -> Tuple[Optional[AttributeClaim], Optional[str]]:
    if len(attribute_key) > MAX_KEY_CHARS:
        return None, "key too long to store"
    text, number, refusal = _text_and_number(value)
    if text is None:
        return None, refusal or "no value"
    if len(text) > MAX_TEXT_CHARS:
        # Refused rather than truncated. A truncated value is a *wrong* value
        # that reads as a right one, and this table's whole purpose is that a
        # later reader can trust what it says.
        return None, "value too long to store"
    raw: Optional[str] = None
    if original is not None:
        raw = str(original).strip()[:MAX_TEXT_CHARS] or None
    score: Optional[float] = None
    if isinstance(confidence, (int, float, Decimal)) and not isinstance(confidence, bool):
        score = float(confidence)
    return AttributeClaim(
        attribute_key=attribute_key,
        value_text=text,
        value_num=number,
        original_value=raw,
        unit=unit_for(attribute_key),
        confidence=score,
    ), None


def claims_from_values(values: Mapping[str, Any],
                       meta: Optional[Mapping[str, Any]] = None) -> Extraction:
    """Claims for a mapping of decoded field to value.

    ``meta`` is the engine's ``field_meta`` where the caller has it — one entry
    per field carrying ``confidence`` and ``raw``. Where it is absent both
    columns are left NULL, which is the honest answer: the confidence of a read
    nobody recorded is unknown, and inventing 1.0 for it would be exactly the
    benign default §1 refuses.

    Sorted by key, so two runs over the same input produce the same order —
    which is what makes the writer's flush order, and therefore a rerun,
    identical.
    """
    claims: List[AttributeClaim] = []
    refused: List[Tuple[str, str]] = []
    for key in sorted(values):
        entry = (meta or {}).get(key)
        entry = entry if isinstance(entry, Mapping) else {}
        claim, reason = _claim(key, values[key], original=entry.get("raw"),
                               confidence=entry.get("confidence"))
        if claim is None:
            refused.append((key, reason or "no value"))
        else:
            claims.append(claim)
    return Extraction(claims=tuple(claims), refused=tuple(refused))


def claims_from_decode(outcome: Any) -> Extraction:
    """Claims for one ``master_health.geometry.DecodeOutcome``.

    ``slots`` holds every fact that decoded and drops the nulls — the outcome's
    own contract, so a null cannot be mistaken for a decoded absence — and this
    reads whatever is in it. It has already been projected through
    ``NON_FACT_FIELDS``; only :data:`ROUTE_FIELDS` is left to remove, the same
    removal :func:`decoded_facts` makes on the other path.

    No ``meta``, because ``field_meta`` is one of the fields that projection
    removes: it is provenance about the reading rather than a fact about the
    product, and ``geometry``'s own note says an attribute row keyed
    ``row_confidence`` would be one join away from being ranked on. The cost is
    that this path stores no per-field confidence and no ``original_value``, and
    the honest form of that is NULL in both columns.

    ``routed_family`` is deliberately not a claim either, for the reason
    :data:`ROUTE_FIELDS` gives: a route is a guess the engine declines to vouch
    for, and a table whose whole value is that its rows can be trusted is the
    wrong home for one.
    """
    slots = getattr(outcome, "slots", None) or {}
    return claims_from_values({k: v for k, v in slots.items()
                               if k not in ROUTE_FIELDS})


def claims_from_catalogue_record(record: Mapping[str, Any]) -> Extraction:
    """Claims for one decoded manufacturer catalogue row.

    The same projection as the decode path — ``NON_FACT_FIELDS`` and then
    :data:`ROUTE_FIELDS` — plus the ``field_meta`` that path never sees. So this
    is the source kind that can say how well each value was read and what the
    description actually said, and the DECODED_NAME rows beside it cannot.
    """
    meta = record.get("field_meta")
    return claims_from_values(decoded_facts(record),
                              meta if isinstance(meta, Mapping) else None)
