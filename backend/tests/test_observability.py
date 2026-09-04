"""Tests for observability infrastructure."""
from __future__ import annotations

import threading

import pytest

from datetime import timedelta

from sqlalchemy.orm import sessionmaker

import dbsupport
from app.clock import now
from app.domain import models
from app.observability.capacity import Utilization, CapacityCalculator
from app.observability.dashboard import DashboardService
from app.observability.health import ComponentHealth, HealthRegistry, HealthStatus
from app.observability.metrics import Counter, Gauge, Histogram, MetricRegistry


ORG = "org_obs"
OTHER_ORG = "org_next_door"


@pytest.fixture()
def session():
    s = sessionmaker(bind=dbsupport.fresh_engine(), future=True)()
    yield s
    s.close()


def _run(session, run_id: str, status: str, *, org: str = ORG,
         started_minutes_ago: float = 30, heartbeat_minutes_ago=None,
         finished_minutes_ago=None, connection_id=None, phase=None,
         error=None, **counters):
    """One sync_runs row, positioned in time relative to now."""
    t = now()

    def _at(minutes):
        return None if minutes is None else t - timedelta(minutes=minutes)

    run = models.SyncRun(
        sync_run_id=run_id, organization_id=org, source="api", status=status,
        connection_id=connection_id, phase=phase, error=error,
        started_at=_at(started_minutes_ago),
        heartbeat_at=_at(heartbeat_minutes_ago),
        finished_at=_at(finished_minutes_ago), **counters)
    session.add(run)
    session.commit()
    return run


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
        counter.inc(labels={"endpoint": "/api/v1/quotes"})
        counter.inc(labels={"endpoint": "/api/v1/quotes"})
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


class TestMetricsExportSaysWhoseNumbersTheseAre:
    """The export must not present one worker's counters as the platform's.

    There is no shared backing store and there deliberately is not going to be
    one: metrics are written on every request, and putting that write on the
    request path is the SQLite locking incident CLAUDE.md §4 documents. So the
    registry is per-process, the deployment runs several, and a scrape returns
    whichever worker answered — roughly 1/N of the traffic, a different 1/N
    next time.

    That is defensible only while the payload says so. An operator reading a
    p99 needs to know it came from one worker's samples, and needs a stable
    identity on it so two scrapes can be told apart: same id, the deltas mean
    something; different id, a different population, and subtracting them is
    meaningless.
    """

    def test_the_payload_names_its_scope_and_its_worker(self):
        from app.leases import holder_id

        registry = MetricRegistry()
        registry.counter("scoped_counter", "help").inc()
        payload = registry.export()

        assert payload["scope"] == "worker", "not the deployment"
        assert payload["worker"] == holder_id(), (
            "the process identity the scheduler lease already uses, reused "
            "rather than reinvented")
        assert "percentile" in payload["composition"].lower(), (
            "counters sum across workers and percentiles do not; combining "
            "them wrongly is silent, so the payload has to say which is which")

    def test_the_worker_id_is_stable_across_scrapes(self):
        first, second = MetricRegistry().export(), MetricRegistry().export()
        assert first["worker"] == second["worker"]

    def test_an_undeclared_worker_count_is_null_not_one(self, monkeypatch):
        """`null` is "nobody said"; `1` would tell a scraper it had them all."""
        from app.config import settings

        monkeypatch.setattr(settings, "UVICORN_WORKERS", None)
        assert MetricRegistry().export()["workers_configured"] is None

        monkeypatch.setattr(settings, "UVICORN_WORKERS", 2)
        assert MetricRegistry().export()["workers_configured"] == 2


