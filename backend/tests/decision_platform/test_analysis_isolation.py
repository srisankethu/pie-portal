"""A failure in the derived analysis must cost the analysis, not the pull.

What happened: a Customer × Item signal carried a 74-character composite
subject into a `String(64)` column. Postgres rejected the batch — and the
damage that followed had nothing to do with the width.

  * The write went out at the **next phase's** commit, not inside the `try`
    that was meant to contain it, so "best-effort" caught nothing.
  * The failed flush left the session in a state where every later statement
    raises `PendingRollbackError`, including the reload of an expired
    attribute. The `finally` whose whole job is to record how the pull went
    died on `run.sync_run_id`.
  * So an hour-long sync that had already imported every customer, item and
    invoice ended with no counters, no skips, and a generic crash message.

The width is fixed in `test_column_widths.py`. These are about the blast
radius, which is the part that turns a bad row into a lost hour.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain import models
from app.ingestion import jobs

ORG = "org_isolation"


@pytest.fixture()
def run(session):
    session.add(models.Organization(organization_id=ORG, name="SLS Engineers",
                                    currency="INR", config={}))
    row = models.SyncRun(sync_run_id="run_iso", organization_id=ORG,
                         source="fixture", status="RUNNING", phase="Starting")
    session.add(row)
    session.commit()
    return row


def _rejected_row(session) -> None:
    """Write something the database will refuse, the way a too-narrow column
    refuses a composite subject. NOT NULL rather than a length, because SQLite
    enforces the first and ignores the second — the point here is the aftermath
    of a rejected flush, and any rejection produces the same aftermath."""
    session.add(models.Signal(
        signal_id="sig_bad", organization_id=None,  # NOT NULL — refused
        signal_type="CI_MARGIN_EROSION", subject_entity_type="CUSTOMER_ITEM",
        subject_entity_id="c::p", detector_version="v0",
        threshold_config_version="ci_x"))


# ── the savepoint ───────────────────────────────────────────────────────────
def test_a_rejected_write_leaves_the_session_usable(session, run):
    notes: dict = {}

    with jobs._isolated(session, "Detecting signals", notes):
        _rejected_row(session)

    assert session.is_active, (
        "the session has to survive, or nothing after this can record anything")
    # And it can still write: this is the property the `finally` needed.
    run.phase = "Reading invoices"
    session.commit()
    assert session.get(models.SyncRun, "run_iso").phase == "Reading invoices"


def test_the_rejected_rows_are_gone_not_half_written(session, run):
    """A savepoint rollback, so the phase leaves nothing behind."""
    notes: dict = {}
    with jobs._isolated(session, "Detecting signals", notes):
        session.add(models.Signal(
            signal_id="sig_ok", organization_id=ORG, signal_type="CI_X",
            subject_entity_type="CUSTOMER_ITEM", subject_entity_id="c::p",
            detector_version="v0", threshold_config_version="ci_x"))
        _rejected_row(session)

    session.commit()
    assert session.scalars(select(models.Signal)).all() == [], (
        "the good row from a failed phase goes too — the phase is rebuilt whole "
        "on the next sync, and half a detection is not a detection")


def test_the_failure_is_named_rather_than_swallowed(session, run):
    notes: dict = {}
    with jobs._isolated(session, "Detecting signals", notes):
        _rejected_row(session)

    assert notes["analysis_failures"][0]["phase"] == "Detecting signals"
    assert notes["analysis_failures"][0]["error"]

    gaps = jobs.analysis_gaps(notes)
    assert gaps[0]["code"] == "ANALYSIS_PHASE_FAILED"
    assert gaps[0]["label"] == "Detecting signals"
    assert "next sync" in gaps[0]["fix"], "a gap has to say what to do about it"


def test_a_phase_that_works_is_left_alone(session, run):
    """The isolation must not become a reason for writes to go missing."""
    notes: dict = {}
    with jobs._isolated(session, "Detecting signals", notes):
        session.add(models.Signal(
            signal_id="sig_good", organization_id=ORG, signal_type="CI_X",
            subject_entity_type="CUSTOMER_ITEM", subject_entity_id="c::p",
            detector_version="v0", threshold_config_version="ci_x"))
    session.commit()

    assert session.get(models.Signal, "sig_good") is not None
    assert "analysis_failures" not in notes


# ── the run's own record ────────────────────────────────────────────────────
class _Source:
    """A pull that imports one of everything, so there is a record worth keeping."""

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active"}]

    def list_users(self): return []

    def list_invoices(self, skip=None):
        return [{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                 "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                 "quantity": 10, "rate": 500,
                                 "item_total": 5000}]}]

    def list_bills(self, skip=None): return []


def test_a_pull_still_records_what_it_imported_when_analysis_is_refused(
        session, run, monkeypatch):
    """The whole incident, end to end: the analysis writes a row the database
    refuses, and the sync still reports the hour of work it did."""
    monkeypatch.setattr("app.ingestion.sync.get_source", lambda *a, **kw: _Source())

    def poisoned(_session, _org, **_kw):
        _rejected_row(_session)
        return {"signals_emitted": 0}

    monkeypatch.setattr("app.signals.engine.run_detectors", poisoned)

    maker = sessionmaker(bind=session.get_bind(), autoflush=False,
                         expire_on_commit=False, future=True)
    import app.db as db_module
    monkeypatch.setattr(db_module, "SessionLocal", maker)

    jobs.run_job("run_iso", date(2026, 1, 1), False, None)

    session.expire_all()
    row = session.get(models.SyncRun, "run_iso")
    assert row.status in ("OK", "PARTIAL"), (
        "the pull imported real trade; a refused derived signal must not make "
        "that a failure")
    # Counted, not zero: the pull's own numbers are what a poisoned session
    # used to swallow. (The window slicing re-reads the fixture's one invoice
    # per slice, so the figure is "more than none" rather than exactly one.)
    assert row.customers >= 1 and row.sales_txns >= 1, "the counters survived"
    assert row.finished_at is not None and row.phase is None
    codes = [u.get("code") for u in (row.unresolved or [])]
    assert "ANALYSIS_PHASE_FAILED" in codes, "and the gap is on the run, visibly"


def test_a_poisoned_session_still_gets_the_run_recorded(session, run, monkeypatch):
    """The safety net under the savepoints.

    `_isolated` contains the failures we know about. This is what happens when
    a write is rejected somewhere that has no savepoint around it — the case
    that actually occurred, and the reason the `finally` must roll back before
    it touches anything. Without it the block that records the run dies on the
    first attribute it reads, and a finished pull reports nothing at all.
    """
    monkeypatch.setattr("app.ingestion.sync.get_source", lambda *a, **kw: _Source())

    def poison_and_die(session_, *_a, **_kw):
        _rejected_row(session_)
        session_.flush()          # the rejection, outside any savepoint
        return {}

    monkeypatch.setattr(jobs, "execute_analysis", poison_and_die)

    jobs.execute_sync(session, run, since=date(2026, 1, 1))

    assert session.is_active, "the finally has to leave a usable session behind"
    session.commit()

    row = session.get(models.SyncRun, "run_iso")
    assert row.status == "PARTIAL", "rows were written, so it is not a clean FAILED"
    assert "IntegrityError" in (row.error or ""), "and the cause is named"
    assert row.customers >= 1, "the counters the crash used to swallow"
    assert row.finished_at is not None and row.phase is None
