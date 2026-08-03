"""Sync as a background job: start it, watch it, and never start two.

A pull reads every invoice and bill individually, so a real organization's
first sync takes minutes. Doing that inside the request meant the browser sat
on a spinner with no idea whether the work had started, stalled or died — and a
second click was answered with "Sync is in progress", which says nothing about
*which* sync, since when, or how it is going.

So the run is dispatched to the background and the ``SyncRun`` row becomes the
thing the UI reads. That row already existed as the audit record; it now also
carries the live state, which is why there is no second job table. One row per
pull, written when it is queued and updated as it goes:

    QUEUED -> RUNNING -> OK | PARTIAL | FAILED

Three things this has to get right, all of them ways a job model rots:

**A dead job must not block the next one.** A process killed mid-pull cannot
write its own failure. Without a heartbeat its row stays RUNNING forever and
every later sync is refused as a duplicate — the worst outcome, because it
looks like the feature working. ``heartbeat_at`` is touched as the work
proceeds; a run whose heartbeat has gone cold is reaped and the new sync
proceeds.

**A second click must not start a second pull.** Two concurrent pulls of the
same books would double-fetch every document and race on the same rows. The
guard returns the *existing* job so the UI can show it, rather than raising an
error the screen has to translate.

**The background thread gets its own session.** The request's session is closed
the moment the response is sent; the job would then be writing through a dead
connection.

Deliberately a thread rather than a broker. This deployment is a single uvicorn
process with no Redis and no worker, and a thread is honest about that: the
in-process lock below is not distributed, and the module says so rather than
implying a guarantee it cannot make. If this ever runs multi-process, the lock
must become a database one — the reaping and status model would not change.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain import models

log = logging.getLogger("pie_portal.sync_jobs")

#: States a run is still going through. Anything else is terminal.
ACTIVE = ("QUEUED", "RUNNING")

#: How long a RUNNING row may go without a heartbeat before it is treated as
#: dead. Generous on purpose: a slow Zoho page is not a crash, and reaping a
#: live job would let a second pull race the first. The job touches its
#: heartbeat at every phase boundary, and phases are minutes at worst.
STALE_AFTER = timedelta(minutes=10)

#: Serialises the start-a-job decision within this process. See the module
#: docstring: this is not a cross-process lock and is not claimed to be.
_start_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes; comparisons need a timezone."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_stale(run: models.SyncRun, *, now: Optional[datetime] = None) -> bool:
    """Has this active run stopped reporting for itself?"""
    if run.status not in ACTIVE:
        return False
    now = now or _now()
    last = _aware(run.heartbeat_at) or _aware(run.started_at) or now
    return (now - last) > STALE_AFTER


def active_run(session: Session, organization_id: str) -> Optional[models.SyncRun]:
    """The sync currently in flight for this organization, if any.

    Reaps a dead one as a side effect rather than reporting it as live: a row
    nobody is updating is not a job in progress, and treating it as one locks
    the organization out of syncing until somebody edits the database.
    """
    row = session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == organization_id,
               models.SyncRun.status.in_(ACTIVE))
        .order_by(models.SyncRun.started_at.desc())
        .limit(1)).first()
    if row is None:
        return None
    if is_stale(row):
        log.warning("reaping stale sync run %s (last heartbeat %s)",
                    row.sync_run_id, row.heartbeat_at)
        row.status = "FAILED"
        row.phase = None
        row.error = (
            "This sync stopped reporting and was assumed dead — usually the "
            "server restarted mid-pull. Anything it had already written was "
            "kept; running it again carries on from there.")
        row.finished_at = _now()
        session.flush()
        return None
    return row


def last_finished_run(session: Session, organization_id: str) -> Optional[models.SyncRun]:
    """The most recent run that actually ended, whatever the outcome."""
    return session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == organization_id,
               models.SyncRun.status.notin_(ACTIVE))
        .order_by(models.SyncRun.started_at.desc())
        .limit(1)).first()


def last_successful_run(session: Session, organization_id: str) -> Optional[models.SyncRun]:
    """The last one that finished cleanly — 'last successful sync' on screen.

    PARTIAL is excluded: it wrote rows, but it did not finish, and calling it
    the last successful sync would overstate what is actually held.
    """
    return session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == organization_id,
               models.SyncRun.status == "OK")
        .order_by(models.SyncRun.started_at.desc())
        .limit(1)).first()


# ── the work itself ─────────────────────────────────────────────────────────
def execute_sync(session: Session, run: models.SyncRun, *,
                 since: date, full: bool = False,
                 connection_id: Optional[str] = None) -> dict:
    """Pull, detect, recompute, decide — the whole cycle, against one run row.

    Lifted out of the request handler unchanged in behaviour so that the
    background job and any scripted caller share one implementation. It updates
    the run as it goes, which is what makes the job observable at all.

    Never raises: a sync that fails must leave a row saying so, because a job
    that vanishes is indistinguishable from one that never started.
    """
    from ..decisions.service import DecisionService
    from ..seed import ensure_org_and_users
    from ..signals.engine import run_detectors
    from .sync import SyncReport, SyncService, get_source

    org = run.organization_id

    def phase(name: str) -> None:
        """Record what is happening, and prove the job is still alive."""
        run.phase = name
        run.heartbeat_at = _now()
        session.flush()

    run.status = "RUNNING"
    phase("Starting")

    demo_removed: dict[str, int] = {}
    commercial_report: Optional[dict] = None
    svc: Optional[SyncService] = None
    report = SyncReport(organization_id=org)   # placeholder until a source resolves

    try:
        ensure_org_and_users(session)

        if settings.ZOHO_SOURCE == "api":
            # A real sync means a real Zoho account is linked — any demo/sample
            # rows and the decisions built from them must not go on sitting
            # alongside real data. Demo rows carry fixed ids no live sync ever
            # produces, so this never touches a real Zoho record. Best-effort:
            # a purge problem must not block the pull.
            try:
                from ..demo import purge_demo_seed
                demo_removed = purge_demo_seed(session, org)
            except Exception:  # noqa: BLE001
                log.exception("demo-data purge failed; continuing with the sync")

        phase("Connecting to Zoho")
        source = (get_source(session, org, since=since, connection_id=connection_id)
                  if connection_id else get_source(session, org, since=since))

        svc = SyncService(session, source, org, resume=not full, on_phase=phase)
        svc.run()
        report = svc.report
        session.flush()

        phase("Detecting signals")
        detected = run_detectors(session, org)
        run.signals_emitted = detected.get("signals_emitted", 0)

        # Customer × Item metrics are derived from what just landed, so they are
        # rebuilt here rather than on the next page load. Targeted at the
        # relationships this pull actually moved — a full rebuild would scan the
        # organization's entire history to re-derive rows nothing changed.
        # Best-effort: a metrics problem must not fail a pull that succeeded.
        phase("Recomputing customer × item metrics")
        try:
            from ..commercial.compute import recompute as recompute_commercial

            ci = recompute_commercial(
                session, org, customer_ids=(report.touched_customer_ids or None))
            run.signals_emitted += sum(ci.signals_by_type.values())
            commercial_report = ci.to_dict()
        except Exception:  # noqa: BLE001
            log.exception("customer-item recompute failed; the pull itself is kept")

        phase("Generating decisions")
        generated = DecisionService(session, org).generate()
        run.decisions_created = generated.get("created", 0)
        run.status = "OK"
    except Exception as e:  # noqa: BLE001 — a failed sync must be visible, not silent
        log.exception("sync failed")
        if svc is not None:
            report = svc.report
        run.status = "PARTIAL" if report.wrote_anything else "FAILED"
        run.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        # Counters come from the report either way: a run that wrote 336 sales
        # lines and then died wrote 336 sales lines, and saying zero would make
        # the database unreadable from its own audit trail.
        run.customers = report.customers
        run.products = report.products
        run.sales_txns = report.sales_txns
        run.cost_records = report.cost_records
        run.assignments = report.assignments
        run.documents_fetched = report.documents_fetched or getattr(
            svc.source if svc is not None else None, "documents_fetched", 0)
        run.documents_resumed = report.documents_resumed or getattr(
            svc.source if svc is not None else None, "documents_resumed", 0)
        run.skipped_count = len(report.skipped)
        run.skipped_sample = report.skipped[:20]
        run.phase = None
        run.finished_at = _now()
        run.heartbeat_at = _now()
        # Persisted rather than returned: the caller that asked for this sync
        # got its response minutes ago, so the row is what the screen reads.
        notes: dict = {}
        if any(demo_removed.values()):
            notes["demo_data_removed"] = demo_removed
        if commercial_report is not None:
            notes["commercial"] = commercial_report
        run.notes = notes
        session.flush()

    return dict(run.notes or {})


# ── dispatch ────────────────────────────────────────────────────────────────
def _in_thread(sync_run_id: str, since: date, full: bool,
               connection_id: Optional[str]) -> None:
    """Run the job on its own session, because the request's is already closed."""
    from ..db import SessionLocal

    session = SessionLocal()
    try:
        run = session.get(models.SyncRun, sync_run_id)
        if run is None:            # deleted between queueing and starting
            return
        execute_sync(session, run, since=since, full=full,
                     connection_id=connection_id)
        session.commit()
    except Exception:  # noqa: BLE001
        log.exception("sync job %s crashed outside its own handler", sync_run_id)
        session.rollback()
        # Leave a readable row rather than one stuck at RUNNING until it is
        # reaped ten minutes later.
        try:
            run = session.get(models.SyncRun, sync_run_id)
            if run is not None and run.status in ACTIVE:
                run.status = "FAILED"
                run.phase = None
                run.error = "The sync job stopped unexpectedly. See the server log."
                run.finished_at = _now()
                session.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not record the crash of sync job %s", sync_run_id)
    finally:
        session.close()