class TestJobAndSyncEndpointsReadTheRunTable:
    """`/observability/jobs` and `/observability/syncs`, from `sync_runs`.

    These two endpoints used to be served by an in-memory `WorkloadTracker`
    that no application code ever called. Every field was structurally zero —
    forever, on every deployment — and zero active jobs reads as "all quiet".
    That is the benign default CLAUDE.md §1 forbids: the evidence was not thin,
    it was absent, and the screen said the platform was fine.

    The tracker is deleted rather than wired up. `sync_runs` already held these
    facts and holds them better: it is authoritative, every worker sees the same
    rows, and it survives a restart, none of which a per-process dict does.

    What these tests pin is the honesty of the payload, not its prettiness —
    a number that cannot be known comes back null with a sentence naming what
    is missing, a run that stopped reporting is not counted as running, and one
    organization's activity never appears in another's.
    """

    def test_no_runs_reports_unknown_throughput_rather_than_zero(self, session):
        payload = DashboardService(session, ORG).get_zoho_sync_status()
        window = payload["recent_24h"]

        assert window["throughput_records_per_sec"] is None, (
            "0 rec/s is what a sync moving no data looks like; an idle window "
            "must not be reported as one")
        assert "No run ended" in window["throughput_basis"]
        assert payload["active"]["count"] == 0

    def test_a_finished_run_supplies_the_window_figures(self, session):
        _run(session, "r_ok", "OK", started_minutes_ago=40,
             finished_minutes_ago=30, documents_fetched=120,
             customers=10, products=20, sales_txns=70)

        window = DashboardService(session, ORG).get_zoho_sync_status()["recent_24h"]

        assert window["completed"] == 1
        assert window["total_records_fetched"] == 120
        assert window["total_records_processed"] == 100
        # 100 records over ten minutes.
        assert window["throughput_records_per_sec"] == pytest.approx(100 / 600, abs=0.01)
        assert window["throughput_runs_excluded"] == 0

    def test_a_run_with_no_finish_time_is_excluded_and_counted(self, session):
        """The denominator must not silently absorb a run it cannot time.

        `finished_at` is nullable, and the previous implementation summed
        `duration_seconds or 0` — so a run with no end time contributed its
        records to the numerator and nothing to the denominator, inflating the
        rate. Excluding it is right; excluding it *quietly* is not.
        """
        _run(session, "r_timed", "OK", started_minutes_ago=40,
             finished_minutes_ago=30, sales_txns=100)
        _run(session, "r_untimed", "OK", started_minutes_ago=50, sales_txns=900)

        window = DashboardService(session, ORG).get_zoho_sync_status()["recent_24h"]

        assert window["throughput_runs_excluded"] == 1
        assert window["throughput_records_per_sec"] == pytest.approx(100 / 600, abs=0.01)

    def test_a_stalled_run_is_reported_apart_from_a_live_one(self, session):
        _run(session, "r_live", "RUNNING", started_minutes_ago=5,
             heartbeat_minutes_ago=0, phase="Reading invoices")
        _run(session, "r_cold", "RUNNING", started_minutes_ago=180,
             heartbeat_minutes_ago=120, connection_id="conn_a")

        payload = DashboardService(session, ORG).get_background_jobs()

        assert payload["active"]["count"] == 1, "a cold run is not work in progress"
        assert payload["active"]["by_phase"] == {"Reading invoices": 1}
        assert payload["stalled"]["count"] == 1, "nor is it nothing"
        assert payload["stalled"]["detail"]

    def test_reporting_does_not_reap_a_stalled_run(self, session):
        """A GET anyone may poll must not write somebody else's job off as failed.

        `jobs.active_run` reaps deliberately — a dead row must not hold the
        next sync's slot. A dashboard has no such need, and reaping from it
        would mean an observability screen racing the worker that owns the run.
        """
        _run(session, "r_cold", "RUNNING", started_minutes_ago=180,
             heartbeat_minutes_ago=120)

        DashboardService(session, ORG).get_background_jobs()

        assert session.get(models.SyncRun, "r_cold").status == "RUNNING"

    def test_partial_is_counted_as_itself(self, session):
        _run(session, "r_part", "PARTIAL", started_minutes_ago=40,
             finished_minutes_ago=35, error="Zoho stopped answering",
             sales_txns=5)

        payload = DashboardService(session, ORG).get_background_jobs()

        assert payload["recent_24h"]["partial"] == 1
        assert payload["recent_24h"]["completed"] == 0, (
            "a run that did not finish must not be counted as one that did")
        assert payload["recent_24h"]["failed"] == 0
        assert [f["status"] for f in payload["failures"]] == ["PARTIAL"]

    def test_a_run_older_than_the_window_is_not_in_it(self, session):
        _run(session, "r_old", "OK", started_minutes_ago=60 * 30,
             finished_minutes_ago=60 * 29, sales_txns=10)

        payload = DashboardService(session, ORG).get_background_jobs()

        assert payload["recent_24h"]["completed"] == 0
        assert payload["recent_24h"]["basis"], "the window must say what it selected"

    def test_another_organizations_runs_are_not_reported(self, session):
        _run(session, "r_theirs", "RUNNING", org=OTHER_ORG,
             started_minutes_ago=5, heartbeat_minutes_ago=0)
        _run(session, "r_theirs_done", "OK", org=OTHER_ORG,
             started_minutes_ago=40, finished_minutes_ago=30, sales_txns=50)

        payload = DashboardService(session, ORG).get_zoho_sync_status()

        assert payload["organization_id"] == ORG
        assert payload["active"]["count"] == 0
        assert payload["recent_24h"]["completed"] == 0

    def test_worker_utilization_counts_every_organizations_live_runs(self, session):
        """Capacity is a property of the deployment, not of one tenant.

        A tenant-scoped count would understate what the workers are carrying.
        Only the integer crosses — no other organization's rows, names or
        numbers appear.
        """
        calc = CapacityCalculator(session)
        assert calc.calculate_worker_utilization() == 0.0

        _run(session, "r_mine", "RUNNING", started_minutes_ago=2,
             heartbeat_minutes_ago=0)
        _run(session, "r_theirs", "RUNNING", org=OTHER_ORG,
             started_minutes_ago=2, heartbeat_minutes_ago=0)
        # A connection of its own: the partial unique index allows one active
        # umbrella run per organization, and `r_mine` already holds that slot.
        _run(session, "r_cold", "RUNNING", connection_id="conn_z",
             started_minutes_ago=180, heartbeat_minutes_ago=120)

        assert calc.calculate_worker_utilization() == pytest.approx(
            2 / calc.worker_limit), "the stalled run occupies no worker"

    def test_the_load_block_does_not_invent_an_active_request_count(self, session):
        """Nothing tracks in-flight requests, so the answer is null, not 0."""
        api = DashboardService(session, ORG).get_current_load()["api"]

        assert api["active_requests"] is None
        assert api["basis"], "and the payload says why"


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

    def test_capacity_calculator_get_utilizations(self, session):
        """Every component is either a real fraction or an explained UNKNOWN.

        The looser form of this test (`0 <= current <= 1` for all) is what let
        `api_requests` report a lifetime request count divided by a per-second
        limit: the value was in range, so the assertion passed while the number
        meant nothing. What matters is that a figure nobody measured cannot
        masquerade as one that was — so an unknown must be null, must band as
        `unknown` rather than `healthy`, and must say what is missing.
        """
        calc = CapacityCalculator(session)
        utils = calc.get_utilizations()
        assert len(utils) > 0
        for util in utils:
            if util.current is None:
                assert util.status == "unknown", (
                    f"{util.name}: an unmeasured component must not band as a "
                    f"load level, got {util.status!r}")
                assert util.basis, (
                    f"{util.name}: a null must name what is missing")
                assert util.safe_capacity_multiplier is None, (
                    f"{util.name}: headroom over an unmeasured base is meaningless")
            else:
                assert 0 <= util.current <= 1
                assert util.status in ("healthy", "warning", "critical")

    def test_overall_capacity(self, session):
        calc = CapacityCalculator(session)
        capacity = calc.get_overall_capacity()
        assert "timestamp" in capacity
        assert "components" in capacity
        assert "bottleneck" in capacity
        assert "safe_capacity_headroom" in capacity
        assert "recommended_action" in capacity

    def test_growth_capacity_without_history(self, session):
        calc = CapacityCalculator(session)
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
        """The registered check functions, with the global registry restored after.

        A real session factory rather than a stub: the scheduler check reads the
        ``sync-scheduler`` lease — every process runs the thread, so a live
        thread is no longer the same claim as "this deployment is ticking" — and
        the queue check reads the depth. A check that needs the database is
        given one here rather than being loosened to suit the test.
        """
        import dbsupport
        from sqlalchemy.orm import sessionmaker

        from app.observability.health import health, register_health_checks

        engine = dbsupport.fresh_engine()
        maker = sessionmaker(bind=engine, autoflush=False,
                             expire_on_commit=False, future=True)
        saved = dict(health._components)
        health._components.clear()
        register_health_checks(engine, maker)
        checks = {name: comp.check_fn for name, comp in health._components.items()}
        checks["_session"] = maker                       # for a test that seeds
        yield checks
        health._components.clear()
        health._components.update(saved)

    @pytest.mark.requires_pie
    def test_the_pie_parser_check_can_report_ready(self, checks):
        """The engine, not a catalogue.

        This used to assert on ``catalog_available``, which was a process-wide
        fact while one deployment-wide catalogue answered every organization.
        Catalogues are per company now, so a process probe has no single
        catalogue to report and no session to enumerate companies with — and a
        probe that loaded several tenants' indexes to answer a liveness check
        would be a worse thing than the one it replaced.
        """
        status, message = checks["pie_parser"]()
        assert status is HealthStatus.HEALTHY, message
        # It says where the per-company answer lives, rather than implying that
        # a healthy process means every company can resolve.
        assert "catalogue" in message.lower()

    def test_the_pie_parser_check_names_a_missing_engine(self, checks, monkeypatch, tmp_path):
        from app.config import settings

        monkeypatch.setattr(settings, "PIE_PARSER_ROOT", tmp_path)
        status, message = checks["pie_parser"]()
        assert status is HealthStatus.DEGRADED, message
        assert "no attribute" not in message, (
            "the check must report the engine, not its own broken read")
        assert "pie-parser is not present" in message

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

        # A live thread *and* this deployment's lease held: both halves of
        # "something is actually ticking".
        from app import leases

        session = checks["_session"]()
        try:
            leases.acquire(session, sync_scheduler.LEASE, "process-under-test")
        finally:
            session.close()

        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, name="sync-scheduler", daemon=True)
        thread.start()
        try:
            status, message = checks["scheduler"]()
        finally:
            stop.set()
            thread.join(timeout=5)
        assert status is HealthStatus.HEALTHY, message
        assert "process-under-test" in message, message

    def test_the_scheduler_check_notices_that_nobody_is_ticking(self, checks,
                                                                monkeypatch):
        """A live thread with no lease holder anywhere is the state that looks
        healthy and schedules nothing — reporting it green would be exactly the
        benign default the invariants forbid."""
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
        assert status is HealthStatus.DEGRADED, message
        assert "lease" in message

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
