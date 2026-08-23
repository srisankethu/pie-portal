"""One process ticks the schedule, and the tick costs the same at any size.

Both halves of a defect that was invisible at three tenants and routine at a
hundred and fifty. `scheduler.py` reasoned that its tick was safe because "this
deployment is one uvicorn process" while the image has shipped `--workers 2`
throughout — so two threads ticked, started together, and could both decide the
same organization was due. And the tick itself asked two questions per
organization every minute, which at 150 books is most of a million statements a
day spent answering "no".
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app import leases
from app.clock import now as _now
from app.config import settings
from app.domain import models
from app.ingestion import jobs, scheduler


@pytest.fixture()
def maker(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


@pytest.fixture()
def dispatched(monkeypatch):
    """Capture dispatches instead of starting real pulls."""
    runs: list[tuple] = []
    monkeypatch.setattr(jobs, "thread_dispatch",
                        lambda run_id, since, full, conn: runs.append(run_id))
    return runs


def _connected_org(session, org_id: str) -> None:
    session.add(models.Organization(organization_id=org_id, name=org_id,
                                    config={}))
    session.add(models.ZohoConnection(connection_id=f"conn_{org_id}",
                                      organization_id=org_id,
                                      zoho_organization_id="z1", enabled=True))
    session.flush()


# ── the lease itself ─────────────────────────────────────────────────────────

def test_one_holder_at_a_time(session):
    assert leases.acquire(session, "sync-scheduler", "process-a") is True
    assert leases.acquire(session, "sync-scheduler", "process-b") is False


def test_the_holder_renews_rather_than_locking_itself_out(session):
    leases.acquire(session, "sync-scheduler", "process-a")
    assert leases.acquire(session, "sync-scheduler", "process-a") is True


def test_an_expired_lease_is_taken_over(session):
    """A lease, not a lock: the process that held it is gone, and the schedule
    must not stop because nobody released it."""
    leases.acquire(session, "sync-scheduler", "dead-process",
                   ttl=timedelta(seconds=1))
    later = _now() + timedelta(minutes=5)

    assert leases.acquire(session, "sync-scheduler", "live-process",
                          now=later) is True
    assert leases.current_holder(session, "sync-scheduler",
                                 now=later) == "live-process"


def test_a_live_lease_is_not_taken_over(session):
    leases.acquire(session, "sync-scheduler", "busy", ttl=timedelta(minutes=10))
    assert leases.acquire(session, "sync-scheduler", "impatient") is False


def test_releasing_hands_it_on_without_waiting_out_the_expiry(session):
    leases.acquire(session, "sync-scheduler", "process-a",
                   ttl=timedelta(hours=1))
    assert leases.release(session, "sync-scheduler", "process-a") is True
    assert leases.acquire(session, "sync-scheduler", "process-b") is True


def test_only_the_holder_may_release(session):
    """Otherwise releasing is a way to make two processes tick on purpose."""
    leases.acquire(session, "sync-scheduler", "process-a",
                   ttl=timedelta(hours=1))
    assert leases.release(session, "sync-scheduler", "process-b") is False
    assert leases.acquire(session, "sync-scheduler", "process-b") is False


def test_nobody_holds_an_expired_lease(session):
    leases.acquire(session, "sync-scheduler", "process-a",
                   ttl=timedelta(seconds=1))
    assert leases.current_holder(
        session, "sync-scheduler", now=_now() + timedelta(minutes=5)) is None


def test_two_processes_starting_together_produce_one_ticker(engine, maker):
    """The shape of the actual failure: two uvicorn workers boot at the same
    instant and both ask. One is told yes."""
    sessions = [maker() for _ in range(4)]
    try:
        results = [leases.acquire(s, "sync-scheduler", f"process-{i}")
                   for i, s in enumerate(sessions)]
    finally:
        for s in sessions:
            s.close()
    assert results.count(True) == 1, results


# ── what that prevents ───────────────────────────────────────────────────────

def test_two_schedulers_queue_one_sync_not_two(engine, maker, dispatched,
                                               monkeypatch):
    """The defect, end to end. Both processes tick in the same instant; without
    the lease both read "nothing running" and both queue a pull of the same
    books.

    Asserted through the lease rather than by racing threads, because the
    SQLite fixture shares one connection between sessions — the concurrent
    claim is exercised for real on Postgres by the gate.
    """
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    setup = maker()
    _connected_org(setup, "org_s")
    setup.commit()
    setup.close()

    first, second = maker(), maker()
    try:
        a_ticks = leases.acquire(first, scheduler.LEASE, "process-a")
        b_ticks = leases.acquire(second, scheduler.LEASE, "process-b")
        assert (a_ticks, b_ticks) == (True, False)

        queued = scheduler.tick(first) if a_ticks else 0
        first.commit()
        queued += scheduler.tick(second) if b_ticks else 0
        second.commit()

        assert queued == 1
        assert len(dispatched) == 1
        runs = first.query(models.SyncRun).all()
        assert len(runs) == 1, "two scheduler processes queued two pulls"
    finally:
        first.close()
        second.close()


# ── the tick's cost ──────────────────────────────────────────────────────────

def test_the_tick_costs_the_same_at_any_number_of_tenants(engine, maker,
                                                          dispatched,
                                                          monkeypatch):
    """It was two statements per organization per minute. At 150 connected
    books that is ~864,000 a day, across both API processes, to answer a
    question that is almost always "no"."""
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 0)   # nothing is due
    setup = maker()
    for i in range(3):
        _connected_org(setup, f"org_{i}")
    setup.commit()
    setup.close()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    session = maker()
    event.listen(engine, "before_cursor_execute", record)
    try:
        scheduler.tick(session)
        few = len(statements)
        statements.clear()

        for i in range(3, 30):
            _connected_org(session, f"org_{i}")
        session.commit()
        statements.clear()
        scheduler.tick(session)
        many = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)
        session.close()

    assert few == many, (
        f"{few} statements for 3 organizations and {many} for 30 — the tick is "
        "still per-organization")
    assert many <= 4, f"{many} statements for one tick"


def test_an_empty_deployment_asks_almost_nothing(session):
    """No connected books, no work: the wake should cost one query."""
    assert scheduler.tick(session) == 0


def test_the_tick_still_reads_each_organizations_own_cadence(maker, dispatched,
                                                             monkeypatch):
    """Batching the queries must not flatten the per-tenant setting into one."""
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    session = maker()
    try:
        _connected_org(session, "org_on")
        _connected_org(session, "org_off")
        session.get(models.Organization, "org_off").config = {"auto_sync_hours": 0}
        session.commit()

        assert scheduler.tick(session) == 1
        session.commit()
        started = [r.organization_id for r in session.query(models.SyncRun).all()]
        assert started == ["org_on"]
    finally:
        session.close()


def test_the_window_is_still_the_existing_coverage(maker, dispatched, monkeypatch):
    """The batched read must not change what a scheduled run asks Zoho for —
    coverage, never "since the last sync"."""
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    session = maker()
    try:
        _connected_org(session, "org_s")
        session.add(models.SyncRun(
            organization_id="org_s", source="api", status="OK",
            started_at=_now() - timedelta(days=30),
            since=date(2025, 1, 1)))
        session.commit()

        assert scheduler.tick(session) == 1
        session.commit()
        fresh = (session.query(models.SyncRun)
                 .filter(models.SyncRun.status == "QUEUED").one())
        assert fresh.since == date(2025, 1, 1)
    finally:
        session.close()
