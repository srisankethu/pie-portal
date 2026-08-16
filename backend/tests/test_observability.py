"""Tests for observability infrastructure."""
from __future__ import annotations


import pytest

from app.observability.capacity import Utilization, CapacityCalculator
from app.observability.health import ComponentHealth, HealthRegistry, HealthStatus
from app.observability.metrics import Counter, Gauge, Histogram, MetricRegistry
from app.observability.workload import JobStatus, SyncType, WorkloadTracker


class TestMetricsRegistry:
    """Tests for metrics collection."""

    def test_counter_increment(self):
        registry = MetricRegistry()
        counter = registry.counter("test_counter", "A test counter")
        assert counter.get() == 0
        counter.inc()
        assert counter.get() == 1
        counter.inc(5)
        assert counter.get() == 6

    def test_counter_with_labels(self):
        counter = Counter("test", "help")
        counter.inc(labels={"endpoint": "/api/quotes"})
        counter.inc(labels={"endpoint": "/api/quotes"})
        counter.inc(labels={"endpoint": "/api/data"})
        assert counter.get() == 3

    def test_gauge_set_and_inc_dec(self):
        gauge = Gauge("test", "help")
        gauge.set(10)
        assert gauge.get() == 10
        gauge.inc(5)
        assert gauge.get() == 15
        gauge.dec(3)
        assert gauge.get() == 12

    def test_histogram_percentiles(self):
        hist = Histogram("test", "help")
        for i in range(1, 101):
            hist.observe(i)
        assert hist.get_percentile(0) == 1
        assert hist.get_percentile(50) == 50
        assert hist.get_percentile(99) == 99
        assert hist.get_percentile(100) == 100

    def test_histogram_export(self):
        hist = Histogram("test", "help")
        hist.observe(0.5)
        hist.observe(1.0)
        hist.observe(2.0)
        export = hist.export()
        assert export["count"] == 3
        assert export["sum"] == 3.5
        assert export["min"] == 0.5
        assert export["max"] == 2.0

    def test_registry_export(self):
        registry = MetricRegistry()
        counter = registry.counter("counter", "")
        gauge = registry.gauge("gauge", "")
        counter.inc(5)
        gauge.set(10)
        export = registry.export()
        assert "timestamp" in export
        assert len(export["metrics"]) == 2


class TestHealthRegistry:
    """Tests for health checks."""

    def test_component_health_check(self):
        def check_fn() -> tuple[HealthStatus, str]:
            return HealthStatus.HEALTHY, "All good"

        component = ComponentHealth("test")
        component.check_fn = check_fn
        component.check()
        assert component.status == HealthStatus.HEALTHY
        assert component.message == "All good"

    def test_health_registry(self):
        registry = HealthRegistry()

        def check1() -> tuple[HealthStatus, str]:
            return HealthStatus.HEALTHY, "OK"

        def check2() -> tuple[HealthStatus, str]:
            return HealthStatus.DEGRADED, "Warning"

        registry.register("service1", check1)
        registry.register("service2", check2)
        registry.check_all()

        overall = registry.get_overall_status()
        assert overall == HealthStatus.DEGRADED

    def test_overall_status_calculation(self):
        registry = HealthRegistry()

        # All healthy
        registry.register("a", lambda: (HealthStatus.HEALTHY, ""))
        registry.register("b", lambda: (HealthStatus.HEALTHY, ""))
        registry.check_all()
        assert registry.get_overall_status() == HealthStatus.HEALTHY

        # One degraded
        registry._components["b"].status = HealthStatus.DEGRADED
        assert registry.get_overall_status() == HealthStatus.DEGRADED

        # One unhealthy
        registry._components["a"].status = HealthStatus.UNHEALTHY
        assert registry.get_overall_status() == HealthStatus.UNHEALTHY


class TestWorkloadTracker:
    """Tests for workload metrics."""

    def test_job_lifecycle(self):
        tracker = WorkloadTracker()
        job_id = tracker.start_job("test_job", "org_123")
        assert job_id is not None

        active = tracker.get_active_jobs()
        assert job_id in active
        assert active[job_id].job_type == "test_job"

        tracker.update_job(job_id, JobStatus.COMPLETED, records_processed=100)
        active = tracker.get_active_jobs()
        assert job_id not in active

        recent = tracker.get_recent_jobs(hours=1)
        assert len(recent) == 1
        assert recent[0].records_processed == 100

    def test_sync_lifecycle(self):
        tracker = WorkloadTracker()
        sync_id = tracker.start_sync(SyncType.INVOICE, "org_123", "conn_456")
        assert sync_id is not None

        active = tracker.get_active_syncs()
        assert sync_id in active

        tracker.update_sync(
            sync_id,
            records_fetched=1000,
            records_inserted=800,
            records_updated=150,
            records_failed=50,
            completed=True,
        )

        active = tracker.get_active_syncs()
        assert sync_id not in active

        recent = tracker.get_recent_syncs(hours=1)
        assert len(recent) == 1
        assert recent[0].records_fetched == 1000
        assert recent[0].duration_seconds >= 0

    def test_parsing_metrics(self):
        tracker = WorkloadTracker()
        tracker.record_parsing(0.5, success=True)
        tracker.record_parsing(1.0, success=False)
        # Verify no exceptions

    def test_quote_resolution_metrics(self):
        tracker = WorkloadTracker()
        tracker.record_quote_resolution(
            duration=0.3,
            exact_matches=5,
            equivalent_matches=2,
            unresolved=1,
        )
        # Verify no exceptions


class TestCapacityCalculator:
    """Tests for capacity calculations."""

    def test_utilization_status(self):
        util = Utilization("test", 0.5, 0.75, 0.90)
        assert util.status == "healthy"

        util = Utilization("test", 0.8, 0.75, 0.90)
        assert util.status == "warning"

        util = Utilization("test", 0.95, 0.75, 0.90)
        assert util.status == "critical"

    def test_safe_capacity_multiplier(self):
        util = Utilization("test", 0.5, 0.75, 0.90)
        # (0.90 - 0.5) / 0.5 = 0.8
        assert util.safe_capacity_multiplier == pytest.approx(0.8)

        util = Utilization("test", 0.9, 0.75, 0.90)
        # (0.90 - 0.9) / 0.9 ≈ 0
        assert util.safe_capacity_multiplier < 0.01

    def test_capacity_calculator_get_utilizations(self):
        calc = CapacityCalculator()
        utils = calc.get_utilizations()
        assert len(utils) > 0
        for util in utils:
            assert 0 <= util.current <= 1
            assert util.status in ("healthy", "warning", "critical")

    def test_overall_capacity(self):
        calc = CapacityCalculator()
        capacity = calc.get_overall_capacity()
        assert "timestamp" in capacity
        assert "components" in capacity
        assert "bottleneck" in capacity
        assert "safe_capacity_headroom" in capacity
        assert "recommended_action" in capacity

    def test_growth_capacity_without_history(self):
        calc = CapacityCalculator()
        forecast = calc.estimate_growth_capacity()
        assert forecast["status"] == "insufficient_data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
