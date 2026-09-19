"""What an organization's own ERP fields mean, and reading one record through it.

The properties pinned here are the ones the layer is worthless without, and
each of them has a named failure behind it:

* **Supersede, never mutate.** A quote diagnosed in March under one reading of
  a field must stay explainable when somebody re-maps it in June. The reading
  in force at a date is a query, not an inference from what is live now.
* **Nothing is silently nothing.** A value nobody declared, a field nobody
  declared, a concept nobody declared — each comes back saying which of those
  it is. This whole layer exists because a field was dropped in silence.
* **Interpretation, never arithmetic.** A declaration cannot supply a price, a
  cost, a quantity or a date, and that is enforced on the way in *and* on the
  way out, so a row written by hand cannot put a number into a reading either.
* **The engine reads a concept, never a source key.** There is no parameter
  that takes one and no field that returns one; this file asserts the second
  half over the real dataclasses, because the first half is only true while
  nobody adds a parameter.
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta, timezone

import pytest

from app import clock
from app.commercial import source_concepts as sc
from app.domain import models

ORG = "org_test"
MARCH = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)
JUNE = datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)
SEPTEMBER = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)


def _declare(session, **kw):
    kw.setdefault("connector", "epicor_p21")
    kw.setdefault("entity", "quote")
    kw.setdefault("source_key", "UD_Field_07")
    kw.setdefault("concept", sc.QUOTE_INTENT)
    kw.setdefault("value_map", {"TENDER": "TENDER"})
    return sc.declare(session, ORG, **kw)


# ── the vocabulary is closed, and it is four ────────────────────────────────
def test_the_concepts_are_exactly_the_four_and_each_has_a_vocabulary():
    assert sc.CONCEPTS == ("quote_intent", "sourcing_reason", "urgency",
                           "strategic_account")
    assert set(sc.VOCABULARY) == set(sc.CONCEPTS)
    for concept, labels in sc.VOCABULARY.items():
        assert labels, f"{concept} reads as nothing"
        assert len(set(labels)) == len(labels)


def test_a_concept_nothing_consumes_is_refused(session):
    with pytest.raises(sc.MappingError) as e:
        _declare(session, concept="customer_mood",
                 value_map={"HAPPY": "HAPPY"})
    assert "customer_mood" in str(e.value)


def test_a_record_kind_nothing_carries_is_refused(session):
    with pytest.raises(sc.MappingError):
        _declare(session, entity="spaceship")


# ── interpretation, never arithmetic ────────────────────────────────────────
@pytest.mark.parametrize("target", [4500, "4500", 4500.0, "2026-03-14", True,
                                    ["TENDER"], None])
def test_a_declaration_cannot_supply_a_number_a_date_or_anything_else(session, target):
    """The structural half of the rule: the right-hand side of a value map is a
    member of a closed vocabulary, so there is no channel through which a
    tenant-configurable number could reach a calculation."""
    with pytest.raises(sc.MappingError):
        _declare(session, value_map={"TENDER": target})


def test_no_column_on_the_table_can_hold_a_number(session):
    """The other half, at rest. A column that could hold a quantity is a column
    somebody eventually reads as one. The three timestamps are this row's own
    provenance and are never read out as a value, which is why the check is for
    a numeric column rather than for every scalar type."""
    numeric = [c.name for c in models.SourceAttributeMapping.__table__.c
               if c.type.python_type in (int, float)]
    assert numeric == []


def test_a_row_written_by_hand_still_cannot_put_a_number_into_a_reading(session):
    """``declare`` is not the only way a row can arrive — a seed, a fix-up
    script, a hand-written INSERT — so the membership check happens again on the
    way out. A mis-declared value reads as unrecognised, never as a value."""
    session.add(models.SourceAttributeMapping(
        organization_id=ORG, connector="epicor_p21", entity="quote",
        source_key="UD_Field_07", pie_concept=sc.QUOTE_INTENT,
        value_map={"TENDER": "4500"}, effective_from=MARCH, recorded_at=MARCH))
    session.flush()

    reading = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                          attributes={"UD_Field_07": "TENDER"},
                          at=JUNE)[sc.QUOTE_INTENT]
    assert reading.status == sc.UNRECOGNISED
    assert reading.value is None


# ── the engine reads a concept, never a source key ──────────────────────────
def test_a_reading_carries_no_source_key(session):
    """Asserted over the dataclass rather than over one example, because the way
    this breaks is somebody adding a convenient field later."""
    names = {f.name for f in dataclass_fields(sc.ConceptReading)}
    assert names == {"concept", "status", "value", "observed", "mapping_id",
                     "effective_from"}

    _declare(session)
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TENDER"}, at=JUNE)
    assert set(out.by_concept) == set(sc.CONCEPTS)
    assert "UD_Field_07" not in repr(out.by_concept)


def test_the_caller_hands_over_the_whole_bag_and_never_names_a_key(session):
    """The signature is the enforcement: there is no parameter that takes a
    source key, so a caller cannot pass one even by mistake."""
    import inspect
    taken = set(inspect.signature(sc.readings).parameters)
    assert taken == {"session", "org", "connector", "entity", "attributes", "at"}
    assert set(inspect.signature(sc.Taxonomy.read).parameters) == {"self",
                                                                   "attributes"}


# ── nothing is silently nothing ─────────────────────────────────────────────
def test_a_declared_value_reads_as_the_concept(session):
    row = _declare(session, value_map={"TENDER": "TENDER", "TND": "TENDER",
                                       "BUDGET": "BUDGETARY"})
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TND"}, at=JUNE)
    reading = out[sc.QUOTE_INTENT]
    assert (reading.status, reading.value) == (sc.RECORDED, "TENDER")
    assert reading.mapping_id == row.mapping_id
    assert out.recorded_values() == {sc.QUOTE_INTENT: "TENDER"}


def test_case_and_padding_are_not_meaning_but_a_different_spelling_is(session):
    _declare(session, value_map={"Tender": "TENDER"})
    for spelling in ("TENDER", "tender", "  Tender  "):
        out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                          attributes={"UD_Field_07": spelling}, at=JUNE)
        assert out[sc.QUOTE_INTENT].value == "TENDER", spelling
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TND"}, at=JUNE)
    assert out[sc.QUOTE_INTENT].status == sc.UNRECOGNISED


def test_an_unmapped_value_says_what_the_source_said(session):
    """Not "nothing recorded" — the source recorded something and nobody has
    said what it means, which is a configuration gap somebody can close."""
    _declare(session, value_map={"TENDER": "TENDER"})
    reading = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                          attributes={"UD_Field_07": "RATE CONTRACT"},
                          at=JUNE)[sc.QUOTE_INTENT]
    assert reading.status == sc.UNRECOGNISED
    assert reading.observed == "RATE CONTRACT"
    assert reading.value is None
    assert reading.recorded is False


def test_an_unmapped_value_is_carried_but_bounded(session):
    _declare(session)
    reading = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                          attributes={"UD_Field_07": "x" * 400},
                          at=JUNE)[sc.QUOTE_INTENT]
    assert reading.status == sc.UNRECOGNISED
    assert len(reading.observed) <= 120


def test_an_undeclared_key_is_counted_and_never_named(session):
    """The engine must not learn a source key, and an organization must still be
    able to see that half its fields mean nothing here yet."""
    _declare(session)
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TENDER", "UD_Field_08": "X",
                                  "cf_branch": "Bangalore"},
                      at=JUNE)
    assert out.undeclared_key_count == 2
    assert "UD_Field_08" not in repr(out)


def test_a_declared_field_this_record_does_not_carry_reads_not_set(session):
    _declare(session)
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"cf_branch": "Bangalore"}, at=JUNE)
    assert out[sc.QUOTE_INTENT].status == sc.NOT_SET
    assert out.undeclared_key_count == 1


def test_a_blank_value_is_not_a_category(session):
    _declare(session)
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "   "}, at=JUNE)
    assert out[sc.QUOTE_INTENT].status == sc.NOT_SET


def test_an_empty_taxonomy_reads_not_declared_rather_than_a_guess(session):
    """The honest limit: on a live book these fields are mostly empty and five
    of the six connectors emit nothing at all. That has to read as "nothing is
    recorded", never as a classification."""
    out = sc.readings(session, ORG, connector="netsuite", entity="quote",
                      attributes={"custentity_quote_type": "TENDER"},
                      at=JUNE)
    assert {r.status for r in out.by_concept.values()} == {sc.NOT_DECLARED}
    assert out.recorded_values() == {}


