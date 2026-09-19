"""What a quote's own record says about why it was priced — and what it refuses.

The wording is the feature here, so most of this file asserts sentences. Each
property below has a named failure behind it, and three of them are the same
failure this repository has already found in three unrelated places.

* **Four statuses are four sentences.** ``source_concepts`` distinguishes "no
  field has been declared for this", "the field is declared and this quote is
  blank in it", "the field holds something nobody declared a meaning for" and
  "here is what it says". Collapsing any pair of those into a boolean is the
  defect; the middle two in particular are a configuration gap and a data gap,
  and only the second is even weak evidence about this quote.

* **Absence of a recorded reason is never evidence of absence of a reason.** The
  sentence is fixed and is asserted verbatim. "Unintentional" and anything like
  it must not appear anywhere in this module's output.

* **The leakage claim needs a declared field to be absent from.** On a book where
  nothing is declared there is no absence to observe, and a claim that fired
  anyway would fire on every below-band line of every tenant today — CLAUDE.md
  §1's *absence of evidence is not a pass*, pointed at a whole book.

* **Point in time.** The taxonomy is read at the diagnosis's ``knowable_by``, so
  a declaration typed after the quote was written is not one the quoter had, and
  a correction made in June leaves March explained by March's row.

* **The split reaches the desk at the reading and stops at the claim.** A desk
  told the quote was recorded as a tender has the reason a price is low; a desk
  told the line may be leaking margin has a judgement about a number it may not
  see. ``intent.Reading`` has no field for the second, and
  ``POSSIBLE_MARGIN_LEAKAGE`` is outside ``OPERATIONS_CODES``.
"""
from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone

import pytest

from app.commercial import source_concepts as sc
from app.commercial.quote_diagnosis import intent, render, rules

ORG = "org_test"
CONNECTOR = "epicor_p21"

MARCH = datetime(2026, 3, 14, 23, 59, 59, tzinfo=timezone.utc)
JUNE = datetime(2026, 6, 20, 23, 59, 59, tzinfo=timezone.utc)

#: The shape of a line the engine has already found below the range this
#: customer's history supports. The recorded-reason reading never makes that
#: finding; it is handed it.
BELOW = (rules.BELOW_HISTORICAL_RANGE,)
WITHIN = (rules.WITHIN_HISTORICAL_RANGE,)


def _declare(session, **kw):
    kw.setdefault("connector", CONNECTOR)
    kw.setdefault("entity", "quote")
    kw.setdefault("source_key", "UD_Field_07")
    kw.setdefault("concept", sc.QUOTE_INTENT)
    kw.setdefault("value_map", {"TENDER": "TENDER", "Tender enquiry": "TENDER"})
    return sc.declare(session, ORG, **kw)


def _read(session, attributes, *, at=MARCH, codes=BELOW, strength=rules.STRONG,
          connector=CONNECTOR, found=True):
    """One quote read through whatever this organization has declared by ``at``."""
    record = intent.SourceRecord(found=found, connector=connector,
                                 attributes=attributes)
    taxonomy = (sc.in_force(session, ORG, connector=connector,
                            entity=intent.ENTITY, at=at)
                if connector else None)
    return intent.assess(record=record, taxonomy=taxonomy, codes=codes,
                         strength=strength)


def _sentence(reading, concept):
    return next(r.sentence for r in reading.reasons if r.concept == concept)


# ── four statuses, four sentences ────────────────────────────────────────────

def test_every_reading_answers_for_all_four_concepts_and_never_a_subset(session):
    _declare(session)
    got = _read(session, {"UD_Field_07": "TENDER"})

    assert [r.concept for r in got.reading.reasons] == list(sc.CONCEPTS)
    assert got.reading.read is True


