"""Health check infrastructure for PIE.

Provides liveness, readiness, and dependency health endpoints.
Tracks health status of key components: database, ERP connection, queue, workers.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

log = logging.getLogger("pie_portal.observability.health")


class HealthStatus(str, Enum):
    """Health status of a component."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ComponentHealth:
    """Health status of a single component."""

    def __init__(self, name: str, status: HealthStatus = HealthStatus.UNKNOWN):
        self.name = name
        self.status = status
        self.timestamp = datetime.now(timezone.utc)
        self.message: Optional[str] = None
        self.details: dict[str, Any] = {}
        self.check_fn: Optional[Callable[[], tuple[HealthStatus, Optional[str]]]] = None

    def check(self) -> None:
        """Run the health check for this component."""
        if self.check_fn:
            status, message = self.check_fn()
            self.status = status
            self.message = message
            self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Export as dict."""
        return {
            "name": self.name,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "details": self.details,
        }


class HealthRegistry:
    """Registry of health checks."""

    def __init__(self):
        self._components: dict[str, ComponentHealth] = {}

    def register(self, name: str, check_fn: Callable[[], tuple[HealthStatus, Optional[str]]]) -> ComponentHealth:
        """Register a health check function."""
        component = ComponentHealth(name)
        component.check_fn = check_fn
        self._components[name] = component
        return component

    def check_all(self) -> None:
        """Run all health checks."""
        for component in self._components.values():
            try:
                component.check()
            except Exception as e:
                log.exception("health check failed for %s", component.name)
                component.status = HealthStatus.UNHEALTHY
                component.message = str(e)

    def get_overall_status(self) -> HealthStatus:
        """Get overall health status."""
        statuses = [c.status for c in self._components.values()]
        if not statuses:
            return HealthStatus.UNKNOWN
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        if HealthStatus.HEALTHY in statuses:
            return HealthStatus.HEALTHY
        return HealthStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        """Export as dict."""
        components = {name: comp.to_dict() for name, comp in self._components.items()}
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": self.get_overall_status().value,
            "components": components,
        }


# Global health registry
health = HealthRegistry()


def register_health_checks(engine: Any, session_factory: Any) -> None:
    """Register standard health checks for database and dependencies."""

    def check_database() -> tuple[HealthStatus, Optional[str]]:
        """Check database connectivity."""
        try:
            from sqlalchemy import text
            with session_factory() as session:
                session.execute(text("SELECT 1"))
            return HealthStatus.HEALTHY, "Database responding"
        except Exception as e:
            return HealthStatus.UNHEALTHY, f"Database error: {str(e)}"

    def check_pie_parser() -> tuple[HealthStatus, Optional[str]]:
        """Whether the catalogue pie-parser resolves against actually loaded.

        ``catalog_available`` is the fact worth reporting and the only cheap one:
        it is memoised (failure included, so a broken index is not re-tried per
        request), it never raises by contract, and it is true of the thing that
        matters — with no index every identity lookup returns None and every
        quote line resolves PIE OFFLINE.

        Absent is a *supported* build (``deploy/backend.Dockerfile`` ships
        without the engine, and ``PieService._ensure_index`` says so in as many
        words), so it is DEGRADED and named rather than UNHEALTHY. It is never
        reported as ready: a missing catalogue is evidence, not a benign default.

        No local ``except`` on purpose. This check used to read
        ``pie_service.engine``, an attribute ``PieService`` has never defined, and
        its own ``except Exception`` turned the AttributeError into DEGRADED — so
        it was permanently amber for a reason that was never true and could never
        report healthy. ``HealthRegistry.check_all`` already catches, logs the
        traceback and marks the component UNHEALTHY, which is the loud outcome
        that gets a broken check fixed instead of scrolled past.
        """
        from ..pie_service import pie_service

        if not pie_service.catalog_available:
            return HealthStatus.DEGRADED, (
                "PIE catalogue not loaded: identity lookups and line resolution "
                "are unavailable"
            )
        return HealthStatus.HEALTHY, "PIE catalogue loaded"

    def check_scheduler() -> tuple[HealthStatus, Optional[str]]:
        """Whether the auto-sync scheduler is in the state this deployment asked for.

        ``start_scheduler`` declines a fixture source by design — sample data has
        nothing to keep fresh — so "not running" is correct there and reporting
        it as a fault would be exactly the noise this check is being fixed to
        stop making. Where the source is live the thread is meant to be up, and
        the two ways it can be missing are different facts an operator acts on
        differently: never started (``start_scheduler`` raised at boot, which
        ``main`` logs and continues past) versus started and since gone.

        Read here rather than asked of the module because ``ingestion.scheduler``
        exposes no object to ask: this check used to import a module-level
        ``scheduler`` that has never existed, and swallowed the ImportError as
        DEGRADED. The thread name mirrors the one ``start_scheduler`` gives it;
        it is in the message so a rename shows up as a nameable false alarm
        rather than a silent amber light.
        """
        from ..config import settings
        from ..ingestion import scheduler as sync_scheduler

        if settings.ZOHO_SOURCE != "api":
            return HealthStatus.HEALTHY, (
                f"Auto-sync not scheduled: ZOHO_SOURCE is {settings.ZOHO_SOURCE!r}"
            )
        if not sync_scheduler._started.is_set():
            return HealthStatus.DEGRADED, "Auto-sync scheduler was never started"
        if not any(t.name == "sync-scheduler" and t.is_alive()
                   for t in threading.enumerate()):
            return HealthStatus.UNHEALTHY, (
                "Auto-sync scheduler was started but no live 'sync-scheduler' "
                "thread remains"
            )
        # Which process is the one that ticks. Every process runs the thread —
        # that is what makes the schedule survive a restart of whichever one
        # held it — so "running" here is not the same claim as "this deployment
        # is scheduling". A live thread with no holder anywhere means the lease
        # is stuck, and reporting it healthy would be the benign default.
        from .. import leases

        with session_factory() as session:
            holder = leases.current_holder(session, sync_scheduler.LEASE)
        if holder is None:
            return HealthStatus.DEGRADED, (
                f"Auto-sync scheduler thread is up but nothing holds the "
                f"{sync_scheduler.LEASE} lease, so no process is ticking"
            )
        return HealthStatus.HEALTHY, (
            f"Auto-sync scheduler running (tick {sync_scheduler.TICK_SECONDS}s); "
            f"ticker is {holder}"
        )

    def check_queue() -> tuple[HealthStatus, Optional[str]]:
        """Whether background work is being drained, and whether any of it died.

        Three facts, in the order they change what an operator does: a
        deployment that does not use the queue has nothing to report; one that
        does but has no live worker is not running its syncs at all; and a
        dead-lettered message is work that has failed every attempt and is
        waiting for a person. Dead letters are DEGRADED rather than UNHEALTHY —
        the platform is serving, one job is not — and they are *named*, because
        a queue that reports healthy while holding failed work is the benign
        default §1 warns about.
        """
        from ..config import settings
        from ..messaging import depth, worker_running

        if not settings.queue_dispatch and not settings.queue_worker_enabled:
            return HealthStatus.HEALTHY, (
                f"Queue not in use: SYNC_DISPATCH is {settings.SYNC_DISPATCH!r}"
            )
        if not worker_running():
            return HealthStatus.UNHEALTHY, (
                "Queue dispatch is on but no live 'queue-worker' thread "
                "remains: queued jobs will not run"
            )
        with session_factory() as session:
            counts = depth(session)
        if counts.get("DEAD_LETTER"):
            return HealthStatus.DEGRADED, (
                f"{counts['DEAD_LETTER']} queued message(s) failed every "
                f"attempt and are waiting for a person; "
                f"{counts.get('PENDING', 0)} pending"
            )
        return HealthStatus.HEALTHY, (
            f"Queue worker running ({counts.get('PENDING', 0)} pending, "
            f"{counts.get('CLAIMED', 0)} in flight)"
        )

    def check_threshold_registry() -> tuple[HealthStatus, Optional[str]]:
        """Whether every stamp this process has written can be dereferenced.

        Four observations, not estimates — each one was counted at the moment
        it happened, in this process, since start-up:

        ``post_epoch_gaps``  a stamp was asked for, was not recorded, and the
                             registry was demonstrably running when it was
                             minted. That is an invariant violation: a stamp
                             reached a persisted row without its values being
                             captured, and those rows are unexplainable forever.
                             UNHEALTHY, because the damage is already written
                             and every further row makes it larger.
        ``collisions``       two different policies produced the same ten-hex
                             stamp. Rows carrying it may mean either.
        ``stamps without a pre-image``  a flush carried a version this process
                             never minted *and* no recorded row explains, so
                             there was nothing to record. Not yet a gap — the
                             row is stamped and may be resolvable from
                             elsewhere — but it is the shape that becomes one.
        ``recording failures``  a recording write could not be made: the table
                             is not there yet in a deploy-before-migrate
                             window, a permission error, a dialect with no
                             ON CONFLICT support. The business write it rode
                             inside was unaffected, which is the design — but
                             the rows it committed meanwhile are stamped with
                             a policy nothing recorded.

        Nothing here reads the database. A clean registry is a *quiet* one, and
        a per-request scan of eight stamped tables to prove a negative would
        cost more than the fault it is looking for. A zero count is a genuine
        "nothing has gone wrong in this process", never an absence of evidence
        dressed as a pass — the counters only move when something is observed.
        """
        from .. import threshold_registry as registry

        if not registry.installed():
            return HealthStatus.UNHEALTHY, (
                "Threshold registry is not installed: threshold versions "
                "stamped from now on will not be dereferenceable"
            )
        # Summed across tenants here on purpose, and *said* to be: this is one
        # process's health, not one book's. The two per-organization counters
        # are keyed by org since a review found `CoverageReport` reporting one
        # tenant's gaps as another's; a health check legitimately wants the
        # whole process, and the message below no longer claims otherwise.
        gaps = sum(registry._POST_EPOCH_GAPS.values())
        collisions = sum(registry._COLLISIONS.values())
        orphans = sum(registry._MISSING_PRE_IMAGE.values())
        failures = registry._RECORDING_FAILURES["count"]
        if gaps:
            return HealthStatus.UNHEALTHY, (
                f"{gaps} threshold stamp(s) were minted after a tenant's "
                f"registry epoch and never recorded; the rows carrying them "
                f"cannot be explained. This is a defect in the recording path"
            )
        if collisions:
            return HealthStatus.DEGRADED, (
                f"{collisions} threshold version collision(s): two policies "
                f"share one stamp, so rows carrying it are ambiguous"
            )
        # DEGRADED rather than UNHEALTHY, deliberately, and it is the one
        # judgement call in this check. A failed bookkeeping write is a real
        # defect and rows are committing unexplainable while it lasts — but its
        # commonest cause is a deploy that is ahead of its migration, and
        # answering that with a 503 would take the deployment out of rotation
        # over the bookkeeping rather than over the business write, inverting
        # the priority this whole module is built around. The schema gap itself
        # is reported by ``check_database``, which is where a 503 belongs.
        if failures:
            return HealthStatus.DEGRADED, (
                f"{failures} threshold recording write(s) failed, so rows "
                f"committed since carry stamps nothing recorded. Check whether "
                f"threshold_versions exists and is writable"
            )
        if orphans:
            return HealthStatus.DEGRADED, (
                f"{orphans} row(s) were flushed carrying a threshold version "
                f"this process never minted, so nothing was recorded for them"
            )
        return HealthStatus.HEALTHY, "Threshold versions recorded as stamped"

    health.register("database", check_database)
    health.register("pie_parser", check_pie_parser)
    health.register("scheduler", check_scheduler)
    health.register("queue", check_queue)
    health.register("threshold_registry", check_threshold_registry)
