"""The broker: every state a queued message can be in, and the only code that
moves it between them.

    PENDING --claim--> CLAIMED --complete--> DONE
       ^                  |
       |                  +--fail (attempts left)--> PENDING, later
       |                  |
       |                  +--fail (attempts spent)--> DEAD_LETTER
       |                  |
       +---- reap_stale --+   (the worker holding it stopped reporting)

The two things that make this safe to run from more than one process:

**The claim is a conditional UPDATE, not a read-then-write.** Selecting the
oldest due message and then updating it is a race with a window wide enough to
lose: two workers read the same row, both start the same sync, and the second
one's work fights the first for the same tables. So the update carries
``status == 'PENDING'`` in its WHERE clause and the winner is decided by which
one changes a row. A zero-row update is not an error — it is the other worker
having got there first, and the loop simply looks for the next message.

**A worker that dies does not take its message with it.** ``heartbeat_at`` is
touched while the handler runs; a CLAIMED message that has stopped reporting
for longer than ``STALE_AFTER`` is returned to PENDING by :func:`reap_stale`.
This is the same mechanism, for the same reason, as ``SyncRun.heartbeat_at``:
without it a killed process leaves work held forever by nobody.

Retries back off exponentially and are bounded. A message that exhausts its
attempts is DEAD_LETTER with its last error attached — visible in the table
and in ``/api/health`` — rather than dropped, because a queue that silently
loses work is worse than one that visibly fails to do it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..clock import aware as _aware, now as _now
from ..config import settings
from ..domain import models

log = logging.getLogger("pie_portal.queue")

#: States a message is still going through; anything else is terminal.
ACTIVE = ("PENDING", "CLAIMED")
PENDING = "PENDING"
CLAIMED = "CLAIMED"
DONE = "DONE"
DEAD_LETTER = "DEAD_LETTER"

#: How long a CLAIMED message may go without a heartbeat before it is treated
#: as a dead worker's. Deliberately generous, and for the reason
#: ``ingestion.jobs.STALE_AFTER`` gives: a slow pull is not a crash, and
#: reclaiming a live message would run the same job twice.
STALE_AFTER = timedelta(minutes=int(settings.QUEUE_STALE_MINUTES))

#: How long the first retry waits. Each further attempt doubles it, up to
#: ``MAX_BACKOFF``. A handler that failed because Zoho was throttling wants
#: tens of seconds, not one.
BACKOFF_SECONDS = float(settings.QUEUE_BACKOFF_SECONDS)
MAX_BACKOFF_SECONDS = float(settings.QUEUE_MAX_BACKOFF_SECONDS)

#: How many times the claim is retried when another worker wins the race.
#: Small: contention means there is work being done, and the loop will come
#: back around in a moment anyway.
_CLAIM_ATTEMPTS = 5


def enqueue(session: Session, topic: str, payload: Optional[Dict[str, Any]] = None, *,
            organization_id: Optional[str] = None,
            dedupe_key: Optional[str] = None,
            delay_seconds: float = 0.0,
            max_attempts: Optional[int] = None,
            now: Optional[datetime] = None) -> models.QueuedMessage:
    """Record a request to do work, and return the message that will do it.

    Does **not** commit: the caller decides the transaction, because the point
    of a durable queue is that the message lands in the same commit as whatever
    made it necessary. A sync run row and the message that runs it must be
    committed together or a crash between them leaves one without the other.

    ``dedupe_key`` names the work rather than the request. While a message with
    that key is PENDING or CLAIMED, this returns *that* message instead of
    adding a second — the same answer ``jobs.start_sync`` gives a second click,
    for the same reason.
    """
    now = now or _now()
    if dedupe_key:
        existing = session.scalar(
            select(models.QueuedMessage)
            .where(models.QueuedMessage.dedupe_key == dedupe_key,
                   models.QueuedMessage.status.in_(ACTIVE))
            .order_by(models.QueuedMessage.created_at.asc())
            .limit(1))
        if existing is not None:
            log.debug("queue: %s already queued as %s", dedupe_key,
                      existing.message_id)
            return existing

    message = models.QueuedMessage(
        topic=topic,
        payload=dict(payload or {}),
        organization_id=organization_id,
        status=PENDING,
        dedupe_key=dedupe_key,
        available_at=now + timedelta(seconds=max(0.0, delay_seconds)),
        attempts=0,
        max_attempts=int(max_attempts if max_attempts is not None
                         else settings.QUEUE_MAX_ATTEMPTS),
        created_at=now,
    )
    session.add(message)
    session.flush()          # so the caller has an id before its own commit
    return message


def claim(session: Session, *, worker: str, topics: Optional[tuple] = None,
          now: Optional[datetime] = None) -> Optional[models.QueuedMessage]:
    """Take ownership of the oldest due message, or return None.

    Commits: a claim that is not committed is not a claim, and the handler that
    follows may run for minutes on this same session. Committing here also
    keeps the write transaction short, which is what §4 asks of anything that
    writes while others read.
    """
    now = now or _now()
    for _ in range(_CLAIM_ATTEMPTS):
        query = (
            select(models.QueuedMessage.message_id)
            .where(models.QueuedMessage.status == PENDING,
                   models.QueuedMessage.available_at <= now)
            .order_by(models.QueuedMessage.available_at.asc(),
                      models.QueuedMessage.created_at.asc())
            .limit(1))
        if topics:
            query = query.where(models.QueuedMessage.topic.in_(tuple(topics)))
        message_id = session.scalar(query)
        if message_id is None:
            return None

        # The race is decided here: whoever changes the row owns the message.
        result = session.execute(
            update(models.QueuedMessage)
            .where(models.QueuedMessage.message_id == message_id,
                   models.QueuedMessage.status == PENDING)
            .values(status=CLAIMED, claimed_by=worker, claimed_at=now,
                    heartbeat_at=now,
                    attempts=models.QueuedMessage.attempts + 1))
        session.commit()
        if result.rowcount:
            message = session.get(models.QueuedMessage, message_id)
            log.info("queue: %s claimed %s (%s, attempt %d/%d)", worker,
                     message.message_id, message.topic, message.attempts,
                     message.max_attempts)
            return message
        # Lost the race. Look again — there may be another message due.
    return None


def heartbeat(session: Session, message: models.QueuedMessage, *,
              now: Optional[datetime] = None) -> None:
    """Say the worker still holds this message. Commits, so it is visible to
    the reaper in another process — an uncommitted heartbeat is invisible
    exactly to the reader it exists for."""
    session.execute(
        update(models.QueuedMessage)
        .where(models.QueuedMessage.message_id == message.message_id)
        .values(heartbeat_at=now or _now()))
    session.commit()


def complete(session: Session, message: models.QueuedMessage, *,
             now: Optional[datetime] = None) -> None:
    """The handler returned. Commits."""
    now = now or _now()
    session.execute(
        update(models.QueuedMessage)
        .where(models.QueuedMessage.message_id == message.message_id)
        .values(status=DONE, finished_at=now, heartbeat_at=now))
    session.commit()
    log.info("queue: %s done (%s)", message.message_id, message.topic)


def retry_delay(attempts: int) -> float:
    """Seconds before the next attempt: exponential, capped, never negative."""
    if attempts <= 0:
        return BACKOFF_SECONDS
    return min(BACKOFF_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)


def fail(session: Session, message: models.QueuedMessage, error: str, *,
         terminal: bool = False, now: Optional[datetime] = None) -> str:
    """The handler raised. Retry it later, or dead-letter it. Commits.

    Returns the status the message ended in, so the caller can log which of the
    two happened without re-reading the row.

    ``terminal`` for a failure that waiting cannot fix — an unknown topic is
    the one today. Retrying that three times produces three identical rows and
    delays the moment somebody reads the real reason.
    """
    now = now or _now()
    # Read the attempt count from the row rather than the object: the claim
    # incremented it with a SQL expression, and this session may be holding a
    # copy from before that.
    attempts = session.scalar(
        select(models.QueuedMessage.attempts)
        .where(models.QueuedMessage.message_id == message.message_id)) or 0
    max_attempts = message.max_attempts or int(settings.QUEUE_MAX_ATTEMPTS)
    text = (error or "")[:2000]

    if terminal or attempts >= max_attempts:
        session.execute(
            update(models.QueuedMessage)
            .where(models.QueuedMessage.message_id == message.message_id)
            .values(status=DEAD_LETTER, last_error=text, finished_at=now))
        session.commit()
        log.error("queue: %s dead-lettered after %d attempt(s) (%s): %s",
                  message.message_id, attempts, message.topic, text)
        return DEAD_LETTER

    delay = retry_delay(attempts)
    session.execute(
        update(models.QueuedMessage)
        .where(models.QueuedMessage.message_id == message.message_id)
        .values(status=PENDING, last_error=text, claimed_by=None,
                claimed_at=None,
                available_at=now + timedelta(seconds=delay)))
    session.commit()
    log.warning("queue: %s failed (attempt %d/%d, retrying in %.0fs): %s",
                message.message_id, attempts, max_attempts, delay, text)
    return PENDING


def reap_stale(session: Session, *, now: Optional[datetime] = None,
               stale_after: Optional[timedelta] = None) -> int:
    """Return messages held by workers that stopped reporting. Commits.

    A message whose attempts are already spent is dead-lettered instead of
    requeued: it has had its chances, and cycling it forever would hide a
    handler that kills its worker every time it runs.
    """
    now = now or _now()
    # `is None`, not `or`: a zero timedelta is falsy, and "reap everything
    # claimed" is a legitimate thing to ask for — silently substituting ten
    # minutes for it would be a check that reports success without looking.
    stale_after = STALE_AFTER if stale_after is None else stale_after
    cutoff = now - stale_after

    rows = session.scalars(
        select(models.QueuedMessage)
        .where(models.QueuedMessage.status == CLAIMED)).all()
    reaped = 0
    for message in rows:
        last = (_aware(message.heartbeat_at) or _aware(message.claimed_at)
                or _aware(message.created_at) or now)
        if last > cutoff:
            continue
        held_by = message.claimed_by
        if (message.attempts or 0) >= (message.max_attempts or 1):
            message.status = DEAD_LETTER
            message.finished_at = now
            message.last_error = (
                "The worker holding this message stopped reporting, and its "
                "attempts were spent.")
            log.error("queue: %s dead-lettered — worker %s went silent",
                      message.message_id, held_by)
        else:
            message.status = PENDING
            message.claimed_by = None
            message.claimed_at = None
            message.available_at = now
            message.last_error = (
                "The worker holding this message stopped reporting; it was "
                "returned to the queue.")
            log.warning("queue: %s requeued — worker %s went silent",
                        message.message_id, held_by)
        reaped += 1
    if reaped:
        session.commit()
    return reaped


def requeue(session: Session, message: models.QueuedMessage, *,
            now: Optional[datetime] = None) -> models.QueuedMessage:
    """Put a dead-lettered message back on the queue. Commits.

    The other half of dead-lettering. Without it "fix the cause and try again"
    is advice that ends at a hand-written UPDATE, which is how failed work
    quietly becomes permanent — and a queue whose only exit from failure is a
    DBA is a queue that loses work slowly instead of quickly.

    Attempts go back to zero: the operator is asserting that the cause is
    fixed, so this is a first attempt at work that now has a chance, not a
    fourth at work that does not. ``last_error`` is kept — it is the record of
    why this needed a person, and the next failure overwrites it anyway.

    Only a terminal message may be requeued. One that is still PENDING or
    CLAIMED is either waiting or running, and "retrying" it would mean two
    workers on one job.
    """
    now = now or _now()
    if message.status in ACTIVE:
        raise ValueError(
            f"message {message.message_id} is {message.status}; it is already "
            "queued or in flight, and requeuing it would run it twice")
    session.execute(
        update(models.QueuedMessage)
        .where(models.QueuedMessage.message_id == message.message_id)
        .values(status=PENDING, attempts=0, available_at=now, claimed_by=None,
                claimed_at=None, heartbeat_at=None, finished_at=None))
    session.commit()
    session.refresh(message)
    log.info("queue: %s requeued by hand (%s)", message.message_id, message.topic)
    return message


def prune(session: Session, *, now: Optional[datetime] = None,
          done_days: Optional[int] = None,
          dead_letter_days: Optional[int] = None) -> Dict[str, int]:
    """Delete terminal messages older than their retention. Commits.

    Rows are kept after completion on purpose — a queue with no history cannot
    answer "did that run, and when" — but "kept" and "kept forever" are
    different promises, and only one of them is a table that stops growing. At
    one sync per organization per cadence this is the largest table in the
    database within a year, holding rows nobody has read since the day they
    finished.

    The two retentions differ because the rows mean different things. A DONE
    message is a receipt; a DEAD_LETTER is unfinished business somebody may
    still act on, so it is kept far longer and deleting one is deleting the
    evidence of a failure. Either ``0`` means keep forever.

    Deletes rather than archives: these are derived rows about work whose real
    record lives elsewhere (a ``SyncRun``, a metrics table). Nothing computes
    from them.
    """
    now = now or _now()
    done_days = (settings.QUEUE_DONE_RETENTION_DAYS if done_days is None
                 else done_days)
    dead_letter_days = (settings.QUEUE_DEAD_LETTER_RETENTION_DAYS
                        if dead_letter_days is None else dead_letter_days)

    removed = {DONE: 0, DEAD_LETTER: 0}
    for status, days in ((DONE, done_days), (DEAD_LETTER, dead_letter_days)):
        if days <= 0:
            continue
        cutoff = now - timedelta(days=days)
        result = session.execute(
            delete(models.QueuedMessage)
            .where(models.QueuedMessage.status == status,
                   # ``finished_at`` is set on both terminal transitions;
                   # ``created_at`` covers a row from before it was.
                   func.coalesce(models.QueuedMessage.finished_at,
                                 models.QueuedMessage.created_at) < cutoff))
        removed[status] = int(result.rowcount or 0)
    if any(removed.values()):
        session.commit()
        log.info("queue: pruned %d done and %d dead-lettered message(s)",
                 removed[DONE], removed[DEAD_LETTER])
    return removed


def depth(session: Session, *, organization_id: Optional[str] = None
          ) -> Dict[str, int]:
    """How many messages sit in each status — the queue's one health number.

    Grouped in the database rather than counted in Python: this is read by a
    health check, and a health check that loads the table is a health check
    that becomes the outage.
    """
    query = (select(models.QueuedMessage.status,
                    func.count(models.QueuedMessage.message_id))
             .group_by(models.QueuedMessage.status))
    if organization_id:
        query = query.where(
            models.QueuedMessage.organization_id == organization_id)
    counts = {status: 0 for status in (PENDING, CLAIMED, DONE, DEAD_LETTER)}
    for status, count in session.execute(query):
        counts[status] = int(count)
    return counts