def test_the_four_statuses_get_four_different_sentences(session):
    """The property the whole module turns on.

    One quote that exercises all four at once: a declared field holding a value
    the map covers, a declared field holding nothing, a declared field holding
    something nobody declared, and a concept nobody declared at all.
    """
    _declare(session, concept=sc.QUOTE_INTENT, source_key="cf_quote_type",
             value_map={"Tender enquiry": "TENDER"})
    _declare(session, concept=sc.URGENCY, source_key="cf_urgency",
             value_map={"Breakdown": "EMERGENCY"})
    _declare(session, concept=sc.SOURCING_REASON, source_key="cf_why",
             value_map={"Machine down": "BREAKDOWN"})

    got = _read(session, {"cf_quote_type": "Tender enquiry",
                          "cf_urgency": "",
                          "cf_why": "Somebody typed this"})
    reading = got.reading

    by_concept = {r.concept: r for r in reading.reasons}
    assert by_concept[sc.QUOTE_INTENT].status == sc.RECORDED
    assert by_concept[sc.URGENCY].status == sc.NOT_SET
    assert by_concept[sc.SOURCING_REASON].status == sc.UNRECOGNISED
    assert by_concept[sc.STRATEGIC_ACCOUNT].status == sc.NOT_DECLARED

    # Four sentences, and no two of them the same. A renderer that had collapsed
    # a pair would still pass a status assertion and fail this one.
    sentences = [r.sentence for r in reading.reasons]
    assert len(set(sentences)) == 4

    assert _sentence(reading, sc.QUOTE_INTENT) == (
        "This quote records the quote type as TENDER.")
    assert _sentence(reading, sc.URGENCY) == (
        "This quote has a declared field for the urgency and holds nothing in "
        "it.")
    assert _sentence(reading, sc.SOURCING_REASON) == (
        "This quote's field for the sourcing reason holds “Somebody typed "
        "this”, which this organization's declaration does not give a meaning "
        "for.")
    assert _sentence(reading, sc.STRATEGIC_ACCOUNT) == (
        "No field on this quote has been declared to carry the account's "
        "standing, so nothing is recorded about it either way.")


def test_a_value_nobody_declared_is_carried_back_and_is_not_a_concept(session):
    """Visible rather than dropped — this layer exists because a field was
    dropped in silence — and it is a *value*, never a key."""
    _declare(session, source_key="cf_quote_type",
             value_map={"Tender enquiry": "TENDER"})
    got = _read(session, {"cf_quote_type": "Framework agreement"})

    reason = next(r for r in got.reading.reasons if r.concept == sc.QUOTE_INTENT)
    assert reason.status == sc.UNRECOGNISED
    assert reason.observed == "Framework agreement"
    assert reason.value is None
    assert rules.PRICING_REASON_UNRECOGNISED in got.reading.codes
    # And it is not read as an absence: something *was* entered.
    assert rules.NO_PRICING_REASON_RECORDED not in got.reading.codes


# ── the sentence this module exists for ──────────────────────────────────────

def test_nothing_recorded_says_so_and_says_nothing_more(session):
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""})

    assert got.reading.headline == (
        "No pricing reason has been recorded for this quote.")
    assert rules.NO_PRICING_REASON_RECORDED in got.reading.codes

    body = json.dumps([got.reading.headline, got.reading.basis, got.exposure,
                       *[r.sentence for r in got.reading.reasons]]).lower()
    for forbidden in ("unintentional", "deliberate", "careless", "had no reason",
                      "for no reason", "without a reason", "did not have"):
        assert forbidden not in body, f"{forbidden!r} is a claim about intent"
    # "there was no reason" appears exactly once and only inside the sentence
    # that denies it. The bare phrase is the claim this module must never make,
    # and the qualification is the only place it is allowed to be written down.
    assert (body.count("there was no reason")
            == body.count("not evidence that there was no reason") == 1)
    # And the qualification that keeps it honest is on every reading.
    assert ("Absence of a recorded reason is not evidence that there was no "
            "reason.") in got.reading.basis


def test_what_was_recorded_is_stated_in_the_engines_own_vocabulary(session):
    """No gloss. ``TENDER`` is the concept value and travels as itself: a term
    worded one way by the engine and another by a renderer is two terms."""
    _declare(session, source_key="cf_quote_type",
             value_map={"Tender enquiry": "TENDER"})
    got = _read(session, {"cf_quote_type": "Tender enquiry"})

    assert got.reading.headline == (
        "Recorded on this quote: the quote type is TENDER.")
    assert got.reading.recorded_values == {sc.QUOTE_INTENT: "TENDER"}
    assert rules.PRICING_REASON_RECORDED in got.reading.codes


