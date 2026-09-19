"""Proof that the suite next door can fail.

Every check in ``harness.CHECKS`` is run twice here: once against a connector
doing everything right, where it must report nothing, and once against the same
connector with exactly one thing wrong, where it must report that thing and
name it. Without the first half the second proves only that something is red;
without the second the whole package is a screen nobody has seen catch
anything.

The headline is ``test_a_connector_that_claims_a_source_time_and_omits_it``.
That omission is the incident ``spec.py`` was written for: three document
projections lost ``created_time``, every row landed with a NULL
``source_recorded_at``, every quote line answered INSUFFICIENT_EVIDENCE, and
the sync reported success.
"""
from __future__ import annotations

import pytest

import dbsupport
from app.db import Base
from app.domain import models
from app.ingestion.sync import SyncService

from . import broken
from .conftest import make_org, open_session
from .harness import CHECKS, SOURCE_TIME_PATH, capturing


def _pull(source: broken.Source, organization_id: str = "org_broken") -> list:
    """The broken source through the real sync, exactly as a real one goes."""
    engine = dbsupport.fresh_engine()
    session = open_session(engine)
    emissions: list = []
    try:
        make_org(session, organization_id)
        with capturing(broken.KEY, emissions):
            SyncService(session, source, organization_id,
                        connector=broken.KEY, connection_id="conn-broken").run()
        return emissions
    finally:
        session.rollback()
        session.close()
        if not dbsupport.TEST_SERVER_URL:
            Base.metadata.drop_all(engine)


@pytest.fixture(scope="module")
def clean() -> list:
    return _pull(broken.Source())


# ── the control ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("check", sorted(CHECKS))
def test_a_connector_doing_it_right_passes_every_check(clean, check):
    """Without this, every assertion below proves only that the checks are red.

    A check that cannot pass is a check that will be deleted the first time it
    blocks somebody, and one that cannot fail is the thing this file exists to
    disprove. Both halves, or neither is worth having.
    """
    assert clean, "the clean fixture emitted nothing"
    assert not CHECKS[check](clean, broken.declaration()), check


# ── contract 2: the source time ─────────────────────────────────────────────
def test_a_connector_that_claims_a_source_time_and_omits_it():
    """The headline. ``records_source_time = True`` and no value carried.

    The failure must name the entity and the field, because "conformance
    failed" sends somebody to read six connectors and this sends them to one
    line of one translator.
    """
    emissions = _pull(broken.Source(drop_source_time=True))
    failures = CHECKS["expected-fields-carried"](emissions, broken.declaration())
    assert failures
    # Both grains the evidence builder reads. ``quote_doc`` is the third and no
    # registered connector produces one, which ``test_conformance`` says out
    # loud rather than leaving as an unexplained gap.
    for entity in ("sales_txn", "cost_record"):
        assert any(f"/{entity}:" in f and SOURCE_TIME_PATH in f for f in failures), (
            f"{entity} not named:\n  " + "\n  ".join(failures))


def test_a_connector_that_declares_nothing_at_all_fails():
    """``False`` is a claim a person can check. Silence is not an answer, and
    it must not read like one."""
    emissions = _pull(broken.Source(drop_source_time=True))
    failures = CHECKS["expected-fields-carried"](emissions, broken.UNDECLARED_SOURCE_TIME)
    assert failures
    assert any("declares neither" in f for f in failures), failures


def test_a_connector_whose_erp_has_no_source_time_is_not_punished_for_it():
    """The other side of the same declaration, and the reason it is a
    declaration rather than a flag somebody sets to silence a failure.

    ``False`` says the ERP exposes nothing, which is a statement about a system
    somebody can go and check against ``source_time_note``. It stops this
    check, and it stops nothing else.
    """
    emissions = _pull(broken.Source(drop_source_time=True))
    said_so = broken.declaration(records_source_time=False)
    failures = CHECKS["expected-fields-carried"](emissions, said_so)
    assert not any(SOURCE_TIME_PATH in f for f in failures), failures


def test_a_bill_with_no_vendor_fails_the_contract_s_other_expectation():
    """``CostRecordIn.vendor_external_id`` is the contract's second EXPECTED
    field, and it has no per-connector escape: every bill has a vendor."""
    emissions = _pull(broken.Source(drop_vendor=True))
    failures = CHECKS["expected-fields-carried"](emissions, broken.declaration())
    assert any("/cost_record:" in f and "vendor_external_id" in f for f in failures), failures


# ── contract 1: the schema's own half ───────────────────────────────────────
def test_a_record_missing_a_required_field_fails():
    failures = CHECKS["required-fields-present"](
        broken.a_record_missing_a_required_field(), broken.declaration())
    assert any("REQUIRED date" in f for f in failures), failures


# ── contract 3: money ───────────────────────────────────────────────────────
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_a_money_field_holding_a_float_fails():
    failures = CHECKS["money-is-decimal"](
        broken.a_record_whose_money_is_a_float(), broken.declaration())
    assert any("unit_price is float" in f for f in failures), failures


def test_money_the_translator_computed_in_float_fails():
    emissions = _pull(broken.Source(money_as_float=True))
    failures = CHECKS["money-not-computed-in-float"](emissions, broken.declaration())
    assert any("item_total" in f and "float" in f for f in failures), failures


def test_a_line_totalled_at_the_list_rate_fails():
    """The defect found on the quote side and again on the bill side: the unit
    price is net of the discount and the line total is not."""
    failures = CHECKS["line-total-net-of-discount"](
        broken.a_line_totalled_at_the_list_rate(), broken.declaration())
    assert any("line_revenue" in f and "list rate" in f for f in failures), failures


def test_a_fixture_with_no_discounted_line_fails_rather_than_passes():
    """The vacuity guard. A rule screened against nothing reports clean, which
    is how a check stops being a check."""
    emissions = _pull(broken.Source(no_discount=True))
    failures = CHECKS["line-total-net-of-discount"](emissions, broken.declaration())
    assert any("screened against nothing" in f for f in failures), failures


# ── contract 4: identity and provenance ─────────────────────────────────────
def test_a_reference_built_from_something_absent_fails():
    failures = CHECKS["identity-and-provenance"](
        broken.a_record_with_no_usable_identity(), broken.declaration())
    assert any("built from something absent" in f for f in failures), failures
    assert any("source_ref.record_id" in f for f in failures), failures


def test_a_record_claiming_another_system_fails():
    """The promise ``normalize``'s ``system`` argument exists to keep: a pull
    under one connector must leave no row claiming another."""
    failures = CHECKS["identity-and-provenance"](
        broken.a_record_claiming_another_system(), broken.declaration())
    assert any("but this pull read" in f for f in failures), failures


# ── contract 5: idempotency ─────────────────────────────────────────────────
def test_a_connector_whose_ids_move_between_pulls_doubles_the_read_model():
    """What the idempotency check is looking at, shown failing.

    A source that mints a fresh document id per listing passes every
    single-record check in this package — the ids are non-empty, stable within
    one pull, and perfectly well formed. It is only the second sync that shows
    it, and only by counting.
    """
    engine = dbsupport.fresh_engine()
    session = open_session(engine)
    try:
        organization_id = make_org(session, "org_broken_twice")
        source = broken.Source(unstable_ids=True)
        for _ in range(2):
            SyncService(session, source, organization_id, connector=broken.KEY,
                        connection_id="conn-broken").run()
            session.flush()
        assert session.query(models.SalesTxn).filter(
            models.SalesTxn.organization_id == organization_id).count() == 2
    finally:
        session.rollback()
        session.close()
        if not dbsupport.TEST_SERVER_URL:
            Base.metadata.drop_all(engine)
