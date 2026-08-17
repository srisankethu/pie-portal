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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import aware as _aware, now as _now, today as _clock_today
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


def is_stale(run: models.SyncRun, *, now: Optional[datetime] = None) -> bool:
    """Has this active run stopped reporting for itself?"""
    if run.status not in ACTIVE:
        return False
    now = now or _now()
    last = _aware(run.heartbeat_at) or _aware(run.started_at) or now
    return (now - last) > STALE_AFTER


def active_run(session: Session, organization_id: str, *,
               connection_id: Optional[str] = None,
               any_connection: bool = True) -> Optional[models.SyncRun]:
    """The sync currently in flight, if any.

    Two questions, deliberately kept distinct because they have different right
    answers:

    **"Is anything running?"** — ``any_connection=True``, the default. What the
    Data screen asks, because a person watching a progress bar wants to see
    whatever is actually happening.

    **"Is *this connection* already running?"** — pass ``connection_id`` with
    ``any_connection=False``. What ``start_sync`` asks, and the reason this
    parameter exists: the check used to be organization-wide, so syncing one
    Zoho company silently handed back the other company's in-flight job instead
    of starting anything. Three connected companies could only ever be pulled
    one after another, and the second click looked like it had worked.

    Reaps a dead one as a side effect rather than reporting it as live: a row
    nobody is updating is not a job in progress, and treating it as one locks
    syncing out until somebody edits the database.
    """
    stmt = (select(models.SyncRun)
            .where(models.SyncRun.organization_id == organization_id,
                   models.SyncRun.status.in_(ACTIVE)))
    if not any_connection:
        stmt = stmt.where(models.SyncRun.connection_id == connection_id)
    row = session.scalars(
        stmt.order_by(models.SyncRun.started_at.desc()).limit(1)).first()
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


def active_runs(session: Session, organization_id: str) -> list[models.SyncRun]:
    """Every sync in flight, one per connection.

    ``active_run`` answers "is anything running" with a single row, which is the
    right answer for a headline and the wrong one for a screen that lists
    connections. Three connected Zoho companies can pull at once — that is the
    whole point of scoping the start guard by connection — and a status endpoint
    that reports one of them makes the other two invisible. The screen then has
    no way to show two progress bars, and no way to tell which company the one
    it *can* see belongs to.

    Stale rows are reaped here too, through ``active_run``, so a dead job does
    not hold a connection's button hostage.
    """
    rows = session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == organization_id,
               models.SyncRun.status.in_(ACTIVE))
        .order_by(models.SyncRun.started_at.desc())).all()
    live: dict[Optional[str], models.SyncRun] = {}
    for row in rows:
        if is_stale(row):
            # One reaper, in `active_run`, rather than a second copy of the
            # same three assignments that could drift from it.
            active_run(session, organization_id,
                       connection_id=row.connection_id, any_connection=False)
            continue
        # Newest first from the query, so the first row seen for a connection is
        # the one to report. A second would be a bug in the start guard, and
        # showing the older one would hide it.
        live.setdefault(row.connection_id, row)
    return list(live.values())


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


# ── slicing a long pull into calendar windows ───────────────────────────────
#
# Two things this buys, and one it does not.
#
# **A denominator that is real.** Zoho does not say how many documents it will
# return until they have been paged through, so a percentage of documents would
# be invented. The number of months between the start date and today is known
# before the first call, so "month 7 of 18" is a fact. It is a measure of
# calendar coverage, not of remaining time — months are not equal in volume, and
# the screen says so rather than implying an ETA.
#
# **Far fewer calls.** The window is sent to Zoho as a list filter instead of
# being applied after the fact, so a slice asks for its own months rather than
# every source walking the company's entire ledger and discarding most of it.
#
# What it does not buy is atomicity. A pull that dies in month 12 has genuinely
# written months 1–11, which is the behaviour that already made a resumed sync
# cheap; slicing makes the boundary explicit rather than introducing it.

#: Windows longer than this are not worth the extra round trips.
MAX_WINDOWS = 60


def resolve_since(value: Optional[date]) -> date:
    """A concrete start date, always.

    ``since`` used to be allowed to stay None and the client filled it in with
    a rolling window at request time. Slicing needs a real lower bound before
    the first call — and a run row that records "rolling window" cannot later
    say which months it actually read.
    """
    return value or (date.today() - timedelta(days=settings.ZOHO_HISTORY_DAYS))