def test_a_null_bag_reads_the_same_as_an_empty_one(session):
    _declare(session)
    for bag in (None, {}):
        out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                          attributes=bag, at=JUNE)
        assert out[sc.QUOTE_INTENT].status == sc.NOT_SET
        assert out.undeclared_key_count == 0


def test_a_record_whose_connector_was_never_recorded_matches_nothing(session):
    _declare(session)
    out = sc.readings(session, ORG, connector=None, entity="quote",
                      attributes={"UD_Field_07": "TENDER"}, at=JUNE)
    assert out[sc.QUOTE_INTENT].status == sc.NOT_DECLARED


def test_a_declaration_for_one_record_kind_does_not_read_another(session):
    _declare(session, entity="quote")
    out = sc.readings(session, ORG, connector="epicor_p21", entity="customer",
                      attributes={"UD_Field_07": "TENDER"}, at=JUNE)
    assert out[sc.QUOTE_INTENT].status == sc.NOT_DECLARED


def test_every_concept_gets_a_reading_on_every_call(session):
    _declare(session)
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TENDER"}, at=JUNE)
    assert set(out.by_concept) == set(sc.CONCEPTS)
    assert all(r.status in sc.STATUSES for r in out.by_concept.values())


def test_asking_for_something_that_is_not_a_concept_refuses(session):
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes=None, at=JUNE)
    with pytest.raises(sc.MappingError):
        out["margin"]


