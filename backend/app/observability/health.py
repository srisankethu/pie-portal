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
        """Whether the engine every company's resolution runs through is here.

        Absent is a *supported* build (``deploy/backend.Dockerfile`` ships
        without the engine, and ``PieService._view`` says so in as many words),
        so it is DEGRADED and named rather than UNHEALTHY. It is never reported
        as ready: a missing engine is evidence, not a benign default.

        No local ``except`` on purpose. This check used to read
        ``pie_service.engine``, an attribute ``PieService`` has never defined, and
        its own ``except Exception`` turned the AttributeError into DEGRADED — so
        it was permanently amber for a reason that was never true and could never
        report healthy. ``HealthRegistry.check_all`` already catches, logs the
        traceback and marks the component UNHEALTHY, which is the loud outcome
        that gets a broken check fixed instead of scrolled past.
        """
        from ..config import settings

        # **The engine, not a catalogue.** This used to ask
        # `catalog_available`, which was a process-wide fact while one
        # deployment-wide catalogue answered everything. Catalogues are per
        # company now, so there is no single answer — and this check has no
        # database session to enumerate companies with, nor any business
        # asking a *process* health probe to load several tenants' indexes to
        # answer it.
        #
        # So it reports the half that is genuinely about this process: whether
        # the engine is present at all. Whether a given company has built its
        # catalogue is a per-tenant fact, and `Setup → Decoded catalogue`
        # reports it per company, with the corpus and pack that explain it.
        if not (settings.PIE_PARSER_ROOT / "tools" / "resolve_rfq.py").exists():
            return HealthStatus.DEGRADED, (
                "pie-parser is not present, so identity lookups and line "
                "resolution are unavailable for every company"
            )
        return HealthStatus.HEALTHY, (
            "pie-parser engine present; each company's catalogue is reported "
            "on Setup → Decoded catalogue"
        )

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

    def check_tenant_isolation() -> tuple[HealthStatus, Optional[str]]:
        """Whether the connection serving requests is one policies actually bind.

        The companion to ``check_backups``: both answer a question that is
        otherwise a memory, and both refuse the comfortable answer. Row-level
        security is the security control most likely to be *installed and
        inert*, because a PostgreSQL superuser carries ``rolbypassrls`` — which
        no policy overrides and ``FORCE ROW LEVEL SECURITY`` does not touch,
        since FORCE binds a table's owner and nothing binds BYPASSRLS. Both the
        compose role and the sandbox role are superusers by construction
        (``initdb -U`` makes the bootstrap superuser), so this is the expected
        state of a deployment that has not been changed, not a hypothetical.

        Asked of ``AppSessionLocal`` rather than ``SessionLocal``, and that is
        the entire point. The privileged connection is *supposed* to bypass —
        migrations and cross-tenant background jobs need it to. What must not
        bypass is the one that serves requests.

        SQLite is healthy and says so. There are no policies on that dialect at
        all, and amber on every developer's machine is a light nobody reads —
        the same argument ``check_scheduler`` makes about a fixture source. What
        it costs is stated in the message rather than hidden: on SQLite the
        Python ``organization_id`` filters are the only tenant boundary there
        has ever been.

        No local ``except``. ``check_all`` catches and marks UNHEALTHY, which is
        the loud outcome — the lesson ``check_pie_parser`` records above.
        """
        from sqlalchemy import text

        from ..db import AppSessionLocal, app_engine, engine

        if app_engine.dialect.name != "postgresql":
            return HealthStatus.HEALTHY, (
                f"No row-level security on {app_engine.dialect.name}: tenant "
                "scoping here is the application's own organization_id filters "
                "and nothing else"
            )

        with AppSessionLocal() as session:
            row = session.execute(text(
                "SELECT current_user AS who, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user")).one()

        if row.rolsuper or row.rolbypassrls:
            # Deliberately not softened by whether any policy exists yet. A
            # connection that cannot be governed is the finding; policies
            # arriving later would silently do nothing, which is the shape this
            # check exists to make impossible.
            shared = " (the same connection as migrations and background jobs)" \
                if app_engine is engine else ""
            return HealthStatus.UNHEALTHY, (
                f"Requests are served as {row.who!r}{shared}, which bypasses "
                "every row-level security policy: "
                f"rolsuper={row.rolsuper}, rolbypassrls={row.rolbypassrls}. "
                "Set APP_DATABASE_URL to a role that is neither. See "
                "docs/postgres.md."
            )
        return HealthStatus.HEALTHY, (
            f"Requests are served as {row.who!r}, which row-level security "
            "policies apply to"
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
    health.register("backups", check_backups)
    health.register("tenant_isolation", check_tenant_isolation)
