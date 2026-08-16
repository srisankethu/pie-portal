"""Operations dashboard API endpoints.

Provides aggregated metrics, capacity, health, and workload information
for the PIE Operations Dashboard.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from .capacity import CapacityCalculator
from .health import health
from .metrics import metrics
from .workload import workload

log = logging.getLogger("pie_portal.observability.dashboard")


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


class DashboardService:
    """Service for dashboard data aggregation."""

    def __init__(self, session: Session):
        self.session = session
        self.capacity_calc = CapacityCalculator()

    def get_system_health(self) -> dict[str, Any]:
        """Get overall system health."""
        health.check_all()
        return health.to_dict()

    def get_current_load(self) -> dict[str, Any]:
        """Get current system load metrics."""
        api_requests = metrics._metrics.get("api_requests_total")
        db_queries = metrics._metrics.get("db_queries_total")
        active_jobs = len(workload.get_active_jobs())
        active_syncs = len(workload.get_active_syncs())

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "api": {
                "requests_total": api_requests.get() if api_requests else 0,
                "active_requests": self._estimate_active_requests(),
            },
            "database": {
                "queries_total": db_queries.get() if db_queries else 0,
                "active_jobs": active_jobs,
            },
            "background": {
                "active_jobs": active_jobs,
                "active_syncs": active_syncs,
                "total_active": active_jobs + active_syncs,
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
        """Get background job status."""
        active = workload.get_active_jobs()
        recent = workload.get_recent_jobs(hours=24)

        completed = [j for j in recent if j.status.value == "completed"]
        failed = [j for j in recent if j.status.value == "failed"]

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active": {
                "count": len(active),
                "by_type": self._group_by_type([m.job_type for m in active.values()]),
            },
            "recent_24h": {
                "completed": len(completed),
                "failed": len(failed),
                "total_records_processed": sum(j.records_processed for j in completed),
            },
            "failures": [
                {
                    "job_type": j.job_type,
                    "error": j.error,
                    "timestamp": j.start_time.isoformat(),
                } for j in failed[-10:]  # Last 10 failures
            ],
        }

    def get_zoho_sync_status(self) -> dict[str, Any]:
        """Get Zoho sync status."""
        active = workload.get_active_syncs()
        recent = workload.get_recent_syncs(hours=24)

        completed = [s for s in recent if not s.error]
        failed = [s for s in recent if s.error]

        total_records = sum(s.records_fetched for s in completed)
        total_processed = sum(s.records_inserted + s.records_updated for s in completed)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active": {
                "count": len(active),
                "by_type": self._group_by_type([s.sync_type.value for s in active.values()]),
            },
            "recent_24h": {
                "completed": len(completed),
                "failed": len(failed),
                "total_records_fetched": total_records,
                "total_records_processed": total_processed,
                "throughput_records_per_sec": self._calc_throughput(completed),
            },
            "issues": [
                {
                    "sync_type": s.sync_type.value,
                    "tenant": s.tenant_id[:8],  # First 8 chars of tenant ID
                    "error": s.error,
                    "timestamp": s.start_time.isoformat(),
                } for s in failed[-5:]  # Last 5 failures
            ],
        }

    def get_capacity(self) -> dict[str, Any]:
        """Get capacity analysis."""
        return self.capacity_calc.get_overall_capacity()

    def get_tenant_usage(self, limit: int = 20) -> dict[str, Any]:
        """Get usage by tenant."""
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

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenants": tenants,
                "total_tenants": len(tenants),
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
    def _group_by_type(items: list[str]) -> dict[str, int]:
        """Group items by type and count."""
        result = {}
        for item in items:
            result[item] = result.get(item, 0) + 1
        return result

    @staticmethod
    def _estimate_active_requests() -> int:
        """Estimate active requests (request-scoped instrumentation)."""
        # This would be tracked at the middleware level in production
        # For now, return 0 as a placeholder
        return 0

    @staticmethod
    def _calc_throughput(syncs: list) -> float:
        """Calculate throughput in records/sec."""
        if not syncs:
            return 0.0
        total_duration = sum(s.duration_seconds or 0 for s in syncs)
        total_records = sum(s.records_inserted + s.records_updated for s in syncs)
        if total_duration == 0:
            return 0.0
        return round(total_records / total_duration, 2)
