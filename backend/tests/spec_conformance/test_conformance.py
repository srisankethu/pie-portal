"""Six registered connectors, held against the published ingestion contract.

Every assertion below reads ``spec.entity_field_contracts()``. Nothing here
names a field except ``harness.SOURCE_TIME_PATH``, which is the join between
the contract and the per-connector declaration and is itself asserted to exist.

The pulls are real: ``SyncService`` over each connector's own source class over
that ERP's own records. What is replaced is the HTTP call and nothing else.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

import dbsupport
from app.db import Base
from app.domain import models, spec
from app.ingestion import erp

from . import broken
from .conftest import ORG, make_org, open_session, pull
from .connectors import UNDER_TEST
from .harness import (CHECKS, EVIDENCE_ENTITIES, SOURCE_TIME_PATH, UNDECLARED,
                      _decimal_paths, declaration_of, values_at)

#: The connectors this suite runs over, taken from the registry rather than
#: from the fixture list beside it. ``sage.py`` registers two specs from one
#: module, so a hand-written list is a list that can be one short and report
#: clean about the one it left out.
KEYS = sorted(s.key for s in erp.catalog())
_FIXTURES = {u.key: u for u in UNDER_TEST}


def _fixture(key: str):
    under_test = _FIXTURES.get(key)
    assert under_test is not None, (
        f"{key} is registered and has no fixture, so every check below would "
        f"report clean about a connector it never ran")
    return under_test


# ── the screens are not empty ───────────────────────────────────────────────
def test_every_registered_connector_has_a_fixture():
    """A connector with no rows behind it would pass every check below.

    That is the empty-screen failure this package exists to prevent, and the
    registry is where it would arrive: connector number seven registers itself
    and inherits a clean conformance report it was never run against.
    """
    assert set(_FIXTURES) == set(KEYS)


def test_the_contract_still_says_what_this_suite_screens_for():
    """``SOURCE_TIME_PATH`` is really an EXPECTED contract, on every entity.

    The one field name this suite holds. If the contract renamed it, or
    downgraded it to OPTIONAL, every ``source_recorded_at`` assertion below
    would quietly screen for nothing and report clean — which is exactly how
    the omission behind ``spec.py`` went unnoticed in the first place.
    """
    contracts = spec.entity_field_contracts()
    assert contracts, "the contract is empty"
    for entity, fields in contracts.items():
        expected = {f.path: f for f in fields if f.status == spec.EXPECTED}
        assert SOURCE_TIME_PATH in expected, f"{entity} no longer expects {SOURCE_TIME_PATH}"
        assert expected[SOURCE_TIME_PATH].reason.strip(), entity


def test_the_broken_fixture_connector_is_registered_nowhere():
    """It must not reach ``catalog()``, which is what the connect screen, the
    router's validation and every other generic caller read."""
    assert broken.KEY not in {s.key for s in erp.catalog()}
    with pytest.raises(erp.UnknownConnectorError):
        erp.get_spec(broken.KEY)


@pytest.mark.parametrize("key", KEYS)
def test_a_pull_emitted_something_to_check(pulled, key):
    """A connector whose fixture produced no records would pass everything."""
    assert pulled[key].emissions, f"{key} emitted no canonical records"


@pytest.mark.parametrize("key", KEYS)
def test_the_money_checks_had_money_to_look_at(pulled, key):
    """``money-is-decimal`` walks the Decimal fields of whatever was emitted,
    so a connector whose records happened to populate none of them would pass
    it without a value having been examined.

    The same empty-screen failure as a fixture with no discounted line, one
    layer down, and it needs the same guard.
    """
    examined = [(e.entity, path) for e in pulled[key].emissions
                for path in _decimal_paths(type(e.record))
                for _, found in values_at(e.values(), path) if found is not None]
    assert examined, f"{key}: no Decimal field anywhere carried a value"


@pytest.mark.parametrize("key", KEYS)
def test_every_connector_completes_a_whole_sync(pulled, key):
    """``SyncService.run()`` over the real source, start to finish.

    Separate from the contract checks because it is a different kind of
    failure: a connector that cannot finish a pull has no conformance to
    report, and the checks below would be reading a truncated sample while
    saying nothing about why it was short.
    """
    error = pulled[key].error
    assert error is None, f"{key}: the sync raised {type(error).__name__}: {error}"


# ── the declaration ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", KEYS)
def test_every_connector_declares_whether_its_erp_records_a_source_time(key):
    """There is no third state.

    ``True`` obliges the connector to carry the value. ``False`` is a claim
    about the ERP that a person can check, and the note is where they check it.
    Absence is not an answer: an omission nothing checks is indistinguishable
    from a source that has no such concept, which is the whole incident.
    """
    stated = declaration_of(erp.get_spec(key))
    assert stated.records_source_time is not UNDECLARED, (
        f"{key} declares neither records_source_time nor source_time_note")
    assert isinstance(stated.records_source_time, bool), key
    assert stated.source_time_note.strip(), (
        f"{key}: source_time_note is empty, so a reader cannot tell whether a "
        f"missing source time is this ERP's gap or ours")


