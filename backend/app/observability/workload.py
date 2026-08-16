"""PIE-specific workload instrumentation.

Tracks background jobs, ERP syncs, product processing, quote resolution,
and other PIE-specific operations.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from .metrics import metrics

log = logging.getLogger("pie_portal.observability.workload")


class JobStatus(str, Enum):
    """Status of a background job."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRIED = "retried"


class SyncType(str, Enum):
    """Type of synchronization operation."""
    INVOICE = "invoice"
    BILL = "bill"
    CUSTOMER = "customer"
    VENDOR = "vendor"
    ITEM = "item"
    FULL = "full"


@dataclass
class JobMetrics:
    """Metrics for a background job."""
    job_type: str
    status: JobStatus
    tenant_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    records_processed: int = 0
    records_failed: int = 0
    error: Optional[str] = None


@dataclass
class SyncMetrics:
    """Metrics for a Zoho sync operation."""
    sync_type: SyncType
    tenant_id: str
    connection_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    api_calls: int = 0
    rate_limit_hits: int = 0
    error: Optional[str] = None


# Metric definitions
jobs_created = metrics.counter("jobs_created_total", "Total jobs created")
jobs_completed = metrics.counter("jobs_completed_total", "Total jobs completed")
jobs_failed = metrics.counter("jobs_failed_total", "Total jobs failed")
jobs_duration = metrics.histogram("job_duration_seconds", "Job execution duration")
jobs_active = metrics.gauge("jobs_active", "Active jobs")
jobs_retried = metrics.counter("jobs_retried_total", "Total job retries")
records_processed = metrics.counter("records_processed_total", "Total records processed")

syncs_started = metrics.counter("syncs_started_total", "Total syncs started")
syncs_completed = metrics.counter("syncs_completed_total", "Total syncs completed")
syncs_failed = metrics.counter("syncs_failed_total", "Total syncs failed")
syncs_duration = metrics.histogram("sync_duration_seconds", "Sync execution duration")
syncs_active = metrics.gauge("syncs_active", "Active syncs")
sync_records_fetched = metrics.counter("sync_records_fetched_total", "Total records fetched from ERP")
sync_records_processed = metrics.counter("sync_records_processed_total", "Total records processed in sync")
sync_api_calls = metrics.counter("sync_api_calls_total", "Total API calls to ERP")
sync_api_errors = metrics.counter("sync_api_errors_total", "Total ERP API errors")
sync_rate_limits = metrics.counter("sync_rate_limits_total", "Total ERP rate limit events")

parsing_operations = metrics.counter("parsing_operations_total", "Total parsing operations")
parsing_duration = metrics.histogram("parsing_duration_seconds", "Product parsing duration")
parsing_failures = metrics.counter("parsing_failures_total", "Total parsing failures")

quote_resolutions = metrics.counter("quote_resolutions_total", "Total quote resolutions")
quote_duration = metrics.histogram("quote_resolution_duration_seconds", "Quote resolution duration")
quote_exact_matches = metrics.counter("quote_exact_matches_total", "Total exact product matches")
quote_equivalent_matches = metrics.counter("quote_equivalent_matches_total", "Total equivalent product matches")
quote_unresolved = metrics.counter("quote_unresolved_total", "Total unresolved products")

analytics_queries = metrics.counter("analytics_queries_total", "Total analytics queries")
analytics_duration = metrics.histogram("analytics_query_duration_seconds", "Analytics query duration")


