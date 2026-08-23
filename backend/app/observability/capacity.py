"""Capacity monitoring and calculation for PIE.

Measures current resource utilization and calculates safe headroom.
Provides forecasting based on recent trends if sufficient history exists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..ingestion import jobs
from .metrics import metrics

log = logging.getLogger("pie_portal.observability.capacity")


@dataclass
class Utilization:
    """Resource utilization metrics.

    ``basis`` says *whose* load the figure describes, and it is not decoration:
    the metric-derived components below count one API worker's traffic, while
    the worker component counts rows every process shares. A dashboard that
    prints them side by side without saying so invites the reader to add them
    up. Empty for a component that never states one.

    ``current`` is ``None`` when the figure is **not measurable**, and that is a
    distinct answer from 0.0. A component whose inputs were never written has no
    load *known*, not no load — and 0.0 renders green, which is the benign
    default §1 forbids. When it is None, ``basis`` must name what is missing, so
    the reader learns why rather than seeing a blank. A ``basis`` on a number
    nobody computed correctly is worse than no basis at all: it vouches for the
    scope of a figure that was never measured.
    """
    name: str
    current: Optional[float]  # 0-1, or None when not measurable
    threshold_warning: float = 0.75
    threshold_critical: float = 0.90
    timestamp: datetime = None
    basis: str = ""

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    @property
    def status(self) -> str:
        """Get status band: healthy, warning, critical, or unknown."""
        if self.current is None:
            return "unknown"
        if self.current >= self.threshold_critical:
            return "critical"
        if self.current >= self.threshold_warning:
            return "warning"
        return "healthy"

    @property
    def safe_capacity_multiplier(self) -> Optional[float]:
        """How many times the current load can be added before threshold_critical.

        ``None`` when the load is unknown: headroom over an unmeasured base is a
        number with no meaning, and a large one reads as reassurance.
        """
        if self.current is None:
            return None
        if self.current >= 0.99:
            return 0.0
        return (self.threshold_critical - self.current) / max(self.current, 0.01)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current": None if self.current is None else round(self.current, 4),
            "status": self.status,
            "percentage": (None if self.current is None
                           else round(self.current * 100, 1)),
            "safe_capacity_multiplier": (
                None if self.safe_capacity_multiplier is None
                else round(self.safe_capacity_multiplier, 2)),
            "threshold_warning": self.threshold_warning,
            "threshold_critical": self.threshold_critical,
            "basis": self.basis,
        }


#: What the API and database components are counted from, said once because
#: three components repeat it.
_PER_WORKER = "This API worker's own counters — one of UVICORN_WORKERS processes."


class CapacityCalculator:
    """Calculates current and projected capacity.

    Takes a session because background load is a database question. It used to
    read an in-memory tracker that nothing ever wrote to, so worker utilization
    was structurally 0.0 — reported as "healthy, no load" on a deployment that
    might have had three syncs running. Counting rows in ``sync_runs`` is both
    true and visible to every worker.
    """

    def __init__(self, session: Session):
        self.session = session
        self.api_rps_limit = 1000  # requests per second
        self.db_connection_limit = 20
        self.db_cpu_limit = 0.80
        self.worker_limit = 10

    #: Why ``api_requests`` reports UNKNOWN rather than a number.
    API_RATE_UNMEASURABLE = (
        "Not measured: api_requests_total is a lifetime counter and this "
        "process keeps no earlier sample to difference it against, so no "
        "request rate can be derived from it. It previously divided that "
        "cumulative count by a per-second limit, which pinned every worker at "
        "100% 'critical' permanently once it had served api_rps_limit requests "
        "— a saturation alarm that was really an uptime counter. Scrape "
        "/api/v1/internal/observability/prometheus and use rate() on "
        "api_requests_total: differencing across scrapes is the second sample "
        "this process does not have.")

    def calculate_api_utilization(self) -> Optional[float]:
        """API request-rate utilization, or ``None`` because it is not derivable.

        A rate needs two samples and the interval between them. What exists is
        one monotonic counter incremented per request by
        ``instrumentation.api_instrumentation_middleware`` and never reset, so
        dividing it by ``api_rps_limit`` measures how long the worker has been
        up, not how busy it is.

        Refusing is the whole point rather than a gap: the alternative answers
        are a wrong number, or 0.0, and both render as a colour an operator
        acts on. Computing this honestly needs a previous (count, timestamp)
        pair — either held here across scrapes or differenced by whatever
        scrapes it — and that belongs with the exporter, not here.

        **That exporter now exists**, so this is a division of labour rather
        than an open gap. ``observability/exposition`` publishes
        ``api_requests_total`` as a raw Prometheus counter and Prometheus's
        ``rate()`` differences it across scrapes — the second sample this
        process cannot hold. This method still answers ``None``, and should:
        the request rate is a *deployment* question, and one worker holding one
        prior sample of its own traffic would answer a different one while
        looking like the same number.
        """
        return None

    def calculate_db_utilization(self) -> dict[str, Optional[float]]:
        """Database utilization, with ``None`` where nothing was ever recorded.

        Both components are absent rather than zero when their metric was never
        written. ``main.lifespan`` calls ``instrument_database(engine)`` inside a
        ``try/except Exception: log.exception(...); continue``, so a deployment
        where that raised serves every request with no database metrics at all —
        and the previous code answered 0.0, i.e. "healthy", for precisely the
        deployment that had lost its instrumentation.

        The latency guard is the §1 tell written out: ``_sum / max(_count, 1)``
        wraps the guard around the *arithmetic* (avoid dividing by zero) when it
        belongs around the *objection* (with no samples there is no average).
        """
        utilization: dict[str, Optional[float]] = {}

        connections_gauge = metrics._metrics.get("db_connections")
        utilization["connections"] = (
            min(connections_gauge.get() / self.db_connection_limit, 1.0)
            if connections_gauge is not None else None)

        query_histogram = metrics._metrics.get("db_query_duration_seconds")
        if query_histogram is not None and query_histogram._count > 0:
            avg_latency = query_histogram._sum / query_histogram._count
            # Assume >100ms indicates elevated load
            utilization["cpu"] = min(avg_latency / 0.1, 1.0)
        else:
            utilization["cpu"] = None

        return utilization

    def calculate_worker_utilization(self) -> float:
        """Background worker utilization, counted across the whole deployment.

        Every organization's live runs, not this caller's: worker capacity is a
        property of the deployment, and a tenant-scoped count would understate
        what the workers are actually carrying. It is a single integer of load,
        which is why reading across tenants here is not a disclosure — no row,
        name or number of another organization's leaves this method.

        Stalled runs are excluded. A row whose heartbeat went cold is not
        occupying a worker, and counting it as load would keep the dashboard
        reporting saturation long after the process died.
        """
        live = [r for r in jobs.unfinished_runs(self.session)
                if not jobs.is_stale(r)]
        return min(len(live) / self.worker_limit, 1.0)

    #: Named once so an unmeasured component cannot borrow the wording of a
    #: measured one. A ``basis`` is a claim about scope; on a null it has to be
    #: a claim about the gap instead.
    _DB_UNWRITTEN = ("Not measured: this worker has recorded no {metric}. "
                     "instrument_database is called inside a try/except at "
                     "startup, so a deployment where it raised reports nothing "
                     "here rather than reporting a healthy zero.")

    def get_utilizations(self) -> list[Utilization]:
        """Every utilization component, measured ones and unmeasured alike.

        An unmeasurable component is included and marked UNKNOWN rather than
        dropped. Omitting it would leave a dashboard whose remaining rows are
        all green and whose reader has no way to notice something is missing.
        """
        db_util = self.calculate_db_utilization()
        connections, cpu = db_util.get("connections"), db_util.get("cpu")
        return [
            Utilization("api_requests", self.calculate_api_utilization(),
                        0.75, 0.90, basis=self.API_RATE_UNMEASURABLE),
            Utilization(
                "db_connections", connections, 0.70, 0.90,
                basis=(_PER_WORKER if connections is not None
                       else self._DB_UNWRITTEN.format(metric="connection gauge"))),
            Utilization(
                "db_cpu", cpu, 0.75, 0.90,
                basis=(_PER_WORKER if cpu is not None
                       else self._DB_UNWRITTEN.format(metric="query timings"))),
            Utilization(
                "workers", self.calculate_worker_utilization(), 0.75, 0.90,
                basis="Live sync runs across every organization, from sync_runs."),
        ]

    def get_overall_capacity(self) -> dict[str, Any]:
        """Overall capacity, and what is not known about it.

        The bottleneck is the most saturated component *that was measured*. An
        unmeasured one cannot be ranked — ``None`` is not a low number — so the
        unknowns are named in ``unmeasured`` instead of being sorted among the
        others, and the headroom claim says how many components it rests on.
        A "3.4x headroom" derived from one of four components, three of them
        unmeasured, is the reassuring-looking answer §1 is about.
        """
        utilizations = self.get_utilizations()
        measured = [u for u in utilizations if u.current is not None]
        unmeasured = [u.name for u in utilizations if u.current is None]

        if not measured:
            # Nothing was measurable. Refuse rather than return a shape whose
            # nulls read as calm.
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "components": [u.to_dict() for u in utilizations],
                "bottleneck": None,
                "safe_capacity_headroom": None,
                "unmeasured": unmeasured,
                "recommended_action": (
                    "Capacity is unknown: no component reported a usable "
                    "figure. Check that instrumentation started — see each "
                    "component's basis."),
            }

        most_saturated = max(measured, key=lambda u: u.current)
        overall_headroom = most_saturated.safe_capacity_multiplier
        caveat = (f" Measured on {len(measured)} of {len(utilizations)} "
                  f"components; {', '.join(unmeasured)} not measured."
                  if unmeasured else "")

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": [u.to_dict() for u in utilizations],
            "unmeasured": unmeasured,
            "bottleneck": {
                "component": most_saturated.name,
                "current": round(most_saturated.current, 4),
                "status": most_saturated.status,
            },
            "safe_capacity_headroom": {
                "multiplier": round(overall_headroom, 2),
                "message": (
                    f"Can handle {overall_headroom:.1f}x current load before "
                    f"reaching critical threshold on {most_saturated.name}."
                    + caveat
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