def test_the_evidence_entities_are_the_rows_that_promote_a_source_time():
    """``harness.EVIDENCE_ENTITIES`` is derived from the read model; this is
    where the derivation is held.

    Three tables promote ``source_recorded_at`` out of ``source_ref`` into a
    column of their own, and ``commercial/quote_diagnosis`` filters evidence on
    exactly that column. A fourth gaining it is the moment somebody has to
    decide whether the source time is asserted there too — so it fails here
    rather than widening the screen by itself, or leaving it narrow by itself.
    """
    promoted = {model.__name__ for model in Base.registry.mappers
                for model in (model.class_,)
                if hasattr(model, "source_recorded_at")}
    assert promoted == {"SalesTxn", "CostRecord", "QuoteDoc"}, sorted(promoted)
    assert EVIDENCE_ENTITIES == {"sales_txn", "cost_record", "quote_doc"}


# ── the contract, connector by connector ────────────────────────────────────
#: Connectors whose canonical shape cannot state a discount at all, with the
#: reason. ``line_total_is_the_list_rate`` refuses to report clean over a
#: sample with no discounted line in it, which is the right default; this is
#: where an ERP that genuinely cannot produce one is excused **by name**.
#:
#: Self-policing: the test below asserts an excused connector really does emit
#: that one message and nothing else, so an entry that stops being true fails
#: here rather than quietly excusing a real defect.
_NO_DISCOUNT_IN_THE_CANONICAL_SHAPE = {
    "sagex3": ("x3_translate_document reads `rate` from NETPRI first — X3's "
               "*net* unit price — so the canonical rate equals the effective "
               "amount and the list price (GROPRI) is never carried. Nothing "
               "downstream is wrong by it; the audit pair rate/discount_percent "
               "is simply empty for this ERP. See the findings."),
}


#: Connectors whose ERP *does* expose a record time, which they *do* carry, and
#: which normalisation then drops because the stamp has no UTC offset.
#:
#: This is not the excuse above. That one is permanent — X3's canonical shape
#: cannot express a list rate. These three are an **open defect awaiting one
#: decision that is not a developer's to make**: which zone does each book
#: state? clock.utc_stamp refuses a naive stamp and normalize._recorded_at
#: drops what it refuses, so the rows land with source_recorded_at NULL and
#: every quote line needing them answers INSUFFICIENT_EVIDENCE — the outcome of
#: the incident this whole package exists to prevent, reached by a different
#: road. No offset is invented anywhere: a fixed one for NetSuite would be a
#: DST bug, and guessing is what the engine refuses on the reader's behalf.
#:
#: Listed rather than deleted, and asserted rather than skipped, because a
#: check that is always red is a check nobody reads — and a check quietly
#: removed is one nobody remembers. The assertion below excuses *only* this
#: failure: any other failure on these connectors still fails, and the day a
#: zone is decided and the stamp survives, it fails too and the entry comes
#: out. It cannot rot in either direction.
_SOURCE_TIME_HAS_NO_ZONE = {
    "netsuite": ("createddate is stated in PST whatever the account's zone, and "
                 "the SuiteQL projection formats it without an offset"),
    "sagex3": ("CREDAT carries the date; the time is in CRETIM, and neither "
               "column carries a zone"),
    "sage100": ("DateCreated carries the date; the time is in TimeCreated, and "
                "neither column carries a zone"),
}

#: The clause in a failure that distinguishes "normalisation dropped it" from
#: "the connector never read it". Only the first is excused above.
_LOST_IN_NORMALISATION = "it was dropped in normalisation"


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("check", sorted(CHECKS))
def test_the_contract_holds(pulled, key, check):
    pulled_ = pulled[key]
    # A truncated pull is named here too. Without it a check reading half a
    # sample reports clean and the reader has to notice, three tests away, that
    # the other half never arrived.
    truncated = ("" if pulled_.error is None else
                 f"\n  (and the sync did not finish: "
                 f"{type(pulled_.error).__name__}: {pulled_.error})")
    failures = CHECKS[check](pulled_.emissions, declaration_of(erp.get_spec(key)))
    zone_gap = _SOURCE_TIME_HAS_NO_ZONE.get(key)
    if check == "expected-fields-carried" and zone_gap:
        assert failures and all(_LOST_IN_NORMALISATION in f for f in failures), (
            f"{key} is a KNOWN OPEN GAP, not a passing connector: {zone_gap}.\n"
            f"  Every failure here must be that stamp losing its zone in "
            f"normalisation. This entry excuses nothing else — and when a zone "
            f"is decided and the stamp survives, this assertion is what fails, "
            f"so the entry comes out with the fix.\n"
            f"  It reported:\n  " + "\n  ".join(failures) + truncated)
        return
    excuse = _NO_DISCOUNT_IN_THE_CANONICAL_SHAPE.get(key)
    if check == "line-total-net-of-discount" and excuse:
        assert failures == [f"{key}: no emitted line carries a discount, so the "
                            f"net-of-discount rule was screened against nothing"], (
            f"{key} is excused from the discount screen because: {excuse}\n"
            f"  but it reported:\n  " + "\n  ".join(failures))
        return
    assert not failures, (f"{key} — {check}:\n  " + "\n  ".join(failures) + truncated)


