"""Only one process does it, and the others carry on being healthy.

Two uvicorn workers each run their own auto-sync scheduler thread, and both
tick in the same minute. ``a7syncguard`` already made the *outcome* right — the
partial unique indexes on ``sync_runs`` let only one run start — so what is
under test here is the waste: the losing worker must not reach ``start_sync``
at all, forever, once a minute.

The lease itself is a generic primitive (``app/lease.py``): a named claim with
an expiry, taken by a conditional UPDATE whose rowcount decides. It is tested
as one, not as a scheduler detail, because the hash-chained audit log needs the
same "one writer right now" and must not grow a second copy of it.

**A real database file and real threads for the race.** Two acquirers that take
turns prove nothing about two that collide, and neither in-memory SQLite nor a
single thread can collide — the same reason ``test_parallel_sync`` and
``test_db_concurrency`` go to disk.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import lease
from app.clock import aware as _aware, now as _now
from app.config import settings
from app.domain import models
from app.ingestion import jobs, scheduler
from app.seed import ensure_org_and_users

ORG = "org_pie"
REPO_BACKEND = pathlib.Path(__file__).resolve().parents[2]
NAME = "a-thing-one-process-does"


@pytest.fixture()
def file_db(tmp_path):
    """A migrated on-disk database, WAL, mirroring ``app/db.py``'s pragmas.

    On disk because the point of these tests is two connections contending, and
    an in-memory SQLite database is private to the connection that opened it.
    """
    path = tmp_path / "lease.db"
    url = f"sqlite:///{path}"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=REPO_BACKEND, env={**os.environ, "DATABASE_URL": url},
                   capture_output=True, check=True)

    engine = create_engine(url, future=True,
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):     # mirrors app/db.py
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
        finally:
            cur.close()

    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    yield Maker
    engine.dispose()


# ── the lease itself ─────────────────────────────────────────────────────────

def test_two_processes_claiming_at_once_produce_exactly_one_holder(file_db):
    """The whole mechanism in one assertion.

    A barrier so both threads issue the statement in the same instant rather
    than politely after one another, which is the case that has to hold: the
    predicate and the write are one statement precisely so there is no window
    between reading who holds the lease and taking it.
    """
    gate = threading.Barrier(2)
    won: list[str] = []
    # A thread that dies raises into the thread excepthook, which pytest never
    # sees — so "one winner, one crashed" would be indistinguishable from "one
    # winner, one correctly lost", and this test would keep passing through
    # exactly the regression it exists to catch (a dialect raising
    # IntegrityError where the loser expects a False).
    failed: list[BaseException] = []
    lock = threading.Lock()

    def claim(who: str) -> None:
        session = file_db()
        try:
            gate.wait(timeout=10)
            if lease.hold(session, NAME, seconds=60, holder=who):
                with lock:
                    won.append(who)
        except BaseException as e:            # noqa: BLE001 — re-asserted below
            with lock:
                failed.append(e)
        finally:
            session.close()

    threads = [threading.Thread(target=claim, args=(f"worker-{i}",))
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not failed, f"a claimant raised instead of losing cleanly: {failed!r}"
    assert not [t for t in threads if t.is_alive()], "a claimant hung"
    assert len(won) == 1, f"exactly one holder, got {won}"

    session = file_db()
    assert lease.held_by(session, NAME) == won[0], (
        "and the database agrees with the winner about who won")
    session.close()


def test_the_holder_renews_and_a_stranger_cannot_steal_it(file_db):
    session = file_db()
    assert lease.hold(session, NAME, seconds=60, holder="mine") is True
    assert lease.hold(session, NAME, seconds=60, holder="mine") is True, (
        "the holder keeps holding — leadership must not be re-elected every tick")
    assert lease.hold(session, NAME, seconds=60, holder="theirs") is False, (
        "an unexpired claim is not available to anyone else")
    assert lease.held_by(session, NAME) == "mine"
    session.close()


def test_a_renewal_actually_moves_the_expiry(file_db):
    """Renewing without extending would be a lease that quietly lapses under a
    leader that is doing everything right."""
    session = file_db()
    start = _now()
    lease.hold(session, NAME, seconds=60, holder="mine", now=start)
    first = session.get(models.ProcessLease, NAME).expires_at

    later = start + timedelta(seconds=30)
    lease.hold(session, NAME, seconds=60, holder="mine", now=later)
    session.expire_all()
    second = session.get(models.ProcessLease, NAME).expires_at

    assert second > first
    session.close()


def test_an_expired_lease_is_claimable_by_someone_else(file_db):
    """The failover path: no supervisor, no restart — the survivor's own
    already-running loop simply wins the next attempt."""
    session = file_db()
    dead = _now() - timedelta(hours=1)
    assert lease.hold(session, NAME, seconds=60, holder="the-dead-worker",
                      now=dead) is True
    assert lease.held_by(session, NAME) is None, (
        "a lapsed claim is nobody's, and reports as nobody's")

    assert lease.hold(session, NAME, seconds=60, holder="the-survivor") is True
    assert lease.held_by(session, NAME) == "the-survivor"
    session.close()


def test_a_steady_state_follower_costs_no_failing_insert(file_db):
    """The follower path must not reach the INSERT at all.

    `hold` used to fall straight from a losing UPDATE to the INSERT, so a
    follower issued a guaranteed primary-key violation on every tick — three
    write statements and a constraint error a minute, per worker, forever, which
    Postgres logs as an ERROR. That is precisely the per-tick waste and
    confusing log line this module exists to remove, reintroduced one layer
    down, and the naive test ("does hold() return False?") passes throughout.

    So this asserts on the statements, not the answer.
    """
    session = file_db()
    assert lease.hold(session, NAME, seconds=60, holder="leader") is True

    seen: list[str] = []
    follower = file_db()
    event.listen(follower.get_bind(), "before_cursor_execute",
                 lambda conn, cur, stmt, *a: seen.append(stmt.split()[0].upper()))

    assert lease.hold(follower, NAME, seconds=60, holder="follower") is False

    assert "INSERT" not in seen, (
        f"a follower must never attempt the INSERT; statements were {seen}")
    assert seen.count("UPDATE") == 1, f"one conditional UPDATE, got {seen}"
    follower.close()
    session.close()


def test_renewing_does_not_move_acquired_at(file_db):
    """`acquired_at` answers "how long has this leader led", so a renewal must
    not touch it.

    Writing `now` unconditionally made the column mean "when did the current
    leader last renew" — a value bounded above by one tick, and never the
    question models.ProcessLease says it is there for. Taking over from another
    holder *must* still move it.
    """
    session = file_db()
    t0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    assert lease.hold(session, NAME, seconds=60, holder="a", now=t0) is True
    first = _aware(session.execute(
        select(models.ProcessLease.acquired_at)
        .where(models.ProcessLease.name == NAME)).scalar_one())

    # Four hours of uninterrupted renewals by the same holder.
    for minutes in (1, 60, 240):
        assert lease.hold(session, NAME, seconds=60, holder="a",
                          now=t0 + timedelta(minutes=minutes)) is True
    assert _aware(session.execute(
        select(models.ProcessLease.acquired_at)
        .where(models.ProcessLease.name == NAME)).scalar_one()) == first, (
        "a renewal must leave acquired_at where the leadership began")

    # A genuine handover does move it: the lease has lapsed and someone else takes it.
    later = t0 + timedelta(minutes=400)
    assert lease.hold(session, NAME, seconds=60, holder="b", now=later) is True
    assert _aware(session.execute(
        select(models.ProcessLease.acquired_at)
        .where(models.ProcessLease.name == NAME)).scalar_one()) == later


def test_two_leases_with_different_names_do_not_contend(file_db):
    """The reason this is a lease table and not a scheduler flag: the audit log
    will want its own single writer without waiting on the sync scheduler."""
    session = file_db()
    assert lease.hold(session, "one", seconds=60, holder="a") is True
    assert lease.hold(session, "two", seconds=60, holder="b") is True
    assert lease.held_by(session, "one") == "a"
    assert lease.held_by(session, "two") == "b"
    session.close()


def test_holding_is_visible_to_another_connection_immediately(file_db):
    """A claim nobody else can read is not a claim. ``hold`` commits for exactly
    this reason — the follower reads through its own connection."""
    holder_session = file_db()
    reader_session = file_db()
    try:
        assert lease.hold(holder_session, NAME, seconds=60, holder="mine") is True
        assert lease.held_by(reader_session, NAME) == "mine"
    finally:
        holder_session.close()
        reader_session.close()


# ── the scheduler loop behind it ─────────────────────────────────────────────

@pytest.fixture()
def scheduled(file_db, monkeypatch):
    """An organization due for a sync, with the pull itself stubbed out."""
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    dispatched: list[str] = []
    monkeypatch.setattr(jobs, "thread_dispatch",
                        lambda run_id, since, full, conn: dispatched.append(run_id))

    session = file_db()
    ensure_org_and_users(session)
    session.add(models.ZohoConnection(organization_id=ORG, label="SLS",
                                      zoho_organization_id="z1", enabled=True))
    session.commit()
    session.close()
    return dispatched


def test_two_workers_ticking_together_queue_one_sync(file_db, scheduled, monkeypatch):
    """The defect this closes: the follower must not even ask.

    ``start_sync`` is counted rather than the runs, because the runs were
    already right — the unique index saw to that. What was wrong was that the
    second worker spent a query and logged a confusing line to be told so.
    """
    asked: list[str] = []
    real_start = jobs.start_sync

    def counting_start(session, organization_id, **kw):
        asked.append(organization_id)
        return real_start(session, organization_id, **kw)

    monkeypatch.setattr(jobs, "start_sync", counting_start)

    a, b = file_db(), file_db()
    try:
        assert scheduler.run_once(a, holder="worker-a") is True
        assert scheduler.run_once(b, holder="worker-b") is False
    finally:
        a.close()
        b.close()

    assert len(asked) == 1, "the follower did not reach start_sync at all"
    assert len(scheduled) == 1, "and exactly one pull was dispatched"

    session = file_db()
    assert session.scalars(select(models.SyncRun)).all().__len__() == 1
    session.close()


def test_the_leader_keeps_leading_across_ticks(file_db, scheduled):
    """Leadership that alternated would put both workers back on the button."""
    a, b = file_db(), file_db()
    try:
        for _ in range(3):
            assert scheduler.run_once(a, holder="worker-a") is True
            assert scheduler.run_once(b, holder="worker-b") is False
    finally:
        a.close()
        b.close()


def test_the_follower_takes_over_when_the_leader_stops_renewing(file_db, scheduled):
    """No supervisor: the survivor's loop was running all along."""
    a, b = file_db(), file_db()
    try:
        assert scheduler.run_once(a, holder="worker-a") is True
        assert scheduler.run_once(b, holder="worker-b") is False

        # worker-a's process dies. Nothing renews; the claim lapses.
        session = file_db()
        row = session.get(models.ProcessLease, scheduler.LEASE_NAME)
        row.expires_at = _now() - timedelta(seconds=1)
        session.commit()
        session.close()

        assert scheduler.run_once(b, holder="worker-b") is True
    finally:
        a.close()
        b.close()