def _add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def plan_windows(since: date, until: Optional[date] = None,
                 step_months: int = 1) -> list[tuple[date, date]]:
    """Split [since, until] into inclusive calendar slices, oldest first.

    Oldest first on purpose: a pull that is interrupted should leave the
    *history* in place and the recent end missing, because the recent end is
    what the next run will fetch anyway and what the detectors need last.

    Returns a single window when the range is short enough that slicing would
    cost more round trips than it saves.
    """
    until = until or date.today()
    if until < since:
        return [(since, since)]

    months = (until.year - since.year) * 12 + (until.month - since.month) + 1
    if months <= 1 or step_months <= 0:
        return [(since, until)]

    # Keep the number of slices sane on a very long history by widening them
    # rather than by refusing to slice at all.
    while months / step_months > MAX_WINDOWS:
        step_months += 1

    windows: list[tuple[date, date]] = []
    start = since
    while start <= until:
        boundary = _add_months(date(start.year, start.month, 1), step_months)
        end = min(boundary - timedelta(days=1), until)
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


def _persist_skips(session: Session, run: models.SyncRun,
                   pulls: list[tuple[Optional[str], list[dict]]]) -> None:
    """Write every skipped row of this run, not a sample of them.

    ``run.skipped_sample`` keeps the first twenty for the status card to render
    without a second request. Twenty is a preview: a run reporting 1,304 skips
    persisted 20 and lost 1,284 when the process ended, so the one question
    worth asking — *which* rows, and what do they have in common — had no answer
    anywhere in the system. These rows are what make the list exportable, and an
    export is what makes it fixable.

    ``pulls`` is per connected company rather than the merged report, because
    the merge concatenates the lists and drops which book each came from. For a
    business running three legal entities that is the first thing to know about
    1,304 skips: one company's item master, or all three.

    Uncapped on purpose. A cap here is the same defect one order of magnitude
    up — the screen would report a complete export that silently was not one,
    and a partial reconciliation reads as a clean one. Rows are small and a run
    that skips enough of them to be a storage problem is a run whose skips are
    the most valuable thing it produced.

    Best-effort: this is the audit trail of a pull that has already written its
    rows, and failing the run over it would discard real imported trade to
    protect a report about what was not imported.
    """
    try:
        # A SAVEPOINT, so a failure here undoes these inserts and *only* these.
        # A plain rollback would discard the counters the caller has just
        # written onto the run row and not yet flushed — losing the audit trail
        # of a successful pull to protect the report about its skips.
        with session.begin_nested():
            # Idempotent for a re-run against the same row (a resumed job).
            session.query(models.SyncSkip).filter_by(
                sync_run_id=run.sync_run_id).delete(synchronize_session=False)
            _insert_skips(session, run, pulls)
        session.flush()
    except Exception:  # noqa: BLE001 — the pull's own rows matter more
        log.exception("could not persist the skipped rows for run %s",
                      run.sync_run_id)


def _insert_skips(session: Session, run: models.SyncRun,
                  pulls: list[tuple[Optional[str], list[dict]]]) -> None:
    """One ``SyncSkip`` per reported skip, numbered in the order they were met."""
    seq = 0
    for connection_id, skipped in pulls:
        for row in skipped:
            ctx = row.get("context") or {}
            session.add(models.SyncSkip(
                organization_id=run.organization_id,
                sync_run_id=run.sync_run_id,
                connection_id=connection_id,
                seq=seq,
                kind=str(row.get("kind") or "")[:32],
                ref=str(row.get("ref") or "")[:255],
                code=str(row.get("code") or "")[:64],
                detail=str(row.get("detail") or "")[:512],
                missing_id=_clip(ctx.get("missing_id"), 64),
                label=_clip(ctx.get("label"), 255),
                sku=_clip(ctx.get("sku"), 128),
                document=_clip(ctx.get("document"), 128),
                document_date=_clip(ctx.get("document_date"), 32),
                party=_clip(ctx.get("party"), 255),
                qty=_decimal(ctx.get("qty")),
                line_value=_decimal(ctx.get("line_value")),
                fix=str(ctx.get("fix")) if ctx.get("fix") else None,
            ))
            seq += 1