def test_nothing_declared_is_its_own_answer_and_not_an_absent_reason(session):
    """The ordinary answer on a live book, and the one that must not read as a
    quote with no reason. Nothing has been declared, so there is no field this
    quote could have been blank in."""
    got = _read(session, {"cf_quote_type": "Tender enquiry"})

    assert got.reading.read is True
    assert got.reading.codes == (rules.PRICING_REASON_NOT_DECLARED,)
    assert rules.NO_PRICING_REASON_RECORDED not in got.reading.codes
    assert all(r.status == sc.NOT_DECLARED for r in got.reading.reasons)
    # And the note names what would finish it rather than leaving the silence
    # to be read as a fact about the quote.
    assert "until somebody declares one" in got.reading.basis


# ── the claim, and the four things it needs ──────────────────────────────────

def test_a_below_band_line_with_a_declared_field_left_empty_is_a_possibility(session):
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""}, codes=BELOW,
                strength=rules.MODERATE)

    # The code is on ``codes`` and the sentence is a sentence. Separately
    # checkable, which is the point of keeping them apart.
    assert rules.POSSIBLE_MARGIN_LEAKAGE in got.codes
    assert rules.POSSIBLE_MARGIN_LEAKAGE not in got.exposure
    assert got.exposure.startswith(
        "This line is below the range this customer's own history supports")
    # The strongest form permitted, and it is a possibility.
    assert "potential margin leakage and no further" in got.exposure
    assert "an unrecorded reason is not an absent one" in got.exposure


@pytest.mark.parametrize("why,kw,attributes", [
    ("a book with nothing declared has no absence to observe",
     {"codes": BELOW, "strength": rules.STRONG}, {"cf_quote_type": "anything"}),
])
def test_no_claim_where_nothing_was_declared(session, why, kw, attributes):
    got = _read(session, attributes, **kw)
    assert got.exposure == "", why
    assert rules.POSSIBLE_MARGIN_LEAKAGE not in got.codes


def test_a_recorded_reason_removes_the_claim_rather_than_qualifying_it(session):
    """The quote says why. There is nothing unexplained left to call a
    possibility, and saying it anyway would be the engine ignoring its own
    evidence."""
    _declare(session, source_key="cf_quote_type")
    _declare(session, concept=sc.URGENCY, source_key="cf_urgency",
             value_map={"Breakdown": "EMERGENCY"})
    got = _read(session, {"cf_quote_type": "", "cf_urgency": "Breakdown"},
                codes=BELOW, strength=rules.STRONG)

    assert got.exposure == ""
    assert rules.PRICING_REASON_RECORDED in got.reading.codes
    assert rules.NO_PRICING_REASON_RECORDED not in got.reading.codes


def test_a_line_inside_its_band_is_never_a_possibility(session):
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""}, codes=WITHIN,
                strength=rules.STRONG)
    assert got.exposure == ""


@pytest.mark.parametrize("grade", [rules.WEAK, rules.INSUFFICIENT])
def test_an_unbelievable_band_cannot_support_the_claim(session, grade):
    """The claim rests on the band the price was judged against, so it is gated
    on the same MODERATE floor ``drivers.attribute`` applies."""
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""}, codes=BELOW, strength=grade)
    assert got.exposure == ""


# ── the refusals, which are not "nothing was recorded" ───────────────────────

def test_a_quote_with_no_source_document_says_so(session):
    """A draft in the Quote Builder exists here and in no ERP. "There is nothing
    to read" is a different sentence from "nothing was recorded", and a reader
    given the second about the first has been told something false."""
    got = _read(session, None, found=False, connector=None)

    assert got.reading.read is False
    assert got.reading.reason == intent.NO_SOURCE_RECORD
    assert got.reading.reasons == ()
    assert got.reading.headline == ""
    assert intent.NO_SOURCE_RECORD not in got.reading.basis
    assert got.reading.basis.startswith(
        "No document from a source system was found")
    assert got.exposure == ""


def test_a_document_that_does_not_name_its_system_says_so(session):
    """``QuoteDoc.connector`` is nullable, and a declaration is always about a
    named system. A gap in the row's provenance is not a gap in the
    organization's configuration, so it is not ``NOT_DECLARED``."""
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": "TENDER"}, connector=None)

    assert got.reading.reason == intent.SOURCE_NOT_RECORDED
    assert "does not say which system issued it" in got.reading.basis
    assert got.reading.codes == ()