def test_the_lease_lasts_longer_than_a_tick_and_not_much_longer():
    """The arithmetic in the module comment, pinned.

    Shorter than a tick and the leader loses its own lease between renewals and
    the workers trade leadership every minute. Much longer and a dead leader
    holds the schedule hostage.
    """
    assert scheduler.LEASE_SECONDS > scheduler.TICK_SECONDS
    assert scheduler.LEASE_SECONDS <= 5 * scheduler.TICK_SECONDS


def test_tick_still_answers_without_any_lease(scheduled):
    """``tick`` is a pure decision function and stays one. The lease lives in
    the loop around it, so a direct call — which is how every schedule test in
    ``test_auto_sync`` drives it — needs no lease and takes none."""
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    session = Maker()
    ensure_org_and_users(session)
    session.add(models.ZohoConnection(organization_id=ORG, label="SLS",
                                      zoho_organization_id="z1", enabled=True))
    session.commit()

    assert scheduler.tick(session) == 1
    session.commit()

    assert session.scalars(select(models.ProcessLease)).all() == [], (
        "tick must not reach for a lease; a loop decides who ticks")
    session.close()


# ── what health says about a follower ────────────────────────────────────────

@pytest.fixture()
def scheduler_check(file_db, monkeypatch):
    """The registered scheduler check, driven against a real session factory,
    with a live thread standing in for a started scheduler."""
    from app.observability.health import health, register_health_checks

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    started = threading.Event()
    started.set()
    monkeypatch.setattr(scheduler, "_started", started)

    saved = dict(health._components)
    health._components.clear()
    register_health_checks(object(), file_db)
    check = health._components["scheduler"].check_fn

    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, name="sync-scheduler", daemon=True)
    thread.start()
    try:
        yield check
    finally:
        stop.set()
        thread.join(timeout=5)
        health._components.clear()
        health._components.update(saved)