class WorkloadTracker:
    """Tracks and records workload metrics."""

    def __init__(self):
        self.lock = threading.Lock()
        self._active_jobs: dict[str, JobMetrics] = {}
        self._active_syncs: dict[str, SyncMetrics] = {}
        self._job_history: list[JobMetrics] = []
        self._sync_history: list[SyncMetrics] = []

    def start_job(self, job_type: str, tenant_id: str) -> str:
        """Record job start."""
        job_id = f"job_{int(time.time() * 1000)}"
        metric = JobMetrics(
            job_type=job_type,
            status=JobStatus.QUEUED,
            tenant_id=tenant_id,
            start_time=datetime.now(timezone.utc),
        )
        with self.lock:
            self._active_jobs[job_id] = metric
        jobs_created.inc(labels={"job_type": job_type})
        jobs_active.inc()
        return job_id

    def update_job(
        self,
        job_id: str,
        status: JobStatus,
        records_processed: int = 0,
        records_failed: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Update job status."""
        with self.lock:
            metric = self._active_jobs.get(job_id)
            if not metric:
                return
            metric.status = status
            metric.records_processed = records_processed
            metric.records_failed = records_failed
            metric.error = error
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                metric.end_time = datetime.now(timezone.utc)
                metric.duration_seconds = (metric.end_time - metric.start_time).total_seconds()
                self._job_history.append(metric)
                del self._active_jobs[job_id]

        if status == JobStatus.COMPLETED:
            jobs_completed.inc(labels={"job_type": metric.job_type})
            jobs_duration.observe(metric.duration_seconds or 0, labels={"job_type": metric.job_type})
            records_processed.inc(metric.records_processed, labels={"job_type": metric.job_type})
            jobs_active.dec()
        elif status == JobStatus.FAILED:
            jobs_failed.inc(labels={"job_type": metric.job_type})
            jobs_active.dec()
        elif status == JobStatus.RETRIED:
            jobs_retried.inc(labels={"job_type": metric.job_type})

    def start_sync(self, sync_type: SyncType, tenant_id: str, connection_id: str) -> str:
        """Record sync start."""
        sync_id = f"sync_{int(time.time() * 1000)}"
        metric = SyncMetrics(
            sync_type=sync_type,
            tenant_id=tenant_id,
            connection_id=connection_id,
            start_time=datetime.now(timezone.utc),
        )
        with self.lock:
            self._active_syncs[sync_id] = metric
        syncs_started.inc(labels={"sync_type": sync_type.value})
        syncs_active.inc()
        return sync_id

    def update_sync(
        self,
        sync_id: str,
        records_fetched: int = 0,
        records_inserted: int = 0,
        records_updated: int = 0,
        records_skipped: int = 0,
        records_failed: int = 0,
        api_calls: int = 0,
        rate_limit_hits: int = 0,
        completed: bool = False,
        error: Optional[str] = None,
    ) -> None:
        """Update sync metrics."""
        with self.lock:
            metric = self._active_syncs.get(sync_id)
            if not metric:
                return
            metric.records_fetched = records_fetched
            metric.records_inserted = records_inserted
            metric.records_updated = records_updated
            metric.records_skipped = records_skipped
            metric.records_failed = records_failed
            metric.api_calls = api_calls
            metric.rate_limit_hits = rate_limit_hits

            if completed or error:
                metric.end_time = datetime.now(timezone.utc)
                metric.duration_seconds = (metric.end_time - metric.start_time).total_seconds()
                metric.error = error
                self._sync_history.append(metric)
                del self._active_syncs[sync_id]

        if completed or error:
            if completed and not error:
                syncs_completed.inc(labels={"sync_type": metric.sync_type.value})
            elif error:
                syncs_failed.inc(labels={"sync_type": metric.sync_type.value})
            syncs_duration.observe(metric.duration_seconds or 0, labels={"sync_type": metric.sync_type.value})
            sync_records_fetched.inc(records_fetched, labels={"sync_type": metric.sync_type.value})
            sync_records_processed.inc(
                records_inserted + records_updated,
                labels={"sync_type": metric.sync_type.value},
            )
            sync_api_calls.inc(api_calls, labels={"sync_type": metric.sync_type.value})
            if error:
                sync_api_errors.inc(labels={"sync_type": metric.sync_type.value})
            if rate_limit_hits > 0:
                sync_rate_limits.inc(rate_limit_hits, labels={"sync_type": metric.sync_type.value})
            syncs_active.dec()

    def record_parsing(self, duration: float, success: bool = True, tenant_id: Optional[str] = None) -> None:
        """Record a product parsing operation."""
        parsing_operations.inc(labels={"status": "success" if success else "failure"})
        parsing_duration.observe(duration)
        if not success:
            parsing_failures.inc()

    def record_quote_resolution(
        self,
        duration: float,
        exact_matches: int = 0,
        equivalent_matches: int = 0,
        unresolved: int = 0,
        tenant_id: Optional[str] = None,
    ) -> None:
        """Record a quote resolution operation."""
        quote_resolutions.inc()
        quote_duration.observe(duration)
        quote_exact_matches.inc(exact_matches)
        quote_equivalent_matches.inc(equivalent_matches)
        quote_unresolved.inc(unresolved)

    def record_analytics_query(self, duration: float, tenant_id: Optional[str] = None) -> None:
        """Record an analytics query."""
        analytics_queries.inc()
        analytics_duration.observe(duration)

    def get_active_jobs(self) -> dict[str, JobMetrics]:
        """Get currently active jobs."""
        with self.lock:
            return dict(self._active_jobs)

    def get_active_syncs(self) -> dict[str, SyncMetrics]:
        """Get currently active syncs."""
        with self.lock:
            return dict(self._active_syncs)

    def get_recent_jobs(self, hours: int = 24) -> list[JobMetrics]:
        """Get recent completed/failed jobs."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self.lock:
            return [j for j in self._job_history if j.end_time and j.end_time >= cutoff]

    def get_recent_syncs(self, hours: int = 24) -> list[SyncMetrics]:
        """Get recent completed/failed syncs."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self.lock:
            return [s for s in self._sync_history if s.end_time and s.end_time >= cutoff]


# Global workload tracker
workload = WorkloadTracker()