def test_a_refusal_is_never_silent(session):
    """Both refusals put a sentence on the card. An empty block reads as
    "nothing to report", which is the failure CLAUDE.md §1 names."""
    for got in (_read(session, None, found=False, connector=None),
                _read(session, {}, connector=None)):
        view = render.render_intent(got.reading, surfaces=True)
        assert view.renders is True
        assert view.note
        assert view.read is False


# ── point in time ────────────────────────────────────────────────────────────

def test_a_declaration_typed_after_the_quote_is_not_a_reading_the_quoter_had(session):
    """The same rule every other piece of evidence here follows. The declaration
    is superseded in June; a March quote keeps March's reading."""
    _declare(session, source_key="cf_quote_type",
             value_map={"Tender enquiry": "TENDER"})
    # June: the business decides that field means something else.
    _declare(session, source_key="cf_quote_type",
             value_map={"Tender enquiry": "BUDGETARY"}, effective_from=JUNE)

    march = _read(session, {"cf_quote_type": "Tender enquiry"}, at=MARCH)
    june = _read(session, {"cf_quote_type": "Tender enquiry"}, at=JUNE)

    assert march.reading.recorded_values == {sc.QUOTE_INTENT: "TENDER"}
    assert june.reading.recorded_values == {sc.QUOTE_INTENT: "BUDGETARY"}
    # And the reading names the row that decided it, so March stays explainable.
    assert march.at == MARCH
    marched = next(r for r in march.reading.reasons
                   if r.concept == sc.QUOTE_INTENT)
    assert marched.mapping_id and marched.mapping_id != next(
        r.mapping_id for r in june.reading.reasons
        if r.concept == sc.QUOTE_INTENT)


def test_a_first_declaration_reads_the_book_that_was_already_there(session):
    """The other half, and the reason point-in-time costs nothing on a fresh
    tenant: ``declare`` makes a first declaration effective from the beginning,
    so a field declared today explains a quote written last March."""
    _declare(session, source_key="cf_quote_type",
             value_map={"Tender enquiry": "TENDER"})
    got = _read(session, {"cf_quote_type": "Tender enquiry"}, at=MARCH)
    assert got.reading.recorded_values == {sc.QUOTE_INTENT: "TENDER"}


# ── the desk / owner line ────────────────────────────────────────────────────

def test_the_reading_the_desk_gets_has_no_field_a_margin_claim_could_sit_in(session):
    """The structural half. ``exposure`` is declared on ``PricingIntent`` and on
    nothing else, so the desk's projection cannot carry it — there is no field
    to forget to remove, which is ``rules.OperationsDiagnosis``' guarantee in a
    second place."""
    names = {f.name for f in dataclass_fields(intent.Reading)}

    assert "exposure" not in names
    assert not (names & rules.FORBIDDEN_OPERATIONS_FIELDS)
    assert "exposure" in rules.FORBIDDEN_OPERATIONS_FIELDS
    # And the owner's type is where it lives.
    assert "exposure" in {f.name for f in dataclass_fields(intent.PricingIntent)}


def test_the_desks_codes_are_the_one_allowlist_and_the_claim_is_outside_it(session):
    """One vocabulary, not two. Every code the reading can emit is inside
    ``rules.OPERATIONS_CODES``; the claim's code is deliberately outside it, so
    the allowlist stops it as well as the type does."""
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""}, codes=BELOW,
                strength=rules.MODERATE)

    assert set(got.reading.codes) <= rules.OPERATIONS_CODES
    assert rules.POSSIBLE_MARGIN_LEAKAGE not in rules.OPERATIONS_CODES
    assert rules.POSSIBLE_MARGIN_LEAKAGE in got.codes


def test_the_operations_projection_carries_the_reading_and_not_the_claim(session):
    """Through the real projection rather than by reading the types: a desk card
    renders the four sentences and never the possibility."""
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""}, codes=BELOW,
                strength=rules.MODERATE)

    desk = render.render_intent(got.reading, surfaces=True)
    owner = render.render_intent(got, surfaces=True)

    assert len(desk.lines) == 4
    assert all("leakage" not in line.lower() for line in desk.lines)
    assert desk.headline == "No pricing reason has been recorded for this quote."
    # The owner's copy leads with the claim and keeps the same four under it.
    assert owner.lines[0] == got.exposure
    assert owner.lines[1:] == desk.lines
    # And each projection publishes its own codes rather than one filtered
    # twice: the claim's code is on the owner's view and on no other.
    assert rules.POSSIBLE_MARGIN_LEAKAGE in owner.codes
    assert rules.POSSIBLE_MARGIN_LEAKAGE not in desk.codes