def test_a_follower_is_healthy_and_says_who_leads(scheduler_check, file_db):
    """Following is the normal state for every worker but one. Reporting it as
    a fault would make the component amber on most workers and flap depending on
    which one answered."""
    from app.observability.health import HealthStatus

    session = file_db()
    lease.hold(session, scheduler.LEASE_NAME, seconds=60, holder="worker-a")
    session.close()

    status, message = scheduler_check()

    assert status is HealthStatus.HEALTHY, message
    assert "worker-a" in message, "an operator reading two workers needs to be able to tell one story from two"
    assert "following" in message


def test_the_leader_says_so(scheduler_check, file_db):
    from app.observability.health import HealthStatus

    session = file_db()
    lease.hold(session, scheduler.LEASE_NAME, seconds=60)
    session.close()

    status, message = scheduler_check()

    assert status is HealthStatus.HEALTHY, message
    assert "this worker holds the lease" in message


def test_an_unheld_lease_is_named_rather_than_smoothed_over(scheduler_check):
    """The gap between a leader lapsing and the next tick claiming. Healthy —
    the thread is up — but the message must not imply someone is scheduling."""
    from app.observability.health import HealthStatus

    status, message = scheduler_check()

    assert status is HealthStatus.HEALTHY, message
    assert "unheld" in message


def test_health_never_claims_the_lease(scheduler_check, file_db):
    """A check that elected a leader by being asked how things are would make
    the answer depend on who asked first."""
    scheduler_check()

    session = file_db()
    assert session.scalars(select(models.ProcessLease)).all() == []
    session.close()
