"""The diagnosis as a stored row: reproducible, immutable, and measurable.

Scenarios 33 and 34 of the build specification, plus the structural checks the
scenarios cannot reach — that the outcome table has no write path into a
diagnosis, and that the replay fails loudly rather than quietly returning a
different answer.

These need a database, unlike the engine tests next door, because what is being
tested *is* the persistence.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.clock import aware
from app.commercial.config import CommercialThresholds
from app.commercial.quote_diagnosis import cutover, render, replay, service
from app.domain import models
from app.domain.enums import QuoteOutcomeStatus

ORG = "org_a"
TH = CommercialThresholds()
QUOTE_DAY = date(2026, 6, 1)
KNOWABLE_BY = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _seed(session, *, prices=("1000",) * 12, product="prd_1",
          customer="cst_a", unit="Nos", category="Turning"):
    # The organization row exists because ``zoho_connections`` carries a real
    # foreign key to it — the one place this fixture cannot stay minimal.
    session.add(models.Organization(organization_id=ORG, name="Acme Tools"))
    session.add(models.Product(product_id=product, organization_id=ORG,
                               external_id="i1", name="Insert", uom=unit,
                               source_item_category=category))
    session.add(models.Customer(customer_id=customer, organization_id=ORG,
                                external_id="c1", name="Acme"))
    for i, price in enumerate(prices):
        day = date(2026, 1, 5) + timedelta(days=7 * i)
        session.add(models.SalesTxn(
            sales_txn_id=f"stx_{i}", organization_id=ORG, external_ref=f"inv:{i}",
            customer_id=customer, product_id=product, date=day,
            qty=Decimal("10"), unit_price=Decimal(price),
            line_revenue=Decimal(price) * 10,
            # Authored in the ERP: an invoice's record time is its own day.
            source_recorded_at=datetime(day.year, day.month, day.day, 9,
                                        tzinfo=timezone.utc),
            source_ref={"system": "zoho", "record_type": "invoice"}))
    session.flush()


def _diagnose(session, *, quoted="850", th=None):
    return service.diagnose_line(
        session, ORG, quote_id="q1", line_id="ln_1", customer_id="cst_a",
        product_id="prd_1", qty=Decimal("10"),
        quoted_unit_price=Decimal(quoted), as_of=QUOTE_DAY,
        knowable_by=KNOWABLE_BY, th=th if th is not None else TH,
        backfill_before=service.backfill_cutover(session, ORG))


# ── 33. a replay reproduces the diagnosis ────────────────────────────────────

def test_rediagnose_reproduces_an_old_diagnosis_with_a_matching_hash(session):
    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    done = replay.rediagnose(session, ORG, quote_line_id="ln_1", th=TH)

    assert done.result.evidence_hash == stored.evidence_hash
    assert done.verdict_matches is True
    assert list(done.result.owner.codes) == list(stored.codes)


def test_a_replay_is_unmoved_by_invoices_that_arrive_afterwards(session):
    """The reason a hash is meaningful at all.

    A re-sync brings in a line dated before the quote but keyed in after it. The
    visibility filter excludes it, so the cited set does not move. On an
    event-date engine this row would silently join the band and every stored
    hash would be noise.
    """
    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    session.add(models.SalesTxn(
        sales_txn_id="stx_late", organization_id=ORG, external_ref="inv:late",
        customer_id="cst_a", product_id="prd_1", date=date(2026, 5, 20),
        qty=Decimal("10"), unit_price=Decimal("400"),
        line_revenue=Decimal("4000"),
        source_recorded_at=datetime(2026, 6, 25, 9, tzinfo=timezone.utc),
        source_ref={}))
    session.commit()

    done = replay.rediagnose(session, ORG, quote_line_id="ln_1", th=TH)

    assert done.result.evidence_hash == stored.evidence_hash
    assert "stx_late" not in done.result.evidence_ids


def test_evidence_drift_fails_loudly_and_names_what_moved(session):
    """A replay that quietly returned a different answer would be worse than no
    replay, because the number it returned would look like the original."""
    _seed(session)
    service.record(session, ORG, quote_id="q1", result=_diagnose(session))
    session.commit()

    # Somebody corrects a historical invoice so it now predates the quote *and*
    # was visible in time. That legitimately changes what the engine can see,
    # and every stored diagnosis from before it is unexplainable.
    session.add(models.SalesTxn(
        sales_txn_id="stx_new", organization_id=ORG, external_ref="inv:new",
        customer_id="cst_a", product_id="prd_1", date=date(2026, 5, 20),
        qty=Decimal("10"), unit_price=Decimal("1000"),
        line_revenue=Decimal("10000"),
        source_recorded_at=datetime(2026, 5, 21, 9, tzinfo=timezone.utc),
        source_ref={}))
    session.commit()

    with pytest.raises(replay.EvidenceDrift) as caught:
        replay.rediagnose(session, ORG, quote_line_id="ln_1", th=TH)

    assert "stx_new" in caught.value.added
    assert caught.value.removed == []


def test_replaying_at_a_different_date_is_refused_rather_than_answered(session):
    """'Rediagnose as of today' is a reasonable thing to want and a catastrophic
    thing to get by accident from a function that promised a reproduction."""
    _seed(session)
    service.record(session, ORG, quote_id="q1", result=_diagnose(session))
    session.commit()

    with pytest.raises(ValueError, match="not a reproduction"):
        replay.rediagnose(session, ORG, quote_line_id="ln_1", th=TH,
                          as_of=date(2026, 9, 1))


# ── 34. the outcome layer cannot touch the diagnosis ─────────────────────────

def test_writing_a_quote_outcome_leaves_the_diagnosis_byte_identical(session):
    from app.commercial import quote_service

    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()
    before = _snapshot(session, stored.quote_diagnosis_id)

    quote_service.set_outcome(session, ORG, quote_id="q1",
                              status=QuoteOutcomeStatus.SENT,
                              customer_ref="Acme")
    session.commit()
    quote_service.set_outcome(session, ORG, quote_id="q1",
                              status=QuoteOutcomeStatus.WON,
                              customer_ref="Acme")
    session.commit()
    session.expire_all()

    assert _snapshot(session, stored.quote_diagnosis_id) == before


def test_a_dismissal_is_a_new_row_and_not_an_edit(session):
    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()
    before = _snapshot(session, stored.quote_diagnosis_id)

    service.dismiss(session, ORG, quote_diagnosis_id=stored.quote_diagnosis_id,
                    reason_code="VOLUME_COMMITMENT", note="annual contract",
                    user_id="usr_1")
    session.commit()
    session.expire_all()

    assert _snapshot(session, stored.quote_diagnosis_id) == before
    rows = session.scalars(select(models.QuoteDiagnosisDismissal)).all()
    assert len(rows) == 1 and rows[0].reason_code == "VOLUME_COMMITMENT"


def test_a_dismissal_reason_outside_the_vocabulary_is_refused(session):
    """A reason nobody can aggregate tunes nothing, which is the whole point of
    capturing them."""
    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    with pytest.raises(ValueError, match="not a dismissal reason"):
        service.dismiss(session, ORG,
                        quote_diagnosis_id=stored.quote_diagnosis_id,
                        reason_code="BECAUSE_I_SAID_SO")


def test_rediagnosing_appends_and_never_updates(session):
    _seed(session)
    first = service.record(session, ORG, quote_id="q1", result=_diagnose(session))
    session.commit()
    first_id = first.quote_diagnosis_id

    second = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session, quoted="700"))
    session.commit()

    assert second.quote_diagnosis_id != first_id
    assert session.scalar(select(models.QuoteDiagnosis).where(
        models.QuoteDiagnosis.quote_diagnosis_id == first_id)) is not None
    assert service.latest(session, ORG,
                          quote_line_id="ln_1").quoted_unit_price == Decimal("700")


def test_the_service_module_contains_no_update_path(session):
    """Append-only by construction, checked rather than trusted.

    ``QuoteDecision`` makes the same promise in a docstring; this reads the
    module's own source for the two shapes that would break it.
    """
    import inspect
    source = inspect.getsource(service)

    assert ".update(" not in source
    assert "QuoteDiagnosis)" not in source.split("def record")[0].replace(
        "models.QuoteDiagnosis)", "")


# ── the evaluation reads, and only reads ─────────────────────────────────────

def test_the_evaluation_scores_codes_against_outcomes_and_writes_nothing(session):
    from app.commercial import quote_service

    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    service.dismiss(session, ORG, quote_diagnosis_id=stored.quote_diagnosis_id,
                    reason_code="COMPETITIVE_PRESSURE")
    session.commit()
    quote_service.set_outcome(session, ORG, quote_id="q1",
                              status=QuoteOutcomeStatus.SENT, customer_ref="Acme")
    quote_service.set_outcome(session, ORG, quote_id="q1",
                              status=QuoteOutcomeStatus.WON, customer_ref="Acme")
    session.commit()
    before = _snapshot(session, stored.quote_diagnosis_id)

    out = replay.evaluate(session, ORG)
    session.expire_all()

    scored = out.by_code["BELOW_HISTORICAL_RANGE"]
    assert scored.diagnosed == 1 and scored.won == 1 and scored.dismissed == 1
    assert scored.dismissal_reasons["COMPETITIVE_PRESSURE"] == 1
    assert _snapshot(session, stored.quote_diagnosis_id) == before


def test_a_quote_with_no_recorded_outcome_is_counted_as_unrecorded(session):
    """Not as a loss. On one live book 54% of quotes simply expired."""
    _seed(session)
    service.record(session, ORG, quote_id="q1", result=_diagnose(session))
    session.commit()

    out = replay.evaluate(session, ORG)

    assert out.without_outcome == 1
    assert out.by_code["BELOW_HISTORICAL_RANGE"].unrecorded == 1
    assert out.by_code["BELOW_HISTORICAL_RANGE"].decided == 0


def test_only_the_diagnosis_in_force_is_scored(session):
    """Counting superseded rows would weight a line re-diagnosed three times
    three times as heavily."""
    _seed(session)
    service.record(session, ORG, quote_id="q1", result=_diagnose(session))
    session.commit()
    service.record(session, ORG, quote_id="q1",
                   result=_diagnose(session, quoted="1000"))
    session.commit()

    out = replay.evaluate(session, ORG)

    assert out.lines == 1
    assert "WITHIN_HISTORICAL_RANGE" in out.by_code
    assert "BELOW_HISTORICAL_RANGE" not in out.by_code


# ── the seam's own resolutions ───────────────────────────────────────────────

def test_the_cutover_is_never_guessed_when_nobody_has_stated_one(session):
    _seed(session)

    assert service.backfill_cutover(session, ORG) is None
    result = _diagnose(session)
    assert result.owner.evidence["backfill_cutover_unknown"] is True


def test_a_stated_cutover_excludes_the_bulk_loaded_rows(session):
    _seed(session)
    session.add(models.ZohoConnection(
        connection_id="cxn_1", organization_id=ORG, connector="zoho",
        zoho_organization_id="60063559751",
        history_loaded_before=date(2026, 4, 1)))
    session.flush()

    result = _diagnose(session)

    assert service.backfill_cutover(session, ORG) == date(2026, 4, 1)
    reasons = result.owner.evidence["excluded_by_reason"]
    assert reasons.get("BACKFILLED", 0) > 0
    assert result.owner.evidence["backfill_cutover_unknown"] is False


def test_the_detector_reads_the_boundary_off_the_connections_own_history(session):
    """It suggests; a person confirms; the column decides."""
    _seed(session)
    for i in range(30):
        session.add(models.SalesTxn(
            sales_txn_id=f"mig_{i}", organization_id=ORG,
            external_ref=f"old:{i}", customer_id="cst_a", product_id="prd_1",
            date=date(2025, 4, 1) + timedelta(days=i * 3), qty=Decimal("1"),
            unit_price=Decimal("1000"), line_revenue=Decimal("1000"),
            source_recorded_at=datetime(2026, 3, 18, 9, tzinfo=timezone.utc),
            source_ref={}))
    session.flush()

    found = cutover.detect(service.cutover_observations(session, ORG))

    assert found.peak_month == "2026-03"
    assert found.suggested == date(2026, 4, 1)
    # And nothing applied it: the column is still empty.
    assert service.backfill_cutover(session, ORG) is None


def test_a_segment_roster_comes_from_the_groups_a_customer_is_in(session):
    _seed(session)
    session.add(models.EntityGroup(group_id="grp_1", organization_id=ORG,
                                   entity_kind="CUSTOMER", slug="aerospace",
                                   name="Aerospace"))
    for cid in ("cst_a", "cst_b"):
        session.add(models.EntityGroupMember(
            organization_id=ORG, group_id="grp_1", entity_id=cid))
    session.flush()

    assert service.segment_roster(session, ORG, "cst_a") == frozenset(
        {"cst_a", "cst_b"})
    assert service.segment_roster(session, ORG, "cst_zzz") is None


def test_the_dismissal_vocabulary_the_api_offers_is_the_one_it_accepts(session):
    """One list. A front end offering a reason the service refuses is a dead
    button somebody discovers in front of a customer."""
    offered = {code for code, _ in render.dismissal_reasons()}

    assert offered == set(render.DISMISS_REASONS)


def _snapshot(session, diagnosis_id: str) -> dict:
    """Every column, read back from the database, on one timezone line.

    Read back rather than taken off the in-memory object, because the two are
    not the same thing: the instance holds what was assigned, and the row holds
    what SQLite stored — which for a ``DateTime(timezone=True)`` comes back
    naive. Comparing one against the other would report a difference that is a
    property of the driver rather than of the record, and this test would then
    have been failing for a reason nobody would trust the next time.

    ``clock.aware`` puts both on the UTC line, the same helper the engine uses
    for the same driver behaviour.
    """
    session.expire_all()
    row = session.get(models.QuoteDiagnosis, diagnosis_id)
    return copy.deepcopy({
        c.name: (aware(getattr(row, c.name))
                 if isinstance(getattr(row, c.name), datetime)
                 else getattr(row, c.name))
        for c in models.QuoteDiagnosis.__table__.columns})


# ── driver attribution is computed, not stored ───────────────────────────────
#
# The decision and its reasoning are in ``rules.ENGINE_VERSION``. These are the
# checks that keep it true: nothing about the stored row moved, so nothing about
# replaying one did either, and the stamp stays where it is.

def test_the_stored_row_gains_no_column_and_the_engine_version_does_not_move():
    """Why ``qd-1`` is still ``qd-1``.

    ``engine_version`` answers "which code produced the columns in this row".
    An attribution is computed from baselines that are already settled and
    changes no code, no grade and no cited id, so every column ``record``
    writes is what it would have been. Moving the stamp would mark every
    pre-existing row as the product of different code when the rows are
    identical.

    The column assertion is the other half of the decision: if an attribution is
    ever persisted, this fails, and persisting one is exactly the change that
    must bump the stamp and bring a migration with it.
    """
    from app.commercial.quote_diagnosis import rules

    columns = {c.name for c in models.QuoteDiagnosis.__table__.columns}

    assert rules.ENGINE_VERSION == "qd-1"
    assert not [c for c in columns
                if "attribution" in c or "driver" in c or "effect" in c]


def test_a_row_written_without_an_attribution_still_replays_clean(session):
    """The stored rows are all "old" rows in the sense that matters: none of
    them carries an attribution, and none of them needs to.

    The hash covers the cited evidence ids; the verdict is ``codes`` and
    ``strength``. A replay recomputes an attribution and compares neither, so
    the recomputation cannot make a stored diagnosis stop reproducing.
    """
    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    done = replay.rediagnose(session, ORG, quote_line_id="ln_1", th=TH)

    assert done.result.evidence_hash == stored.evidence_hash
    assert done.verdict_matches is True
    assert done.stored_engine_version == done.current_engine_version == "qd-1"
    # The recomputation did produce one — it is simply not part of what is
    # compared, and not part of what was written.
    assert done.result.owner.attribution is not None
    assert "attribution" not in str(
        {c.name: getattr(stored, c.name)
         for c in models.QuoteDiagnosis.__table__.columns}).lower()


def test_recording_a_diagnosis_writes_no_driver_code_anywhere_in_the_row(session):
    """A serialised check beside the column one: a driver code smuggled into
    ``codes``, ``context`` or one of the JSON blobs would pass a column-name
    assertion and would still be a persisted shape nobody migrated."""
    from app.commercial.quote_diagnosis import drivers

    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    body = str(_snapshot(session, stored.quote_diagnosis_id))

    for code in drivers.DRIVER_CODES:
        assert code not in body
    assert "PRICE_THEN_COST" not in body


# ── the working-capital reading is computed, not stored ──────────────────────
#
# Same decision as the attribution above, reached the same way and held by the
# same two tripwires. ``engine_version`` answers "which code produced the
# columns in this row"; ``record`` writes no column for a reading, the evidence
# hash covers the cited price and cost ids, and the replay compares ``codes``
# and ``strength``. The reading touches none of the three, so every row written
# after this change is byte-identical to the row that would have been written
# before it.

def test_the_reading_adds_no_column_and_the_engine_version_still_does_not_move():
    """Why ``qd-1`` is still ``qd-1`` after a second computed block.

    The column assertion is the other half: if a reading is ever persisted this
    fails, and persisting one is exactly the change that must bump the stamp and
    bring a migration with it.
    """
    from app.commercial.quote_diagnosis import rules

    columns = {c.name for c in models.QuoteDiagnosis.__table__.columns}

    assert rules.ENGINE_VERSION == "qd-1"
    assert not [c for c in columns
                if "capital" in c or "funding" in c or "charge" in c
                or "settlement" in c]


def test_a_row_written_without_a_working_capital_reading_still_replays(session):
    """The recomputation cannot make a stored diagnosis stop reproducing.

    A replay recomputes the reading from this account's settlements and this
    supplier's terms — rows that move — and compares neither, because neither is
    part of the hash or of the verdict.
    """
    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    done = replay.rediagnose(session, ORG, quote_line_id="ln_1", th=TH)

    assert done.result.evidence_hash == stored.evidence_hash
    assert done.verdict_matches is True
    assert done.stored_engine_version == done.current_engine_version == "qd-1"
    # It was produced — it is simply not compared and not written.
    assert done.result.owner.working_capital is not None


def test_recording_a_diagnosis_writes_no_working_capital_figure_in_the_row(
        session):
    """A serialised check beside the column one.

    A reason code or a money figure smuggled into ``codes``, ``context`` or one
    of the JSON blobs would pass a column-name assertion and would still be a
    persisted shape nobody migrated.
    """
    from app.commercial.quote_diagnosis import working_capital

    _seed(session)
    stored = service.record(session, ORG, quote_id="q1",
                            result=_diagnose(session))
    session.commit()

    body = str(_snapshot(session, stored.quote_diagnosis_id))

    for reason in (working_capital.ASSESSED, working_capital.NO_RATE,
                   working_capital.NO_COST_BASELINE,
                   working_capital.NO_SUPPLIER_TERMS,
                   working_capital.NO_RECEIVABLE_DAYS,
                   working_capital.MEASURED_LAG, working_capital.GRANTED_TERM,
                   working_capital.AGREED_TERM, working_capital.ERP_TERM):
        assert reason not in body
    assert "capital_per_unit" not in body
    assert "funded_days" not in body


def test_the_row_is_identical_whether_or_not_the_reading_could_be_taken(session):
    """The empirical half of the ``ENGINE_VERSION`` argument.

    The other two tests assert that no column is *named* for the reading and
    that no reason code appears in the serialised row. This one diagnoses the
    same line twice against the same policy and the same purchases, varying only
    the two inputs the reading brought with it — this account's settled invoices
    and this supplier's agreed term — and asserts every stored column comes out
    the same.

    That is the property the stamp actually promises: two rows carrying ``qd-1``
    were produced by code that writes the same columns. An argument from three
    facts is only as good as the three facts; this asks the row.

    ``created_at`` and the primary key are excluded because they are the two
    things that must differ between two inserts.
    """
    import dataclasses

    th = dataclasses.replace(TH, cost_of_capital_annual_pct=0.12)
    _seed(session)
    session.add(models.Vendor(vendor_id="vnd_1", organization_id=ORG,
                              external_id="v1", name="Toolmakers",
                              payment_terms_days=30))
    session.flush()
    for i in range(6):
        day = date(2026, 1, 8) + timedelta(days=21 * i)
        session.add(models.CostRecord(
            cost_record_id=f"c{i}", organization_id=ORG, external_ref=f"b{i}:1",
            product_id="prd_1", vendor_id="vnd_1", date=day, qty=Decimal("10"),
            unit_cost=Decimal("600"), rate=Decimal("600"),
            source_recorded_at=datetime(day.year, day.month, day.day, 9,
                                        tzinfo=timezone.utc),
            source_ref={"record_type": "bill"}))
    session.commit()

    # Nothing to read the receivable leg off yet.
    refused = _diagnose(session, th=th)
    assert refused.owner.working_capital.assessed is False
    bare = service.record(session, ORG, quote_id="q1", result=refused)
    session.commit()

    # The two inputs this reading brought with it, and nothing else.
    session.add(models.VendorPaymentTerm(
        organization_id=ORG, vendor_id="vnd_1", days=45,
        # Stamped before the quote on purpose. ``vendor_payment_terms`` is
        # upserted, so ``updated_at`` is when the values it holds now were
        # written, and a term typed after the quote is not evidence the quoter
        # had — see ``_knowable_term``, and the test below that pins it.
        created_at=_at(date(2026, 1, 2)), updated_at=_at(date(2026, 1, 2))))
    for i in range(8):
        raised = QUOTE_DAY - timedelta(days=340 - 20 * i)
        session.add(models.PaymentReceipt(
            payment_receipt_id=f"rcp_{i}", organization_id=ORG,
            external_ref=f"RCP-{i}", customer_id="cst_a",
            date=raised + timedelta(days=70), amount=Decimal("10000")))
    session.flush()
    for i in range(8):
        raised = QUOTE_DAY - timedelta(days=340 - 20 * i)
        session.add(models.PaymentApplication(
            payment_application_id=f"pa_{i}", organization_id=ORG,
            external_ref=f"PA-{i}", payment_receipt_id=f"rcp_{i}",
            customer_id="cst_a", invoice_external_ref=f"INV-{i}",
            invoice_number=f"INV-{i}", invoice_date=raised,
            invoice_due_date=raised + timedelta(days=45),
            paid_on=raised + timedelta(days=70),
            amount_applied=Decimal("10000")))
    session.commit()

    result = _diagnose(session, th=th)
    # The reading was genuinely taken — otherwise this proves nothing at all,
    # which is the *absence of evidence is not a pass* rule pointed at a test.
    assert result.owner.working_capital.assessed is True
    assert result.owner.working_capital.funded_days == 25

    full = service.record(session, ORG, quote_id="q1", result=result)
    session.commit()

    skip = {"quote_diagnosis_id", "created_at"}
    before = {k: v for k, v in _snapshot(session, bare.quote_diagnosis_id).items()
              if k not in skip}
    after = {k: v for k, v in _snapshot(session, full.quote_diagnosis_id).items()
             if k not in skip}
    assert after == before


def test_a_supplier_term_typed_after_the_quote_is_not_read_back_into_it(session):
    """The stamp threaded from the row is ``updated_at``, and it is checked.

    ``vendor_payment_terms`` is upserted one row per vendor: ``created_at``
    would vouch for a term that has since been retyped, and there is no earlier
    version kept to fall back on. So the agreement carries the instant its
    current values were written, the engine puts that through its one
    ``is_knowable``, and a term agreed last week does not re-date a quote from
    June.
    """
    import dataclasses

    th = dataclasses.replace(TH, cost_of_capital_annual_pct=0.12)
    _seed(session)
    session.add(models.Vendor(vendor_id="vnd_1", organization_id=ORG,
                              external_id="v1", name="Toolmakers",
                              payment_terms_days=30))
    session.flush()
    for i in range(6):
        day = date(2026, 1, 8) + timedelta(days=21 * i)
        session.add(models.CostRecord(
            cost_record_id=f"c{i}", organization_id=ORG, external_ref=f"b{i}:1",
            product_id="prd_1", vendor_id="vnd_1", date=day, qty=Decimal("10"),
            unit_cost=Decimal("600"), rate=Decimal("600"),
            source_recorded_at=_at(day), source_ref={"record_type": "bill"}))
    # Agreed at 45 days, but typed a month after the quote was written.
    session.add(models.VendorPaymentTerm(
        organization_id=ORG, vendor_id="vnd_1", days=45,
        created_at=_at(date(2026, 7, 1)), updated_at=_at(date(2026, 7, 1))))
    for i in range(8):
        raised = QUOTE_DAY - timedelta(days=340 - 20 * i)
        session.add(models.PaymentReceipt(
            payment_receipt_id=f"rcp_{i}", organization_id=ORG,
            external_ref=f"RCP-{i}", customer_id="cst_a",
            date=raised + timedelta(days=70), amount=Decimal("10000")))
    session.flush()
    for i in range(8):
        raised = QUOTE_DAY - timedelta(days=340 - 20 * i)
        session.add(models.PaymentApplication(
            payment_application_id=f"pa_{i}", organization_id=ORG,
            external_ref=f"PA-{i}", payment_receipt_id=f"rcp_{i}",
            customer_id="cst_a", invoice_external_ref=f"INV-{i}",
            invoice_number=f"INV-{i}", invoice_date=raised,
            invoice_due_date=raised + timedelta(days=45),
            paid_on=raised + timedelta(days=70),
            amount_applied=Decimal("10000")))
    session.commit()

    reading = _diagnose(session, th=th).owner.working_capital

    assert reading.assessed is True
    # The ERP's 30 days, not the agreement's 45 — and the residual says both
    # that the ERP value is what ran and why the agreement was not usable.
    assert reading.terms_source == "ERP_TERM"
    assert reading.supplier_credit_days == 30
    assert any("RECORDED_AFTER" in u["reason"] for u in reading.unavailable)


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)
