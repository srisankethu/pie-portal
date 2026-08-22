"""The worker: claim what is due, run it, record what happened.

A daemon thread in this process, for the reason ``ingestion/jobs.py`` gives
about threads — this deployment is one uvicorn process and pretending
otherwise would be a guarantee nothing enforces. The difference from a bare
thread, and the whole point of the queue behind it, is that the *request* is
committed to the database before the work starts: this loop is a consumer, not
the record. Kill the process mid-job and the message is still there, held by a
worker that no longer beats, and the next process's reaper hands it back to the
queue. Run two processes and the atomic claim in ``queue.claim`` decides which
one gets it.

While a handler runs, its message is kept alive by a heartbeat on a session of
its own. A sync takes minutes and the reaper's patience is ten, so a job that
does not report would be reclaimed and run a second time against the same rows
— the exact double-pull ``jobs.start_sync``'s lock exists to prevent.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Callable, Optional, Self

from sqlalchemy.orm import Session

from ..config import settings
from ..domain import models
from . import queue
from .handlers import UnknownTopic, handler_for

log = logging.getLogger("pie_portal.queue")

#: How often the loop looks for work when it found none last time. Waking is
#: one indexed query, so this is about how quickly a queued job starts, not
#: about load.
POLL_SECONDS = float(settings.QUEUE_POLL_SECONDS)

#: How often a running handler's message says it is still alive. Comfortably
#: inside ``queue.STALE_AFTER``: a heartbeat that beats as slowly as the
#: reaper's patience is a race, not a heartbeat.
HEARTBEAT_SECONDS = max(1.0, float(settings.QUEUE_STALE_MINUTES) * 60.0 / 10.0)

#: Set once a worker thread has been started in this process.
_started = threading.Event()
#: Set to stop it. Tests do; so does nothing else today.
_stop = threading.Event()

_THREAD_NAME = "queue-worker"


def worker_name() -> str:
    """Who claimed a message, in words that identify a process during an
    incident rather than a random string that identifies nothing."""
    return f"{os.getpid()}:{uuid.uuid4().hex[:6]}"


def load_handlers() -> None:
    """Import the modules that own a topic, so their registrations exist.

    A worker that never imports ``ingestion.jobs`` would claim a ``sync.run``
    message and fail it as an unknown topic. Listed here explicitly rather than
    discovered by scanning the package: a queue whose capabilities depend on
    import order is a queue that works until somebody removes an unrelated
    import.

    Registration is asked for rather than left to the import, because importing
    an already-imported module runs nothing — so a process that cleared or
    never populated the registry would import successfully and still have no
    handler.
    """
    from ..ingestion import jobs

    jobs.register_topics()


class _Beating:
    """Keeps one claimed message alive while its handler runs.

    Its own session, because the worker's is not thread-safe and the handler
    may be using it — and because an uncommitted heartbeat is invisible to the
    reaper, which is the only reader it has.
    """

    def __init__(self, message: models.QueuedMessage,
                 session_factory: Optional[Callable[[], Session]],
                 interval: float = HEARTBEAT_SECONDS) -> None:
        self._message = message
        self._factory = session_factory
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> Self:
        if self._factory is None or self._interval <= 0:
            return self                      # nothing to beat with; see below

        def beat() -> None:
            while not self._stop.wait(self._interval):
                try:
                    session = self._factory()
                    try:
                        queue.heartbeat(session, self._message)
                    finally:
                        session.close()
                except Exception:  # noqa: BLE001 — a missed beat is not a crash
                    log.exception("queue: heartbeat failed for %s",
                                  self._message.message_id)

        self._thread = threading.Thread(
            target=beat, name=f"queue-beat-{self._message.message_id[:8]}",
            daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def run_message(session: Session, message: models.QueuedMessage, *,
                session_factory: Optional[Callable[[], Session]] = None,
                heartbeat_seconds: float = HEARTBEAT_SECONDS) -> bool:
    """Run one claimed message and record the outcome. Returns whether it ran.

    Never raises: a handler that fails is a message that failed, and a worker
    that dies on one bad job stops doing every other job too.
    """
    try:
        fn = handler_for(message.topic)
    except UnknownTopic as e:
        # Not retryable by waiting: this process does not know how to run it.
        # Dead-lettered immediately rather than after three identical failures,
        # so the row says what is actually wrong.
        queue.fail(session, message, str(e), terminal=True)
        return False

    with _Beating(message, session_factory, heartbeat_seconds):
        try:
            fn(dict(message.payload or {}))
        except Exception as e:  # noqa: BLE001 — deliberate: the queue owns failure
            log.exception("queue: handler for %s raised", message.topic)
            queue.fail(session, message, f"{type(e).__name__}: {e}")
            return False
    queue.complete(session, message)
    return True


def drain_once(session: Session, *, worker: Optional[str] = None,
               topics: Optional[tuple] = None, limit: int = 100,
               session_factory: Optional[Callable[[], Session]] = None,
               heartbeat_seconds: float = HEARTBEAT_SECONDS) -> int:
    """Run every message that is due right now, up to ``limit``. Returns how
    many ran to completion.

    The synchronous half of the worker, and the seam the tests use: a queue
    exercised only through a background thread is a queue tested with a sleep
    and a hope. A CLI or a cron can call it too — it is the whole loop minus
    the waiting.

    ``limit`` bounds one pass so a backlog cannot starve the reaper below it;
    the loop comes straight back for the rest.
    """
    worker = worker or worker_name()
    ran = 0
    for _ in range(max(0, limit)):
        message = queue.claim(session, worker=worker, topics=topics)
        if message is None:
            break
        if run_message(session, message, session_factory=session_factory,
                       heartbeat_seconds=heartbeat_seconds):
            ran += 1
    return ran


def start_worker() -> bool:
    """Start the draining thread, once per process. Returns whether it started.

    Declines when the queue is not the dispatch this deployment chose: a worker
    polling a queue nothing writes to is a thread and a query per second buying
    nothing. ``settings.QUEUE_WORKER`` forces it on for a deployment that runs
    workers separately from the API.
    """
    if not settings.queue_worker_enabled:
        return False
    if _started.is_set():
        return False
    _started.set()
    _stop.clear()
    load_handlers()

    name = worker_name()

    def loop() -> None:
        from ..db import SessionLocal

        while not _stop.is_set():
            try:
                session = SessionLocal()
                try:
                    # Reap first: a message held by a worker that died is not
                    # visible to the claim below until it is handed back.
                    queue.reap_stale(session)
                    ran = drain_once(session, worker=name,
                                     session_factory=SessionLocal)
                finally:
                    session.close()
            except Exception:  # noqa: BLE001 — the loop must survive a bad pass
                log.exception("queue: worker pass failed; retrying in %ss",
                              POLL_SECONDS)
                ran = 0
            # Straight back round while there was work; wait only when idle.
            _stop.wait(0 if ran else POLL_SECONDS)

    threading.Thread(target=loop, name=_THREAD_NAME, daemon=True).start()
    log.info("queue worker running (poll %ss, as %s)", POLL_SECONDS, name)
    return True


def stop_worker(timeout: float = 5.0) -> None:
    """Ask the loop to finish its pass and stop. Used by tests; safe anywhere."""
    _stop.set()
    for thread in threading.enumerate():
        if thread.name == _THREAD_NAME and thread.is_alive():
            thread.join(timeout=timeout)
    _started.clear()


def worker_running() -> bool:
    """Whether a live worker thread exists in this process — the fact the
    health check needs, read from the threads rather than from a flag that
    would still be set after the thread died."""
    return any(t.name == _THREAD_NAME and t.is_alive()
               for t in threading.enumerate())


__all__ = ["drain_once", "load_handlers", "run_message", "start_worker",
           "stop_worker", "worker_name", "worker_running"]