def _clip(value: object, length: int) -> Optional[str]:
    text = str(value).strip() if value not in (None, "") else ""
    return text[:length] or None


def _decimal(value: object) -> Optional[Decimal]:
    """A quantity or a line value as ``Decimal``, or nothing.

    Nothing rather than zero when the source did not say: a missing quantity and
    a quantity of zero are different facts, and an export that writes 0 for both
    invites somebody to reconcile against a number the document never carried.
    """
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _window_label(start: date, end: date) -> str:
    """What the slice is called on screen: 'Mar 2025', or a range if wider."""
    if (start.year, start.month) == (end.year, end.month):
        return start.strftime("%b %Y")
    return f"{start.strftime('%b %Y')} – {end.strftime('%b %Y')}"


# ── the work itself ─────────────────────────────────────────────────────────
def execute_sync(session: Session, run: models.SyncRun, *,
                 since: Optional[date] = None, full: bool = False,
                 connection_id: Optional[str] = None,
                 analysis: bool = True, incremental: bool = True) -> dict:
    """Pull, detect, recompute, decide — the whole cycle, against one run row.

    Lifted out of the request handler unchanged in behaviour so that the
    background job and any scripted caller share one implementation. It updates
    the run as it goes, which is what makes the job observable at all.

    ``analysis=False`` stops after the pull. That is for a caller pulling
    several connections at once: the four phases after the pull are scoped to
    the *organization*, not to the connection, so running them per-connection is
    both wasteful and wrong — see ``execute_analysis``. Such a caller runs them
    once, after every pull has landed. Nothing else should pass it.

    Never raises: a sync that fails must leave a row saying so, because a job
    that vanishes is indistinguishable from one that never started.
    """
    from ..seed import ensure_org_and_users
    from .sync import SyncReport, SyncService, get_source

    org = run.organization_id
    since = resolve_since(since)
    # Recorded on the row, so it says which window it read rather than
    # "rolling window" — which is unanswerable once the pull is over.
    run.since = since

    def phase(name: str) -> None:
        """Record what is happening, prove the job is alive, and end the write.

        ``commit``, not ``flush``, and the difference is the whole point twice
        over.

        A flush writes inside the open transaction, so no other connection can
        see it. The entire pull used to run in one transaction with a single
        commit at the very end, which meant the progress this function records
        was invisible until the sync was already over — the window counter sat
        at 0 for the whole run and then jumped to 18. The screen that polls for
        it was reading a number that could not move.

        The same single transaction is why a sync made the rest of the app
        unusable on SQLite. A large write spills its page cache, escalates to an
        EXCLUSIVE lock, and holds it until commit; in rollback-journal mode that
        blocks every reader, so requests — including ``/api/health`` — failed
        with "database is locked" for the duration. Committing at each phase
        boundary keeps the write windows short.

        Committing part-way also makes an interrupted pull behave the way this
        module already claimed it did: a run that dies in month 12 has genuinely
        written months 1–11. That was the documented design and was not true,
        because nothing had been committed.
        """
        run.phase = name
        run.heartbeat_at = _now()
        session.commit()

    run.status = "RUNNING"
    phase("Starting")

    demo_removed: dict[str, int] = {}
    analysis_notes: dict = {}
    svc: Optional[SyncService] = None
    report = SyncReport(organization_id=org)   # placeholder until a source resolves
    # Declared out here so the `finally` can persist the skips of a run that
    # died before the loop ever built this list.
    services: list[SyncService] = []

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

        phase("Connecting to the books")

        def source_for(start: date, end: Optional[date], conn: Optional[str]):
            """One source per window, each asking Zoho only for its own months."""
            kwargs = {"since": start}
            if conn:
                kwargs["connection_id"] = conn
            src = get_source(session, org, **kwargs)
            # Only the live client can be bounded above; the fixture source has
            # no window to speak of and is left alone.
            if end is not None and hasattr(src, "_until"):
                src._until = end
            return src

        # Which books this run covers. Naming a connection means that one;
        # naming none means *every enabled one*, which is what the button
        # labelled "Sync every company" has always claimed and never did.
        #
        # What it did instead: `get_zoho_credentials` with no connection falls
        # back to the organization's *first* enabled row, so a three-company
        # organization pulled one company and the other two stayed empty for
        # ever. The rows it wrote also carried no connection at all, and a row
        # with no connection belongs to no company — so the Company filter,
        # which builds its options from the rows, had nothing to offer and hid
        # itself. Two different reasons for the same symptom: one connector
        # visible in a product built for three.
        targets = _sync_targets(session, org, connection_id)
        several = len(targets) > 1

        windows = plan_windows(since)
        # Per company, because that is what the progress counter is counting.
        run.windows_total = len(windows) * len(targets)
        run.windows_done = 0

        for index, target in enumerate(targets):
            # Named in every phase line, so a pull that takes minutes says which
            # company it is on rather than reading the same six phases three
            # times over.
            book = f" · {target.label}" if several and target.label else ""

            # The connection this service writes on behalf of, so every row it
            # upserts says which connected company it came from. A NULL
            # connection resolves to no company at all, which is what left the
            # Stock and Customers screens able to name the connector ("Zoho")
            # and never the book.
            svc = SyncService(session, source_for(since, None, target.connection_id), org,
                              resume=not full, on_phase=phase,
                              connector=target.connector,
                              connection_id=target.connection_id,
                              incremental=incremental)
            # Once per run, not once per company: it clears the document cursor
            # for the whole organization, and clearing it again after the first
            # company had already recorded its documents would make the next
            # resume re-read them.
            if index == 0:
                svc.begin()
            # Customers and items are the whole master list whatever the window,
            # so they are read once per company rather than once per slice.
            svc.run_reference()

            for start, end in windows:
                label = _window_label(start, end)
                svc.run_documents(source_for(start, end, target.connection_id),
                                  label=f"{label}{book}")
                run.windows_done += 1
                # Committed per window so a watcher sees the count move, and so
                # an interrupted pull records how far it actually got.
                phase(f"Read {label}{book}")

            # After the documents, because payments resolve to customers the
            # document pass may have created, and stock resolves to products.
            svc.run_supply()
            services.append(svc)

        # Assignments run per company, each against its own book. They used to
        # run once, on the last service, because customers were pooled across
        # the organization and one pass covered everybody — which also meant
        # one book's salesperson ids were looked up in another book's user
        # list, and every miss was reported as that company's problem. Now that
        # a service's customers are its own, the last service's pass would
        # cover only the last book; every service closes out its own.
        for svc_done in services:
            svc_done.finish()

        report = SyncReport(organization_id=org)
        for svc in services:
            report.merge(svc.report)
        session.flush()

        if analysis:
            analysis_notes = execute_analysis(
                session, run, org,
                customer_ids=(report.touched_customer_ids or None),
                timezone=svc.timezone() if svc else None, on_phase=phase)
        else:
            # Handed back so the caller that skipped the analysis can run it
            # once, for the union of everything its parallel pulls touched.
            analysis_notes = {
                "pull_only": True,
                "touched_customer_ids": sorted(report.touched_customer_ids or []),
            }
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
        run.vendors = report.vendors
        run.stock_snapshots = report.stock_snapshots
        run.payments = report.payments
        run.purchase_orders = report.purchase_orders
        run.sales_orders = report.sales_orders
        run.vendor_payments = report.vendor_payments
        run.documents_fetched = report.documents_fetched or getattr(
            svc.source if svc is not None else None, "documents_fetched", 0)
        run.documents_resumed = report.documents_resumed or getattr(
            svc.source if svc is not None else None, "documents_resumed", 0)
        # Whether this run was a backfill, and how much of one. A widened
        # window costs list calls over the months it had never covered; a run
        # that reports zero here read nothing it had not already read, which is
        # the difference between "the backfill worked" and "the backfill was a
        # no-op" — indistinguishable from the outside until now.
        if report.windows_listed_in_full:
            run.notes = {**(run.notes or {}),
                         "windows_listed_in_full": report.windows_listed_in_full}
        run.skipped_count = len(report.skipped)
        run.skipped_sample = report.skipped[:20]
        # And the whole list, per company, in its own table — the sample above
        # is what the status card paints, not the record. A run that died still
        # writes the skips it had already met, for the reason the counters above
        # come from the report either way: a pull that skipped 300 rows and then
        # failed skipped 300 rows.
        pulls: list[tuple[Optional[str], list[dict]]] = [
            (s.connection_id, s.report.skipped) for s in services]
        if svc is not None and svc not in services:
            pulls.append((svc.connection_id, svc.report.skipped))
        _persist_skips(session, run, pulls)
        # Capped, but on *distinct problems* rather than on rows: forty things
        # to fix is a long afternoon, four hundred identical lines is one.
        run.unresolved = report.unresolved()[:40]
        run.phase = None
        run.finished_at = _now()
        run.heartbeat_at = _now()
        # Persisted rather than returned: the caller that asked for this sync
        # got its response minutes ago, so the row is what the screen reads.
        notes: dict = {}
        if any(demo_removed.values()):
            notes["demo_data_removed"] = demo_removed
        # Merged, not assigned over. The analysis phases used to write their own
        # summaries onto ``run.notes`` inside the try block and this block then
        # replaced the whole dict — so "state" and "opportunities" were built,
        # stored, and silently discarded before anything could read them. They
        # now come back as a return value, which is harder to drop by accident.
        notes.update(analysis_notes)
        run.notes = notes
        session.flush()

    return dict(run.notes or {})


