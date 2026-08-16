"""Core metrics collection for PIE observability.

Provides in-memory metrics aggregation with structured export for external systems.
Tracks: counters, gauges, histograms with optional labels (tenant, endpoint, operation).

Thread-safe. Metrics are timestamped and can be queried/exported on demand.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("pie_portal.observability.metrics")


@dataclass
class HistogramBucket:
    """A histogram value with timestamp."""
    value: float
    timestamp: float = field(default_factory=time.time)


class Metric:
    """Base class for all metrics."""
    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self.lock = threading.Lock()
        self.created_at = time.time()

    def export(self) -> dict[str, Any]:
        """Export metric in a standardized format."""
        raise NotImplementedError


class Counter(Metric):
    """A monotonically increasing counter."""
    def __init__(self, name: str, help_text: str):
        super().__init__(name, help_text)
        self._value = 0
        self._by_label: dict[str, int] = {}

    def inc(self, amount: float = 1.0, labels: Optional[dict[str, str]] = None) -> None:
        """Increment counter."""
        with self.lock:
            self._value += amount
            if labels:
                key = tuple(sorted(labels.items()))
                self._by_label[str(key)] = self._by_label.get(str(key), 0) + int(amount)

    def get(self) -> float:
        """Get current value."""
        with self.lock:
            return self._value

    def export(self) -> dict[str, Any]:
        with self.lock:
            return {
                "name": self.name,
                "type": "counter",
                "value": self._value,
                "by_label": dict(self._by_label),
            }


class Gauge(Metric):
    """A metric that can go up or down."""
    def __init__(self, name: str, help_text: str):
        super().__init__(name, help_text)
        self._value = 0.0
        self._by_label: dict[str, float] = {}

    def set(self, value: float, labels: Optional[dict[str, str]] = None) -> None:
        """Set gauge to value."""
        with self.lock:
            self._value = value
            if labels:
                key = tuple(sorted(labels.items()))
                self._by_label[str(key)] = value

    def get(self) -> float:
        """Get current value."""
        with self.lock:
            return self._value

    def inc(self, amount: float = 1.0, labels: Optional[dict[str, str]] = None) -> None:
        """Increment gauge."""
        with self.lock:
            self._value += amount
            if labels:
                key = tuple(sorted(labels.items()))
                self._by_label[str(key)] = self._by_label.get(str(key), 0.0) + amount

    def dec(self, amount: float = 1.0, labels: Optional[dict[str, str]] = None) -> None:
        """Decrement gauge."""
        self.inc(-amount, labels)

    def export(self) -> dict[str, Any]:
        with self.lock:
            return {
                "name": self.name,
                "type": "gauge",
                "value": self._value,
                "by_label": dict(self._by_label),
            }


class Histogram(Metric):
    """A histogram for tracking distributions of values."""
    def __init__(self, name: str, help_text: str, buckets: tuple[float, ...] = ()):
        super().__init__(name, help_text)
        self.buckets = buckets or (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        self._values: list[HistogramBucket] = []
        self._by_label: dict[str, list[HistogramBucket]] = {}
        self._sum = 0.0
        self._count = 0

    def observe(self, value: float, labels: Optional[dict[str, str]] = None) -> None:
        """Record an observation."""
        with self.lock:
            self._values.append(HistogramBucket(value))
            self._sum += value
            self._count += 1
            if labels:
                key = tuple(sorted(labels.items()))
                if str(key) not in self._by_label:
                    self._by_label[str(key)] = []
                self._by_label[str(key)].append(HistogramBucket(value))

    def get_percentile(self, percentile: float) -> Optional[float]:
        """Get percentile value (0-100)."""
        with self.lock:
            if not self._values:
                return None
            sorted_vals = sorted(v.value for v in self._values)
            idx = int(len(sorted_vals) * percentile / 100)
            return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def export(self) -> dict[str, Any]:
        with self.lock:
            if not self._values:
                return {
                    "name": self.name,
                    "type": "histogram",
                    "count": 0,
                    "sum": 0.0,
                }
            return {
                "name": self.name,
                "type": "histogram",
                "count": self._count,
                "sum": self._sum,
                "mean": self._sum / self._count if self._count else 0.0,
                "min": min(v.value for v in self._values),
                "max": max(v.value for v in self._values),
                "p50": self.get_percentile(50),
                "p95": self.get_percentile(95),
                "p99": self.get_percentile(99),
            }


class MetricRegistry:
    """Central registry for all metrics."""
    def __init__(self):
        self.lock = threading.Lock()
        self._metrics: dict[str, Metric] = {}

    def counter(self, name: str, help_text: str = "") -> Counter:
        """Get or create a counter."""
        with self.lock:
            if name not in self._metrics:
                self._metrics[name] = Counter(name, help_text)
            return self._metrics[name]

    def gauge(self, name: str, help_text: str = "") -> Gauge:
        """Get or create a gauge."""
        with self.lock:
            if name not in self._metrics:
                self._metrics[name] = Gauge(name, help_text)
            return self._metrics[name]

    def histogram(self, name: str, help_text: str = "") -> Histogram:
        """Get or create a histogram."""
        with self.lock:
            if name not in self._metrics:
                self._metrics[name] = Histogram(name, help_text)
            return self._metrics[name]

    def export(self) -> dict[str, Any]:
        """Export all metrics."""
        with self.lock:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": [m.export() for m in self._metrics.values()],
            }

    def clear(self) -> None:
        """Clear all metrics (for testing)."""
        with self.lock:
            self._metrics.clear()


# Global metrics registry
metrics = MetricRegistry()
