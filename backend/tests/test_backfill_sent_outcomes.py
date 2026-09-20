"""The repair for quotes sent while the send could not record that it had.

``scripts/backfill_sent_outcomes.py`` opens the SENT row and the ERP link for
every quote that has a document row and no outcome to match — the state every
send left behind between the rename of the written-document record and the fix
in ``routers.quote``. What is pinned here is the part that could do damage: it
must go through ``set_outcome`` and nothing else, it must never move a fact a
person recorded, it must date the send to when the document was written, and a
second run must find nothing to do.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.commercial import quote_service
from app.domain import models
from app.domain.enums import QuoteOutcomeStatus

ORG = "org_a"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_sent_outcomes.py"


@pytest.fixture()
def session():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="module")
def backfill():
    spec = importlib.util.spec_from_file_location("backfill_sent_outcomes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sent(session, quote_id: str, number: str, doc_id: str, *,
          written_at: datetime) -> None:
    session.add(models.QuoteDraft(
        quote_id=quote_id, organization_id=ORG, customer_name="Pitti",
        customer_id="c1", number=number, sequence=int(number.split("-")[1]),
        reference=f"{number}-abcd1234"))
    session.add(models.QuoteDocument(
        organization_id=ORG, quote_id=quote_id, external_system="zoho",
        external_document_id=doc_id, external_document_number=f"EST-{doc_id}",
        reference=f"{number}-abcd1234", line_count=1, fingerprint="f",
        written_at=written_at))
    session.commit()


def test_a_sent_quote_with_no_outcome_row_is_opened_at_sent_dated_to_the_send(session, backfill):
    when = datetime(2026, 9, 10, 9, 30, tzinfo=timezone.utc)
    _sent(session, "q1", "QB-0001", "est-1", written_at=when)

    entries = backfill.plan(session, ORG)
    assert [e["action"] for e in entries] == ["open SENT and link"]
    assert backfill.apply(session, entries) == 1
    session.commit()

    row = quote_service.get_outcome(session, ORG, "q1")
    assert row.status == QuoteOutcomeStatus.SENT.value
    assert row.quote_document_ref == "est-1"
    assert row.customer_ref == "Pitti" and row.customer_id == "c1"
    assert row.sent_at.replace(tzinfo=timezone.utc) == when, \
        "the send happened when the document was written, not when it was repaired"

    # Idempotent: the second run has nothing to do and writes nothing.
    again = backfill.plan(session, ORG)
    assert [e["action"] for e in again] == ["nothing to do"]
    assert backfill.apply(session, again) == 0


def test_a_draft_row_without_a_reference_moves_to_sent(session, backfill):
    _sent(session, "q2", "QB-0002", "est-2", written_at=datetime.now(timezone.utc))
    quote_service.set_outcome(session, ORG, quote_id="q2",
                              status=QuoteOutcomeStatus.DRAFT, user_id="u1")
    session.commit()

    entries = backfill.plan(session, ORG)
    assert entries[0]["action"] == "DRAFT -> SENT and link"
    backfill.apply(session, entries)
    session.commit()
    row = quote_service.get_outcome(session, ORG, "q2")
    assert (row.status, row.quote_document_ref) == ("SENT", "est-2")


def test_a_fact_a_person_recorded_is_reported_never_moved(session, backfill):
    """A row already naming a *different* document is somebody's record."""
    _sent(session, "q3", "QB-0003", "est-3", written_at=datetime.now(timezone.utc))
    quote_service.set_outcome(session, ORG, quote_id="q3",
                              quote_document_ref="est-other",
                              status=QuoteOutcomeStatus.SENT, user_id="u1")
    session.commit()

    entries = backfill.plan(session, ORG)
    assert entries[0]["action"].startswith("REPORT")
    assert backfill.apply(session, entries) == 0
    row = quote_service.get_outcome(session, ORG, "q3")
    assert row.quote_document_ref == "est-other"


def test_a_decided_quote_keeps_its_status_and_only_gains_the_link(session, backfill):
    _sent(session, "q4", "QB-0004", "est-4", written_at=datetime.now(timezone.utc))
    quote_service.set_outcome(session, ORG, quote_id="q4",
                              status=QuoteOutcomeStatus.SENT, user_id="u1")
    quote_service.set_outcome(session, ORG, quote_id="q4",
                              status=QuoteOutcomeStatus.WON, user_id="u1")
    session.commit()

    entries = backfill.plan(session, ORG)
    assert entries[0]["action"] == "keep WON, fill link"
    backfill.apply(session, entries)
    session.commit()
    row = quote_service.get_outcome(session, ORG, "q4")
    assert (row.status, row.quote_document_ref) == ("WON", "est-4")


def test_the_newest_document_is_the_one_linked(session, backfill):
    now = datetime.now(timezone.utc)
    _sent(session, "q5", "QB-0005", "est-5a", written_at=now - timedelta(days=1))
    session.add(models.QuoteDocument(
        organization_id=ORG, quote_id="q5", external_system="zoho",
        external_document_id="est-5b", external_document_number="EST-est-5b",
        reference="QB-0005-abcd1234", line_count=1, fingerprint="g", written_at=now))
    session.commit()

    entries = backfill.plan(session, ORG)
    backfill.apply(session, entries)
    session.commit()
    assert quote_service.get_outcome(session, ORG, "q5").quote_document_ref == "est-5b"


def test_another_organization_is_untouched(session, backfill):
    _sent(session, "q6", "QB-0006", "est-6", written_at=datetime.now(timezone.utc))
    assert backfill.plan(session, "org_b") == []
