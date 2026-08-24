"""One process at a time does the thing — across processes, not within one.

A named lease with an expiry. A process asks to hold ``sync-scheduler``; one
gets it, the others are told no, and if the holder dies its lease runs out and
somebody else takes over.

**Why this exists.** ``ingestion/scheduler`` said, in its own docstring, that
its tick is safe because "this deployment is one uvicorn process" and that a
multi-process deployment would need a database lock. The image has shipped
``--workers 2`` the whole time, so there have always been two ticking threads
asking "is any organization due" — and they start together, so their once-a-
minute ticks land at nearly the same instant. ``start_sync``'s guard is an
in-process lock plus a read of the active runs, with no constraint behind it, so
both processes can read "nothing running" and both queue a pull of the same
books: double the ERP calls, and two progress counters for one job.

The queue's own dedupe does not save that. Each duplicate gets its own
``SyncRun``, so their dedupe keys differ; by then the duplication has already
happened. The decision has to be made once, which means one decider.

**How.** The claim is a conditional UPDATE — the same mechanism, for the same
reason, as ``messaging.queue.claim``: two processes racing produce one changed
row and one zero-row update, and the winner is whoever changed it. That
property is exercised on PostgreSQL by the gate.

**A lease, not a lock.** Nothing here is ever *held* across a failure: the row
carries an expiry, the holder renews while it works, and a process that dies
holding one blocks the next holder only until that expiry passes. A lock a dead
process keeps forever is the failure this shape exists to avoid — the same
reasoning as ``SyncRun.heartbeat_at`` and the queue's stale-message reaper.

Deliberately not a general mutual-exclusion primitive. It answers one question
— "am I the one that ticks?" — with a granularity of seconds, and nothing here
should be used to serialise anything finer.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clock import aware as _aware, now as _now
from .domain import models

log = logging.getLogger("pie_portal.leases")

#: How long a lease is good for once taken. Long enough that a holder busy with
#: a slow tick does not lose it, short enough that a killed process does not
#: stop the schedule for long. The holder renews every tick, so the only thing
#: that waits this long is a takeover.
DEFAULT_TTL = timedelta(seconds=120)


#: This process's identity, and the pid it was derived under. Cached rather than
#: recomputed so that everything attributed to "this worker" — the lease holder,
#: the ``worker`` label on metrics, the writer stamped on an audit entry — is
#: one string, and so that two of them cannot disagree about which process they
#: came from.
_HOLDER: Optional[str] = None
_HOLDER_PID: Optional[int] = None


def holder_id() -> str:
    """This process's identity, computed on first use and re-derived after a fork.

    Deliberately not a module constant. A constant is fixed at import, which is
    unique per worker only while the supervisor *spawns* — as uvicorn's
    ``--workers`` does today. Under a forking supervisor (gunicorn with
    ``UvicornWorker``, an ordinary swap for a FastAPI service, and uvicorn itself
    under ``--preload``) every child would inherit one identity from the parent,
    and two processes sharing a holder string both take the renewal disjunct of
    ``acquire``'s UPDATE: each sees ``holder = :me``, each renews, and both
    believe they lead. That is the one way this mechanism can silently produce
    two leaders, so it is closed here rather than documented as a caveat.

    Keying the cache on the pid makes the fork case self-correcting: the child's
    pid differs from the parent's, so the next call re-derives.
    """
    global _HOLDER, _HOLDER_PID
    pid = os.getpid()
    if _HOLDER is None or _HOLDER_PID != pid:
        _HOLDER = f"{socket.gethostname()}:{pid}:{uuid.uuid4().hex[:8]}"
        _HOLDER_PID = pid
    return _HOLDER


def holder_name() -> str:
    """Who holds it, in words that identify a process during an incident.

    ``holder_id()`` rather than a fresh string per call: the scheduler asks once
    and renews under the same name for the life of the process, and ``metrics``
    and ``trust.audit`` label their own writes with the same identity. One
    process, one name, in the lease row and in everything that names a worker.
    """
    return holder_id()


def acquire(session: Session, name: str, holder: str, *,
            ttl: timedelta = DEFAULT_TTL,
            now: Optional[datetime] = None) -> bool:
    """Take or renew the lease. True when this holder now has it. Commits.

    Three cases, and the middle one is the point:

    * nobody has held it before — insert, and lose gracefully if another
      process inserted first;
    * somebody holds it and it has not expired — this is the answer "no",
      unless *we* are the holder, in which case it is a renewal;
    * somebody held it and let it expire — take it over.

    Never raises on contention. A caller asking "am I the one?" every minute
    wants a boolean, not an exception to handle sixty times an hour.
    """
    now = now or _now()
    expires_at = now + ttl

    row = session.get(models.ProcessLease, name)
    if row is None:
        # First ever. The insert is the claim, and losing it is another process
        # having got there first — which is a "no", not a failure.
        session.add(models.ProcessLease(
            name=name, holder=holder, acquired_at=now, renewed_at=now,
            expires_at=expires_at))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
        log.info("lease %s taken by %s", name, holder)
        return True

    # The race is decided here, exactly as the queue's claim decides its own:
    # whoever changes the row owns the lease. The WHERE clause admits two
    # writers — the current holder renewing, and anyone at all once it has
    # expired — and nobody else.
    result = session.execute(
        update(models.ProcessLease)
        .where(models.ProcessLease.name == name,
               (models.ProcessLease.holder == holder)
               | (models.ProcessLease.expires_at <= now))
        .values(holder=holder, renewed_at=now, expires_at=expires_at)
        # The comparison belongs to the database, not to SQLAlchemy's in-Python
        # re-evaluation of the criteria: SQLite returns a `DateTime(timezone=True)`
        # naive, and comparing that to an aware `now` raises rather than
        # deciding — the exact trap `clock.aware` exists for. The rows this
        # session holds are re-read after the commit anyway.
        .execution_options(synchronize_session=False))
    session.commit()
    if result.rowcount:
        previous = row.holder
        if previous != holder:
            log.info("lease %s taken over from %s by %s (expired at %s)",
                     name, previous, holder, _aware(row.expires_at))
        return True
    return False


def release(session: Session, name: str, holder: str) -> bool:
    """Give the lease up, so the next process does not wait out the expiry.

    Only the holder can. A release that let anyone drop anyone's lease would be
    a way to make two processes tick on purpose.
    """
    result = session.execute(
        update(models.ProcessLease)
        .where(models.ProcessLease.name == name,
               models.ProcessLease.holder == holder)
        .values(expires_at=_now())
        .execution_options(synchronize_session=False))
    session.commit()
    if result.rowcount:
        log.info("lease %s released by %s", name, holder)
    return bool(result.rowcount)


def current_holder(session: Session, name: str, *,
                   now: Optional[datetime] = None) -> Optional[str]:
    """Who holds it right now, or None when nobody does. For a health check —
    "the schedule has a holder" is the fact worth reporting."""
    now = now or _now()
    row = session.scalar(
        select(models.ProcessLease).where(models.ProcessLease.name == name))
    if row is None:
        return None
    expires = _aware(row.expires_at)
    return row.holder if expires is not None and expires > now else None
