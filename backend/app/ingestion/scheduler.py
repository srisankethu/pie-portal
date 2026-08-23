"""The automatic sync: pull the books on a cadence, without being asked.

A platform whose numbers are only as fresh as the last time somebody remembered
to press Sync is a platform that is stale exactly when nobody is looking at it —
and the mornings it matters most are the mornings after nobody pressed anything.
This module makes the pull periodic: a single daemon thread wakes once a minute,
asks a pure function whether any organization's next sync is due, and queues it
through the same ``jobs.start_sync`` a person's click goes through. Same guard
against double-starts, same run row, same screens watching it — the scheduler is
a patient finger on the existing button, not a second sync system.

**The window is the organization's existing coverage, never "since the last
sync".** That narrower window is the intuitive design and it silently loses
documents: Zoho's list filters are by *document date*, and a distributor's
accountant enters last week's bills this week — a bill dated the 3rd, entered
the 11th, falls outside any window that starts at the 10th. Re-asking for the
whole covered window is what catches it, and it is cheap by construction: the
resume cursor skips the detail call for every unchanged document, and the
incremental listing short-circuits page one at the first already-seen
modification stamp. The expensive-looking design is the cheap one.

The cadence lives in ``Organization.config["auto_sync_hours"]`` — per tenant,
edited from the sync screen, ``0`` meaning off — with
``settings.SYNC_AUTO_HOURS`` as the default for an organization that has never
chosen. Threads, not a broker, for the reason ``jobs`` gives.

**Every worker runs its own tick, and one of them acts on it.** The deployment
is two uvicorn workers by default, so this thread exists twice and both copies
find the same organization due in the same minute. Correctness was never the
problem — the tick does not start pulls itself, it calls ``jobs.start_sync``,
and the partial unique indexes on ``sync_runs`` allow only one active run per
connection, so the loser is handed the winner's run. What that leaves is waste:
a query and a puzzling log line on every worker but one, every minute, forever.
So the loop takes a ``lease`` before it acts, and a worker that does not hold it
does nothing this tick.

**The thread still starts on every worker.** Not starting it on followers is the
tempting version and it is wrong twice over. The health check
(``observability/health.check_scheduler``) reads *this process* for a live
``sync-scheduler`` thread, so a deployment of followers would report DEGRADED on
every worker but the leader, and ``/api/v1/internal/observability/health`` would flap depending on which
one answered. And a running thread on every worker is what makes failover free:
when the leader's process dies its lease lapses and another worker's
already-running loop claims it on its next tick — no supervisor, no restart.

The lease goes in the loop, never inside ``tick``. ``tick`` is a decision
function tested directly against a session, and a primitive that reaches for the
database before answering could not be.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import lease
from ..clock import aware as _aware, now as _now
from ..config import settings
from ..domain import models

log = logging.getLogger("pie_portal.sync_scheduler")

#: How often the thread wakes to ask "is anything due". Waking is nearly free —
#: one query per organization with a connection — so this is about latency of
#: the *first* scheduled run after a restart, not about load.
TICK_SECONDS = 60

#: The lease this loop competes for. One name for the whole cluster: the tick
#: sweeps every organization, so there is nothing per-tenant to divide.
LEASE_NAME = "sync-scheduler"

#: How long a claim lives, in ticks.
#:
#: The arithmetic, stated the way ``config`` states the pool's. Longer than a
#: tick or the leader would lose its own lease between renewals and the workers
#: would trade leadership every minute; three ticks leaves two whole missed
#: renewals of slack, so a tick that runs long or a moment of database
#: contention does not cause a hand-off. Short enough that a dead leader is
#: replaced promptly: worst case is the full lease (180s) plus the survivor's
#: wait for its next tick (60s), so at most four minutes with nobody
#: scheduling — against a cadence measured in hours, and with the manual Sync
#: button untouched throughout. Raise it and failover slows; lower it past one
#: tick and leadership thrashes.
LEASE_SECONDS = TICK_SECONDS * 3

_started = threading.Event()
#: Set to stop the loop — today only tests do, so a suite that started the
#: thread does not leak ticks into the tests after it.
_stop = threading.Event()


def auto_sync_hours(org: Optional[models.Organization]) -> int:
    """The cadence this organization chose, or the default it never changed.

    Values are clamped to sane bounds rather than trusted: the config dict is
    writable JSON, and a corrupted or hand-edited value must degrade to a
    number the loop can act on, never to an exception inside the tick.
    """
    raw = (org.config or {}).get("auto_sync_hours") if org is not None else None
    if raw is None:
        raw = settings.SYNC_AUTO_HOURS
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        hours = settings.SYNC_AUTO_HOURS
    return max(0, min(hours, 24 * 7))


def due(last_started_at: Optional[datetime], hours: int,
        now: Optional[datetime] = None) -> bool:
    """Whether the next automatic pull should start, from facts alone.

    Pure, so the schedule is testable without threads or sleeps. ``hours <= 0``
    is off. No prior run means due now — an organization that connected Zoho
    and walked away should find its data arrived anyway. The comparison is
    against when the last run *started*, not finished: a pull that takes forty
    minutes should not push the next one forty minutes later every time, or
    the cadence drifts.
    """
    if hours <= 0:
        return False
    if last_started_at is None:
        return True
    anchored = _aware(last_started_at)
    return (now or _now()) - anchored >= timedelta(hours=hours)


def scheduled_since(session: Session, org_id: str) -> Optional[date]:
    """The window an automatic pull should ask for: the earliest this
    organization has ever covered.

    ``None`` — for an organization that has never completed a sync — lets
    ``jobs.resolve_since`` apply the same default a first manual sync gets.
    Widening never happens automatically (a backfill is a deliberate, priced
    decision), and narrowing never happens at all: coverage that silently
    shrank would un-answer questions the screens already answered.
    """
    return session.scalar(
        select(models.SyncRun.since)
        .where(models.SyncRun.organization_id == org_id,
               models.SyncRun.status == "OK",
               models.SyncRun.since.is_not(None))
        .order_by(models.SyncRun.since.asc())
        .limit(1))


def next_run_at(session: Session, org: models.Organization) -> Optional[datetime]:
    """When the next automatic pull is expected, for the screen to say so.

    An estimate, honestly: the tick has up-to-a-minute granularity and a run
    already in flight postpones nothing here. ``None`` means the schedule is
    off (or the deployment does not sync a live book at all).
    """
    if settings.ZOHO_SOURCE != "api":
        return None
    hours = auto_sync_hours(org)
    if hours <= 0:
        return None
    last = session.scalar(
        select(models.SyncRun.started_at)
        .where(models.SyncRun.organization_id == org.organization_id)
        .order_by(models.SyncRun.started_at.desc())
        .limit(1))
    if last is None:
        return _now()
    return _aware(last) + timedelta(hours=hours)


def tick(session: Session) -> int:
    """One pass over every organization with a connected book. Returns how many
    syncs were queued — the observable a test can hold onto."""
    from . import jobs

    started = 0
    org_ids = session.scalars(
        select(models.ZohoConnection.organization_id)
        .where(models.ZohoConnection.enabled.is_(True))
        .distinct()).all()
    for org_id in org_ids:
        org = session.get(models.Organization, org_id)
        hours = auto_sync_hours(org)
        last = session.scalar(
            select(models.SyncRun.started_at)
            .where(models.SyncRun.organization_id == org_id)
            .order_by(models.SyncRun.started_at.desc())
            .limit(1))
        if not due(last, hours):
            continue
        # `start_sync` re-checks for an active run under its own lock, and the
        # unique indexes on `sync_runs` re-check across workers, so a pull a
        # person started thirty seconds ago — or the other worker's tick started
        # this millisecond — is handed back, not raced.
        _run, fresh = jobs.start_sync(
            session, org_id,
            since=scheduled_since(session, org_id),
            triggered_by="scheduler")
        if fresh:
            started += 1
            log.info("auto-sync queued for %s (every %dh)", org_id, hours)
    return started


def run_once(session: Session, *, holder: Optional[str] = None) -> bool:
    """One pass of the loop's body: take the lease, and tick only if it is ours.

    Returns whether this process led. Separated from the thread so the thing
    that actually changed is testable — two workers ticking in the same minute
    and only one of them acting. Inside the closure it could only be exercised
    by starting two schedulers, which one process cannot do (``_started`` is
    per-process, and rightly).

    ``holder`` is a parameter rather than read from the module for the same
    reason: a test playing both workers has one process and therefore one
    ``lease.holder_id()``, so without it both passes would renew the same claim
    and both would lead. Production never passes it, and ``lease.hold`` resolves
    ``None`` to this process's identity.
    """
    if not lease.hold(session, LEASE_NAME, seconds=LEASE_SECONDS, holder=holder):
        return False
    tick(session)
    session.commit()
    return True


def start_scheduler() -> bool:
    """Start the daemon thread, once per process. Returns whether it started.

    Every worker starts one — see the module docstring. Starting is not
    leading: the loop takes ``LEASE_NAME`` each tick and only the holder acts.

    Guarded on the live source: a fixture deployment has nothing to keep fresh,
    and a scheduler that "syncs" sample data every six hours is noise in the
    run history pretending to be diligence.
    """
    if settings.ZOHO_SOURCE != "api":
        return False
    if _started.is_set():
        return False
    _started.set()

    def loop() -> None:
        from ..db import SessionLocal

        #: Whether this process led on the previous pass. Only for logging: a
        #: line every tick saying "still not the leader" is noise on every
        #: worker but one, while the moment leadership *moves* is the line an
        #: operator wants and cannot reconstruct from a lease that reads the
        #: same whether it was just claimed or renewed.
        leading = False

        while not _stop.is_set():
            try:
                session = SessionLocal()
                try:
                    led = run_once(session)
                    if led and not leading:
                        log.info("auto-sync lease acquired by %s", lease.holder_id())
                    elif leading and not led:
                        log.info("auto-sync lease lost by %s; another worker "
                                 "is scheduling", lease.holder_id())
                    leading = led
                finally:
                    session.close()
            except Exception:  # noqa: BLE001 — the schedule must survive one bad tick
                log.exception("auto-sync tick failed; next tick in %ss", TICK_SECONDS)
            _stop.wait(TICK_SECONDS)

    threading.Thread(target=loop, name="sync-scheduler", daemon=True).start()
    log.info("auto-sync scheduler running (tick %ss, lease %ss, default every %dh)",
             TICK_SECONDS, LEASE_SECONDS, settings.SYNC_AUTO_HOURS)
    return True
