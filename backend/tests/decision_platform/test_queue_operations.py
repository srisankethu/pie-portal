"""Running the queue, rather than the queue itself.

``test_message_queue`` covers what a message does. This covers the parts an
operator touches and the parts that only exist in the real process: the worker
thread, retention, putting a failed message back, and the endpoints that make
any of it visible. Every one of these was a loose end — a queue whose failures
can only be fixed with an UPDATE, whose table never stops growing, and whose
only tested entry point is the seam beside the one production uses.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.clock import now as _now
from app.config import settings
from app.domain import models
from app.messaging import handlers, queue, worker


@pytest.fixture(autouse=True)
def clean_registry():
    handlers.clear()
    yield
    handlers.clear()
    worker.stop_worker()


def _maker(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


# ── the worker thread: the entry point production actually uses ──────────────

def test_the_worker_thread_starts_drains_and_stops(engine, session, monkeypatch):
    """Everything else is tested through ``drain_once``. This is the loop that
    calls it — started from ``main``'s lifespan, and until now never run by a
    test at all."""
    monkeypatch.setattr(settings, "SYNC_DISPATCH", "queue")
    monkeypatch.setattr(settings, "QUEUE_WORKER", "1")
    monkeypatch.setattr(worker, "POLL_SECONDS", 0.05)
    monkeypatch.setattr("app.db.SessionLocal", _maker(engine))
    ran = []
    handlers.register("work.do", lambda payload: ran.append(payload["n"]))

    queue.enqueue(session, "work.do", {"n": 7}, organization_id="org1")
    session.commit()

    assert worker.start_worker() is True
    assert worker.worker_running() is True
    deadline = time.monotonic() + 5
    while not ran and time.monotonic() < deadline:
        time.sleep(0.05)

    assert ran == [7], "the worker thread never ran the queued message"
    worker.stop_worker()
    assert worker.worker_running() is False


def test_the_worker_declines_where_the_queue_is_not_the_dispatch(monkeypatch):
    """A worker polling a queue nothing writes to is a thread and a query per
    second buying nothing."""
    monkeypatch.setattr(settings, "SYNC_DISPATCH", "thread")
    monkeypatch.setattr(settings, "QUEUE_WORKER", "")
    assert worker.start_worker() is False
    assert worker.worker_running() is False


def test_a_worker_can_be_forced_on_beside_a_thread_dispatch(monkeypatch, engine):
    """``QUEUE_WORKER=1`` is how a dedicated worker process runs the same image
    as an API that dispatches differently."""
    monkeypatch.setattr(settings, "SYNC_DISPATCH", "thread")
    monkeypatch.setattr(settings, "QUEUE_WORKER", "1")
    monkeypatch.setattr(worker, "POLL_SECONDS", 0.05)
    monkeypatch.setattr("app.db.SessionLocal", _maker(engine))
    try:
        assert worker.start_worker() is True
    finally:
        worker.stop_worker()


def test_only_one_worker_thread_per_process(monkeypatch, engine):
    monkeypatch.setattr(settings, "QUEUE_WORKER", "1")
    monkeypatch.setattr(worker, "POLL_SECONDS", 0.05)
    monkeypatch.setattr("app.db.SessionLocal", _maker(engine))
    try:
        assert worker.start_worker() is True
        assert worker.start_worker() is False, "a second worker was started"
    finally:
        worker.stop_worker()


# ── retention: the table has to stop growing ─────────────────────────────────

def test_finished_messages_are_swept_out_after_their_retention(session):
    old = queue.enqueue(session, "work.do", {}, organization_id="org1")
    session.commit()
    queue.complete(session, old)
    old.finished_at = _now() - timedelta(days=30)
    session.commit()

    recent = queue.enqueue(session, "work.do", {}, organization_id="org1")
    session.commit()
    queue.complete(session, recent)

    removed = queue.prune(session, done_days=14, dead_letter_days=90)

    assert removed["DONE"] == 1
    remaining = session.scalars(select(models.QueuedMessage.message_id)).all()
    assert remaining == [recent.message_id]


def test_a_dead_letter_outlives_a_receipt(session):
    """They are kept for different reasons: a DONE row is a receipt, a
    dead-lettered one is unfinished business somebody may still act on."""
    dead = queue.enqueue(session, "work.do", {}, organization_id="org1")
    session.commit()
    queue.fail(session, dead, "boom", terminal=True)
    dead.finished_at = _now() - timedelta(days=30)
    session.commit()

    assert queue.prune(session, done_days=14, dead_letter_days=90)["DEAD_LETTER"] == 0
    assert queue.prune(session, done_days=14, dead_letter_days=14)["DEAD_LETTER"] == 1


def test_pruning_never_touches_work_still_to_do(session):
    """The bug this would be: a delete keyed on age rather than on state, and a
    queued job that has been waiting for its backoff disappears."""
    pending = queue.enqueue(session, "work.do", {}, organization_id="org1")
    pending.created_at = _now() - timedelta(days=365)
    session.commit()

    assert queue.prune(session, done_days=1, dead_letter_days=1) == {
        "DONE": 0, "DEAD_LETTER": 0}
    assert session.get(models.QueuedMessage, pending.message_id).status == "PENDING"


def test_retention_can_be_switched_off(session):
    done = queue.enqueue(session, "work.do", {}, organization_id="org1")
    session.commit()
    queue.complete(session, done)
    done.finished_at = _now() - timedelta(days=3650)
    session.commit()

    assert queue.prune(session, done_days=0, dead_letter_days=0)["DONE"] == 0


# ── the way back from a dead letter ──────────────────────────────────────────

def test_a_dead_lettered_message_can_be_put_back(session):
    """Otherwise "fix the cause and try again" ends at a hand-written UPDATE."""
    ran = []
    handlers.register("work.do", lambda _p: ran.append(1))
    message = queue.enqueue(session, "work.do", {}, organization_id="org1",
                            max_attempts=1)
    session.commit()
    claimed = queue.claim(session, worker="w1")
    queue.fail(session, claimed, "the cause, since fixed")

    assert session.get(models.QueuedMessage, message.message_id).status == "DEAD_LETTER"

    queue.requeue(session, message)
    row = session.get(models.QueuedMessage, message.message_id)
    assert row.status == "PENDING"
    assert row.attempts == 0, "a retry after a fix is a first attempt, not a fourth"
    assert "the cause, since fixed" in row.last_error, "the reason is the record"

    assert worker.drain_once(session, worker="w2", heartbeat_seconds=0) == 1
    assert ran == [1]


def test_work_still_in_flight_cannot_be_requeued(session):
    """Requeuing a claimed message would put two workers on one job."""
    queue.enqueue(session, "work.do", {}, organization_id="org1")
    session.commit()
    claimed = queue.claim(session, worker="w1")

    with pytest.raises(ValueError):
        queue.requeue(session, claimed)


# ── the operator's view ──────────────────────────────────────────────────────

def _as_owner(api_client):
    """The seeded owner's header. ``api_client`` is deliberately unauthenticated
    — every suite signs in for itself — and these endpoints are role-gated."""
    from app.seed import SEED_PASSWORD

    token = api_client.post("/api/v1/auth/login", json={
        "email": "s.menon@pie.example", "password": SEED_PASSWORD}).json()["token"]
    api_client.headers.update({"Authorization": f"Bearer {token}"})
    return api_client


def _session_for(api_client):
    """A session on the same database the client serves from."""
    from app.db import get_session

    return next(api_client.app.dependency_overrides[get_session]())


def _seed_queue(session):
    pending = queue.enqueue(session, "sync.run", {"secret": "payload"},
                            organization_id="org_pie")
    dead = queue.enqueue(session, "commercial.recompute", {},
                         organization_id="org_pie", max_attempts=1)
    other = queue.enqueue(session, "sync.run", {}, organization_id="org_other")
    session.commit()
    claimed = queue.claim(session, worker="w1", topics=("commercial.recompute",))
    queue.fail(session, claimed, "Zoho said no")
    return pending, dead, other


def test_the_queue_endpoint_reports_depth_and_dead_letters(api_client):
    client = _as_owner(api_client)
    s = _session_for(client)
    _pending, dead, _other = _seed_queue(s)
    s.commit()

    body = client.get("/api/v1/internal/queue").json()
    assert body["depth"]["PENDING"] == 1, body["depth"]
    assert body["depth"]["DEAD_LETTER"] == 1
    assert [m["message_id"] for m in body["dead_letters"]] == [dead.message_id]
    assert body["dispatch"] == settings.SYNC_DISPATCH
    # Another tenant's work is not this tenant's business.
    assert all(m["topic"] in ("sync.run", "commercial.recompute")
               for m in body["recent"])
    assert body["depth"]["PENDING"] + body["depth"]["CLAIMED"] == 1


def test_a_listing_does_not_print_payloads(api_client):
    """A listing that prints job arguments eventually prints something it
    should not; one message shows its payload on purpose."""
    client = _as_owner(api_client)
    s = _session_for(client)
    pending, _dead, _other = _seed_queue(s)
    s.commit()

    listed = client.get("/api/v1/internal/queue").json()
    assert all("payload" not in m for m in listed["recent"])

    one = client.get(f"/api/v1/internal/queue/{pending.message_id}").json()
    assert one["payload"] == {"secret": "payload"}


def test_another_tenants_message_reads_as_missing(api_client):
    client = _as_owner(api_client)
    s = _session_for(client)
    _pending, _dead, other = _seed_queue(s)
    s.commit()

    assert client.get(f"/api/v1/internal/queue/{other.message_id}").status_code == 404
    assert client.post(
        f"/api/v1/internal/queue/{other.message_id}/retry").status_code == 404


def test_retrying_over_http_puts_the_message_back(api_client):
    client = _as_owner(api_client)
    s = _session_for(client)
    _pending, dead, _other = _seed_queue(s)
    s.commit()

    body = client.post(f"/api/v1/internal/queue/{dead.message_id}/retry").json()
    assert body["status"] == "PENDING"
    assert body["attempts"] == 0

    # And a message that is not failed is refused rather than silently ignored.
    again = client.post(f"/api/v1/internal/queue/{dead.message_id}/retry")
    assert again.status_code == 409


def test_the_cache_counters_are_readable(api_client):
    """The other half of an optimisation: numbers nobody can see are numbers
    nobody can tell are working."""
    from app import cache

    client = _as_owner(api_client)
    probe = cache.register(cache.Cache("endpoint-probe"))
    probe.set("k", 1)
    probe.get("k")
    probe.get("nope")

    body = client.get("/api/v1/internal/observability/caches").json()
    named = {c["name"]: c for c in body["caches"]}
    # Only the probe is asserted on: which caches exist in a process depends on
    # what that process has imported, and this suite deliberately does not load
    # pie-parser. What the endpoint must do is report every registered cache.
    assert named["endpoint-probe"]["hits"] == 1
    assert named["endpoint-probe"]["misses"] == 1
    assert named["endpoint-probe"]["hit_rate"] == 0.5


# ── the dialect production runs ──────────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("PIE_TEST_DATABASE_URL"),
                    reason="needs a PostgreSQL server (PIE_TEST_DATABASE_URL)")
def test_two_processes_racing_on_postgres_run_one_message(engine):
    """The claim's atomicity, on the dialect production runs and with real
    connections.

    This cannot be asserted on the SQLite fixture: it hands every session one
    shared connection (StaticPool), so a thread race there tests the fixture.
    Under Postgres each session is a connection and the race is real — which is
    the whole reason the claim is a conditional UPDATE rather than a read
    followed by a write.
    """
    import threading

    maker = _maker(engine)
    setup = maker()
    queue.enqueue(setup, "work.do", {"n": 1}, organization_id="org1")
    setup.commit()
    setup.close()

    claimed: list[str] = []
    barrier = threading.Barrier(4)

    def race(name: str) -> None:
        s = maker()
        try:
            barrier.wait(timeout=10)
            message = queue.claim(s, worker=name)
            if message is not None:
                claimed.append(name)
        finally:
            s.close()

    threads = [threading.Thread(target=race, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(claimed) == 1, f"{len(claimed)} workers claimed one message"
