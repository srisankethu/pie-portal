"""Operations dashboard API endpoints.

Provides aggregated metrics, capacity, health, and background-job information
for the PIE Operations Dashboard.

**Where each number comes from, because they do not all come from one place.**

- Jobs and syncs are read from ``sync_runs`` — the persisted job model. Every
  worker sees the same rows, they survive a restart, and they are scoped to the
  organization asking.
- API and database figures come from ``metrics``, which is per-process. A
  worker reports its own share of the traffic and cannot see its siblings'. The
  raw export at ``/observability/metrics`` says so in the payload; the capacity
  components below carry a ``basis`` for the same reason.

These used to come from a third place — an in-memory ``WorkloadTracker`` that
no application code ever called, so ``/observability/jobs`` and
``/observability/syncs`` returned zero for every field, always. Zero active
jobs reads as "all quiet", which is the benign default CLAUDE.md §1 forbids:
the evidence was not thin, it was absent, and the endpoints said everything was
fine. The tracker is gone rather than wired up — ``sync_runs`` already held
these facts, more truthfully than a per-process copy could.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..clock import aware as _aware, iso as _iso
from ..ingestion import jobs
from .capacity import CapacityCalculator
from .health import health
from .metrics import metrics

log = logging.getLogger("pie_portal.observability.dashboard")

#: The window the two run-derived endpoints report over, and the sentence that
#: says what the window actually selects. Both are published in the payload:
#: "the last 24 hours" is ambiguous about a run that started yesterday and
#: finished an hour ago, and a reader comparing two numbers needs to know which
#: rows each counted. See ``jobs.runs_touching`` for which runs fall in it.
WINDOW_HOURS = 24
WINDOW_BASIS = "runs that started or finished in the last 24 hours"


def _latency_ms(histogram: Any) -> Optional[dict[str, Optional[float]]]:
    """A histogram's p50/p95/p99 in milliseconds, or None if it cannot say.

    Guards on the histogram *holding data*, not merely existing. The six call
    sites this replaces each read ``hist.get_percentile(50) * 1000`` behind an
    ``if hist else None`` — but every histogram is created at import, before a
    single observation, and ``get_percentile`` correctly returns ``None`` when
    it has nothing to report. ``None * 1000`` is a ``TypeError``, so the guard
    protected against the one case that could not happen and not against the one
    that could.

    Latent rather than live: reaching these endpoints means authenticating
    first, which records a request and a query, so both histograms are usually
    warm by the time anything asks. The case that bites is
    ``instrument_database()`` failing — ``lifespan`` catches that and continues
    — after which ``db_query_duration_seconds`` stays empty for the life of the
    process and ``/observability/database`` is a permanent 500.

    Returns ``None`` for the whole block when there is no histogram at all, and
    a dict of ``None`` percentiles when there is one that has seen nothing. The
    two are different facts: "not instrumented" and "instrumented, no traffic
    yet", and a screen should be able to tell them apart.
    """
    if histogram is None:
        return None
    return {
        f"p{p}": (None if (v := histogram.get_percentile(p)) is None else v * 1000)
        for p in (50, 95, 99)
    }


def _phase_of(run: Any) -> str:
    """What a live run is doing, in the words the run itself uses.

    ``phase`` is the screen-facing sentence the job maintains ("Reading
    invoices"). It is null before the work starts and after it ends, so a
    QUEUED run is reported as queued rather than as a blank bucket.
    """
    return run.phase or ("queued" if run.status == "QUEUED" else "running")


def _count_by(values: Iterable[str]) -> dict[str, int]:
    """Group labels and count them."""
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _elapsed_seconds(run: Any) -> Optional[float]:
    """How long a finished run took, or ``None`` when the row cannot say.

    ``finished_at`` is nullable. A run whose process died without writing one
    has no duration, and inventing zero for it would inflate every throughput
    figure it is averaged into — dividing real records by a duration that
    omitted this run's time. ``None`` here is what lets the caller count how
    many rows it had to leave out and publish that count.
    """
    finished = _aware(run.finished_at)
    if finished is None:
        return None
    started = _aware(run.started_at)
    if started is None:
        return None
    return (finished - started).total_seconds()


class DashboardService:
    """Service for dashboard data aggregation.

    Tenant-scoped by default: the job and sync endpoints take the organization
    from the caller's principal, so a manager of one tenant cannot read
    another's sync activity.

    **Two figures deliberately cross that line, and both are named here rather
    than discovered later.** An exception a docstring does not list is the
    defect this branch has now corrected four times.

    - ``get_tenant_usage`` — a cross-organization ranking that predates this
      work. It exposes other organizations' ids and signal counts to any
      manager, which is a real disclosure and is *not* defended here; it is
      recorded as pre-existing and out of the scope that introduced this note.
    - ``get_capacity`` — worker utilization counts live ``sync_runs`` across
      every organization, because worker capacity is a property of the
      deployment and a tenant-scoped count would understate what the workers
      are actually carrying. What crosses is one integer folded into a ratio:
      no id, no name, no per-tenant number. It is still a channel — a manager
      watching that ratio move can infer that *somebody* is syncing — and the
      trade is made knowingly, because a capacity figure that only counts your
      own load is not a capacity figure. Before this change the component was a
      constant 0.0, so this is new, not inherited.
    """

    def __init__(self, session: Session, organization_id: str):
        self.session = session
        self.organization_id = organization_id
        self.capacity_calc = CapacityCalculator(session)

    # ── the sync-run reads both job-shaped endpoints share ──────────────────

    def _active_runs(self) -> tuple[list[Any], list[Any]]:
        """This organization's unfinished runs, split into live and stalled.

        Two lists rather than one number because they are two facts. A run in
        QUEUED or RUNNING whose heartbeat has gone cold is not work in
        progress — its process is almost certainly gone — but it is not nothing
        either, and it is the thing an operator most needs to see. Counting it
        as active overstates the load; dropping it reports a wedged connection
        as an idle one.
        """
        runs = jobs.unfinished_runs(self.session,
                                    organization_id=self.organization_id)
        live = [r for r in runs if not jobs.is_stale(r)]
        stalled = [r for r in runs if jobs.is_stale(r)]
        return live, stalled

    def _window_runs(self) -> list[Any]:
        """Runs that began or ended inside the reporting window, newest first."""
        since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
        return jobs.runs_touching(self.session, self.organization_id, since)

    def _history(self) -> dict[str, Any]:
        """Anchors that distinguish a quiet window from a dead one.

        A zero in ``recent_24h`` is the same integer whether nothing was due or
        the scheduler stopped queueing a week ago — an all-zero, all-green
        payload is exactly how a revoked Zoho refresh token, or an
        ``auto_sync_hours`` set to 0 by mistake, would present. These two dates
        are what tell them apart, so they are published even when the window is
        busy and there is nothing to diagnose.
        """
        last_any = jobs.last_run(self.session, self.organization_id)
        last_ok = jobs.last_run(self.session, self.organization_id, status="OK")
        return {
            "last_run_at": _iso(getattr(last_any, "started_at", None)),
            "last_successful_run_at": _iso(getattr(last_ok, "started_at", None)),
            "ever_run": last_any is not None,
            "basis": ("No run has ever been recorded for this organization"
                      if last_any is None else
                      "Most recent run of any status, and most recent OK run"),
        }

    @staticmethod
    def _stalled_block(stalled: list[Any]) -> dict[str, Any]:
        return {
            "count": len(stalled),
            "by_phase": _count_by(_phase_of(r) for r in stalled),
            "detail": (
                "Runs the table still calls active whose heartbeat went cold — "
                f"no report for over {int(jobs.STALE_AFTER.total_seconds() // 60)} "
                "minutes. Counted apart from active because they are neither "
                "running nor finished. Starting a sync on the same connection "
                "reaps them."
            ) if stalled else "",
        }

    def get_system_health(self) -> dict[str, Any]:
        """Get overall system health."""
        health.check_all()
        return health.to_dict()

    def get_current_load(self) -> dict[str, Any]:
        """Current load — API counters from this worker, background work from the table.

        The two halves have different bases and the payload says which is which.
        ``requests_total`` and ``queries_total`` are this process's since it
        started; ``background`` counts rows every worker can see.

        ``active_requests`` is ``null``. It used to be ``0``, returned by a
        helper whose comment called it a placeholder — a fabricated number on a
        screen labelled "Active Requests", indistinguishable from a genuinely
        idle server. Nothing tracks in-flight requests, so the honest answer is
        that it is not known, and ``basis`` names what is missing.
        """
        api_requests = metrics._metrics.get("api_requests_total")
        db_queries = metrics._metrics.get("db_queries_total")
        live, stalled = self._active_runs()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "organization_id": self.organization_id,
            "api": {
                "requests_total": api_requests.get() if api_requests else 0,
                "active_requests": None,
                "basis": ("Counters are this worker's since it started, not the "
                          "deployment's. In-flight requests are not tracked, so "
                          "active_requests is null rather than zero."),
            },
            "database": {
                "queries_total": db_queries.get() if db_queries else 0,
                "basis": "This worker's queries since it started.",
            },
            "background": {
                "active_jobs": len(live),
                "active_syncs": len(live),
                "stalled": len(stalled),
                "total_active": len(live),
                "basis": ("Live sync runs for this organization, from the "
                          "sync_runs table — every worker sees the same rows. "
                          "The ERP sync is the only background job kind "
                          "recorded, so jobs and syncs are the same rows."),
            },
        }

    def get_api_performance(self, window_minutes: int = 5) -> dict[str, Any]:
        """Get API performance metrics for a time window."""
        duration_hist = metrics._metrics.get("api_request_duration_seconds")
        errors_counter = metrics._metrics.get("api_errors_total")
        requests_counter = metrics._metrics.get("api_requests_total")

        total_requests = requests_counter.get() if requests_counter else 0
        total_errors = errors_counter.get() if errors_counter else 0

        return {
            "window_minutes": window_minutes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "requests_total": total_requests,
            "errors_total": total_errors,
            "error_rate": (total_errors / total_requests * 100) if total_requests > 0 else 0,
            "latency_ms": _latency_ms(duration_hist),
        }

    def get_database_status(self) -> dict[str, Any]:
        """Get database status and metrics."""
        connections = metrics._metrics.get("db_connections")
        queries = metrics._metrics.get("db_queries_total")
        errors = metrics._metrics.get("db_errors_total")
        query_duration = metrics._metrics.get("db_query_duration_seconds")

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connections": {
                "active": connections.get() if connections else 0,
            },
            "queries": {
                "total": queries.get() if queries else 0,
                "errors": errors.get() if errors else 0,
            },
            "latency_ms": _latency_ms(query_duration),
        }

    def get_background_jobs(self) -> dict[str, Any]:
        """Background job status for this organization, from ``sync_runs``.

        ``job_kinds`` is published because it is the honest answer to a
        question the shape of this payload invites: "which job types are there,
        and are the others all at zero?" There is one. The ERP sync is the
        whole of this deployment's background job model — ``SyncRun`` says so
        in its own docstring — so an empty ``active`` block means no sync is
        running, not that some other kind of job went unmeasured.

        PARTIAL is reported in its own right rather than folded into either
        side. A partial run wrote rows and did not finish; counting it
        completed overstates what was pulled, counting it failed understates it.
        """
        live, stalled = self._active_runs()
        window = self._window_runs()
        completed = [r for r in window if r.status == "OK"]
        partial = [r for r in window if r.status == "PARTIAL"]
        failed = [r for r in window if r.status == "FAILED"]

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "organization_id": self.organization_id,
            "source": "sync_runs",
            "job_kinds": ["erp_sync"],
            "active": {
                "count": len(live),
                "by_phase": _count_by(_phase_of(r) for r in live),
            },
            "stalled": self._stalled_block(stalled),
            "history": self._history(),
            "recent_24h": {
                "basis": WINDOW_BASIS,
                "completed": len(completed),
                "partial": len(partial),
                "failed": len(failed),
                "total_records_processed": sum(
                    jobs.records_written(r) for r in completed + partial),
            },
            "failures": [
                {
                    "job_kind": "erp_sync",
                    "sync_run_id": r.sync_run_id,
                    "connection_id": r.connection_id,
                    "status": r.status,
                    "error": r.error,
                    "timestamp": r.started_at.isoformat() if r.started_at else None,
                } for r in [r for r in window
                            if r.status in ("FAILED", "PARTIAL")][:10]
            ],
        }

    def get_zoho_sync_status(self) -> dict[str, Any]:
        """ERP sync status for this organization, from ``sync_runs``.

        Same rows as ``get_background_jobs``, read for what a sync-watcher
        wants: which connected company is pulling, how much arrived, and how
        fast. ``by_connection`` exists because three connected Zoho companies
        pull independently — a single active count cannot say which of them is
        moving, which is the same gap per-connection health was added to close.

        The tenant id is no longer carried on each issue. The payload is one
        organization's, so repeating it per row said nothing; the previous
        version published the first eight characters of it, which was a
        half-measure in both directions.
        """
        live, stalled = self._active_runs()
        window = self._window_runs()
        completed = [r for r in window if r.status == "OK"]
        partial = [r for r in window if r.status == "PARTIAL"]
        failed = [r for r in window if r.status == "FAILED"]
        ended = completed + partial

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "organization_id": self.organization_id,
            "source": "sync_runs",
            "active": {
                "count": len(live),
                "by_phase": _count_by(_phase_of(r) for r in live),
                "by_connection": _count_by(
                    r.connection_id or "all companies" for r in live),
            },
            "stalled": self._stalled_block(stalled),
            "history": self._history(),
            "recent_24h": {
                "basis": WINDOW_BASIS,
                "completed": len(completed),
                "partial": len(partial),
                "failed": len(failed),
                "total_records_fetched": sum(r.documents_fetched for r in ended),
                "total_records_processed": sum(
                    jobs.records_written(r) for r in ended),
                **self._throughput(ended),
            },
            "issues": [
                {
                    "sync_run_id": r.sync_run_id,
                    "connection_id": r.connection_id,
                    "status": r.status,
                    "error": r.error,
                    "timestamp": r.started_at.isoformat() if r.started_at else None,
                } for r in [r for r in window
                            if r.status in ("FAILED", "PARTIAL")][:5]
            ],
        }

    def get_capacity(self) -> dict[str, Any]:
        """Get capacity analysis."""
        return self.capacity_calc.get_overall_capacity()

    def get_tenant_usage(self, limit: int = 20) -> dict[str, Any]:
        """Signal counts per organization — the caller's own, and no longer every one.

        This query has no organization predicate and never had one, so any
        manager or owner of any tenant read back the *id and signal count of
        every organization on the deployment*. It reaches the screen through
        `/observability/tenants` and again inside `get_full_dashboard`.

        `signals` is now under a row-level security policy
        (`alembic/versions/d1rls_tenant_policies.py`), so on a deployment whose
        serving role does not bypass it this returns one row: the caller's. That
        is the correct answer and the leak is closed — but it is closed *by the
        database*, silently, and the `except` below would not have fired to tell
        anyone. Saying so here is the difference between a fix and a behaviour
        that changed under somebody.

        Two things follow, and they point in opposite directions. The predicate
        is deliberately still absent: adding one would make this look scoped on
        every deployment while it is only actually scoped where the policy binds
        — the appearance of a control instead of the control. And `scope` below
        is reported so a reader can tell which of the two they are looking at
        rather than inferring it from a row count.
        """
        try:
            from ..domain import models
            from sqlalchemy import func

            # Aggregate metrics by organization
            org_counts = (
                self.session.query(
                    models.Signal.organization_id,
                    func.count(models.Signal.id).label("signals_count"),
                )
                .group_by(models.Signal.organization_id)
                .order_by(func.count(models.Signal.id).desc())
                .limit(limit)
                .all()
            )

            tenants = []
            for org_id, count in org_counts:
                tenants.append({
                    "organization_id": org_id,
                    "signals_generated": count,
                })

            others = [t for t in tenants
                      if t["organization_id"] != self.organization_id]
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenants": tenants,
                "total_tenants": len(tenants),
                # Which of the two this is, read off the result rather than
                # asserted: "only this organization came back" is a fact, and
                # it is true either because the policy bound the query or
                # because there is only one tenant. A reader who has to work
                # that out from a row count will get it wrong the day a
                # deployment has one customer.
                "scope": "OWN_ORGANIZATION" if not others else "ALL_ORGANIZATIONS",
            }
        except Exception as e:
            log.exception("failed to fetch tenant usage")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "tenants": [],
            }

    def get_full_dashboard(self) -> dict[str, Any]:
        """Get complete dashboard data."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health": self.get_system_health(),
            "load": self.get_current_load(),
            "api": self.get_api_performance(),
            "database": self.get_database_status(),
            "jobs": self.get_background_jobs(),
            "syncs": self.get_zoho_sync_status(),
            "capacity": self.get_capacity(),
            "tenants": self.get_tenant_usage(),
        }

    @staticmethod
    def _throughput(ended: list[Any]) -> dict[str, Any]:
        """Records per second over the runs that can actually answer it.

        Returns the figure **and the basis for it**, always both. Three ways
        this can fail to be knowable, and none of them is zero:

        - no run ended in the window — nothing to divide;
        - runs ended but none recorded a ``finished_at``, so no duration exists;
        - the runs that did are so short they total zero seconds.

        The previous implementation returned ``0.0`` for the first of these
        (``if not syncs: return 0.0``) and for the third. Zero records per
        second is what a sync that is failing to move data looks like, and it
        was being reported for a system that had simply not been asked to do
        anything — the exact tell CLAUDE.md §1 names. It also summed
        ``duration_seconds or 0``, so a run with no duration on record silently
        shrank the denominator and inflated the rate.

        ``runs_excluded`` is published rather than logged: a throughput
        computed from three of eight runs is a different claim from one
        computed from all eight, and the reader cannot see the rows.
        """
        timed = [(r, e) for r in ended if (e := _elapsed_seconds(r)) is not None]
        excluded = len(ended) - len(timed)
        seconds = sum(e for _, e in timed)

        if not timed:
            reason = ("No run ended in this window." if not ended else
                      "No run that ended recorded a finish time.")
        elif seconds <= 0:
            reason = "The runs that ended total zero measurable seconds."
        else:
            reason = None

        return {
            "throughput_records_per_sec": None if reason else round(
                sum(jobs.records_written(r) for r, _ in timed) / seconds, 2),
            "throughput_basis": reason or (
                f"{len(timed)} run(s) that ended in this window, "
                f"over {round(seconds, 1)}s of run time."),
            "throughput_runs_excluded": excluded,
        }