def execute_analysis(session: Session, run: models.SyncRun, organization_id: str, *,
                     customer_ids: Optional[list[str]] = None,
                     timezone: Optional[str] = None,
                     on_phase: Optional[Callable[[str], None]] = None) -> dict:
    """Detect, recompute, project, decide — the organization-wide half of a cycle.

    Split out of ``execute_sync`` because of what it is scoped to. A pull
    belongs to one connected Zoho company; every one of these four phases
    belongs to the **organization**, and an organization can have several
    connections whose rows land in one read model and are analysed together
    (see ``models.ZohoConnection``). So running this once per connection is
    wrong twice over:

    * **It reads an incomplete book.** Syncing three companies one after another
      ran the detectors after the first, when two thirds of the period's trade
      had not been read yet. The third pass corrected it, so the end state was
      right and nobody noticed — but the first two passes emitted signals, built
      a business state, and spent real AI calls on decisions about a business
      that was two thirds missing.
    * **It cannot be parallelised.** Three pulls write disjoint rows, because
      every imported table is keyed on ``connection_id``. These four phases
      write ``signals``, ``customer_item_metrics``, ``business_states`` and
      ``decisions``, which are keyed on the organization alone. Run them
      concurrently and they race each other for the same rows.

    Returns the note fragments for the run row rather than writing them onto it,
    so a caller running this once for several pulls decides where they land.

    Best-effort in the middle two phases, deliberately: the pull is the
    expensive thing that cannot be redone cheaply, and a projection that fails
    to build must not fail a pull that succeeded.
    """
    from ..decisions.service import DecisionService
    from ..signals.engine import run_detectors

    org = organization_id
    phase = on_phase or (lambda _name: None)
    notes: dict = {}

    phase("Detecting signals")
    detected = run_detectors(session, org)
    run.signals_emitted = detected.get("signals_emitted", 0)

    # Customer × Item metrics are derived from what just landed, so they are
    # rebuilt here rather than on the next page load. Targeted at the
    # relationships the pull actually moved — a full rebuild would scan the
    # organization's entire history to re-derive rows nothing changed.
    phase("Recomputing customer × item metrics")
    try:
        from ..commercial.compute import recompute as recompute_commercial

        ci = recompute_commercial(session, org, customer_ids=customer_ids)
        run.signals_emitted += sum(ci.signals_by_type.values())
        notes["commercial"] = ci.to_dict()
    except Exception:  # noqa: BLE001
        log.exception("customer-item recompute failed; the pull itself is kept")

    # Business state, folded from the events the pull recorded. After the
    # metrics, and inside one try with the queue built from it.
    phase("Building business state")
    try:
        from ..commercial.policy import load_for_org
        from ..state.engine import build as build_state

        th = load_for_org(session, org)
        state = build_state(session, org, as_of=_clock_today(timezone),
                            thresholds_version=th.version)
        notes["state"] = state.to_dict()

        # Decisions folded straight out of that state — deterministic, no AI,
        # and inside the same try: a queue built from a state that failed to
        # build would describe a business as of nothing.
        from ..decisions.opportunities import generate_from_state

        opportunities = generate_from_state(session, org, thresholds=th)
        notes["opportunities"] = opportunities
        run.decisions_created += opportunities.get("created", 0)
    except Exception:  # noqa: BLE001
        log.exception("state build failed; the pull itself is kept")

    phase("Generating decisions")
    generated = DecisionService(session, org).generate()
    run.decisions_created = generated.get("created", 0)

    phase("Measuring what PIE changed")
    notes["attribution"] = _run_attribution(session, org)
    # `phase` commits on the way *in*, which closes the previous phase rather
    # than this one — so without this the ledger rows would ride to whichever
    # commit the caller happens to make, holding a write transaction open past
    # the end of the work that produced them. §4 asks for a commit at the
    # boundary, and this is the boundary.
    session.commit()
    return notes