# ── identity, across two independent pulls ──────────────────────────────────
@pytest.mark.parametrize("key", KEYS)
def test_external_ids_are_stable_across_pulls(pulled, key):
    """Contract 4's other half: an external id is a property of the source
    record, not of the moment it was read.

    An id that moves between pulls passes every single-run check and still
    doubles the read model on the second sync — the failure the idempotency
    test below sees from the other side.
    """
    under_test = _fixture(key)
    engine = dbsupport.fresh_engine()
    session = open_session(engine)
    try:
        organization_id = make_org(session, f"{ORG}_restated")
        again = pull(session, under_test, organization_id, f"conn-{key}").emissions
    finally:
        session.rollback()
        session.close()
        if not dbsupport.TEST_SERVER_URL:
            Base.metadata.drop_all(engine)

    def keys(emissions):
        out = []
        for e in emissions:
            values = e.values()
            reference = values.get("external_ref") or values.get("external_id")
            out.append((e.entity, reference, (values.get("source_ref") or {}).get("record_id")))
        return sorted(out, key=lambda k: tuple(str(part) for part in k))

    assert keys(pulled[key].emissions) == keys(again)


# ── idempotency ─────────────────────────────────────────────────────────────
#: The read-model tables a pull writes. Counted rather than every table with an
#: ``organization_id``, because the event log and the sync report are
#: append-only by design — a second pull *should* add rows there, and folding
#: them in would make this check either always red or meaninglessly broad.
_ROW_MODELS = (models.Customer, models.Product, models.Vendor,
               models.SalesTxn, models.CostRecord)


def _counts(session, organization_id) -> dict[str, int]:
    return {model.__name__: session.scalar(
        select(func.count()).select_from(model).where(
            model.organization_id == organization_id)) or 0
        for model in _ROW_MODELS}


@pytest.mark.parametrize("key", KEYS)
def test_the_same_payload_synced_twice_is_one_row(key):
    """Contract 5. Two identical pulls, and the second writes no new rows.

    Through the real repository's upsert, because that is where the identity a
    re-sync keys on is decided — ``(connector, connection, external id)``, and
    the graded adoption of rows that predate provenance.
    """
    under_test = _fixture(key)
    engine = dbsupport.fresh_engine()
    session = open_session(engine)
    try:
        organization_id = make_org(session, f"{ORG}_twice")
        first_pull = pull(session, under_test, organization_id, f"conn-{key}")
        assert first_pull.error is None, (
            f"{key}: the sync raised {type(first_pull.error).__name__}: "
            f"{first_pull.error}, so idempotency was never reached")
        session.flush()
        first = _counts(session, organization_id)
        assert any(first.values()), f"{key} wrote nothing, so nothing was checked"

        pull(session, under_test, organization_id, f"conn-{key}")
        session.flush()
        assert _counts(session, organization_id) == first
    finally:
        session.rollback()
        session.close()
        if not dbsupport.TEST_SERVER_URL:
            Base.metadata.drop_all(engine)


# ── what these fixtures could not reach, said out loud ──────────────────────
def test_the_stages_no_connector_reads_are_named_not_assumed():
    """``quotes``, ``vendor_payments`` and ``users`` are declared read stages
    that no registered connector implements.

    Asserted rather than left as a gap in the coverage table: a stage arriving
    on a source without a fixture behind it would otherwise be a silent hole in
    this suite, and the entities behind these three — ``quote_doc``,
    ``vendor_payment`` — are therefore exercised here for no connector at all.
    """
    unread = {stage for stage in erp.READ_STAGES
              if not any(hasattr(_fixture(key).build_source(), f"list_{stage}")
                         for key in KEYS)}
    assert unread == {"quotes", "vendor_payments", "users"}, sorted(unread)


def test_the_entities_no_fixture_reaches_are_named_not_assumed(pulled):
    """Which of the nineteen contract entities this suite actually examines.

    Pinned so the answer is a decision rather than a side effect. Eight are
    reached; the other eleven are records the registry's connectors do not
    produce at all — Zoho-only concepts (locations, credit notes, per-location
    stock) or stages no US connector implements. A conformance report that did
    not say which is which would read as coverage it does not have.
    """
    reached = {e.entity for pull_ in pulled.values() for e in pull_.emissions}
    all_entities = {spec.entity_name(m) for m in spec.SPEC_ENTITIES}
    assert reached == {
        "bill", "cost_record", "customer", "invoice", "payment_receipt",
        "product", "purchase_order", "sales_order", "sales_txn",
        "stock_snapshot", "vendor",
    }, sorted(reached)
    assert all_entities - reached == {
        "credit_note", "credit_note_application", "location", "quote_doc",
        "stock_location_snapshot", "vendor_credit", "vendor_credit_application",
        "vendor_payment",
    }, sorted(all_entities - reached)