# ── supersede, never mutate ─────────────────────────────────────────────────
def test_a_first_declaration_explains_the_book_that_already_exists(session):
    """Effective from the beginning, deliberately. A default of "now" would
    leave every quote already on the book unreadable — the benign default this
    codebase has been caught by three times."""
    row = _declare(session)
    assert clock.aware(row.effective_from) == sc.BEGINNING
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TENDER"}, at=MARCH)
    assert out[sc.QUOTE_INTENT].value == "TENDER"


def test_a_correction_leaves_march_explained_by_marchs_reading(session):
    first = _declare(session, value_map={"TENDER": "TENDER"})
    second = _declare(session, value_map={"TENDER": "BUDGETARY"},
                      effective_from=JUNE)

    bag = {"UD_Field_07": "TENDER"}
    assert sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                       attributes=bag, at=MARCH)[sc.QUOTE_INTENT].value == "TENDER"
    assert sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                       attributes=bag, at=SEPTEMBER)[sc.QUOTE_INTENT].value == "BUDGETARY"

    session.refresh(first)
    assert first.value_map == {"TENDER": "TENDER"}, "the old row was mutated"
    assert clock.aware(first.superseded_at) == JUNE
    assert second.superseded_at is None


def test_the_reading_in_force_names_the_row_that_decided_it(session):
    first = _declare(session, value_map={"TENDER": "TENDER"})
    second = _declare(session, value_map={"TENDER": "BUDGETARY"},
                      effective_from=JUNE)
    bag = {"UD_Field_07": "TENDER"}
    assert sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                       attributes=bag, at=MARCH)[sc.QUOTE_INTENT].mapping_id \
        == first.mapping_id
    assert sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                       attributes=bag, at=SEPTEMBER)[sc.QUOTE_INTENT].mapping_id \
        == second.mapping_id


