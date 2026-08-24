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
chosen.

**One ticker, enforced in the database.** This paragraph used to say the tick
was safe because "this deployment is one uvicorn process", and that a
multi-process one would need a database lock. The image has shipped
``--workers 2`` throughout, so that premise was never true: two threads ticked,
starting together and therefore landing within the same instant, and
``start_sync``'s guard is an in-process lock plus a read of the active runs with
no constraint behind it. Both could read "nothing running" and both queue a pull
of the same books — double the ERP calls for one job's worth of data, and a
progress counter that cannot say which run it belongs to. Rare at three tenants;
routine at a hundred and fifty.

So the loop holds the ``sync-scheduler`` lease (``app/leases.py``) and only
ticks while it does. Every process still runs the thread — which is what makes
this survive a restart of whichever one happened to be holding it — but only
the holder decides anything. The decision function below did not change, and
neither did what a tick does; what changed is how many processes do it.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import leases
from ..clock import aware as _aware, now as _now
from ..config import settings
from ..domain import models

log = logging.getLogger("pie_portal.sync_scheduler")

#: How often the thread wakes to ask "is anything due". Waking is nearly free —
#: one query per organization with a connection — so this is about latency of
#: the *first* scheduled run after a restart, not about load.
TICK_SECONDS = 60

_started = threading.Event()
#: Set to stop the loop — today only tests do, so a suite that started the
#: thread does not leak ticks into the tests after it.
_stop = threading.Event()

#: The lease that decides which process ticks. One name, because there is one
#: schedule; every process runs the thread and only the holder acts.
LEASE = "sync-scheduler"

#: Comfortably more than a tick, so a slow pass does not lose the lease to a
#: peer mid-tick, and short enough that a killed holder stops the schedule for
#: two ticks rather than an afternoon.
LEASE_TTL = timedelta(seconds=TICK_SECONDS * 3)


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
    syncs were queued — the observable a test can hold onto.

    Three queries, whatever the tenant count. It used to be two *per
    organization* — a row read for the config and a sort of that org's runs for
    the last start — which at 150 connected books and a wake a minute is most
    of a million statements a day spent asking a question whose answer is
    almost always "no". Asking it for everybody at once is the same question.
    """
    from . import jobs

    started = 0
    org_ids = session.scalars(
        select(models.ZohoConnection.organization_id)
        .where(models.ZohoConnection.enabled.is_(True))
        .distinct()).all()
    if not org_ids:
        return 0

    # Every cadence, and every organization's most recent start, in one query
    # each. `max(started_at)` rather than an ordered limit per org: the question
    # is only "when did the newest one begin".
    orgs = {o.organization_id: o for o in session.scalars(
        select(models.Organization)
        .where(models.Organization.organization_id.in_(org_ids)))}
    last_started = dict(session.execute(
        select(models.SyncRun.organization_id,
               func.max(models.SyncRun.started_at))
        .where(models.SyncRun.organization_id.in_(org_ids))
        .group_by(models.SyncRun.organization_id)).all())

    for org_id in org_ids:
        hours = auto_sync_hours(orgs.get(org_id))
        if not due(last_started.get(org_id), hours):
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


def start_scheduler() -> bool:
    """Start the daemon thread, once per process. Returns whether it started.

    Every worker starts one — see the module docstring. Starting is not
    leading: the loop takes ``LEASE`` each tick and only the holder acts.

    Guarded on the live source: a fixture deployment has nothing to keep fresh,
    and a scheduler that "syncs" sample data every six hours is noise in the
    run history pretending to be diligence.
    """
    if settings.ZOHO_SOURCE != "api":
        return False
    if _started.is_set():
        return False
    _started.set()

    holder = leases.holder_name()

    def loop() -> None:
        from ..db import SessionLocal

        held = False
        while not _stop.is_set():
            try:
                session = SessionLocal()
                try:
                    # Asked every tick rather than once at startup: this both
                    # renews the lease while we hold it and picks it up when
                    # the process that held it has gone.
                    now_held = leases.acquire(session, LEASE, holder,
                                              ttl=LEASE_TTL)
                    if now_held and not held:
                        log.info("auto-sync scheduler is the ticker (%s)", holder)
                    held = now_held
                    if held:
                        tick(session)
                        session.commit()
                finally:
                    session.close()
            except Exception:  # noqa: BLE001 — the schedule must survive one bad tick
                log.exception("auto-sync tick failed; next tick in %ss", TICK_SECONDS)
            _stop.wait(TICK_SECONDS)

        # Hand it on rather than making the next process wait out the expiry.
        # Best-effort: a process being killed does not get to run this, which is
        # exactly why the lease has an expiry at all.
        if held:
            try:
                session = SessionLocal()
                try:
                    leases.release(session, LEASE, holder)
                finally:
                    session.close()
            except Exception:  # noqa: BLE001 — shutdown must not fail over this
                log.exception("could not release the auto-sync lease")

    threading.Thread(target=loop, name="sync-scheduler", daemon=True).start()
    log.info("auto-sync scheduler running (tick %ss, default every %dh); "
             "whether this process is the one that ticks depends on the %s lease",
             TICK_SECONDS, settings.SYNC_AUTO_HOURS, LEASE)
    return True
