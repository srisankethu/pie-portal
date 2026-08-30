"""Phase 1's store: two sources, superseded not mutated, and no churn.

What these pin, in the order the package would break them:

* **Both source kinds, separately.** A decoded name and a linked catalogue
  record are two claims about one field, and the table is keyed to keep both.
  A test that only exercised one would pass while the writer collapsed them.
* **Supersede on change, and nothing on no change.** The second is the one
  worth writing down: a writer that superseded on every run looks identical
  from the outside — same live rows, same values — until the table is a hundred
  times the size it should be and the history is a log of reruns. So the
  no-churn test compares the whole table byte for byte, including the row ids
  and the timestamps, rather than counting rows.
* **Numbers reach ``value_num``.** A retrieval layer filtering
  ``corner_radius_mm BETWEEN 0.4 AND 0.8`` cannot do it over text, which is the
  whole reason the column exists.
* **Org isolation.** On SQLite the ``organization_id`` filters are the only
  tenant boundary there is — ``tenant_isolation`` binds on PostgreSQL alone —
  so the Python scoping is what is under test here.
* **Nothing decodable, nothing stored.** §1: absence of evidence is not a pass.
  A product whose name says nothing gets no row, not a zero and not a blank.

The decoder is the real one where the test is about decoding, because a stub
that returns ``corner_radius_mm`` proves the store works on a value the stub
chose. ``PIE_PARSER_ROOT`` must point at a pie-parser checkout for those; the
pack loads and decodes four names in about a third of a second, so there is no
reason to fake it. The catalogue is stubbed the way ``test_catalog_link`` stubs
it, for the reason that file gives: driving these through the real 13 MB
catalogue would make them slow, and silent about the absent-catalogue branch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

# Twelve tests across this file and its sibling `test_product_attributes.py`
# read the REAL decode rather than a stub, so they need the engine and carry
# `requires_pie`. They shipped unmarked: on a checkout without the submodule
# they did not skip, they FAILED — twelve assertion errors that read as code
# defects and are a missing directory. `conftest.py` has the mechanism that
# prevents exactly this and it simply had not been applied to these files.
# Mark a test here when it asserts on a decoded field; leave it unmarked when
# it asserts on what happens with NO engine, which is the other half of both
# files and must keep running without one.
from sqlalchemy import select

from app.attributes import (CATALOGUE_LINK, DECODED_NAME, attribute_coverage,
                            claims_from_catalogue_record, claims_from_values,
                            decorate_products, live_values, write_claims)
from app.attributes.extract import AttributeClaim
from app.domain import models

ORG = "org_a"
OTHER = "org_b"

#: A real turning-insert designation and a real catalogue record id, so the
#: decode under test is the pack's own answer rather than a fixture's.
INSERT_NAME = "CNMG 120408-49 - TN2000"
INSERT_RECORD_ID = "2001174"


def _org(session, org: str) -> None:
    if session.get(models.Organization, org) is None:
        session.add(models.Organization(organization_id=org, name=org))
        session.flush()


def _product(session, org: str, name: str, *, external_id: str = "i1",
             pie_record_id: Optional[str] = None) -> models.Product:
    _org(session, org)
    row = models.Product(organization_id=org, external_id=external_id, name=name,
                         connector="zoho", connection_id="c1",
                         pie_record_id=pie_record_id)
    session.add(row)
    session.flush()
    return row


class _Catalogue:
    """Stands in for ``pie_service``: a tiny catalogue, or none at all.

    The same shape ``test_catalog_link._Catalog`` uses — one lookup keyed on the
    record id, and an ``available`` flag so the absent branch is reachable.
    """

    def __init__(self, records: Optional[Dict[str, dict]] = None,
                 available: bool = True):
        self._records = records or {}
        self.catalog_available = available
        self.catalog_version = "ck_test_v1"

    def lookup_record(self, identifier):
        if not identifier or not self.catalog_available:
            return None
        return self._records.get(str(identifier).strip())


def _record(**overrides: Any) -> dict:
    """One decoded catalogue row, in the engine's own emitted shape.

    Metadata included deliberately — ``run_id``, ``source_file``,
    ``row_confidence`` and the rest are exactly what must *not* become
    attributes, and a fixture holding only facts could not catch a projection
    that let them through.
    """
    row = {
        "record_id": INSERT_RECORD_ID,
        "description_raw": INSERT_NAME,
        "description_norm": INSERT_NAME,
        "source_file": "corpus.csv", "source_sheet": "-", "source_row": 2,
        "run_id": "abc123", "engine_version": "0.10.0", "schema_version": "1.0.0",
        "ruleset_checksum": "f67131512eb97513",
        "pack_id": "kennametal_widia", "pack_version": "0.10.0",
        "org_id": "zcnc", "org_version": "0.10.0",
        "family_rule_id": "KMT-ROUTE-081", "grammar_id": "G-KMT-ISO-CODE-T",
        "row_confidence": 0.97, "flags": [], "validations": [],
        "text_ambiguous": False, "unresolved_tokens": [],
        "dual_unit_check": "n_a",
        "attributes_ext": {"iso.shape_name": "rhombic 80°"},
        "product_family": "turning_insert",
        "corner_radius_mm": 0.8,
        "edge_length_mm": 12,
        "iso_shape": "C",
        "grade": "TN2000",
        "through_coolant": False,
        "field_meta": {
            "corner_radius_mm": {"confidence": 0.97, "provenance": "GRAMMAR_EXACT",
                                 "raw": "08", "span": [9, 11]},
            "edge_length_mm": {"confidence": 0.97, "provenance": "GRAMMAR_EXACT",
                               "raw": "12", "span": [5, 7]},
            "iso_shape": {"confidence": 0.97, "provenance": "GRAMMAR_EXACT",
                          "raw": "C", "span": [0, 1]},
            "grade": {"confidence": 0.99, "provenance": "EXPLICIT_COLUMN",
                      "raw": "TN2000", "span": None},
            "through_coolant": {"confidence": 0.9, "provenance": "DERIVED",
                                "raw": None, "span": None},
        },
    }
    row.update(overrides)
    return row


def _install(monkeypatch, catalogue: Optional[_Catalogue]) -> None:
    """Swap the module singleton ``decorate`` imports inside its call."""
    import app.pie_service as ps

    monkeypatch.setattr(ps, "pie_service", catalogue or _Catalogue(available=False))


def _rows(session, org: str = ORG) -> List[models.ProductAttributeValue]:
    return list(session.scalars(
        select(models.ProductAttributeValue)
        .where(models.ProductAttributeValue.organization_id == org)
        .order_by(models.ProductAttributeValue.source_kind,
                  models.ProductAttributeValue.attribute_key,
                  models.ProductAttributeValue.created_at)))


def _snapshot(session, org: str = ORG) -> List[tuple]:
    """Every column of every row, so "unchanged" means unchanged.

    Including ``attribute_value_id`` and both timestamps: a writer that deleted
    and re-inserted an identical value would pass a comparison of the visible
    fields and fail this, which is the difference the test is about.
    """
    return [(r.attribute_value_id, r.organization_id, r.product_id, r.attribute_key,
             r.value_num, r.value_text, r.original_value, r.unit, r.source_kind,
             r.source_ref, r.confidence, r.decoder_version, r.created_at,
             r.superseded_at)
            for r in _rows(session, org)]


# ── extraction, without a database ──────────────────────────────────────────
def test_a_number_reaches_value_num_and_a_bool_does_not():
    """``isinstance(True, int)`` is true, so the bool case is the real one."""
    extraction = claims_from_values(
        {"corner_radius_mm": 0.8, "flute_count": 4, "through_coolant": True,
         "iso_shape": "C"})
    by_key = {c.attribute_key: c for c in extraction.claims}

    assert by_key["corner_radius_mm"].value_num == pytest.approx(0.8)
    assert by_key["corner_radius_mm"].value_text == "0.8"
    assert by_key["corner_radius_mm"].unit == "mm"
    assert by_key["flute_count"].value_num == pytest.approx(4.0)
    assert by_key["flute_count"].unit is None

    # A flag that landed in value_num would be matched by a range filter.
    assert by_key["through_coolant"].value_num is None
    assert by_key["through_coolant"].value_text == "true"
    assert by_key["iso_shape"].value_num is None
    assert by_key["iso_shape"].value_text == "C"


def test_nothing_becomes_a_row_for_a_null_a_blank_or_a_structure():
    extraction = claims_from_values(
        {"coating": None, "grade": "   ", "flags": ["A"], "iso_shape": "C"})

    assert [c.attribute_key for c in extraction.claims] == ["iso_shape"]
    # Refused rather than dropped: a coverage number must not be able to fall
    # because something quietly declined to store a field.
    assert dict(extraction.refused) == {
        "coating": "no value", "grade": "blank", "flags": "not a scalar (list)"}


def test_a_catalogue_record_yields_its_facts_and_none_of_its_metadata():
    extraction = claims_from_catalogue_record(_record())
    keys = {c.attribute_key for c in extraction.claims}

    assert {"corner_radius_mm", "edge_length_mm", "iso_shape", "grade",
            "through_coolant"} <= keys
    # The engine emits all of these beside the facts. A store that kept them
    # would hold an attribute named after a checksum.
    assert keys.isdisjoint({"run_id", "source_file", "row_confidence", "field_meta",
                            "ruleset_checksum", "pack_id", "description_raw",
                            "attributes_ext", "flags", "product_family",
                            "dual_unit_check"})

    radius = next(c for c in extraction.claims if c.attribute_key == "corner_radius_mm")
    # `field_meta` is the half the decode path never sees: what the description
    # actually said, and how well it was read.
    assert radius.original_value == "08"
    assert radius.confidence == pytest.approx(0.97)


# ── the two sources ─────────────────────────────────────────────────────────
@pytest.mark.requires_pie
def test_a_decoded_name_and_a_catalogue_link_are_stored_as_two_claims(session, monkeypatch):
    """The same field, from both sources, on one product — and both survive.

    If the writer collapsed them the row count would be right and the *claim*
    would be gone, which is the thing the partial unique index is keyed on
    ``source_kind`` to prevent.
    """
    product = _product(session, ORG, INSERT_NAME, pie_record_id=INSERT_RECORD_ID)
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))

    report = decorate_products(session, ORG)

    assert report.decoders_available, report.decoded_name_unavailable
    assert report.names_decoded == 1
    assert report.catalogue_records_read == 1

    kinds = {r.source_kind for r in _rows(session)}
    assert kinds == {DECODED_NAME, CATALOGUE_LINK}

    radius = {r.source_kind: r for r in _rows(session)
              if r.attribute_key == "corner_radius_mm"}
    assert set(radius) == {DECODED_NAME, CATALOGUE_LINK}
    assert radius[DECODED_NAME].value_num == pytest.approx(0.8)
    assert radius[CATALOGUE_LINK].value_num == pytest.approx(0.8)

    # Provenance differs even where the value agrees, which is the point of
    # keeping both: one says which text it was read out of, the other names the
    # catalogue record and carries the maker's own per-field confidence.
    assert radius[DECODED_NAME].source_ref == INSERT_NAME
    assert radius[DECODED_NAME].confidence is None
    assert radius[CATALOGUE_LINK].source_ref == INSERT_RECORD_ID
    assert radius[CATALOGUE_LINK].confidence == pytest.approx(0.97)
    assert radius[CATALOGUE_LINK].decoder_version == "f67131512eb97513"


def test_a_name_that_decodes_to_nothing_records_nothing(session, monkeypatch):
    """§1: absence of evidence is not a pass — not a zero, not a blank row."""
    _product(session, ORG, "MISC BRACKET ASSEMBLY 4 OFF")
    _install(monkeypatch, None)

    report = decorate_products(session, ORG)

    assert report.names_decoded == 0
    assert _rows(session) == []
    assert report.written.wrote_nothing


@pytest.mark.requires_pie
def test_the_route_is_not_stored_as_an_attribute(session, monkeypatch):
    """The route lands on every routed row, so storing it would fill the table.

    Measured with the real pack: "MISC BRACKET ASSEMBLY 4 OFF", "OFFICE CHAIR"
    and "SOLID CARBIDE DRILL 6.0MM 3XD" all decode to ``product_subfamily``
    ``general`` and nothing else. Kept, that is one attribute on essentially
    every product — decision 002's exit criterion complete on the first run,
    with a bracket counted as a decorated product — and it is a classification
    the engine declines to vouch for rather than a fact anything may gate on.
    """
    _product(session, ORG, "SOLID CARBIDE DRILL 6.0MM 3XD")
    _install(monkeypatch, None)

    decorate_products(session, ORG)

    assert _rows(session) == []
    # And on a name that decodes properly, the route is the only thing missing.
    _product(session, ORG, INSERT_NAME, external_id="i2")
    decorate_products(session, ORG)
    keys = {r.attribute_key for r in _rows(session)}
    assert "corner_radius_mm" in keys
    assert keys.isdisjoint({"product_family", "product_subfamily"})


@pytest.mark.requires_pie
def test_a_product_with_no_catalogue_link_gets_no_catalogue_rows(session, monkeypatch):
    _product(session, ORG, INSERT_NAME)          # no pie_record_id
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))

    decorate_products(session, ORG)

    assert {r.source_kind for r in _rows(session)} == {DECODED_NAME}


# ── supersede, and the absence of churn ─────────────────────────────────────
def test_rerunning_over_identical_input_leaves_the_table_byte_identical(
        session, monkeypatch):
    """The property the whole writer exists for.

    Compared over every column, ids and timestamps included. A writer that
    superseded and re-inserted the same value would keep the same live *values*
    and fail here — and would double the table on every run, which nothing else
    in the system would notice.
    """
    _product(session, ORG, INSERT_NAME, pie_record_id=INSERT_RECORD_ID)
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))

    first = decorate_products(session, ORG)
    before = _snapshot(session)
    assert before, "nothing was written, so this proves nothing about churn"

    second = decorate_products(session, ORG)

    assert _snapshot(session) == before
    assert second.written.created == 0
    assert second.written.superseded == 0
    assert second.written.retracted == 0
    assert second.written.unchanged == first.written.created


def test_a_changed_value_supersedes_the_old_row_and_never_edits_it(session):
    """Written through ``write_claims`` directly, because what is under test is
    the writer's three outcomes rather than any decoder's opinion."""
    product = _product(session, ORG, INSERT_NAME)

    write_claims(session, ORG, product.product_id, DECODED_NAME,
                 [AttributeClaim("corner_radius_mm", "0.8", 0.8, unit="mm")],
                 decoder_version="ck_1")
    original = _rows(session)[0]
    original_id, original_created = original.attribute_value_id, original.created_at

    result = write_claims(session, ORG, product.product_id, DECODED_NAME,
                          [AttributeClaim("corner_radius_mm", "0.4", 0.4, unit="mm")],
                          decoder_version="ck_2")

    assert (result.created, result.superseded, result.unchanged) == (1, 1, 0)
    rows = _rows(session)
    assert len(rows) == 2

    old = next(r for r in rows if r.attribute_value_id == original_id)
    new = next(r for r in rows if r.attribute_value_id != original_id)
    # The old row keeps its value and its birthday. Only `superseded_at` moved,
    # which is what "superseded, never mutated" has to mean to be worth having.
    assert old.value_num == pytest.approx(0.8)
    assert old.created_at == original_created
    assert old.superseded_at is not None
    assert new.value_num == pytest.approx(0.4)
    assert new.superseded_at is None
    assert new.decoder_version == "ck_2"

    live = live_values(session, ORG, product.product_id)
    assert [(r.attribute_key, r.value_num) for r in live] == [("corner_radius_mm", 0.4)]


def test_a_field_the_source_stops_naming_is_retracted_not_left_live(session):
    """A complete source that no longer says something has said something.

    Without this the only code that ever revisits a row is the code that writes
    a replacement, so a corrected pack would leave the wrong value live forever.
    """
    product = _product(session, ORG, INSERT_NAME)
    write_claims(session, ORG, product.product_id, DECODED_NAME,
                 [AttributeClaim("corner_radius_mm", "0.8", 0.8, unit="mm"),
                  AttributeClaim("iso_shape", "C")])

    result = write_claims(session, ORG, product.product_id, DECODED_NAME,
                          [AttributeClaim("iso_shape", "C")])

    assert result.retracted == 1
    assert [r.attribute_key for r in live_values(session, ORG, product.product_id)] \
        == ["iso_shape"]
    # Retracted, not deleted: the row that stopped being true stays readable.
    assert len(_rows(session)) == 2


def test_a_source_that_could_not_be_asked_retracts_nothing(session, monkeypatch):
    """"The pack covered nothing here" and "nobody asked the pack" are different
    facts, and only the first is evidence — ``_link_catalog``'s own rule.

    A deployment built without the catalogue must not silently empty the table.
    """
    product = _product(session, ORG, INSERT_NAME, pie_record_id=INSERT_RECORD_ID)
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))
    decorate_products(session, ORG)
    before = _snapshot(session)
    assert any(r.source_kind == CATALOGUE_LINK for r in _rows(session))

    _install(monkeypatch, _Catalogue(available=False))
    report = decorate_products(session, ORG)

    assert report.catalogue_unavailable
    assert _snapshot(session) == before
    assert live_values(session, ORG, product.product_id, CATALOGUE_LINK)


@pytest.mark.requires_pie
def test_a_link_the_catalogue_no_longer_holds_is_retracted(session, monkeypatch):
    """The catalogue *is* loaded and does not have this record — which is
    evidence, unlike the case above."""
    product = _product(session, ORG, INSERT_NAME, pie_record_id=INSERT_RECORD_ID)
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))
    decorate_products(session, ORG)

    _install(monkeypatch, _Catalogue({}))       # loaded, and missing that record
    report = decorate_products(session, ORG)

    assert report.catalogue_links_unresolved == 1
    assert report.written.retracted > 0
    assert live_values(session, ORG, product.product_id, CATALOGUE_LINK) == []
    assert live_values(session, ORG, product.product_id, DECODED_NAME)


# ── tenancy ─────────────────────────────────────────────────────────────────
def test_one_organizations_attributes_are_invisible_to_another(session, monkeypatch):
    """On SQLite these filters are the entire tenant boundary (Phase 0 §5)."""
    mine = _product(session, ORG, INSERT_NAME, pie_record_id=INSERT_RECORD_ID)
    theirs = _product(session, OTHER, INSERT_NAME, external_id="i2",
                      pie_record_id=INSERT_RECORD_ID)
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))

    decorate_products(session, ORG)

    assert _rows(session, ORG)
    assert _rows(session, OTHER) == []
    assert {r.organization_id for r in _rows(session, ORG)} == {ORG}
    assert live_values(session, OTHER, mine.product_id) == []
    assert attribute_coverage(session, OTHER).live_values == 0

    # And a decode of the other tenant's identical product is its own set of
    # rows, not a share of these — two orgs may hold different values for one
    # product, which is decision 026's whole reason for scoping the table.
    decorate_products(session, OTHER)
    assert {r.product_id for r in _rows(session, OTHER)} == {theirs.product_id}


def test_a_write_never_reads_another_organizations_live_row(session):
    """Same product id in two organizations is not a shared row.

    Impossible through ``decorate_products``, which allocates ids — so it is
    driven through the writer, where a missing ``organization_id`` filter would
    have one tenant supersede the other's history.
    """
    _org(session, ORG)
    _org(session, OTHER)
    shared_id = "p_shared"
    for org in (ORG, OTHER):
        session.add(models.ProductAttributeValue(
            organization_id=org, product_id=shared_id, attribute_key="iso_shape",
            value_text="C", source_kind=DECODED_NAME))
    session.flush()

    write_claims(session, ORG, shared_id, DECODED_NAME,
                 [AttributeClaim("iso_shape", "D")])

    assert [r.value_text for r in live_values(session, OTHER, shared_id)] == ["C"]
    assert [r.value_text for r in live_values(session, ORG, shared_id)] == ["D"]


# ── the exit criterion ──────────────────────────────────────────────────────
@pytest.mark.requires_pie
def test_coverage_counts_what_is_and_refuses_a_denominator_it_does_not_have(
        session, monkeypatch):
    decorated = _product(session, ORG, INSERT_NAME, pie_record_id=INSERT_RECORD_ID)
    _product(session, ORG, "MISC BRACKET ASSEMBLY 4 OFF", external_id="i2")
    _install(monkeypatch, _Catalogue({INSERT_RECORD_ID: _record()}))
    decorate_products(session, ORG)

    report = attribute_coverage(session, ORG)

    assert report.products_total == 2
    assert report.products_with_any_attribute == 1
    assert report.coverage_rate == pytest.approx(0.5)
    assert report.live_values == sum(k.values for k in report.by_key)
    assert report.attributes_per_decorated_product == pytest.approx(report.live_values)

    by_key = {k.attribute_key: k for k in report.by_key}
    # One product, claimed by both sources: one product, two live values.
    assert by_key["corner_radius_mm"].products == 1
    assert by_key["corner_radius_mm"].values == 2
    assert by_key["corner_radius_mm"].fill_rate == pytest.approx(0.5)
    assert {kind for kind, _ in report.by_source_kind} == {DECODED_NAME, CATALOGUE_LINK}
    assert dict(report.by_source_kind)[CATALOGUE_LINK] == len(
        live_values(session, ORG, decorated.product_id, CATALOGUE_LINK))


def test_coverage_of_an_organization_with_no_products_is_unknown_not_zero(session):
    """§1: a ratio with no denominator is not 0%, which would read as a
    measured failure of a catalogue that does not exist."""
    _org(session, ORG)

    report = attribute_coverage(session, ORG)

    assert report.products_total == 0
    assert report.coverage_rate is None
    assert report.attributes_per_decorated_product is None
    assert report.by_key == ()