def _run_attribution(session: Session, org: str) -> dict:
    """Turn the quote evidence this run refreshed into ledger rows.

    Last phase on purpose: it reads ``QuoteDecision`` and ``QuoteOutcome``, and
    an outcome that arrived in this pull should be classified in this run rather
    than waiting a day for the next one.

    Best-effort, like the demo purge above. A detection problem must not fail a
    sync that has already written the customers, invoices and metrics — the
    ledger is derived state and the next run rebuilds it from the same evidence.
    The failure is logged and named in the notes rather than swallowed, because
    a report that silently stops being updated is the absence-of-evidence
    failure this whole module exists to avoid.

    Also recomputes the trial baseline. ``capture_baseline`` runs once at first
    connect, before any sync has happened, so the 90 days before the trial
    started held no rows and the "before" half of the comparison was empty —
    always, not merely usually, which made the central evaluation claim
    unproducible. Here the history has been pulled, so the same window can
    finally be measured. It is derived state and rewriting it is what it is for.
    """
    from ..attribution import detectors, ledger
    from ..attribution import evaluator as attribution_evaluator
    from ..domain.enums import ValueClass

    out: dict = {}
    try:
        results = detectors.run_all(session, org)
        drafts = [d for result in results.values() for d in result.events]
        _, created = ledger.record_all(session, org, drafts)
        # A full run over the whole window, so anything POTENTIAL that this run
        # did not re-find is an opportunity that has closed — most often a quote
        # the customer declined. Only correct because the run above is unscoped;
        # doing this after a windowed run would retire live rows outside it.
        retired = ledger.supersede_closed_opportunities(
            session, org,
            [d.event_key for d in drafts
             if d.value_class is ValueClass.POTENTIAL])
        out = {"events_recorded": created,
               "opportunities_closed": retired,
               "gaps": detectors.skip_summary(results)}

        trial = attribution_evaluator.current_trial(session, org)
        if trial is not None:
            attribution_evaluator.capture_baseline(session, org, trial)
            out["baseline_recomputed"] = True
    except Exception as exc:  # noqa: BLE001
        log.exception("attribution failed for %s; the ledger is unchanged", org)
        out = {"failed": str(exc)}
    return out


