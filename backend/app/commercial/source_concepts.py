""""In this organization's ERP, that field means this concept" — and the
reading of one record through it.

``source_attributes`` carries every custom field an administrator configured,
verbatim, under the keys their own system wrote. It is deliberately
uninterpreted: ``normalize._source_attributes``, ``zoho_client`` and
``Customer.source_attributes`` all say in the same words that mapping a source
key onto a concept this platform reasons with is "a separate, versioned
exercise". This module is that exercise, and
``models.SourceAttributeMapping`` is where the declarations live.

Why it exists at all
--------------------
A customer running Epicor puts ``"TENDER"`` in ``UD_Field_07``. A customer
running Zoho puts ``"Tender enquiry"`` in ``cf_quote_type``. Without a
declaration layer the first rule that wants to know whether a quote was a
tender names one of those keys, and the second tenant needs a fork of the rule
— which is the same failure the engine/pack split exists to prevent next door
in pie-parser. So the organization declares once what its field means, and
every rule after that reads the **concept**. Nothing downstream is ever given a
source key: there is no parameter on ``read``/``readings`` that could carry one
in, and no field on a ``ConceptReading`` that carries one out.

The two halves of the vocabulary, and why only one of them is closed
--------------------------------------------------------------------
The **left** side of a ``value_map`` is the tenant's own spelling and is open —
``"TENDER"``, ``"Tender"``, ``"TND"`` may all mean one thing, and an ERP nobody
here has seen will spell it a fourth way. Case and surrounding whitespace are
not meaning, so both sides of the comparison are folded; a different spelling
*is* meaning, so it needs its own entry.

The **right** side is closed, and that is the structural form of the rule that
a mapping decides interpretation and never arithmetic. ``VOCABULARY`` fixes the
labels each concept may take, ``declare`` refuses anything else on the way in,
and ``_reading`` checks membership again on the way out — so no row in that
table, however it was written, can put a price, a cost, a quantity or a date
into a reading. A tenant-configurable input to a money calculation is an
unauditable number, and CLAUDE.md §1's first line reaches tenant configuration
by exactly the argument it reaches a model.

``CONCEPTS`` is closed too, and it is four. A mapping onto a concept nothing
consumes is one that silently does nothing, and every concept added is a
one-way door: once a rule reads one, an organization's data gets shaped around
it. Four, not fifteen.

Nothing is silently nothing
---------------------------
This layer exists because a field was dropped in silence, so it does not drop
anything in silence itself. Every one of the four concepts gets a reading on
every call, and a reading always says which of four things happened:

``RECORDED``      a declaration was in force, the record carried a value under
                  its field, and the declaration says what that value means.
``UNRECOGNISED``  the record carried a value and nothing in the declaration
                  turns it into a concept. The value is carried back verbatim
                  so somebody can finish the declaration — it is a *value*,
                  never a key, and there is no API that turns it into a
                  concept.
``NOT_SET``       a declaration is in force and this record holds nothing under
                  its field.
``NOT_DECLARED``  this organization has declared nothing for this concept on
                  this connector and record kind.

The last two are both "not recorded" to a rule and they are different facts to
a person: one is a gap in the data, the other a gap in the configuration. Which
is why they are not collapsed. **On a live book these fields are mostly empty
and five of the six connectors emit no source attributes at all**, so
``NOT_DECLARED`` is the ordinary answer today, and a caller that reads it as
anything other than "nothing is recorded" is asserting an absence it has no
evidence for.

A source key the organization has not declared is *counted* and not named
(``Readings.undeclared_key_count``). Counted, because a tenant whose fields are
half-declared should be able to see that; not named, because a name is the one
thing this module exists to keep out of the engine — and the names are already
readable where the source's own fields are already shown
(``commercial/insight/quote_book``), so a second home for them would be the
duplication CLAUDE.md §2 is about.

Point in time
-------------
A quote diagnosed in March under one reading of a field must stay explainable
when somebody re-maps it in June. Declarations are superseded rather than
mutated, and ``in_force`` takes the instant to read *as of* — the caller passes
the same ``knowable_by`` it uses for evidence. There is no default: an
unqualified "what does this field mean" has two answers once a correction
exists, and picking one silently is how March stops being explainable.

Deterministic and session-passing, imports nothing from ``ai/``. Nothing here
computes a number; it decides what a label means, which is why it sits in
``commercial/`` beside ``principals`` and ``jurisdiction`` rather than in
``ingestion/`` (which carries these fields and must keep not interpreting them)
or in ``decisions/`` (which is the seam where a fact meets an interpretation,
and this is a fact).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models

# ── the closed vocabularies ──────────────────────────────────────────────────
QUOTE_INTENT = "quote_intent"
SOURCING_REASON = "sourcing_reason"
URGENCY = "urgency"
STRATEGIC_ACCOUNT = "strategic_account"

#: Every concept this platform reads out of a source's own fields. Closed, and
#: four. See the module docstring for why a fifth is a decision rather than an
#: addition.
CONCEPTS: tuple[str, ...] = (QUOTE_INTENT, SOURCING_REASON, URGENCY,
                             STRATEGIC_ACCOUNT)

#: What each concept may be read as. Labels, never numbers — this is the
#: structural form of "the mapping decides interpretation, never arithmetic".
#:
#: ``urgency`` reads as ordered and is **not a scale**: nothing may subtract two
#: of these, average them, or turn one into a multiplier. An ordered label that
#: acquires arithmetic is a number a tenant configured.
VOCABULARY: dict[str, tuple[str, ...]] = {
    QUOTE_INTENT: ("TENDER", "BUDGETARY", "FIRM_ENQUIRY", "REPEAT_ORDER",
                   "SAMPLE"),
    SOURCING_REASON: ("BREAKDOWN", "NEW_PROJECT", "REPLENISHMENT",
                      "SECOND_SOURCE", "IMPORT_SUBSTITUTION"),
    URGENCY: ("ROUTINE", "PLANNED", "EXPEDITED", "EMERGENCY"),
    STRATEGIC_ACCOUNT: ("STRATEGIC", "STANDARD"),
}

#: The record kinds that carry ``source_attributes`` — this platform's words,
#: not table names. The same ERP spells a field differently on a quote and on an
#: account, so a concept is declared once per kind.
ENTITIES: tuple[str, ...] = ("quote", "customer", "product", "vendor",
                             "sales_order", "purchase_order", "invoice", "bill")

# ── what a reading can say ───────────────────────────────────────────────────
RECORDED = "RECORDED"
UNRECOGNISED = "UNRECOGNISED"
NOT_SET = "NOT_SET"
NOT_DECLARED = "NOT_DECLARED"

STATUSES: tuple[str, ...] = (RECORDED, UNRECOGNISED, NOT_SET, NOT_DECLARED)

#: A first declaration is effective from here rather than from the moment it was
#: typed. See ``declare``.
BEGINNING = datetime(1, 1, 1, tzinfo=timezone.utc)

#: How much of an unrecognised value is carried back. Bounded because a source
#: field can hold a paragraph, and this travels into a response.
_OBSERVED_CHARS = 120


class MappingError(ValueError):
    """A declaration that cannot be accepted, with the reason."""


def fold(value: Any) -> str:
    """A source value or key as the token this module compares on.

    Upper-cased with whitespace collapsed. Case and padding are not meaning —
    an ERP that writes ``Tender`` today and ``TENDER`` after an upgrade has not
    changed what it means — while a different spelling is, and gets its own
    entry in the map.
    """
    return " ".join(str(value).split()).upper()


# ── one record, read ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ConceptReading:
    """What one concept says about one record, and how it came to say it.

    Carries no source key, by construction: there is no field here that could
    hold one. ``observed`` is the source's own *value* and exists so that a
    value nobody has declared is visible rather than dropped; it is not a
    concept, nothing turns it into one, and a rule that branches on it has put
    a tenant's spelling back inside the engine.
    """

    concept: str
    #: One of ``STATUSES``.
    status: str
    #: A member of ``VOCABULARY[concept]``, and ``None`` for every status but
    #: ``RECORDED``. Never a number: see the module docstring.
    value: Optional[str] = None
    #: The source's own value, trimmed and bounded — only ever set on
    #: ``UNRECOGNISED``.
    observed: Optional[str] = None
    #: Which declaration decided this, and from when it was in force. ``None``
    #: on ``NOT_DECLARED``. This is what makes a March reading explainable in
    #: June: the row is still there and it is named here.
    mapping_id: Optional[str] = None
    effective_from: Optional[datetime] = None

    @property
    def recorded(self) -> bool:
        """True only when a concept value was actually read."""
        return self.status == RECORDED


@dataclass(frozen=True)
class Readings:
    """All four concepts, for one record. Always four — never a subset.

    A caller cannot accidentally not-ask about a concept, which is the same
    reason ``QuoteDoc.outcome`` is NOT NULL with an UNRECORDED default: a
    missing key invites a ``get(..., None)`` at the point of reading and
    silence starts meaning whatever the last reader chose.
    """

    by_concept: Mapping[str, ConceptReading]
    #: How many of this record's own fields this organization has not declared.
    #: A count, never a name — see the module docstring.
    undeclared_key_count: int = 0

    def of(self, concept: str) -> ConceptReading:
        try:
            return self.by_concept[concept]
        except KeyError:
            raise MappingError(f"{concept!r} is not a concept "
                               f"({', '.join(CONCEPTS)}).") from None

    def __getitem__(self, concept: str) -> ConceptReading:
        return self.of(concept)

    def recorded_values(self) -> dict[str, str]:
        """Just the concepts that were actually read, as ``{concept: value}``.

        For a caller that wants the positive facts and nothing else. An empty
        dict is the ordinary answer on a book whose fields are empty, and it
        means "nothing is recorded" — never "there was no reason".
        """
        return {c: r.value for c, r in self.by_concept.items()
                if r.status == RECORDED and r.value is not None}


@dataclass(frozen=True)
class Taxonomy:
    """One organization's declarations for one connector and record kind, as
    they stood at one instant.

    Loaded once by ``in_force`` and applied to as many records as the caller
    has. Holding it as a value rather than re-querying per record is what keeps
    a diagnosis over a book from being one query per quote — and it is also
    what makes the point-in-time promise legible: the instant is on the object,
    so a reading cannot silently be made against "now".
    """

    connector: Optional[str]
    entity: str
    at: datetime
    #: concept → the declaration in force. Missing concept means none declared.
    declarations: Mapping[str, models.SourceAttributeMapping]

    def read(self, attributes: Optional[Mapping[str, Any]]) -> Readings:
        """This record's own fields, as concepts.

        ``attributes`` is the whole ``source_attributes`` bag, verbatim and
        under the source's own keys — the caller hands over what it holds and
        does not choose which key matters. ``None`` (the column's NULL, meaning
        no source fields are held on this record) reads the same as an empty
        bag: every declared concept is ``NOT_SET``.
        """
        held = dict(attributes or {})
        # A case-folded index beside the verbatim keys, so an administrator who
        # typed ``ud_field_07`` for a field the ERP spells ``UD_Field_07`` gets
        # a reading rather than a silent ``NOT_SET`` — which is the same silence
        # this layer exists to end. The verbatim key is tried first, so where a
        # source genuinely holds two keys differing only in case the exact one
        # still wins and the other is simply undeclared.
        folded = {fold(key): key for key in held}
        used: set[str] = set()
        out: dict[str, ConceptReading] = {}
        for concept in CONCEPTS:
            row = self.declarations.get(concept)
            if row is None:
                out[concept] = ConceptReading(concept=concept, status=NOT_DECLARED)
                continue
            key = row.source_key if row.source_key in held else folded.get(
                fold(row.source_key))
            if key is not None:
                used.add(key)
            out[concept] = _reading(concept, row,
                                    held.get(key) if key is not None else None)
        return Readings(by_concept=out,
                        undeclared_key_count=len(set(held) - used))


def _reading(concept: str, row: models.SourceAttributeMapping,
             raw: Any) -> ConceptReading:
    """One declaration applied to one value.

    The membership check on the way out is deliberate belt-and-braces: ``declare``
    already refuses a target outside the vocabulary, but this table can also be
    written by a seed or by hand, and a reading is the last place a number could
    enter. A target that is not a concept value is treated as no mapping for
    that value at all — from a rule's side it is the same fact ("nothing in this
    organization's declaration turns what the source said into a concept"), and
    the difference between an undeclared value and a mis-declared one is a
    configuration question answerable from the row, not something a diagnosis
    should branch on.
    """
    common = {"mapping_id": row.mapping_id,
              "effective_from": clock.aware(row.effective_from)}
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ConceptReading(concept=concept, status=NOT_SET, **common)
    target = (row.value_map or {}).get(fold(raw))
    if isinstance(target, str) and target in VOCABULARY[concept]:
        return ConceptReading(concept=concept, status=RECORDED, value=target,
                              **common)
    return ConceptReading(concept=concept, status=UNRECOGNISED,
                          observed=_observed(raw), **common)


def _observed(raw: Any) -> str:
    text = str(raw).strip()
    return text if len(text) <= _OBSERVED_CHARS else text[:_OBSERVED_CHARS - 1] + "…"


# ── reading the declarations ─────────────────────────────────────────────────
def _order(row: models.SourceAttributeMapping) -> tuple[datetime, datetime, str]:
    """A stable sort key over declarations, on comparable timestamps.

    Through ``clock.aware`` rather than on the raw columns: SQLite returns a
    ``DateTime(timezone=True)`` as naive while Postgres returns it aware, so a
    list holding one row freshly written in this session and one read back from
    the database would raise ``TypeError`` on one backend and sort silently on
    the other. The tie-break past ``effective_from`` is what keeps two
    declarations made in the same instant from ordering differently on two
    engines.
    """
    stamp = clock.aware(row.effective_from) or BEGINNING
    return (stamp, clock.aware(row.recorded_at) or stamp, row.mapping_id)


def in_force(session: Session, org: str, *, connector: Optional[str],
             entity: str, at: datetime) -> Taxonomy:
    """The declarations that were in force at ``at``, for one record kind.

    ``at`` is required and has no default. An unqualified "what does this field
    mean" has two answers the moment a correction exists, and picking one
    silently is how a quote sent in March stops being explainable in June. A
    caller diagnosing a quote passes the same ``knowable_by`` it uses for
    evidence.

    ``connector=None`` — a record whose own connector was never recorded —
    matches nothing and yields an empty taxonomy. A declaration is always about
    a named system, and guessing which one an unattributed row came from would
    be inventing provenance; "source not recorded" is what the rest of the
    platform says about the same row.
    """
    if entity not in ENTITIES:
        raise MappingError(f"{entity!r} is not a record kind "
                           f"({', '.join(ENTITIES)}).")
    cutoff = clock.aware(at)
    if cutoff is None:
        raise MappingError("A reading needs the instant to read it as of.")
    declarations: dict[str, models.SourceAttributeMapping] = {}
    if connector:
        rows = session.scalars(
            select(models.SourceAttributeMapping)
            .where(models.SourceAttributeMapping.organization_id == org,
                   models.SourceAttributeMapping.connector == connector,
                   models.SourceAttributeMapping.entity == entity)
            # Ordered in SQL and tie-broken in Python on a stable key: two
            # declarations effective at the same instant are possible across
            # concepts, and leaving their order to the database would make the
            # same book read differently on two engines.
            .order_by(models.SourceAttributeMapping.effective_from)).all()
        for row in sorted(rows, key=_order):
            if row.pie_concept not in CONCEPTS:
                # A row naming a concept nothing consumes. Unreachable through
                # ``declare``; skipped rather than raised, because a hand-written
                # row must not take a diagnosis down, and it decides nothing
                # either way.
                continue
            start = clock.aware(row.effective_from)
            end = clock.aware(row.superseded_at)
            if start is not None and start <= cutoff and (end is None or end > cutoff):
                declarations[row.pie_concept] = row
    return Taxonomy(connector=connector, entity=entity, at=cutoff,
                    declarations=declarations)


def readings(session: Session, org: str, *, connector: Optional[str], entity: str,
             attributes: Optional[Mapping[str, Any]], at: datetime) -> Readings:
    """One record's own fields, as concepts, under the taxonomy in force at ``at``.

    The convenience over ``in_force`` + ``Taxonomy.read`` for a caller holding
    one record. A caller holding many loads the taxonomy once and reads each
    record against it — this is the same two calls, not a second implementation.
    """
    return in_force(session, org, connector=connector, entity=entity,
                    at=at).read(attributes)


def history(session: Session, org: str, *, connector: str, entity: str,
            concept: Optional[str] = None) -> list[models.SourceAttributeMapping]:
    """Every declaration ever made, oldest first — the audit read.

    ``in_force`` answers "which mapping explained this quote"; this answers
    "how has this organization read this field over time", which is the
    question somebody asks after the answer on a screen changed.
    """
    stmt = (select(models.SourceAttributeMapping)
            .where(models.SourceAttributeMapping.organization_id == org,
                   models.SourceAttributeMapping.connector == connector,
                   models.SourceAttributeMapping.entity == entity))
    if concept is not None:
        stmt = stmt.where(models.SourceAttributeMapping.pie_concept == concept)
    return sorted(session.scalars(stmt).all(), key=_order)


# ── declaring one ────────────────────────────────────────────────────────────
def declare(session: Session, org: str, *, connector: str, entity: str,
            source_key: str, concept: str, value_map: Mapping[Any, Any],
            source_ref: str = "", declared_by_user_id: Optional[str] = None,
            effective_from: Optional[datetime] = None
            ) -> models.SourceAttributeMapping:
    """Record what one of this organization's fields means, superseding whatever
    said so before.

    The previous live declaration for the same ``(connector, entity, concept)``
    is stamped ``superseded_at = effective_from`` and left otherwise untouched,
    so the intervals tile with no gap and no overlap and a point-in-time read
    returns exactly one row. Nothing is ever updated in place and nothing is
    ever deleted: the old reading is how a quote sent under it stays
    explainable.

    ``effective_from`` defaults asymmetrically, and the asymmetry is the whole
    of the design decision:

    * A **first** declaration is effective from ``BEGINNING``. There is no
      earlier reading it could be contradicting, so applying it to the whole
      book is the only cut that is not arbitrary — and a default of "now" would
      quietly leave every quote already on the book unreadable, which for a
      feature whose value is reading the existing book is the benign default
      CLAUDE.md §1 warns about.
    * A **supersession** is effective from now. It contradicts a reading that
      was genuinely in force, and leaving that reading in force until the moment
      of the correction is exactly what keeps March explained by March's row.

    An author who knows better — "the business changed what it puts in that
    field from 1 July" — passes the date, and it is refused if it is not after
    the declaration it replaces, because overlapping intervals have two answers.

    Refuses, naming the problem, rather than storing a declaration that decides
    nothing or decides a number.
    """
    connector = str(connector or "").strip()
    if not connector:
        raise MappingError("A declaration must name the system it is about.")
    if entity not in ENTITIES:
        raise MappingError(f"{entity!r} is not a record kind "
                           f"({', '.join(ENTITIES)}).")
    if concept not in CONCEPTS:
        raise MappingError(
            f"{concept!r} is not a concept this platform reads "
            f"({', '.join(CONCEPTS)}). A mapping onto a concept nothing "
            f"consumes silently does nothing.")
    key = str(source_key or "").strip()
    if not key:
        raise MappingError("A declaration must name the field it reads.")
    allowed = VOCABULARY[concept]
    folded: dict[str, str] = {}
    for raw_value, raw_target in (value_map or {}).items():
        token = fold(raw_value)
        if not token:
            raise MappingError("A source value cannot be blank.")
        if not isinstance(raw_target, str) or raw_target not in allowed:
            raise MappingError(
                f"{concept} cannot be read as {raw_target!r}. It is one of "
                f"{', '.join(allowed)} — a declaration supplies a meaning, "
                f"never a number.")
        if folded.get(token, raw_target) != raw_target:
            raise MappingError(
                f"{token!r} is declared as both {folded[token]!r} and "
                f"{raw_target!r}.")
        folded[token] = raw_target
    if not folded:
        raise MappingError(
            f"A declaration for {concept} with no values reads nothing. Say "
            f"what at least one of this field's values means.")

    live = _live(session, org, connector=connector, entity=entity, concept=concept)
    now = clock.now()
    when = clock.aware(effective_from) or (now if live is not None else BEGINNING)
    if live is not None:
        previous = clock.aware(live.effective_from)
        if previous is not None and when <= previous:
            raise MappingError(
                "A correction takes effect after the reading it replaces, "
                f"which has been in force since {clock.iso(previous)}.")
        live.superseded_at = when
    row = models.SourceAttributeMapping(
        organization_id=org, connector=connector, entity=entity, source_key=key,
        pie_concept=concept, value_map=folded, source_ref=str(source_ref or "")[:255],
        declared_by_user_id=declared_by_user_id, effective_from=when,
        recorded_at=now)
    session.add(row)
    session.flush()
    return row


def _live(session: Session, org: str, *, connector: str, entity: str,
          concept: str) -> Optional[models.SourceAttributeMapping]:
    return session.scalars(
        select(models.SourceAttributeMapping)
        .where(models.SourceAttributeMapping.organization_id == org,
               models.SourceAttributeMapping.connector == connector,
               models.SourceAttributeMapping.entity == entity,
               models.SourceAttributeMapping.pie_concept == concept,
               models.SourceAttributeMapping.superseded_at.is_(None))).first()
