"""Capacity monitoring and calculation for PIE.

Measures current resource utilization and calculates safe headroom.
Provides forecasting based on recent trends if sufficient history exists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .metrics import metrics
from .workload import workload

log = logging.getLogger("pie_portal.observability.capacity")


@dataclass
class Utilization:
    """Resource utilization metrics."""
    name: str
    current: float  # 0-1
    threshold_warning: float = 0.75
    threshold_critical: float = 0.90
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    @property
    def status(self) -> str:
        """Get status band: healthy, warning, critical."""
        if self.current >= self.threshold_critical:
            return "critical"
        if self.current >= self.threshold_warning:
            return "warning"
        return "healthy"

    @property
    def safe_capacity_multiplier(self) -> float:
        """How many times the current load can be added before hitting threshold_critical."""
        if self.current >= 0.99:
            return 0.0
        return (self.threshold_critical - self.current) / max(self.current, 0.01)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current": round(self.current, 4),
            "status": self.status,
            "percentage": round(self.current * 100, 1),
            "safe_capacity_multiplier": round(self.safe_capacity_multiplier, 2),
            "threshold_warning": self.threshold_warning,
            "threshold_critical": self.threshold_critical,
        }


class CapacityCalculator:
    """Calculates current and projected capacity."""

    def __init__(self):
        self.api_rps_limit = 1000  # requests per second
        self.db_connection_limit = 20
        self.db_cpu_limit = 0.80
        self.worker_limit = 10
        self.sync_limit = 5

    def calculate_api_utilization(self) -> float:
        """Calculate API request rate utilization."""
        counter = metrics._metrics.get("api_requests_total")
        if not counter or counter.get() == 0:
            return 0.0

        # This is a simplified calculation; in production you'd track
        # requests per second over a time window
        return min(counter.get() / self.api_rps_limit, 1.0)

    def calculate_db_utilization(self) -> dict[str, float]:
        """Calculate database utilization."""
        utilization = {}

        # Connection pool utilization
        connections_gauge = metrics._metrics.get("db_connections")
        if connections_gauge:
            conn_count = connections_gauge.get()
            utilization["connections"] = min(conn_count / self.db_connection_limit, 1.0)
        else:
            utilization["connections"] = 0.0

        # Query duration (as indicator of CPU load)
        query_histogram = metrics._metrics.get("db_query_duration_seconds")
        if query_histogram:
            # If queries are getting slower, DB is under more load
            avg_latency = query_histogram._sum / max(query_histogram._count, 1)
            # Assume >100ms indicates elevated load
            utilization["cpu"] = min(avg_latency / 0.1, 1.0)
        else:
            utilization["cpu"] = 0.0

        return utilization

    def calculate_worker_utilization(self) -> float:
        """Calculate background worker utilization."""
        active_jobs = len(workload.get_active_jobs())
        active_syncs = len(workload.get_active_syncs())
        total_active = active_jobs + active_syncs
        return min(total_active / self.worker_limit, 1.0)

    def get_utilizations(self) -> list[Utilization]:
        """Get all utilization metrics."""
        result = []

        # API utilization
        api_util = self.calculate_api_utilization()
        result.append(Utilization("api_requests", api_util, 0.75, 0.90))

        # Database utilization
        db_util = self.calculate_db_utilization()
        result.append(Utilization("db_connections", db_util.get("connections", 0), 0.70, 0.90))
        result.append(Utilization("db_cpu", db_util.get("cpu", 0), 0.75, 0.90))

        # Worker utilization
        worker_util = self.calculate_worker_utilization()
        result.append(Utilization("workers", worker_util, 0.75, 0.90))

        return result

    def get_overall_capacity(self) -> dict[str, Any]:
        """Get overall capacity status and headroom."""
        utilizations = self.get_utilizations()
        utilization_dicts = [u.to_dict() for u in utilizations]

        # Overall constraint is the most saturated component
        most_saturated = max(utilizations, key=lambda u: u.current)
        overall_headroom = most_saturated.safe_capacity_multiplier

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": utilization_dicts,
            "bottleneck": {
                "component": most_saturated.name,
                "current": round(most_saturated.current, 4),
                "status": most_saturated.status,
            },
            "safe_capacity_headroom": {
                "multiplier": round(overall_headroom, 2),
                "message": (
                    f"Can handle {overall_headroom:.1f}x current load before reaching "
                    f"critical threshold on {most_saturated.name}"
                ),
            },
            "recommended_action": self._get_recommended_action(most_saturated),
        }

    def _get_recommended_action(self, bottleneck: Utilization) -> str:
        """Get recommended action based on bottleneck."""
        if bottleneck.status == "critical":
            if bottleneck.name == "api_requests":
                return "Scale API servers or implement rate limiting"
            elif bottleneck.name == "db_connections":
                return "Increase database connection pool size or optimize connection usage"
            elif bottleneck.name == "db_cpu":
                return "Optimize slow queries or upgrade database CPU"
            elif bottleneck.name == "workers":
                return "Increase worker count or parallelize background jobs"
        elif bottleneck.status == "warning":
            return f"Monitor {bottleneck.name} closely and prepare to scale"
        return "All systems within normal parameters"

    def estimate_growth_capacity(self, days_of_history: int = 7) -> dict[str, Any]:
        """Estimate when capacity will be exceeded based on recent growth."""
        # This requires historical metrics. For now, return a placeholder
        # In production, you'd track metrics over time in a time-series database
        return {
            "status": "insufficient_data",
            "message": "Growth forecasting requires > 7 days of historical metrics",
            "note": "Enable time-series metrics storage to enable forecasting",
        }