# ── dispatch ────────────────────────────────────────────────────────────────
def run_job(sync_run_id: str, since: date, full: bool,
            connection_id: Optional[str], *, analysis: bool = True,
            incremental: bool = True) -> None:
    """Run the job on its own session, because the request's is already closed.

    Public because ``start_all`` submits it to a thread pool of its own rather
    than to ``thread_dispatch``: it has to *wait* for the pulls, and a detached
    daemon thread cannot be waited on.
    """
    from ..db import SessionLocal

    session = SessionLocal()
    try:
        run = session.get(models.SyncRun, sync_run_id)
        if run is None:            # deleted between queueing and starting
            return
        execute_sync(session, run, since=since, full=full,
                     connection_id=connection_id, analysis=analysis,
                     incremental=incremental)
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
        target=run_job, args=(sync_run_id, since, full, connection_id),
        name=f"sync-{sync_run_id[:8]}", daemon=True).start()


Dispatch = Callable[[str, date, bool, Optional[str]], None]


@dataclass(frozen=True)
class _Target:
    """One connected company a run will read, and what to call it on screen."""
    connection_id: Optional[str]
    label: str
    #: Which system this book is read from — the connection row's own
    #: discriminator, carried explicitly because the alternative is a caller
    #: that omits it and silently takes a default: a pull that labels its rows
    #: with the wrong connector has them adopted into that connector's id
    #: space by ``repositories._for_upsert``, and no test in the tree would
    #: notice. The default exists only for the connectionless legacy pull,
    #: which is Zoho by definition (it reads the ``ZOHO_*`` environment).
    connector: str = "zoho"


