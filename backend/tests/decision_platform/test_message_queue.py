"""The durable queue: what a message does, and what happens when it cannot.

The property that justifies the table at all is the first test here — a message
enqueued by one process is run by another, because it is a committed row rather
than a thread. Everything after it is the short list of ways a queue rots:
work run twice, work held forever by a dead worker, work retried without bound,
work dropped silently.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.clock import now as _now
from app.domain import models
from app.messaging import handlers, queue, worker


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test owns the registry. A fake handler leaking into the next module
    would make a real topic run something invented here."""
    handlers.clear()
    yield
    handlers.clear()


def _maker(engine):
    """A second session factory over the same database — the stand-in for
    "another process" in a test that cannot start one."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


# ── the property the table exists for ────────────────────────────────────────

def test_a_message_survives_the_process_that_enqueued_it(engine):
    """Enqueue on one session, commit, run it from a different one.

    This is the whole difference from ``thread_dispatch``: the thread is the
    process, and a run queued when the container is replaced is never done.
    """
    enqueuer = _maker(engine)()
    queue.enqueue(enqueuer, "work.do", {"n": 1})
    enqueuer.commit()
    enqueuer.close()

    ran = []
    handlers.register("work.do", lambda payload: ran.append(payload["n"]))

    consumer = _maker(engine)()
    assert worker.drain_once(consumer, worker="w1", heartbeat_seconds=0) == 1
    assert ran == [1]
    message = consumer.scalars(select(models.QueuedMessage)).one()
    assert message.status == "DONE"
    assert message.attempts == 1
    consumer.close()


def test_a_second_worker_cannot_claim_a_message_that_is_already_claimed(session):
    """One message, one worker: the second finds an empty queue rather than a
    second run of the same job against the same rows."""
    queue.enqueue(session, "work.do", {"n": 1})
    session.commit()

    assert queue.claim(session, worker="w1") is not None
    assert queue.claim(session, worker="w2") is None


def test_the_claim_is_decided_by_the_update_not_by_the_read(session):
    """The mechanism behind the test above, asserted directly.

    Selecting the oldest due message and then updating it is a race with a
    window wide enough to lose — two workers read the same row and both start
    the job. So the update carries ``status == 'PENDING'`` and the winner is
    whoever changes a row. This is that condition: replaying the claim's own
    UPDATE against an already-claimed message changes nothing.

    Asserted as a statement rather than by racing two threads, because the
    SQLite test fixture hands every session one shared connection (StaticPool)
    — a thread race over that tests the fixture, not the queue.
    """
    queue.enqueue(session, "work.do", {})
    session.commit()
    message = queue.claim(session, worker="w1")

    result = session.execute(
        update(models.QueuedMessage)
        .where(models.QueuedMessage.message_id == message.message_id,
               models.QueuedMessage.status == "PENDING")
        .values(status="CLAIMED", claimed_by="w2"))
    session.commit()
    assert result.rowcount == 0
    assert session.get(models.QueuedMessage,
                       message.message_id).claimed_by == "w1"


def test_the_same_work_queued_twice_is_one_message(session):
    """``dedupe_key`` names the work, not the request — the same answer
    ``start_sync`` gives a second click."""
    first = queue.enqueue(session, "sync.run", {"a": 1}, dedupe_key="sync:org1")
    second = queue.enqueue(session, "sync.run", {"a": 2}, dedupe_key="sync:org1")
    session.commit()
    assert first.message_id == second.message_id
    assert queue.depth(session)["PENDING"] == 1


def test_a_finished_message_does_not_block_the_next_one(session):
    """Dedupe holds only while the work is in flight. A sync that finished an
    hour ago must not stop the next one being queued."""
    first = queue.enqueue(session, "sync.run", {}, dedupe_key="sync:org1")
    session.commit()
    queue.complete(session, first)

    second = queue.enqueue(session, "sync.run", {}, dedupe_key="sync:org1")
    session.commit()
    assert second.message_id != first.message_id


# ── failure, bounded and visible ─────────────────────────────────────────────

def test_a_failing_handler_is_retried_then_dead_lettered(session):
    """Three attempts, growing delays, then a row that says what went wrong.

    Dead-lettered rather than dropped: a queue that silently loses work is
    worse than one that visibly fails to do it.
    """
    attempts = []

    def explode(_payload):
        attempts.append(1)
        raise RuntimeError("Zoho said no")

    handlers.register("work.do", explode)
    queue.enqueue(session, "work.do", {}, max_attempts=3)
    session.commit()

    for _ in range(3):
        message = queue.claim(session, worker="w1", now=_now() + timedelta(hours=1))
        assert message is not None
        worker.run_message(session, message, heartbeat_seconds=0)

    assert len(attempts) == 3
    row = session.get(models.QueuedMessage, message.message_id)
    assert row.status == "DEAD_LETTER"
    assert "Zoho said no" in row.last_error
    assert queue.depth(session)["DEAD_LETTER"] == 1


def test_a_failed_message_waits_before_its_next_attempt(session):
    """Backoff is real, not decorative: a handler that failed because a rate
    limiter said no must not be tried again a millisecond later."""
    handlers.register("work.do", lambda _p: (_ for _ in ()).throw(RuntimeError("no")))
    queue.enqueue(session, "work.do", {}, max_attempts=3)
    session.commit()

    message = queue.claim(session, worker="w1")
    worker.run_message(session, message, heartbeat_seconds=0)

    row = session.get(models.QueuedMessage, message.message_id)
    assert row.status == "PENDING"
    assert row.available_at > _now()
    # And it is genuinely not claimable yet.
    assert queue.claim(session, worker="w1") is None


def test_an_unknown_topic_fails_once_rather_than_three_times(session):
    """A message this process cannot run is a deployment mismatch. Waiting does
    not fix it, and three identical failures only delay the reader."""
    queue.enqueue(session, "work.nobody-registered", {}, max_attempts=3)
    session.commit()

    message = queue.claim(session, worker="w1")
    assert worker.run_message(session, message, heartbeat_seconds=0) is False

    row = session.get(models.QueuedMessage, message.message_id)
    assert row.status == "DEAD_LETTER"
    assert row.attempts == 1
    assert "no handler registered" in row.last_error


# ── a worker that dies ───────────────────────────────────────────────────────

def test_a_message_held_by_a_dead_worker_goes_back_to_the_queue(session):
    """Without this a killed process holds work forever and nothing says so —
    the same failure ``SyncRun.heartbeat_at`` exists to prevent."""
    handlers.register("work.do", lambda _p: None)
    queue.enqueue(session, "work.do", {}, max_attempts=3)
    session.commit()

    message = queue.claim(session, worker="dead-worker")
    assert message.status == "CLAIMED"

    # Its worker stopped reporting. (Zero patience rather than a future clock,
    # so the requeued message is available *now* and the next line is the real
    # next pass rather than one an hour from here.)
    assert queue.reap_stale(session, stale_after=timedelta(seconds=0)) == 1
    row = session.get(models.QueuedMessage, message.message_id)
    assert row.status == "PENDING"
    assert row.claimed_by is None

    # And the work actually happens on the next pass.
    assert worker.drain_once(session, worker="live", heartbeat_seconds=0) == 1
    assert session.get(models.QueuedMessage, message.message_id).status == "DONE"


def test_a_live_worker_keeps_its_message(session):
    """Reaping a message whose worker is still beating would run the job twice.
    The heartbeat is what separates slow from dead."""
    handlers.register("work.do", lambda _p: None)
    queue.enqueue(session, "work.do", {})
    session.commit()
    message = queue.claim(session, worker="w1")

    queue.heartbeat(session, message)
    assert queue.reap_stale(session) == 0
    assert session.get(models.QueuedMessage, message.message_id).status == "CLAIMED"


def test_a_dead_worker_with_no_attempts_left_is_dead_lettered(session):
    """A handler that kills its worker every time must become readable, not
    cycle through the queue forever."""
    queue.enqueue(session, "work.do", {}, max_attempts=1)
    session.commit()
    message = queue.claim(session, worker="doomed")

    queue.reap_stale(session, now=_now() + timedelta(hours=1))
    row = session.get(models.QueuedMessage, message.message_id)
    assert row.status == "DEAD_LETTER"
    assert "stopped reporting" in row.last_error


# ── scheduling and ordering ──────────────────────────────────────────────────

def test_a_delayed_message_is_not_claimed_early(session):
    queue.enqueue(session, "work.do", {}, delay_seconds=600)
    session.commit()
    assert queue.claim(session, worker="w1") is None
    assert queue.claim(session, worker="w1", now=_now() + timedelta(hours=1)) is not None


def test_the_oldest_due_message_is_claimed_first(session):
    first = queue.enqueue(session, "work.do", {"n": 1})
    second = queue.enqueue(session, "work.do", {"n": 2}, delay_seconds=1)
    session.commit()

    claimed = queue.claim(session, worker="w1", now=_now() + timedelta(minutes=1))
    assert claimed.message_id == first.message_id
    assert queue.claim(session, worker="w1",
                       now=_now() + timedelta(minutes=1)).message_id == second.message_id


def test_a_worker_can_be_restricted_to_its_own_topics(session):
    """So an API process draining nothing but light work cannot pick up an
    hour-long sync it has no business starting."""
    queue.enqueue(session, "sync.run", {})
    session.commit()
    assert queue.claim(session, worker="w1", topics=("mail.send",)) is None
    assert queue.claim(session, worker="w1", topics=("sync.run",)) is not None


def test_depth_counts_every_status(session):
    handlers.register("work.do", lambda _p: None)
    queue.enqueue(session, "work.do", {})
    queue.enqueue(session, "work.do", {}, organization_id="org1")
    session.commit()

    assert queue.depth(session)["PENDING"] == 2
    assert queue.depth(session, organization_id="org1")["PENDING"] == 1

    worker.drain_once(session, worker="w1", heartbeat_seconds=0)
    counts = queue.depth(session)
    assert counts["DONE"] == 2 and counts["PENDING"] == 0


# ── the registry ─────────────────────────────────────────────────────────────

def test_two_handlers_for_one_topic_is_refused(session):
    """The loser would be a job that silently never runs."""
    handlers.register("work.do", lambda _p: None)
    with pytest.raises(ValueError):
        handlers.register("work.do", lambda _p: None)


def test_the_sync_topic_is_registered_by_the_module_that_owns_the_work():
    """``ingestion.jobs`` owns ``sync.run``; the queue knows nothing about
    syncs. A worker that never imported it would fail every sync message as an
    unknown topic, which is why ``load_handlers`` names the module."""
    worker.load_handlers()
    from app.ingestion import jobs

    assert jobs.SYNC_TOPIC in handlers.topics()


# ── the producer: a background sync, dispatched through the queue ────────────

def test_a_sync_dispatched_through_the_queue_is_run_from_the_message(
        engine, session, monkeypatch):
    """The end of the wire. ``start_sync`` commits its run row and a message;
    a worker — here, on a different session, as a second process would be —
    claims that message and runs the same job the thread dispatch runs.
    """
    from app.config import settings
    from app.ingestion import jobs

    maker = _maker(engine)
    monkeypatch.setattr(settings, "SYNC_DISPATCH", "queue")
    monkeypatch.setattr("app.db.SessionLocal", maker)
    ran = []
    # The job itself is stubbed; the handler around it is the real one, so the
    # payload really does have to survive the round trip through JSON.
    monkeypatch.setattr(jobs, "run_job", lambda *args, **kw: ran.append(args))
    worker.load_handlers()

    run, started = jobs.start_sync(session, "org-1", connection_id="conn-a")
    assert started
    # Nothing has run yet: the dispatch's whole job was to commit a row.
    assert ran == []
    assert queue.depth(session)["PENDING"] == 1

    consumer = maker()
    assert worker.drain_once(consumer, worker="w1", heartbeat_seconds=0) == 1
    consumer.close()
    assert ran and ran[0][0] == run.sync_run_id


def test_the_default_dispatch_is_the_thread_unless_asked_otherwise(monkeypatch):
    """A queue dispatch with nothing draining it makes the Sync button do
    nothing, so the default stays what this deployment has always done."""
    from app.config import settings
    from app.ingestion import jobs

    monkeypatch.setattr(settings, "SYNC_DISPATCH", "thread")
    assert jobs.default_dispatch() is jobs.thread_dispatch
    monkeypatch.setattr(settings, "SYNC_DISPATCH", "queue")
    assert jobs.default_dispatch() is jobs.queue_dispatch


def test_a_whole_organization_rebuild_can_be_queued(engine, session, monkeypatch):
    """The queue's second producer: ``POST /commercial/recompute``.

    A whole-organization rebuild is O(customers × items) with a detector pass
    per pair — the shape the sync had before it moved to the background, and it
    fails the same way, with a gateway giving up before the work does.
    """
    from app.commercial import jobs as commercial_jobs

    maker = _maker(engine)
    monkeypatch.setattr("app.db.SessionLocal", maker)
    ran = []
    monkeypatch.setattr("app.commercial.compute.recompute",
                        lambda s, org, **kw: ran.append((org, kw)) or _Report())
    worker.load_handlers()

    commercial_jobs.enqueue_recompute(session, "org-1", emit_signals=False)
    session.commit()
    assert ran == []                       # committed, not run

    consumer = maker()
    assert worker.drain_once(consumer, worker="w1", heartbeat_seconds=0) == 1
    consumer.close()
    assert ran and ran[0][0] == "org-1"
    assert ran[0][1]["emit_signals"] is False


class _Report:
    def to_dict(self):
        return {"ok": True}


def test_a_rebuild_queued_twice_while_pending_is_one_rebuild(session):
    """Two clicks are one pass over the same rows — the answer ``start_sync``
    gives, for the same reason."""
    from app.commercial import jobs as commercial_jobs

    first = commercial_jobs.enqueue_recompute(session, "org-1")
    second = commercial_jobs.enqueue_recompute(session, "org-1")
    session.commit()
    assert first.message_id == second.message_id

    # A different scope is different work.
    scoped = commercial_jobs.enqueue_recompute(session, "org-1", customer_id="c1")
    session.commit()
    assert scoped.message_id != first.message_id
