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
        """The worst thing any component says, where UNKNOWN is worse than fine.

        The ordering used to end ``if HEALTHY in statuses: return HEALTHY``,
        which meant **one** healthy component made the whole registry healthy
        while another sat at UNKNOWN — a component whose state could not be
        determined was invisible in the verdict. Nothing hit it while every
        registered check returned a real status after ``check_all``, and it is
        the same shape as every defect §1 lists: absence of evidence read as a
        pass. A registry that has never been checked now says UNKNOWN rather
        than reporting the health of whichever component was registered first.

        UNKNOWN ranks above HEALTHY and below DEGRADED deliberately: "we cannot
        tell" needs attention, and a known impairment needs more.
        """
        statuses = [c.status for c in self._components.values()]
        if not statuses:
            return HealthStatus.UNKNOWN
        for verdict in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED,
                        HealthStatus.UNKNOWN):
            if verdict in statuses:
                return verdict
        return HealthStatus.HEALTHY

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

    def check_backups() -> tuple[HealthStatus, Optional[str]]:
        """Whether a recent, plausible database dump exists where one was promised.

        ``scripts/restore_drill.py`` proves on every ``make verify`` that the
        *procedure* in ``docs/hosting.md`` round-trips this schema. It says
        nothing whatever about a backup existing — it dumps a database it
        created seconds earlier. This is the other half, and it is the half an
        operator finds out about at the worst possible moment.

        Five outcomes, and each is a different thing to go and do:

        * **Not production and nothing configured** — healthy, and named. A
          development database is derived: a full re-sync rebuilds it from Zoho
          and ``app.bootstrap`` builds it from nothing, so there is nothing
          there worth a retention policy. This mirrors ``check_scheduler``
          reporting a fixture source as healthy rather than as a fault.
        * **Production and nothing configured** — the loudest thing this check
          can say, because it means there are no backups. An unset value is not
          a statement that backups are handled elsewhere; it is the absence of
          one, and reading it as good news is the §1 failure this component
          exists to close.
        * **Configured but unreadable or empty** — somebody said dumps land
          here and none do. A missing directory is usually a volume that did
          not mount; an empty one is a cron that has never once succeeded.
        * **Newest dump too small to be a database** — freshness alone would
          let a zero-byte file pass as a backup, which is a green check over an
          empty set. ``BACKUP_MIN_BYTES`` is the floor.
        * **Newest dump older than the window** — the cron has stopped. Amber
          rather than red: a stale backup is still a backup, and the distinction
          matters at three in the morning.

        The file's own **mtime** is the age, not its name. A name is written by
        whoever wrote the file and a date in one proves only that somebody typed
        it; ``backup.sh`` renames into place only after ``gzip -t`` passes, so
        an mtime here is the moment a *complete* dump landed.

        No local ``except``: ``check_all`` catches, logs the traceback and marks
        the component UNHEALTHY, which is the loud outcome that gets a broken
        check fixed rather than a permanently amber one nobody reads — the
        lesson ``check_pie_parser`` records above.
        """
        from ..config import settings

        directory = settings.BACKUP_DIR
        if directory is None:
            if not settings.is_production:
                return HealthStatus.HEALTHY, (
                    f"Backups not checked: APP_ENV is {settings.APP_ENV!r} and "
                    "this database is derived — a full sync rebuilds it"
                )
            return HealthStatus.UNHEALTHY, (
                "No BACKUP_DIR is configured on a production deployment, so "
                "nothing here can say a backup exists. See docs/hosting.md."
            )

        if not directory.is_dir():
            return HealthStatus.UNHEALTHY, (
                f"BACKUP_DIR {directory} is not a readable directory — usually "
                "a volume that did not mount"
            )

        dumps = sorted((p for p in directory.glob("*.sql.gz") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not dumps:
            return HealthStatus.UNHEALTHY, (
                f"BACKUP_DIR {directory} holds no *.sql.gz dump, so the backup "
                "job has never once completed"
            )

        newest = dumps[0]
        size = newest.stat().st_size
        if size < settings.BACKUP_MIN_BYTES:
            return HealthStatus.UNHEALTHY, (
                f"The newest dump {newest.name} is {size} bytes, below the "
                f"{settings.BACKUP_MIN_BYTES}-byte floor. That is a file, not a "
                "database."
            )

        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            newest.stat().st_mtime, tz=timezone.utc)
        hours = age.total_seconds() / 3600
        if hours > settings.BACKUP_MAX_AGE_HOURS:
            return HealthStatus.DEGRADED, (
                f"The newest dump {newest.name} is {hours:.1f}h old, past the "
                f"{settings.BACKUP_MAX_AGE_HOURS}h window. The backup job has "
                f"stopped. {len(dumps)} dump(s) retained."
            )
        return HealthStatus.HEALTHY, (
            f"Newest dump {newest.name} is {hours:.1f}h old "
            f"({size / 1_048_576:.1f} MiB); {len(dumps)} retained"
        )

    health.register("database", check_database)
    health.register("pie_parser", check_pie_parser)
    health.register("scheduler", check_scheduler)
    health.register("backups", check_backups)
