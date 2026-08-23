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

        **A follower is HEALTHY.** Only one worker holds the scheduler lease at
        a time and the rest tick without acting, so following is the normal
        state for all but one process — reporting it as a fault would make
        this component amber on most workers and flap depending on which one
        answered, which is exactly the noise this check was fixed to stop
        making. (The component checks are served by
        ``/api/v1/internal/observability/health`` via ``check_all``, not by
        ``/api/health``, which reads migration state directly.)

        The verdict stays the local, per-process fact: is this worker's thread
        up. Which worker leads is *context*, appended to the message so an
        operator reading two workers' health can tell one story from two.

        The lease is read, never written. A health check that claims a lease
        would elect a leader by being asked how things are, and one that could
        only pass while the database accepts writes would duplicate
        ``check_database`` badly.

        That read *is* guarded, unlike the rest of this function, and the
        exception it swallows is the point rather than an oversight. The
        verdict above is the local per-process fact — is this worker's thread
        up — and the lease only decorates it. Letting the read decide would
        make the scheduler component UNHEALTHY on the ordinary BEHIND state
        (§4): deploy this code against a database still at ``a7syncguard``,
        ``process_leases`` does not exist yet, and an operator is told the
        scheduler is broken when the thread is running fine and the real answer
        — a pending migration — is already reported by ``check_database`` and
        by ``/api/health``, which reports migration state directly and is the
        endpoint an operator is pointed at for it. Misattributing a schema
        gap to a healthy subsystem
        is the "degrade, not lie" rule read backwards.
        """
        from sqlalchemy.exc import SQLAlchemyError

        from .. import lease
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
        try:
            with session_factory() as session:
                leader = lease.held_by(session, sync_scheduler.LEASE_NAME)
        except SQLAlchemyError as e:
            # See the docstring: the thread is up, which is this check's
            # verdict. Name what could not be read rather than dropping the
            # context silently — an operator who sees this alongside a red
            # `database` component has the whole story.
            role = f"leadership unreadable ({type(e).__name__})"
        else:
            if leader is None:
                # Nobody holds it — the gap between a leader lapsing and the
                # next tick claiming it. Named rather than smoothed over: if it
                # persists across several checks, no worker is scheduling.
                role = "lease unheld; the next tick claims it"
            elif leader == lease.holder_id():
                role = "this worker holds the lease"
            else:
                role = f"following {leader}"
        return HealthStatus.HEALTHY, (
            f"Auto-sync scheduler running (tick {sync_scheduler.TICK_SECONDS}s; "
            f"{role})"
        )

    health.register("database", check_database)
    health.register("pie_parser", check_pie_parser)
    health.register("scheduler", check_scheduler)
