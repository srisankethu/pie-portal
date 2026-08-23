"""Only one process should do this right now.

The deployment runs two uvicorn workers (``deploy/backend.Dockerfile``,
``compose.yaml``), so anything started at module scope or in the lifespan exists
twice. For most of it that is the point. For work that is correct *once per
cluster* — the auto-sync tick — the second copy is pure waste, and this is what
removes it.

A lease is a named claim with an expiry. ``hold`` asks the database "am I the
one, right now"; whoever gets ``True`` does the work and everyone else does
nothing this round. Nobody blocks: a lease is not a mutex you wait on, it is a
question you re-ask on your own schedule, which is why the caller keeps its own
loop and calls again next tick.

**Why a conditional UPDATE and not ``pg_try_advisory_lock``.** Dev and test run
SQLite (``settings.DATABASE_URL``); production runs Postgres. An advisory lock
exists only on one of them, so the primitive protecting production would be the
one no test ever exercises — the drift CLAUDE.md §6 was written about. ``UPDATE
… WHERE`` and its rowcount behave identically on both, so the code under test is
the code that ships.

**Why the rowcount is the decision.** The predicate and the write are one
statement, so there is no window between reading who holds the lease and taking
it. Two processes issuing it at the same instant serialise at the database — one
UPDATE matches, the other matches nothing — and neither has to be told it lost
by anything other than the number of rows it changed.

**Clocks.** Expiry is compared against a timestamp this application supplies
(``clock.now``), never the database's own ``now()``: SQLite has no comparable
function and the two dialects would otherwise disagree about what "expired"
means. That makes the mechanism correct only while the processes sharing a lease
share a clock. They do here — the workers are threads of one container's uvicorn
master, reading one kernel clock. If this ever spans hosts, clock skew subtracts
from the lease's effective life (a follower whose clock runs fast sees the
leader's claim expire early and takes it), so the guard would need either NTP
discipline well inside the lease duration or a database-side ``now()``.

**This bounds waste, not correctness, and the distinction is the whole contract.**
Two leaders must not be able to corrupt anything a caller cares about. The sync
path already satisfies that without any lease — the partial unique indexes on
``sync_runs`` allow one active run per connection whatever the schedulers do —
and every future caller must be able to say the same before relying on this.

Concretely, why that bar exists: there is no fence token. A leader can hold an
unexpired claim and still not be running (a stop-the-world pause, a frozen
cgroup, a stalled disk), and worse, it can resume *after* its lease has lapsed
and another worker has taken it, and act believing it still leads. Nothing here
detects that; the check happened before the pause and the work happens after.

So a hash-chained audit log — which needs a genuine single writer and a total
order — **cannot** be built on this alone. It needs the ordering enforced where
the write lands (a monotonic sequence the database itself allocates, a unique
constraint on the chain position, or a fence token compared at write time), with
the lease serving only to keep contention rare. Anyone reaching for this to get
"one writer" should read this paragraph as a refusal.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import case, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clock import aware as _aware, now as _now
from .domain import models

log = logging.getLogger("pie_portal.lease")

# This process's identity, behind ``holder_id()`` below. Opaque by design: the
# mechanism only ever compares it for equality, and the rest of it is there so a
# log line or a health message can name which worker is leading.
#
# The random tail matters. Host and pid alone are unique among *live*
# processes, but an operating system reuses pids — so a worker restarted into
# its predecessor's pid would inherit its predecessor's claim and renew a lease
# it never took. The cost of the tail is that a restarted worker waits out the
# old lease instead of stepping straight back into it, which is the failover
# latency the caller already budgets for.
_HOLDER: Optional[str] = None
_HOLDER_PID: Optional[int] = None


def holder_id() -> str:
    """This process's identity, computed on first use and re-derived after a fork.

    Deliberately not a module constant. A constant is fixed at import, which is
    unique per worker only while the supervisor *spawns* — as uvicorn's
    ``--workers`` does today. Under a forking supervisor (gunicorn with
    ``UvicornWorker``, an ordinary swap for a FastAPI service, and certain under
    ``--preload``) every child would inherit one identity from the parent, and
    two processes sharing a holder string both take the renewal disjunct of the
    UPDATE: each sees ``holder = :me``, each renews, and both believe they lead.
    That is the one way this mechanism can silently produce two leaders, so it
    is closed here rather than documented as a caveat.

    Keying the cache on the pid makes the fork case self-correcting: the child's
    pid differs from the parent's, so the next call re-derives.
    """
    global _HOLDER, _HOLDER_PID
    pid = os.getpid()
    if _HOLDER is None or _HOLDER_PID != pid:
        _HOLDER = f"{socket.gethostname()}:{pid}:{uuid.uuid4().hex[:8]}"
        _HOLDER_PID = pid
    return _HOLDER


def hold(session: Session, name: str, *, seconds: int,
         holder: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """Claim ``name`` for ``seconds``, or extend a claim this holder already has.

    Returns whether this holder owns the lease when the call returns. Claiming
    and renewing are one statement rather than two because they are one
    question — "is it mine now?" — and splitting them would add a window
    between the renew failing and the acquire trying in which a third process
    could take it.

    **Commits.** A claim nobody else can read is not a claim: until the
    transaction commits, the row the other worker reads still says the lease is
    free. So this commits the session, and callers should treat it the way
    ``jobs.start_sync`` is treated — call it on a session with no unrelated
    pending work.
    """
    holder = holder or holder_id()
    now = _aware(now) or _now()
    expires = now + timedelta(seconds=seconds)

    if _claim(session, name, holder, now, expires):
        return True

    # The UPDATE matched nothing, and there are two very different reasons for
    # that: the row does not exist yet, or it exists and somebody else holds it
    # unexpired. Only the first is worth an INSERT.
    #
    # Distinguishing them is not a nicety. Falling straight through to the
    # INSERT costs a *steady-state follower* a guaranteed primary-key violation
    # on every single tick, forever — three write statements and a constraint
    # error a minute, per worker, logged as an ERROR by Postgres. That is the
    # per-tick waste and confusing log line this whole module exists to remove,
    # reintroduced one layer down. A follower now costs one UPDATE and one
    # SELECT, and raises nothing.
    if _exists(session, name):
        return False
    if _create(session, name, holder, now, expires):
        return True
    # The row did not exist a moment ago and does now, so another process
    # created it in between — and that process is holding it. One retry rather
    # than a loop: the UPDATE now has a row to match and decides honestly (it
    # loses to a live holder, wins over an expired one), and nothing in this
    # module ever deletes a row, so there is no third state to spin on.
    return _claim(session, name, holder, now, expires)


def held_by(session: Session, name: str,
            now: Optional[datetime] = None) -> Optional[str]:
    """Who holds ``name`` at this moment, or ``None`` if nobody does.

    A read, so it is safe on a path that must not write — a health check asking
    "is this worker the leader" must not become a health check that only passes
    while the database accepts writes.
    """
    now = _aware(now) or _now()
    row = session.execute(
        select(models.ProcessLease.holder, models.ProcessLease.expires_at)
        .where(models.ProcessLease.name == name)).first()
    if row is None or row.holder is None:
        return None
    expires = _aware(row.expires_at)
    return row.holder if expires is not None and expires > now else None


def _claim(session: Session, name: str, holder: str,
           now: datetime, expires: datetime) -> bool:
    """The conditional UPDATE. ``True`` when it matched the one row."""
    result = session.execute(
        update(models.ProcessLease)
        .where(models.ProcessLease.name == name,
               or_(
                   # Mine already: this is the renewal, and it is what keeps a
                   # working leader leading rather than re-electing every tick.
                   models.ProcessLease.holder == holder,
                   # Free. Nothing writes NULL today; the disjunct is here so a
                   # lease that is explicitly released stays claimable without
                   # waiting out an expiry that already passed.
                   models.ProcessLease.holder.is_(None),
                   # Lapsed. The holder stopped renewing — died, or was stopped
                   # — and this is the failover path.
                   models.ProcessLease.expires_at <= now,
               ))
        .values(
            holder=holder,
            # Only when leadership actually changes hands. Writing ``now``
            # unconditionally would move ``acquired_at`` forward on every
            # renewal, and the column would then answer "when did the current
            # leader last renew" — a value bounded above by one tick, and never
            # the question the model docstring says it is here for ("how long
            # has this leader been leading", the first thing asked after "who
            # leads"). A CASE keeps that in the one statement; splitting it
            # would put a window between the two.
            acquired_at=case((models.ProcessLease.holder == holder,
                              models.ProcessLease.acquired_at),
                             else_=now),
            expires_at=expires)
        .execution_options(synchronize_session=False))
    session.commit()
    return result.rowcount == 1


def _exists(session: Session, name: str) -> bool:
    """Whether the row is there at all — not whether anyone holds it."""
    return session.execute(
        select(models.ProcessLease.name)
        .where(models.ProcessLease.name == name)).first() is not None


def _create(session: Session, name: str, holder: str,
            now: datetime, expires: datetime) -> bool:
    """First claim of a lease nobody has ever taken. ``False`` if it lost.

    The row is created lazily rather than seeded by a migration because the set
    of lease names is chosen by callers, and a migration written today cannot
    know the ones added next year.
    """
    try:
        session.execute(insert(models.ProcessLease).values(
            name=name, holder=holder, acquired_at=now, expires_at=expires))
        session.commit()
        return True
    except IntegrityError:
        # Another process inserted the same primary key first. An ordinary
        # outcome, not an error — the same race the UPDATE resolves, decided by
        # the primary key instead. The failed commit already rolled back at the
        # database; this is the session catching up with that.
        session.rollback()
        return False
