"""Tests for observability infrastructure."""
from __future__ import annotations

import threading

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

    def test_export_does_not_deadlock(self):
        """`export` must not re-enter its own lock — and must fail *fast* if it does.

        This is a regression test with an unusual shape, for a reason worth
        stating. `export` used to call `self.get_percentile` from inside
        `with self.lock`, and `Metric.lock` is a plain `threading.Lock`, not an
        `RLock` — so the call blocked on a lock the same thread already held and
        never came back. Nothing raised, nothing timed out, nothing was logged.

        Asserting on the return value alone cannot catch that: the assertion is
        never reached, the test hangs, and the whole suite hangs with it. That is
        exactly what happened — the file sat at four tests for over ten minutes
        and CI reported nothing at all. So the call is made on a separate thread
        and joined with a deadline, which turns "hangs forever" into one failing
        test with a sentence attached.

        The production stake, not just the test's: `GET
        /internal/observability/metrics` calls `MetricRegistry.export`, which
        calls this for every metric, and the API middleware records latency into
        a histogram. One request to the metrics endpoint wedged a worker for the
        life of the process.
        """
        hist = Histogram("test", "help")
        for i in range(1, 21):
            hist.observe(i)

        result: dict = {}
        worker = threading.Thread(
            target=lambda: result.update(hist.export()), daemon=True)
        worker.start()
        worker.join(timeout=10)

        assert not worker.is_alive(), (
            "Histogram.export() did not return within 10s — it is almost "
            "certainly re-entering self.lock, as it did before this test existed.")
        assert result["count"] == 20
        assert result["p50"] == 10

    def test_percentiles_are_nearest_rank(self):
        """Small-N cases, where an off-by-one is unmissable.

        The bug this pins was `int(N * p / 100)` — a floor, which lands one
        element too high whenever the product is a whole number. On 1..100 it
        answered 51 for the median. These are the numbers the capacity dashboard
        reports latency with, so a wrong p95 is a wrong operational decision.
        """
        hist = Histogram("test", "help")
        for value in (10, 20, 30, 40):
            hist.observe(value)

        assert hist.get_percentile(0) == 10       # 0 is the minimum by convention
        assert hist.get_percentile(25) == 10      # rank 1
        assert hist.get_percentile(50) == 20      # rank 2 — not 30
        assert hist.get_percentile(75) == 30      # rank 3
        assert hist.get_percentile(100) == 40     # the maximum

    def test_the_sample_window_is_bounded(self):
        """The one that pins the leak shut.

        `_values` was a plain list appended to on every observation and never
        trimmed. The API middleware observes into this on every request, so on a
        process that runs for weeks it was a straight line up — 50,000 requests
        retained about 14 MB in a single histogram.
        """
        hist = Histogram("test", "help", capacity=64)
        for i in range(10_000):
            hist.observe(i)

        assert len(hist._values) == 64
        assert hist.export()["sampled"] == 64
        assert hist.export()["capacity"] == 64

    def test_labels_are_not_retained_per_label(self):
        """`_by_label` is gone, not bounded — nothing ever read it.

        It was a dict of unbounded lists keyed by label set, and the middleware
        labels every observation with `request.url.path` — the raw path, so one
        key per distinct URL. It grew in two dimensions and was write-only:
        `Counter` and `Gauge` publish theirs in `export`, this never did.

        `observe` still accepts `labels` so the call sites and the sibling
        signatures are unchanged; it simply aggregates across them.
        """
        hist = Histogram("test", "help", capacity=1000)
        for i in range(500):
            hist.observe(0.01, labels={"endpoint": f"/api/v1/decisions/{i}/detail"})

        assert not hasattr(hist, "_by_label")
        assert len(hist._values) == 500        # the aggregate is still recorded
        assert hist.export()["count"] == 500

    def test_lifetime_figures_survive_eviction(self):
        """Totals and extremes are lifetime; only the percentiles are windowed.

        Kept that way deliberately. `min`/`max` cost two floats to carry
        properly, and silently redefining `max` from "worst ever seen" to "worst
        in the last 2048" would move a number on a dashboard with nobody told.
        """
        hist = Histogram("test", "help", capacity=10)
        hist.observe(1000.0)                   # the extreme, evicted almost at once
        for _ in range(500):
            hist.observe(1.0)

        export = hist.export()
        assert export["count"] == 501          # every observation, not the window
        assert export["sum"] == 1000.0 + 500
        assert export["max"] == 1000.0         # survived falling out of the ring
        assert export["min"] == 1.0
        assert export["sampled"] == 10         # but the percentiles see only these
        assert export["p50"] == 1.0

    def test_an_empty_histogram_still_reports_its_capacity(self):
        """So a reader can tell "nothing observed" from "window not yet full"."""
        export = Histogram("test", "help", capacity=32).export()
        assert export["count"] == 0
        assert export["sampled"] == 0
        assert export["capacity"] == 32

    def test_percentile_of_an_empty_histogram_is_none(self):
        """Not zero. Nothing was observed, so there is no answer to give, and a
        zero here would read as a suspiciously fast p99 on a dashboard."""
        assert Histogram("test", "help").get_percentile(95) is None

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