def test_the_intervals_tile_with_no_gap_and_no_overlap(session):
    _declare(session, value_map={"TENDER": "TENDER"})
    _declare(session, value_map={"TENDER": "BUDGETARY"}, effective_from=JUNE)
    _declare(session, value_map={"TENDER": "SAMPLE"}, effective_from=SEPTEMBER)

    rows = sc.history(session, ORG, connector="epicor_p21", entity="quote",
                      concept=sc.QUOTE_INTENT)
    assert [clock.aware(r.superseded_at) for r in rows] == [JUNE, SEPTEMBER, None]
    for moment in (sc.BEGINNING, MARCH, JUNE, JUNE + timedelta(days=1),
                   SEPTEMBER, SEPTEMBER + timedelta(days=90)):
        live = sc.in_force(session, ORG, connector="epicor_p21", entity="quote",
                           at=moment).declarations
        assert sc.QUOTE_INTENT in live, moment


def test_a_correction_may_name_a_different_field_entirely(session):
    """What is unique is which field answers a concept, not the field. An
    organization that moves quote intent from one custom field to another
    supersedes; it does not end up with two fields answering one question."""
    _declare(session, source_key="UD_Field_07")
    _declare(session, source_key="UD_Field_11", effective_from=JUNE)

    late = {"UD_Field_11": "TENDER", "UD_Field_07": "TENDER"}
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes=late, at=SEPTEMBER)
    assert out[sc.QUOTE_INTENT].value == "TENDER"
    assert out.undeclared_key_count == 1, "the retired field is now undeclared"


def test_a_correction_before_the_reading_it_replaces_is_refused(session):
    _declare(session, value_map={"TENDER": "TENDER"}, effective_from=JUNE)
    with pytest.raises(sc.MappingError):
        _declare(session, value_map={"TENDER": "SAMPLE"}, effective_from=MARCH)


def test_only_one_declaration_per_concept_is_ever_live(session):
    _declare(session)
    _declare(session, value_map={"TENDER": "SAMPLE"}, effective_from=JUNE)
    live = session.query(models.SourceAttributeMapping).filter_by(
        organization_id=ORG, pie_concept=sc.QUOTE_INTENT,
        superseded_at=None).all()
    assert len(live) == 1


def test_a_second_live_row_is_refused_by_the_database(session):
    """The partial unique index, not the writer. One live reading per concept
    has to be a database guarantee: a reading with two answers is not
    deterministic, and the writer is not the only path a row can arrive by."""
    from sqlalchemy.exc import IntegrityError

    _declare(session)
    session.commit()
    session.add(models.SourceAttributeMapping(
        organization_id=ORG, connector="epicor_p21", entity="quote",
        source_key="UD_Field_09", pie_concept=sc.QUOTE_INTENT,
        value_map={"TENDER": "SAMPLE"}, effective_from=JUNE, recorded_at=JUNE))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_nothing_is_ever_deleted(session):
    _declare(session)
    _declare(session, value_map={"TENDER": "SAMPLE"}, effective_from=JUNE)
    _declare(session, value_map={"TENDER": "BUDGETARY"}, effective_from=SEPTEMBER)
    assert len(sc.history(session, ORG, connector="epicor_p21",
                          entity="quote")) == 3


def test_history_is_oldest_first_and_deterministic(session):
    _declare(session)
    _declare(session, value_map={"TENDER": "SAMPLE"}, effective_from=JUNE)
    rows = sc.history(session, ORG, connector="epicor_p21", entity="quote")
    once = [r.mapping_id for r in rows]
    twice = [r.mapping_id for r in
             sc.history(session, ORG, connector="epicor_p21", entity="quote")]
    assert once == twice
    assert [clock.aware(r.effective_from) for r in rows] == [sc.BEGINNING, JUNE]


