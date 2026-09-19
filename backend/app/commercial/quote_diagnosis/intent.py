"""What this quote's own record says about why it was priced as it was — and a
refusal to say anything more than that.

The engine can already say a line sits below the range this customer's history
supports. What it has never been able to say is whether anybody *recorded a
reason*: that this was a tender, that the customer was down and needed the item
today, that the account is a strategic one. Those facts exist on the quote in
the source system, under whatever key that system's administrator chose, and
until A4.2 nothing here could read them without naming that key.

``commercial/source_concepts`` is the layer that ended that. An organization
declares once that its ``cf_quote_type`` (or ``UD_Field_07``, or whatever the
next ERP calls it) carries ``quote_intent`` and that ``"Tender enquiry"`` means
``TENDER``; this module reads the **concept**. It names no source key, it has no
parameter that could carry one in, and a ``ConceptReading`` has no field that
could carry one out. That is the whole reason anything built on it works for a
second ERP.

── the four statuses are four different sentences ───────────────────────────

``source_concepts`` returns one of four answers per concept and they are
deliberately not collapsed, so nothing here may collapse them either:

``RECORDED``      a declaration was in force and this quote holds a value the
                  declaration gives a meaning for.
``NOT_SET``       a declaration is in force and this quote holds nothing under
                  its field. **The field exists and this quote is blank in it.**
``NOT_DECLARED``  this organization has declared nothing for this concept, so
                  there is no field to be blank in.
``UNRECOGNISED``  this quote holds something and the declaration does not say
                  what it means. Somebody has to finish the declaration.

Each gets its own sentence below, and the difference between the middle two is
what the whole module turns on. "Nobody declared a field for this" is not "the
field is empty": the first is a gap in the configuration and carries no
information about this quote at all, and the second is a gap in the data and is
the only one of the four that is even weak evidence about how this quote was
priced. A design that read them as one boolean would report every quote on a
book nobody has configured as a quote with no reason — which is CLAUDE.md §1's
*absence of evidence is not a pass*, on a book where it would fire on every row.

── what may be claimed, and the wording that is the feature ─────────────────

**Never assert leakage.** ``POSSIBLE_MARGIN_LEAKAGE`` is the strongest form this
module has and it is worded as a possibility, in the same register as
``rules``' ``POSSIBLE_EXCEPTIONAL_PRICE`` and ``POSSIBLE_COST_DRIVEN``. It
fires only where all four of these hold, and each one is load-bearing:

1. the engine already found the line ``BELOW_HISTORICAL_RANGE`` — the claim
   rests on the price finding, it does not make one;
2. the band is graded ``MODERATE`` or better — an unbelievable band cannot
   support a claim about a price sitting under it, which is the gate
   ``drivers.attribute`` applies for the same reason;
3. no concept is ``RECORDED`` — nothing on the quote says why; and
4. at least one concept is ``NOT_SET`` — this organization *has* a field for a
   pricing reason and this quote is blank in it.

Condition 4 is the one that keeps this honest. Without it the claim would fire
on every below-band line of every tenant who has declared nothing, which is
every tenant today. With it, the claim says something narrow and true: there was
a place to record a reason and nothing was put in it.

**"No pricing reason has been recorded for this quote"** — never "this was
unintentional", never "the desk had no reason". An unrecorded reason is not an
absent one. The ledger does not hold the reason a price was set; it holds
whether anybody wrote one down, and those are different facts. Every sentence
in this module is about the record.

**Recorded fields only.** No inference over free text, no model, no guess from a
customer name or a line description. ``commercial/`` imports no ``ai/`` and this
module computes no number at all — it decides what a label means, which is what
``source_concepts`` sits in ``commercial/`` to do.

── who sees which half ──────────────────────────────────────────────────────

Two types, not one filtered twice, which is ``rules``' arrangement and this
module's for the same reason.

``Reading`` is what the record says. Every sentence in it is a fact about a
field on a document, and none of them is a claim about margin: "this quote is
recorded as TENDER" explains a low price to a salesperson without revealing
anything about cost, and a desk that knows the quote was a tender argues it
better. So it reaches both roles, and its codes are all inside
``rules.OPERATIONS_CODES``.

``PricingIntent`` is the same reading plus ``exposure`` — the potential-leakage
sentence. **That is a margin claim**: it says this line is under-priced in a way
nobody explained, which is a statement about the money on the line rather than
about the record. It is RESTRICTED, it lives on this type and on no other, and
``POSSIBLE_MARGIN_LEAKAGE`` is deliberately outside ``OPERATIONS_CODES`` so the
allowlist stops it as well as the type does. The word "margin" in the code is
itself caught by ``tests/cost_sweep.WORDS``, so a projection that leaked it to
the desk fails an existing test rather than a review.

── point in time ────────────────────────────────────────────────────────────

``at`` is the diagnosis's ``knowable_by`` and never its ``as_of`` or the wall
clock. A declaration is evidence like any other: one typed after the quote was
written is not a reading the quoter had, and ``source_concepts.in_force`` takes
the instant for exactly this — its docstring says a caller diagnosing a quote
passes the same ``knowable_by`` it uses for evidence. ``as_of`` is a date rather
than an instant and would cut the taxonomy off at midnight while the evidence
was cut off at the quote's own visibility boundary: two boundaries for one
question, and the pair would disagree on the day somebody declared a field.

This costs nothing on a fresh tenant. ``source_concepts.declare`` makes a
**first** declaration effective from the beginning of time precisely so that
reading the existing book works, so a taxonomy declared tomorrow is in force for
a quote written last March. What the instant buys is the day after a
*correction*: March keeps March's reading.

── not stored, and therefore not replayed ───────────────────────────────────

``quote_diagnoses`` gets no column for any of this, which is a decision and is
argued at ``rules.ENGINE_VERSION``. The short form: a taxonomy can move
retroactively under a stored diagnosis, so a reading recomputed at replay can
legitimately differ from the one somebody saw — which is exactly why the
working-capital reading is not stored either. A stored row says so rather than
answering with silence (``render.INTENT_NOT_STORED``).

── the honest limit ─────────────────────────────────────────────────────────

**On the live 4U book these fields are mostly empty, and the five non-Zoho
connectors emit no source attributes at all.** So ``NOT_DECLARED`` is the
ordinary answer today and ``PRICING_REASON_NOT_DECLARED`` is what this module
mostly says. That is the correct answer and it is built for: an empty taxonomy
yields "nothing is recorded", never a classification. A quote drafted in the
Quote Builder has no source document at all and gets ``NO_SOURCE_RECORD``, which
is a different sentence again — the reading is a reading of a document the ERP
issued, and the ERP-quote screen is where it has anything to read.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from .. import source_concepts
from ..source_concepts import (ConceptReading, NOT_DECLARED, NOT_SET,
                               RECORDED, Taxonomy, UNRECOGNISED)
from .rules import (BELOW_HISTORICAL_RANGE, MODERATE,
                    NO_PRICING_REASON_RECORDED, POSSIBLE_MARGIN_LEAKAGE,
                    PRICING_REASON_NOT_DECLARED, PRICING_REASON_RECORDED,
                    PRICING_REASON_UNRECOGNISED, _RANK)

#: Which record kind this reads. One of ``source_concepts.ENTITIES``, and the
#: only one: a diagnosis is about one quote, so it reads that quote's own bag.
#: Reading a second record's bag — the customer's, for ``strategic_account`` —
#: would need a precedence rule between two readings of one concept, and a
#: precedence rule is how four distinct statuses become "whichever record
#: answered first". An organization whose ERP carries a strategic flag on the
#: quote declares it here; one that carries it on the account gets
#: ``NOT_DECLARED``, which is true.
ENTITY = "quote"


# ── outcomes ─────────────────────────────────────────────────────────────────

#: The record was found and read. Everything else is a refusal.
READ = "READ"

#: No document from a source system was found for this quote. A quote drafted in
#: the Quote Builder is the ordinary case: it exists here and nowhere else, so
#: there are no source fields to read. Its own reason rather than
#: ``NOT_DECLARED``, because "this organization has declared nothing" describes a
#: configuration that may be perfectly complete — a refusal may be silent about
#: what it cannot see; it may not be wrong about it.
NO_SOURCE_RECORD = "NO_SOURCE_RECORD"

#: The document exists and does not say which system issued it. ``QuoteDoc``'s
#: ``connector`` is nullable because a row written before that column existed
#: cannot be attributed after the fact, and ``source_concepts.in_force`` matches
#: nothing for a connector it was not given — a declaration is always about a
#: named system. Its own reason for the reason above: this is a gap in the row's
#: provenance, not in the organization's configuration.
SOURCE_NOT_RECORDED = "SOURCE_NOT_RECORDED"


# ── the reader's word for each concept ───────────────────────────────────────
#
# A short noun phrase, chosen to compose into all four sentences below. The
# concept keys are ``source_concepts``' and the values on the right of a reading
# are its closed vocabulary, printed verbatim: a term worded one way by the
# engine and another way here would be two terms, and the one people read is the
# one nobody reviewed. That is ``render``'s rule for driver codes and it is this
# module's for concept values.

_LABEL: dict[str, str] = {
    source_concepts.QUOTE_INTENT: "the quote type",
    source_concepts.SOURCING_REASON: "the sourcing reason",
    source_concepts.URGENCY: "the urgency",
    source_concepts.STRATEGIC_ACCOUNT: "the account's standing",
}

#: The standing qualification, on every reading without exception. The second
#: sentence is the one this module exists to keep on the screen.
#:
#: Named for what it qualifies rather than as a bare ``QUALIFICATION``, because
#: ``render`` already has one of those and it says something else entirely —
#: that a price band may contain deals nobody recorded. Two constants with one
#: name over two unrelated sentences is the thing this repository does not do.
READING_QUALIFICATION = (
    "Read from the fields this quote's own system holds, through this "
    "organization's declaration of what they mean — never inferred from free "
    "text and never by a model. Absence of a recorded reason is not evidence "
    "that there was no reason.")

#: Added when nothing at all has been declared, so a reader is told that the
#: silence is a configuration gap rather than a fact about this quote. It names
#: what would finish it, as ``working_capital``'s refusals name a Settings field.
_NOTHING_DECLARED = (
    " Nothing has been declared for this system's quotes, so no field on this "
    "quote is read as a pricing reason and every quote on this book reads the "
    "same way until somebody declares one.")


# ── one concept, read ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecordedReason:
    """What one concept says about this quote, and the sentence for it.

    Carries no source key, because ``ConceptReading`` has no field that could
    hold one. ``observed`` is the source's own *value* on an ``UNRECOGNISED``
    reading and exists so that a value nobody has declared is visible rather
    than dropped; nothing turns it into a concept and no rule here branches on
    it.
    """

    concept: str
    #: One of ``source_concepts.STATUSES``. Four different facts, four different
    #: sentences — never a boolean.
    status: str
    #: A member of ``source_concepts.VOCABULARY[concept]``, and ``None`` for
    #: every status but ``RECORDED``.
    value: Optional[str]
    #: The source's own value, trimmed and bounded by ``source_concepts``. Only
    #: ever set on ``UNRECOGNISED``.
    observed: Optional[str]
    #: Which declaration decided this. ``None`` on ``NOT_DECLARED``. It is what
    #: makes a March reading explainable in June: the row is still there and it
    #: is named here.
    mapping_id: Optional[str]
    #: The one sentence for this status. Written here rather than downstream
    #: because the wording *is* the claim, and a claim worded in two places is
    #: two claims.
    sentence: str


def _sentence(concept: str, reading: ConceptReading) -> str:
    """The one sentence for one reading. Four statuses, four sentences.

    Each leads with the record rather than with a judgement, and each says
    plainly which of the four things happened. They are kept apart on purpose:
    a reader who cannot tell "no field was declared for this" from "the field is
    empty" from "the field holds something nobody declared a meaning for" has
    been handed one fact where the engine has three.
    """
    label = _LABEL[concept]
    if reading.status == RECORDED:
        return f"This quote records {label} as {reading.value}."
    if reading.status == NOT_SET:
        return (f"This quote has a declared field for {label} and holds "
                f"nothing in it.")
    if reading.status == UNRECOGNISED:
        return (f"This quote's field for {label} holds “{reading.observed}”, "
                f"which this organization's declaration does not give a "
                f"meaning for.")
    return (f"No field on this quote has been declared to carry {label}, so "
            f"nothing is recorded about it either way.")


# ── what the record says ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reading:
    """What this quote's record says, for either role.

    **Everything on this type is a fact about a field on a document.** No cost,
    no margin, no claim about the money on the line — which is why it is the
    half that reaches the desk, and why its codes are all inside
    ``rules.OPERATIONS_CODES``. A salesperson told "this quote is recorded as
    TENDER" has been given the reason a price is low without being given a
    number, and is better placed to argue it.

    **Two shapes, and ``read`` is which.** Either ``reasons`` holds all four
    concepts and ``headline`` says what was recorded, or ``reasons`` is empty and
    ``basis`` names what stopped it. A caller reads the flag rather than testing
    whether ``reasons`` happens to be populated: a predicate re-derived from
    published fields is a guess about what the producer meant, which is the
    lesson CLAUDE.md §1 draws from ``_identity_candidate``.

    Frozen, like every other record in this package.
    """

    #: Whether the record was found and read at all.
    read: bool
    #: ``READ``, or the refusal.
    reason: str
    #: All four concepts, always four, never a subset — the shape
    #: ``source_concepts.Readings`` has and for its reason: a caller cannot
    #: accidentally not-ask about a concept. Empty on a refusal.
    reasons: tuple[RecordedReason, ...]
    #: The status codes, from ``rules``' one vocabulary. Every one of them is in
    #: ``rules.OPERATIONS_CODES``; ``POSSIBLE_MARGIN_LEAKAGE`` is not, and is not
    #: on this type.
    codes: tuple[str, ...]
    #: What was recorded, in one sentence — or, when nothing was,
    #: **"No pricing reason has been recorded for this quote."** ``""`` on a
    #: refusal.
    headline: str
    #: The standing qualification, or the refusal and its reason. Never empty:
    #: a block with nothing written in it would read as "nothing to report".
    basis: str

    @property
    def recorded_values(self) -> dict[str, str]:
        """Just the concepts that were actually read, as ``{concept: value}``.

        An empty dict is the ordinary answer and it means "nothing is recorded"
        — never "there was no reason".
        """
        return {r.concept: r.value for r in self.reasons
                if r.status == RECORDED and r.value is not None}


@dataclass(frozen=True)
class PricingIntent:
    """The reading, plus the one observation a salesperson may not have.

    **RESTRICTED, because of one field.** ``exposure`` says a line is priced
    under the range this customer's own history supports with nothing on record
    explaining it — a claim about the money on the line, not about the record,
    and therefore a margin claim. It is declared here and on no other type, so
    the desk's projection has no field to put it in; ``rules.operations_view``
    hands over ``reading`` and nothing else.

    Everything else on this type is the reading itself, which both roles get.
    """

    #: The half that reaches the desk. Held rather than duplicated, so the two
    #: roles cannot come to be told different facts about the same record.
    reading: Reading
    #: Which system issued the document this was read from. ``None`` on a
    #: refusal, and on a row whose provenance was never recorded.
    connector: Optional[str]
    #: The instant the taxonomy was read as of — the diagnosis's ``knowable_by``.
    #: On the object so that a reading cannot silently have been made against
    #: "now", which is the reason ``source_concepts.Taxonomy`` carries it too.
    at: Optional[datetime]
    #: How many of this quote's own source fields this organization has not
    #: declared. A count, never a name: the names are already readable where the
    #: source's own fields are shown (``commercial/insight/quote_book``), and a
    #: second home for them would put a tenant's spelling back inside the engine.
    undeclared_key_count: int
    #: The reading's codes, plus ``POSSIBLE_MARGIN_LEAKAGE`` where it fired.
    codes: tuple[str, ...]
    #: RESTRICTED. The potential-leakage sentence, or ``""`` where no claim is
    #: made — which is almost always. Never an assertion: see the module
    #: docstring for the four conditions and why each one is load-bearing.
    exposure: str


# ── reading one quote's record ───────────────────────────────────────────────

@dataclass(frozen=True)
class SourceRecord:
    """The quote as its own system holds it.

    Three facts, and the first two are separate because their absences are
    different: a quote with no source document at all is not a quote whose
    document never recorded which system issued it. ``service`` builds this and
    the ``Taxonomy`` beside it from one row, so the connector named here and the
    one the taxonomy was loaded for cannot disagree.
    """

    #: Whether a source document was found for this quote at all.
    found: bool
    #: Which system issued it. ``None`` where the row does not say.
    connector: Optional[str]
    #: The whole ``source_attributes`` bag, verbatim and under the source's own
    #: keys. Handed over whole: the caller does not choose which key matters,
    #: which is the property that keeps a source key out of every layer above
    #: this one. ``None`` is the column's NULL and reads as an empty bag.
    attributes: Optional[Mapping[str, Any]]


#: A caller that has no source document at all. Named rather than constructed at
#: each call site so "there is no record" is one value with one meaning.
NO_RECORD = SourceRecord(found=False, connector=None, attributes=None)


def assess(*, record: SourceRecord, taxonomy: Optional[Taxonomy],
           codes: Sequence[str], strength: str) -> PricingIntent:
    """What this quote's record says about why it was priced as it was.

    Pure, total and deterministic — every input is already loaded. ``taxonomy``
    arrives from ``source_concepts.in_force`` at the diagnosis's ``knowable_by``
    and is applied here rather than re-queried, which is that type's whole
    purpose. Nothing is re-derived: a second reading of a source field would be
    the duplication CLAUDE.md §2 is about, and the two copies would disagree on
    the quotes nobody looks at.

    ``codes`` and ``strength`` are the diagnosis's own, and are read for one
    thing only — whether a potential-leakage observation is even available. The
    price finding is made by ``rules`` and this module never makes one: it says
    what was recorded, and at most that nothing was recorded about a line the
    engine had already found below its band.

    **The refusals, in order**, from the most specific gap to the most general,
    so the reason a reader gets names the thing closest to being fixable: no
    source document, then a document that does not say which system issued it.
    Neither is ``NOT_DECLARED``, which is a statement about an organization's
    configuration and would be a wrong one here.
    """
    if not record.found:
        return _refused(
            NO_SOURCE_RECORD,
            f"{NO_SOURCE_RECORD}: no document from a source system was found "
            f"for this quote, so there are no source fields to read. What a "
            f"quote records about why it was priced is read from the record "
            f"its own system holds, and a quote drafted here has none yet.")
    if record.connector is None or taxonomy is None:
        return _refused(
            SOURCE_NOT_RECORDED,
            f"{SOURCE_NOT_RECORDED}: this quote's record does not say which "
            f"system issued it, and a declaration is always about a named "
            f"system. Nothing is read rather than a system being guessed at.")

    readings = taxonomy.read(record.attributes)
    reasons = tuple(
        RecordedReason(
            concept=concept, status=one.status, value=one.value,
            observed=one.observed, mapping_id=one.mapping_id,
            sentence=_sentence(concept, one))
        for concept, one in ((c, readings.of(c)) for c in source_concepts.CONCEPTS))

    by_status = {r.status for r in reasons}
    # Fixed order, so two runs over one quote produce the same bytes.
    found: list[str] = []
    if RECORDED in by_status:
        found.append(PRICING_REASON_RECORDED)
    elif NOT_SET in by_status:
        # Only where a field was declared and left blank. A book with nothing
        # declared has no absence to observe, and saying it had one would be
        # this engine's own *absence of evidence is not a pass* pointed at a
        # book where it would fire on every row.
        found.append(NO_PRICING_REASON_RECORDED)
    if UNRECOGNISED in by_status:
        found.append(PRICING_REASON_UNRECOGNISED)
    if by_status == {NOT_DECLARED}:
        found.append(PRICING_REASON_NOT_DECLARED)

    reading = Reading(
        read=True, reason=READ, reasons=reasons, codes=tuple(found),
        headline=_headline(reasons),
        basis=READING_QUALIFICATION + (
            _NOTHING_DECLARED if by_status == {NOT_DECLARED} else ""))
    exposure = _exposure(reading, codes=codes, strength=strength)
    return PricingIntent(
        reading=reading, connector=record.connector, at=taxonomy.at,
        undeclared_key_count=readings.undeclared_key_count,
        codes=reading.codes + ((POSSIBLE_MARGIN_LEAKAGE,) if exposure else ()),
        exposure=exposure)


def _headline(reasons: tuple[RecordedReason, ...]) -> str:
    """What was recorded, in one sentence — or that nothing was.

    The negative sentence is fixed and is the point of the module: **"No pricing
    reason has been recorded for this quote."** Not "this was unintentional",
    not "the desk had no reason". It says what the record holds and stops.
    """
    said = [f"{_LABEL[r.concept]} is {r.value}" for r in reasons
            if r.status == RECORDED and r.value is not None]
    if not said:
        return "No pricing reason has been recorded for this quote."
    return f"Recorded on this quote: {', '.join(said)}."


def _exposure(reading: Reading, *, codes: Sequence[str], strength: str) -> str:
    """The potential-leakage sentence, or ``""``. RESTRICTED.

    All four conditions, and each one is what keeps the sentence from being a
    claim the evidence does not carry. The wording is the strongest permitted
    and is deliberately a possibility rather than a finding — ``rules``'
    ``POSSIBLE_EXCEPTIONAL_PRICE`` and ``POSSIBLE_COST_DRIVEN`` are the register.
    """
    if NO_PRICING_REASON_RECORDED not in reading.codes:
        return ""
    if BELOW_HISTORICAL_RANGE not in codes:
        return ""
    if _RANK[strength] < _RANK[MODERATE]:
        # The claim rests on the band the price was judged against. An
        # unbelievable band cannot support a statement about a price sitting
        # under it — the gate ``drivers.attribute`` applies, for its reason.
        return ""
    return (
        f"{POSSIBLE_MARGIN_LEAKAGE}: this line is below the range this "
        f"customer's own history supports, and the field this organization "
        f"records a pricing reason in is empty on this quote. Read that as a "
        f"potential margin leakage and no further — an unrecorded reason is "
        f"not an absent one, and nothing on the record says why this price was "
        f"set.")


def _refused(reason: str, basis: str) -> PricingIntent:
    """A refusal, in both shapes at once.

    One constructor, so the reading a desk is handed and the object an owner is
    handed cannot come to refuse differently. ``exposure`` is ``""``: a claim
    about a line whose record could not be read would be a claim with nothing
    behind it.
    """
    return PricingIntent(
        reading=Reading(read=False, reason=reason, reasons=(), codes=(),
                        headline="", basis=basis),
        connector=None, at=None, undeclared_key_count=0, codes=(), exposure="")