class TestRegisteredHealthChecks:
    """The checks `register_health_checks` installs must read facts that exist.

    Two of the three read attributes nothing ever defined — `pie_service.engine`
    and a module-level `scheduler` object in `ingestion/scheduler.py` — and each
    sat behind its own `except Exception` that turned the resulting
    AttributeError / ImportError into DEGRADED. So both were permanently amber
    for a reason that was never true, and neither could ever report healthy no
    matter what the component was doing. A monitor that is always the same
    colour is one nobody reads, which is this repo's own stated reason for
    consolidating its gates.

    These tests drive the registered closures directly, with the components they
    inspect stubbed, so they assert on what the check *concludes* rather than on
    which attribute it happens to read.
    """

    @pytest.fixture()
    def checks(self):
        """The registered check functions, with the global registry restored after."""
        from app.observability.health import health, register_health_checks

        saved = dict(health._components)
        health._components.clear()
        register_health_checks(object(), object())
        yield {name: comp.check_fn for name, comp in health._components.items()}
        health._components.clear()
        health._components.update(saved)

    def test_the_pie_parser_check_can_report_ready(self, checks, monkeypatch):
        import app.pie_service as pie_module

        class _Loaded:
            catalog_available = True

        monkeypatch.setattr(pie_module, "pie_service", _Loaded())
        status, message = checks["pie_parser"]()
        assert status is HealthStatus.HEALTHY, message

    def test_the_pie_parser_check_names_a_missing_catalogue(self, checks, monkeypatch):
        import app.pie_service as pie_module

        class _Unloaded:
            catalog_available = False

        monkeypatch.setattr(pie_module, "pie_service", _Unloaded())
        status, message = checks["pie_parser"]()
        assert status is HealthStatus.DEGRADED, message
        assert "no attribute" not in message, (
            "the check must report the catalogue, not its own broken read")
        assert "catalogue" in message.lower()

    def test_the_scheduler_check_is_quiet_where_nothing_is_scheduled(self, checks, monkeypatch):
        """`start_scheduler` declines a fixture source by design — not a fault."""
        from app.config import settings

        monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
        status, message = checks["scheduler"]()
        assert status is HealthStatus.HEALTHY, message
        assert "fixture" in message

    def test_the_scheduler_check_notices_one_that_never_started(self, checks, monkeypatch):
        from app.config import settings
        from app.ingestion import scheduler as sync_scheduler

        monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
        monkeypatch.setattr(sync_scheduler, "_started", threading.Event())
        status, message = checks["scheduler"]()
        assert status is HealthStatus.DEGRADED, message
        assert "cannot import name" not in message, (
            "the check must report the scheduler, not its own broken import")

    def test_the_scheduler_check_reports_a_live_thread(self, checks, monkeypatch):
        from app.config import settings
        from app.ingestion import scheduler as sync_scheduler

        monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
        started = threading.Event()
        started.set()
        monkeypatch.setattr(sync_scheduler, "_started", started)

        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, name="sync-scheduler", daemon=True)
        thread.start()
        try:
            status, message = checks["scheduler"]()
        finally:
            stop.set()
            thread.join(timeout=5)
        assert status is HealthStatus.HEALTHY, message

    def test_the_scheduler_check_notices_a_thread_that_has_stopped(self, checks, monkeypatch):
        """Started, then gone: the one state a monitor exists to catch."""
        from app.config import settings
        from app.ingestion import scheduler as sync_scheduler

        monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
        started = threading.Event()
        started.set()
        monkeypatch.setattr(sync_scheduler, "_started", started)
        assert not any(t.name == "sync-scheduler" and t.is_alive()
                       for t in threading.enumerate())

        status, message = checks["scheduler"]()
        assert status is HealthStatus.UNHEALTHY, message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