def thread_dispatch(sync_run_id: str, since: date, full: bool,
                    connection_id: Optional[str]) -> None:
    threading.Thread(
        target=_in_thread, args=(sync_run_id, since, full, connection_id),
        name=f"sync-{sync_run_id[:8]}", daemon=True).start()


Dispatch = Callable[[str, date, bool, Optional[str]], None]


def start_sync(session: Session, organization_id: str, *, since: date,
               full: bool = False, connection_id: Optional[str] = None,
               triggered_by: Optional[str] = None,
               dispatch: Optional[Dispatch] = None) -> tuple[models.SyncRun, bool]:
    """Queue a sync, or hand back the one already running.

    Returns ``(run, started)``. ``started`` is False when an existing job was
    returned — the caller shows *that* job rather than reporting a conflict,
    because "a sync is already running" is only useful alongside which one and
    since when.

    ``dispatch`` is injectable so the job can be run inline. That keeps the
    asynchronous path testable without a sleep-and-hope loop, and lets a
    scripted caller (a cron, a CLI) block on the work it just asked for.
    """
    with _start_lock:
        existing = active_run(session, organization_id)
        if existing is not None:
            return existing, False

        run = models.SyncRun(
            organization_id=organization_id, source=settings.ZOHO_SOURCE,
            status="QUEUED", started_at=_now(), heartbeat_at=_now(),
            phase="Queued", triggered_by=triggered_by, since=since,
            connection_id=connection_id)
        session.add(run)
        # Committed before dispatch so the worker's own session can see the row,
        # and so a crash between here and the thread start still leaves a
        # queued job the reaper can resolve.
        session.commit()

    (dispatch or thread_dispatch)(run.sync_run_id, since, full, connection_id)
    # An inline dispatcher has just finished the work through a different
    # session, so this one is holding a QUEUED copy of a row that is now
    # finished. Harmless for the threaded path, where the row really is queued.
    session.expire_all()
    return run, True