# ── no source key ever leaves this layer ─────────────────────────────────────

def test_the_source_key_an_organization_declared_never_reaches_the_output(session):
    """The multi-ERP promise, checked at the one place it could break. A
    diagnosis that named ``cf_quote_type`` would need a fork for the second
    tenant, and the mapping layer would have been pointless."""
    _declare(session, source_key="cf_quote_type",
             value_map={"Tender enquiry": "TENDER"})
    got = _read(session, {"cf_quote_type": "Tender enquiry",
                          "cf_branch": "Peenya"})

    view = render.render_intent(got, surfaces=True)
    body = json.dumps([view.headline, view.note, *view.lines,
                       *view.codes]).lower()
    assert "cf_quote_type" not in body
    assert "cf_branch" not in body
    assert "ud_field" not in body
    # The undeclared field is counted for an owner, never named.
    assert got.undeclared_key_count == 1
    assert "peenya" not in body


# ── determinism ──────────────────────────────────────────────────────────────

def test_the_same_quote_read_twice_produces_the_same_bytes(session):
    _declare(session, source_key="cf_quote_type")
    _declare(session, concept=sc.URGENCY, source_key="cf_urgency",
             value_map={"Breakdown": "EMERGENCY"})
    attributes = {"cf_urgency": "Breakdown", "cf_quote_type": "",
                  "cf_other": "x"}

    def once(bag):
        got = _read(session, bag, codes=BELOW, strength=rules.MODERATE)
        return json.dumps(
            {"codes": list(got.codes), "exposure": got.exposure,
             "headline": got.reading.headline,
             "lines": [r.sentence for r in got.reading.reasons]},
            sort_keys=True)

    assert once(attributes) == once(dict(reversed(list(attributes.items()))))


# ── not stored, and a stored row says so ─────────────────────────────────────

def test_a_stored_row_refuses_rather_than_answering_with_silence():
    """``quote_diagnoses`` has no column for this — see ``rules.ENGINE_VERSION``
    — so a row read back cannot answer. A reader who simply found no block would
    read the absence as "no pricing reason was recorded", which is a claim about
    the quote rather than about the row."""
    view = render.render_intent(render.INTENT_NOT_STORED, surfaces=True)

    assert view.renders is True
    assert view.read is False
    assert view.lines == ()
    assert view.reason == render.NOT_ON_STORED_RECORD
    assert render.NOT_ON_STORED_RECORD not in view.note
    assert view.note.startswith("This is the diagnosis as it was stored")
    assert "Re-assess this line to see it." in view.note


def test_the_engine_version_is_unmoved_because_no_column_moved():
    """Stated as a test rather than only as a comment. The reading adds no code
    to ``codes`` or to ``context``, which are the two persisted columns, so every
    row written after this change is byte-identical to the row that would have
    been written before it."""
    assert rules.ENGINE_VERSION == "qd-1"
    recorded = {rules.PRICING_REASON_RECORDED, rules.NO_PRICING_REASON_RECORDED,
                rules.PRICING_REASON_NOT_DECLARED,
                rules.PRICING_REASON_UNRECOGNISED, rules.POSSIBLE_MARGIN_LEAKAGE}
    # The source of truth for what a stored row carries is ``service.record``.
    import inspect

    from app.commercial.quote_diagnosis import service
    written = inspect.getsource(service.record)
    assert "codes=list(owner.codes)" in written
    assert "context=list(owner.context)" in written
    assert "intent" not in written
    assert not (recorded & set(rules.OPERATIONS_CODES) & {
        rules.POSSIBLE_MARGIN_LEAKAGE})


# ── the gate is not widened ──────────────────────────────────────────────────

def test_the_reading_never_makes_a_card_interrupt_somebody(session):
    """Default silent. An intent signal that surfaced on its own would be alert
    fatigue with a new name, so the block can only ever be quieter than the gate
    ``rules._surfaces`` already decided."""
    _declare(session, source_key="cf_quote_type")
    got = _read(session, {"cf_quote_type": ""}, codes=BELOW,
                strength=rules.MODERATE)

    assert render.render_intent(got.reading, surfaces=False).renders is False
    assert render.render_intent(got.reading, surfaces=True).renders is True