# ── the point-in-time instant is required ───────────────────────────────────
def test_reading_without_an_instant_is_not_possible():
    """No default for ``at``. An unqualified "what does this field mean" has two
    answers the moment a correction exists, and picking one silently is how a
    quote stops being explainable."""
    import inspect
    for fn in (sc.in_force, sc.readings):
        assert inspect.signature(fn).parameters["at"].default \
            is inspect.Parameter.empty


def test_a_taxonomy_carries_the_instant_it_was_read_at(session):
    _declare(session)
    tax = sc.in_force(session, ORG, connector="epicor_p21", entity="quote",
                      at=MARCH)
    assert tax.at == MARCH
    assert tax.entity == "quote"


def test_one_taxonomy_reads_many_records_identically(session):
    """The batch path and the single-record path are the same two calls, not two
    implementations."""
    _declare(session, value_map={"TENDER": "TENDER"})
    tax = sc.in_force(session, ORG, connector="epicor_p21", entity="quote",
                      at=JUNE)
    bag = {"UD_Field_07": "TENDER"}
    assert tax.read(bag)[sc.QUOTE_INTENT] == sc.readings(
        session, ORG, connector="epicor_p21", entity="quote",
        attributes=bag, at=JUNE)[sc.QUOTE_INTENT]


# ── declarations that decide nothing ────────────────────────────────────────
def test_a_declaration_with_no_values_is_refused(session):
    with pytest.raises(sc.MappingError):
        _declare(session, value_map={})


def test_a_declaration_with_no_field_is_refused(session):
    with pytest.raises(sc.MappingError):
        _declare(session, source_key="   ")


def test_a_declaration_with_no_system_is_refused(session):
    with pytest.raises(sc.MappingError):
        _declare(session, connector="")


def test_two_spellings_of_one_value_meaning_different_things_is_refused(session):
    with pytest.raises(sc.MappingError):
        _declare(session, value_map={"Tender": "TENDER", "TENDER": "SAMPLE"})


def test_the_stored_map_is_folded_so_a_re_read_is_identical(session):
    row = _declare(session, value_map={" Tender ": "TENDER", "tnd": "TENDER"})
    assert row.value_map == {"TENDER": "TENDER", "TND": "TENDER"}


# ── org scoping ─────────────────────────────────────────────────────────────
def test_another_organizations_declaration_is_not_read(session):
    sc.declare(session, "org_other", connector="epicor_p21", entity="quote",
               source_key="UD_Field_07", concept=sc.QUOTE_INTENT,
               value_map={"TENDER": "TENDER"})
    out = sc.readings(session, ORG, connector="epicor_p21", entity="quote",
                      attributes={"UD_Field_07": "TENDER"}, at=JUNE)
    assert out[sc.QUOTE_INTENT].status == sc.NOT_DECLARED


def test_a_reading_survives_the_round_trip_through_the_database(engine):
    """SQLite returns a ``DateTime(timezone=True)`` naive and Postgres returns
    it aware, so a declaration read back in a later session compares against a
    different kind of value from the one just written. ``clock.aware`` is why
    that is a correction rather than a ``TypeError`` on one backend and a wrong
    answer on the other — pinned here because the whole point-in-time promise
    runs through those comparisons.
    """
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    writer = maker()
    _declare(writer, value_map={"TENDER": "TENDER"})
    _declare(writer, value_map={"TENDER": "BUDGETARY"}, effective_from=JUNE)
    writer.commit()
    writer.close()

    reader = maker()
    try:
        bag = {"UD_Field_07": "TENDER"}
        assert sc.readings(reader, ORG, connector="epicor_p21", entity="quote",
                           attributes=bag, at=MARCH)[sc.QUOTE_INTENT].value \
            == "TENDER"
        assert sc.readings(reader, ORG, connector="epicor_p21", entity="quote",
                           attributes=bag, at=SEPTEMBER)[sc.QUOTE_INTENT].value \
            == "BUDGETARY"
        assert len(sc.history(reader, ORG, connector="epicor_p21",
                              entity="quote")) == 2
    finally:
        reader.close()