def _sync_targets(session: Session, organization_id: str,
                  connection_id: Optional[str]) -> list[_Target]:
    """The books one run covers: the named connection, or every enabled one.

    The third case is the one that keeps the old behaviour intact rather than
    breaking a single-company deployment: an organization with no connection
    rows at all is either running against the fixture source in development or
    against the legacy environment-variable credentials, and both want exactly
    one unattributed pass — which is what an empty list of connections has
    always produced.
    """
    from .connections import get_connection, list_connections

    def target(conn) -> _Target:
        return _Target(conn.connection_id,
                       conn.label or conn.zoho_organization_id or "",
                       connector=getattr(conn, "connector", None) or "zoho")

    if connection_id is not None:
        try:
            return [target(get_connection(session, organization_id, connection_id))]
        except Exception:  # noqa: BLE001 — a label is never a reason to refuse a pull
            return [_Target(connection_id, "")]

    rows = list_connections(session, organization_id, enabled_only=True)
    if rows:
        return [target(r) for r in rows]
    return [_Target(None, "")]


def start_sync(session: Session, organization_id: str, *,
               since: Optional[date] = None,
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
        # This connection's job, not the organization's. Two connected Zoho
        # companies are two independent pulls against two different APIs, and
        # there is no reason one should wait for the other — they were
        # serialised only because this check did not look at which connection
        # was running.
        #
        # Still one per connection: clicking Sync twice on the same company
        # should show the job already in flight, not start a second pull that
        # fights it for the same rows.
        existing = active_run(session, organization_id,
                              connection_id=connection_id, any_connection=False)
        # An all-companies run covers this connection too, now that it really
        # reads every one of them rather than only the first. Starting a
        # single-company pull beside it would have two jobs writing the same
        # rows from the same API — idempotent, but twice the Zoho calls and a
        # progress display that cannot say which job the counter belongs to.
        # The reverse direction is already covered: the umbrella run's own
        # guard is keyed on a NULL connection.
        if existing is None and connection_id is not None:
            existing = active_run(session, organization_id,
                                  connection_id=None, any_connection=False)
        if existing is not None:
            return existing, False

        since = resolve_since(since)
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


def start_all(session: Session, organization_id: str, *,
              since: Optional[date] = None, full: bool = False,
              triggered_by: Optional[str] = None,
              max_workers: Optional[int] = None,
              incremental: bool = True) -> dict:
    """Pull every enabled connection at once, then analyse the organization once.

    **Fan out, then fan in.** The pulls are independent — Zoho meters its API
    per company, each pull holds its own client and so its own pacer, and every
    imported table is keyed on ``connection_id`` so three pulls write disjoint
    rows. Three companies that took three hours end to end take about as long as
    the slowest one.

    The four phases *after* the pull are not independent and must not be run per
    connection; ``execute_analysis`` says why at length. They run once here,
    against the union of what every pull touched, on the last run to finish —
    which is also the run the Data screen shows first, so the numbers land where
    somebody will look for them.

    Blocks until the whole cycle is done, because the caller is a cron or a CLI
    that needs an exit code. The Data screen's own button still uses
    ``start_sync`` per connection and returns immediately.
    """
    from ..seed import ensure_org_and_users
    from ..trust.keys import ensure_key
    from . import connections as connections_mod

    conns = connections_mod.list_connections(session, organization_id,
                                             enabled_only=True)
    if not conns:
        return {"organization_id": organization_id, "connections": 0,
                "runs": [], "analysed": False,
                "detail": "No enabled Zoho connection to pull from."}

    # Whatever the organization shares, made to exist once, here, before anything
    # is dispatched. Each pull would otherwise create it on first use, and three
    # pulls starting together all find it absent and all insert it — which is a
    # unique-constraint violation that takes down two of the three.
    #
    # ``ensure_key`` is safe on its own now, and this still belongs here: doing
    # setup once in the orchestrator is the same rule the analysis follows, and
    # it keeps the pulls to work that is genuinely per-connection.
    ensure_org_and_users(session)
    ensure_key(session, organization_id)
    session.commit()

    run_ids: list[str] = []
    already_running: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers or len(conns),
                            thread_name_prefix="sync-all") as pool:
        futures = []

        def dispatch(sync_run_id: str, since_: date, full_: bool,
                     connection_id: Optional[str]) -> None:
            # analysis=False: the organization-wide half is this function's job,
            # once, below — not each pull's.
            futures.append(pool.submit(run_job, sync_run_id, since_, full_,
                                       connection_id, analysis=False,
                                       incremental=incremental))

        for conn in conns:
            run, started = start_sync(
                session, organization_id, since=since, full=full,
                connection_id=conn.connection_id, triggered_by=triggered_by,
                dispatch=dispatch)
            run_ids.append(run.sync_run_id)
            if not started:
                # Someone else is already pulling this company. Its rows will
                # land, so the analysis below still needs to wait for it — but
                # this call did not start it and must not claim to have.
                already_running.append(conn.connection_id)
        # Leaving the block joins every pull. `result()` re-raises anything the
        # pool itself failed on; run_job handles its own errors onto the row.
        for future in futures:
            future.result()

    # The worker sessions wrote these rows; this one is still holding the QUEUED
    # copies it created.
    session.expire_all()
    rows = list(session.scalars(
        select(models.SyncRun).where(models.SyncRun.sync_run_id.in_(run_ids))))

    # The union of what the pulls moved, so the metric recompute stays targeted.
    # An empty union would mean a full rebuild of the organization's history, so
    # it is passed as None only when genuinely nothing was touched.
    touched: set[str] = set()
    for row in rows:
        touched.update((row.notes or {}).get("touched_customer_ids") or [])

    landed = [r for r in rows if r.status in ("OK", "PARTIAL")]
    result = {
        "organization_id": organization_id,
        "connections": len(conns),
        "already_running": already_running,
        "runs": [{"sync_run_id": r.sync_run_id, "connection_id": r.connection_id,
                  "status": r.status, "error": r.error,
                  "documents_fetched": r.documents_fetched,
                  "documents_resumed": r.documents_resumed} for r in rows],
        "analysed": False,
    }
    if not landed:
        # Nothing was read, so there is nothing new to analyse. Detecting over
        # an unchanged read model would emit the same signals against a fresh
        # timestamp and spend AI calls restating them.
        result["detail"] = "Every pull failed; the organization was not analysed."
        return result

    host = max(landed, key=lambda r: _aware(r.finished_at) or _aware(r.started_at))
    org_row = session.get(models.Organization, organization_id)

    def phase(name: str) -> None:
        host.phase = name
        host.heartbeat_at = _now()
        session.commit()

    host.status = "RUNNING"
    try:
        notes = execute_analysis(
            session, host, organization_id,
            customer_ids=sorted(touched) or None,
            timezone=(getattr(org_row, "timezone", None) or None), on_phase=phase)
        host.status = "OK" if host.error is None else "PARTIAL"
    except Exception as e:  # noqa: BLE001 — the pulls succeeded; say so
        log.exception("organization-wide analysis failed after %d pulls", len(landed))
        notes = {}
        host.status = "PARTIAL"
        host.error = f"Pull succeeded; analysis failed. {type(e).__name__}: {e}"[:1000]
    finally:
        merged = {k: v for k, v in (host.notes or {}).items() if k != "pull_only"}
        merged.pop("touched_customer_ids", None)
        host.notes = {**merged, **notes}
        host.phase = None
        host.finished_at = _now()
        host.heartbeat_at = _now()
        session.commit()

    result["analysed"] = True
    result["analysis_run_id"] = host.sync_run_id
    result["signals_emitted"] = host.signals_emitted
    result["decisions_created"] = host.decisions_created
    return result
