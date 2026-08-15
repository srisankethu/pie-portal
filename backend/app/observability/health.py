"""Health check infrastructure for PIE.

Provides liveness, readiness, and dependency health endpoints.
Tracks health status of key components: database, ERP connection, queue, workers.
"""
from __future__ import annotations

import logging
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
        """Check if pie-parser engine is available."""
        try:
            from ..pie_service import pie_service
            if pie_service.engine is None:
                return HealthStatus.UNHEALTHY, "PIE parser engine not initialized"
            return HealthStatus.HEALTHY, "PIE parser ready"
        except Exception as e:
            return HealthStatus.DEGRADED, f"PIE parser error: {str(e)}"

    def check_scheduler() -> tuple[HealthStatus, Optional[str]]:
        """Check if background scheduler is running."""
        try:
            from ..ingestion.scheduler import scheduler
            if scheduler and scheduler.running:
                return HealthStatus.HEALTHY, f"Scheduler running with {len(scheduler._threads)} workers"
            return HealthStatus.DEGRADED, "Scheduler not running"
        except Exception as e:
            return HealthStatus.DEGRADED, f"Scheduler check failed: {str(e)}"

    health.register("database", check_database)
    health.register("pie_parser", check_pie_parser)
    health.register("scheduler", check_scheduler)
