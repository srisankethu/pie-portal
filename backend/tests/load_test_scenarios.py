"""Load testing scenarios for PIE observability.

These are example load testing scenarios that can be executed against a running
PIE instance to test observability metrics and measure capacity under different
workload patterns.

Usage:
    pytest tests/load_test_scenarios.py -v

Or with concurrent execution:
    pytest tests/load_test_scenarios.py -v -n 4

Configure load parameters via environment variables:
    LOAD_TEST_USERS=50              # Number of concurrent users
    LOAD_TEST_DURATION_SECONDS=300  # Test duration
    LOAD_TEST_API_URL=http://localhost:8000
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Callable, Optional

import pytest

log = logging.getLogger("pie_portal.load_tests")


class LoadTestConfig:
    """Configuration for load tests."""

    def __init__(self):
        import os
        self.api_url = os.environ.get("LOAD_TEST_API_URL", "http://localhost:8000")
        self.num_users = int(os.environ.get("LOAD_TEST_USERS", "10"))
        self.duration_seconds = int(os.environ.get("LOAD_TEST_DURATION_SECONDS", "60"))
        self.test_org_id = os.environ.get("TEST_ORG_ID", "test_org_load")
        self.test_token = os.environ.get("TEST_TOKEN", "test_token_load")


class LoadTestMetrics:
    """Metrics collector for load tests."""

    def __init__(self):
        self.lock = threading.Lock()
        self.requests = 0
        self.errors = 0
        self.latencies: list[float] = []
        self.start_time = time.time()
        self.end_time: Optional[float] = None

    def record_request(self, latency: float, success: bool = True) -> None:
        """Record a single request."""
        with self.lock:
            self.requests += 1
            if success:
                self.latencies.append(latency)
            else:
                self.errors += 1

    def finish(self) -> None:
        """Mark test as finished."""
        self.end_time = time.time()

    def summary(self) -> dict:
        """Get test summary."""
        with self.lock:
            duration = (self.end_time or time.time()) - self.start_time
            latencies = sorted(self.latencies)

            if latencies:
                p50 = latencies[len(latencies) // 2]
                p95 = latencies[int(len(latencies) * 0.95)]
                p99 = latencies[int(len(latencies) * 0.99)]
            else:
                p50 = p95 = p99 = None

            return {
                "duration_seconds": duration,
                "total_requests": self.requests,
                "successful_requests": self.requests - self.errors,
                "failed_requests": self.errors,
                "error_rate": (self.errors / self.requests * 100) if self.requests > 0 else 0,
                "requests_per_second": self.requests / duration if duration > 0 else 0,
                "latency_ms": {
                    "min": min(self.latencies) * 1000 if self.latencies else None,
                    "max": max(self.latencies) * 1000 if self.latencies else None,
                    "p50": p50 * 1000 if p50 else None,
                    "p95": p95 * 1000 if p95 else None,
                    "p99": p99 * 1000 if p99 else None,
                    "mean": (sum(self.latencies) / len(self.latencies) * 1000) if self.latencies else None,
                },
            }


class LoadTestRunner:
    """Executes load tests with multiple concurrent users."""

    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.metrics = LoadTestMetrics()
        self.stop_event = threading.Event()

    def run_user_session(self, user_id: int, operation: Callable[[str, int], None]) -> None:
        """Execute operations as a single user for the test duration."""
        start = time.time()
        request_count = 0

        while time.time() - start < self.config.duration_seconds and not self.stop_event.is_set():
            try:
                op_start = time.time()
                operation(self.config.test_org_id, user_id)
                latency = time.time() - op_start
                self.metrics.record_request(latency, success=True)
                request_count += 1
            except Exception as e:
                log.warning(f"User {user_id} request failed: {e}")
                self.metrics.record_request(0, success=False)

        log.info(f"User {user_id} completed {request_count} requests")

    def run(self, operation: Callable[[str, int], None]) -> dict:
        """Run load test with multiple concurrent users."""
        log.info(f"Starting load test: {self.config.num_users} users for {self.config.duration_seconds}s")

        with ThreadPoolExecutor(max_workers=self.config.num_users) as executor:
            futures = []
            for user_id in range(self.config.num_users):
                future = executor.submit(self.run_user_session, user_id, operation)
                futures.append(future)

            # Wait for all to complete
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    log.error(f"User thread failed: {e}")

        self.metrics.finish()
        return self.metrics.summary()


# ─────────────────────────────────────────────────────────────────────────────
# Scenario Implementations
# ─────────────────────────────────────────────────────────────────────────────


class NormalUsageScenario:
    """Simulate normal usage: product searches, quote views, decision navigation."""

    name = "Normal Usage"
    operations = [
        "search_products",
        "view_customer",
        "view_decision",
        "list_decisions",
    ]

    @staticmethod
    def search_products(org_id: str, user_id: int) -> None:
        """Simulate product search."""
        # This would be an actual API call in a real test
        query = random.choice(["drill", "insert", "holder", "tool"])
        # GET /api/v1/search/products?q={query}
        pass

    @staticmethod
    def view_customer(org_id: str, user_id: int) -> None:
        """Simulate viewing customer details."""
        # GET /api/v1/commercial/customers/{id}
        pass

    @staticmethod
    def view_decision(org_id: str, user_id: int) -> None:
        """Simulate viewing decision detail."""
        # GET /api/v1/decisions/{id}/detail
        pass

    @staticmethod
    def list_decisions(org_id: str, user_id: int) -> None:
        """Simulate listing decisions."""
        # GET /api/v1/decisions?limit=20
        pass


class HeavyQuoteWorkloadScenario:
    """Simulate heavy quote creation and resolution."""

    name = "Heavy Quote Workload"
    operations = [
        "create_quote",
        "parse_intake",
        "resolve_supply",
    ]

    @staticmethod
    def create_quote(org_id: str, user_id: int) -> None:
        """Create a new quote."""
        # POST /api/quotes
        pass

    @staticmethod
    def parse_intake(org_id: str, user_id: int) -> None:
        """Parse RFQ text."""
        # POST /api/quotes/{id}/intake
        pass

    @staticmethod
    def resolve_supply(org_id: str, user_id: int) -> None:
        """Resolve product via pie-parser."""
        # POST /api/quotes/{id}/set-supply
        pass


class HeavyAnalyticsScenario:
    """Simulate heavy analytics usage: insight pages with large date ranges."""

    name = "Heavy Analytics"
    operations = [
        "insight_revenue_flow",
        "insight_opportunities",
        "insight_weather",
    ]

    @staticmethod
    def insight_revenue_flow(org_id: str, user_id: int) -> None:
        """Load revenue flow analytics."""
        # GET /api/v1/insight/revenue-flow
        pass

    @staticmethod
    def insight_opportunities(org_id: str, user_id: int) -> None:
        """Load opportunities analytics."""
        # GET /api/v1/insight/opportunities
        pass

    @staticmethod
    def insight_weather(org_id: str, user_id: int) -> None:
        """Load weather analytics."""
        # GET /api/v1/insight/weather
        pass


class DataSyncScenario:
    """Simulate Zoho data synchronization."""

    name = "Data Sync"

    @staticmethod
    def sync_data(org_id: str, user_id: int) -> None:
        """Trigger Zoho sync."""
        # POST /api/v1/data/sync
        pass


class MixedWorkloadScenario:
    """Simulate realistic mixed workload: users doing different things."""

    name = "Mixed Workload"

    @staticmethod
    def random_operation(org_id: str, user_id: int) -> None:
        """Perform a random operation."""
        operation = random.choice([
            NormalUsageScenario.search_products,
            NormalUsageScenario.view_customer,
            HeavyQuoteWorkloadScenario.create_quote,
            HeavyAnalyticsScenario.insight_revenue_flow,
        ])
        operation(org_id, user_id)


# ─────────────────────────────────────────────────────────────────────────────
# Test Cases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.load
class TestLoadScenarios:
    """Load test scenarios."""

    def test_normal_usage_scenario(self):
        """Test normal usage workload."""
        config = LoadTestConfig()
        runner = LoadTestRunner(config)

        # Pick a random normal operation
        def operation(org_id: str, user_id: int):
            op = random.choice([
                NormalUsageScenario.search_products,
                NormalUsageScenario.view_customer,
            ])
            op(org_id, user_id)

        summary = runner.run(operation)
        log.info(f"Normal Usage Results: {json.dumps(summary, indent=2)}")

        # Assertions based on expected performance
        assert summary["error_rate"] < 5, "Error rate should be < 5%"
        if summary["latency_ms"]["p99"]:
            assert summary["latency_ms"]["p99"] < 500, "P99 latency should be < 500ms"

    def test_heavy_quote_workload(self):
        """Test heavy quote processing."""
        config = LoadTestConfig()
        config.num_users = min(config.num_users, 5)  # Fewer concurrent users
        runner = LoadTestRunner(config)

        def operation(org_id: str, user_id: int):
            op = random.choice([
                HeavyQuoteWorkloadScenario.create_quote,
                HeavyQuoteWorkloadScenario.resolve_supply,
            ])
            op(org_id, user_id)

        summary = runner.run(operation)
        log.info(f"Heavy Quote Results: {json.dumps(summary, indent=2)}")

        assert summary["error_rate"] < 10, "Error rate should be < 10%"

    def test_heavy_analytics(self):
        """Test heavy analytics workload."""
        config = LoadTestConfig()
        config.num_users = min(config.num_users, 3)  # Analytics is slower
        runner = LoadTestRunner(config)

        def operation(org_id: str, user_id: int):
            op = random.choice([
                HeavyAnalyticsScenario.insight_revenue_flow,
                HeavyAnalyticsScenario.insight_opportunities,
            ])
            op(org_id, user_id)

        summary = runner.run(operation)
        log.info(f"Heavy Analytics Results: {json.dumps(summary, indent=2)}")

        assert summary["error_rate"] < 15, "Error rate should be < 15%"
        # Analytics queries can be slow
        if summary["latency_ms"]["p99"]:
            assert summary["latency_ms"]["p99"] < 3000, "P99 latency should be < 3s"

    def test_mixed_workload(self):
        """Test realistic mixed workload."""
        config = LoadTestConfig()
        runner = LoadTestRunner(config)

        summary = runner.run(MixedWorkloadScenario.random_operation)
        log.info(f"Mixed Workload Results: {json.dumps(summary, indent=2)}")

        assert summary["error_rate"] < 10, "Error rate should be < 10%"
        assert summary["requests_per_second"] > 0, "Should have requests/sec"

        # Print metrics for manual inspection
        print(f"\nMixed Workload Summary:")
        print(f"  Duration: {summary['duration_seconds']:.1f}s")
        print(f"  Requests: {summary['total_requests']}")
        print(f"  Errors: {summary['failed_requests']}")
        print(f"  RPS: {summary['requests_per_second']:.1f}")
        if summary["latency_ms"]["p99"]:
            print(f"  P99 Latency: {summary['latency_ms']['p99']:.1f}ms")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "load"])
