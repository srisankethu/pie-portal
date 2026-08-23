"""Core metrics collection for PIE observability.

Provides in-memory metrics aggregation with structured export for external systems.
Tracks: counters, gauges, histograms with optional labels (tenant, endpoint, operation).

Thread-safe **within one process, and that is the whole of it.** There is no
shared backing store: every counter, gauge and histogram here lives in an
instance attribute of a module-level registry, so each API worker accumulates
its own. See ``MetricRegistry.export`` for what the payload says about that and
why the fix is disclosure rather than a shared store.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import settings
from ..lease import holder_id

log = logging.getLogger("pie_portal.observability.metrics")


#: How many recent observations a histogram keeps in order to answer percentile
#: questions. Everything else it reports — count, sum, mean, min, max — is
#: lifetime and costs a fixed few bytes, so this number bounds the whole thing.
#:
#: 2048 at ~72 bytes a sample is ~150 kB per histogram, ~1.5 MB across the ten
#: this process registers, and it does not grow after that. It is also enough
#: that a p99 means something: the 99th percentile of 2048 samples is the 21st
#: worst, not a single unlucky request.
DEFAULT_SAMPLE_CAPACITY = 2048


@dataclass(slots=True)
class HistogramBucket:
    """A histogram value with the time it was observed.

    ``slots=True`` because these are the only objects here that exist in
    quantity — it drops the per-instance ``__dict__`` and roughly halves the
    footprint of the retained window.

    The timestamp is not read today. It is kept because it is what a real time
    window would need, and ``dashboard.get_api_performance`` already takes a
    ``window_minutes`` it does not honour — see the note on ``Histogram``.
    """

    value: float
    timestamp: float = field(default_factory=time.time)


def _percentile(ordered: list[float], percentile: float) -> float:
    """The nearest-rank percentile of an already-sorted, non-empty list.

    Pure, and takes no lock — which is the point. It is called both from
    ``Histogram.get_percentile`` (which acquires the lock) and from
    ``Histogram.export`` (which already holds it), and the previous arrangement
    of having the second call the first is what deadlocked.

    Nearest rank: ``ceil(p/100 × N)``, as a 0-based index, clamped into the
    list. The previous ``int(N × p / 100)`` was a floor, which is off by one
    everywhere the product lands exactly on an integer — with 100 observations
    of 1..100 it answered 51 for the median and 100 for p99. Not a rounding
    quibble: these are the numbers the capacity dashboard reports latency with,
    so every p95 it showed was one sample pessimistic, and p100 read as the max
    while p99 already had.

    p=0 is the minimum by convention (rank 0 clamps up to the first element),
    p=100 the maximum.
    """
    n = len(ordered)
    rank = math.ceil(n * percentile / 100)
    return ordered[min(max(rank - 1, 0), n - 1)]


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
    """A distribution: lifetime totals, plus percentiles over a recent window.

    **Bounded, and it was not.** ``_values`` was a plain list appended to on
    every observation and never trimmed, and ``_by_label`` was a dict of the
    same, keyed by label set. The API middleware observes into this on every
    request with ``{"endpoint": request.url.path}`` — the *raw* path, so
    ``/api/v1/decisions/<id>/detail`` is a distinct key per id. That grew in two
    dimensions at once: 50,000 requests across 5,000 distinct URLs retained
    100,000 objects, about 14 MB, in one histogram, on a process that runs for
    weeks and never shrinks.

    Two changes fix it, and they are different in kind:

    - ``_values`` is a ``deque`` with ``maxlen``. The oldest sample falls off
      the end, so memory is flat once the ring fills.
    - ``_by_label`` is **gone**. Not bounded — removed. Nothing ever read it:
      ``Counter`` and ``Gauge`` publish theirs in ``export``, and this class
      never did, so it was pure cost. ``observe`` still *accepts* ``labels``,
      because the call sites pass them and the signature matches its siblings,
      but a histogram aggregates across labels. Per-endpoint percentiles would
      be a reasonable feature; they need a cardinality cap and a route template
      instead of the raw path first, which is a separate change.

    **What each figure now describes**, because the two are no longer the same
    population and a number whose basis is unstated is the thing this codebase
    objects to most:

    - ``count``, ``sum``, ``mean``, ``min``, ``max`` — **lifetime**. Kept
      lifetime deliberately: they cost a few fixed bytes, and quietly
      redefining ``max`` from "worst ever seen" to "worst in the last 2048"
      would change a number on a dashboard without anyone being told.
    - ``p50``, ``p95``, ``p99`` — over the **retained window**, whose size is
      reported alongside them as ``sampled`` and ``capacity``.

    ``dashboard.get_api_performance`` takes a ``window_minutes`` and applies it
    to nothing; that predates this and is not fixed here. The timestamp on each
    retained sample is what an honest implementation of it would use.
    """

    def __init__(self, name: str, help_text: str, buckets: tuple[float, ...] = (),
                 capacity: int = DEFAULT_SAMPLE_CAPACITY):
        super().__init__(name, help_text)
        self.buckets = buckets or (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        self._values: deque[HistogramBucket] = deque(maxlen=capacity)
        self._sum = 0.0
        self._count = 0
        # Lifetime extremes, carried as scalars so they survive the samples that
        # produced them falling out of the ring.
        self._min: Optional[float] = None
        self._max: Optional[float] = None

    def observe(self, value: float, labels: Optional[dict[str, str]] = None) -> None:
        """Record an observation.

        ``labels`` is accepted and not stored — see the class docstring. It stays
        in the signature because every call site passes it and because dropping
        it would make this the one metric type with a different shape.
        """
        with self.lock:
            self._values.append(HistogramBucket(value))
            self._sum += value
            self._count += 1
            self._min = value if self._min is None else min(self._min, value)
            self._max = value if self._max is None else max(self._max, value)

    def get_percentile(self, percentile: float) -> Optional[float]:
        """Get percentile value (0-100), over the retained window.

        Takes the lock, so it must not be called from anything that already
        holds it — see ``export``, and ``_percentile`` for the arithmetic.

        ``None`` when nothing has been observed. Not zero: no observations means
        there is no answer, and a zero here would read as a suspiciously fast
        p99 on a dashboard.
        """
        with self.lock:
            if not self._values:
                return None
            return _percentile(sorted(v.value for v in self._values), percentile)

    def export(self) -> dict[str, Any]:
        """A snapshot of the distribution.

        **Sorts once.** This used to call ``self.get_percentile`` three times
        from inside ``with self.lock``, and ``self.lock`` is a plain
        ``threading.Lock`` rather than an ``RLock`` — so the first of those
        three calls blocked forever waiting for a lock this same thread was
        already holding. Nothing recovered: the thread was gone for the life of
        the process.

        That was not only a hung test. ``GET /api/v1/internal/observability/metrics``
        calls ``MetricRegistry.export``, which calls this for every registered
        metric, and the API middleware records request latency into a histogram
        — so on any live deployment the first request to the metrics endpoint
        wedged a worker permanently, and enough of them would exhaust the pool.
        The endpoint that reports the platform's health was the one that took it
        down.

        The fix is to do the work here, under the one lock acquisition, from a
        single sorted copy. That also removes three redundant sorts and two
        full scans per export, which for a latency histogram is the hot path.

        ``sampled`` and ``capacity`` are here because the percentiles below them
        describe a *different population* from the totals above them — the last
        ``sampled`` observations, not all ``count`` of them. A reader comparing
        a p99 against a count of two million needs to be told that, and a number
        whose basis is unstated is exactly what this codebase treats as a defect.
        They are equal until the ring fills, and after that ``sampled`` pins at
        ``capacity``.
        """
        with self.lock:
            if not self._values:
                return {
                    "name": self.name,
                    "type": "histogram",
                    "count": 0,
                    "sum": 0.0,
                    "sampled": 0,
                    "capacity": self._values.maxlen,
                }
            ordered = sorted(v.value for v in self._values)
            return {
                "name": self.name,
                "type": "histogram",
                # Lifetime, all of them — see the class docstring.
                "count": self._count,
                "sum": self._sum,
                "mean": self._sum / self._count if self._count else 0.0,
                "min": self._min,
                "max": self._max,
                # Over the retained window, whose size is the two fields below.
                "p50": _percentile(ordered, 50),
                "p95": _percentile(ordered, 95),
                "p99": _percentile(ordered, 99),
                "sampled": len(ordered),
                "capacity": self._values.maxlen,
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
        """Every metric **this process** holds, labelled as such.

        The numbers below are one worker's. Nothing here is shared: the
        registry is a module-level singleton per process, traffic reaches it
        through per-process middleware, and the deployment runs
        ``UVICORN_WORKERS`` of them. So a scrape returns whichever worker the
        load balancer happened to route it to — roughly ``1/N`` of the traffic,
        a different ``1/N`` each time.

        That was previously presented as the whole platform's figures, which is
        the failure this codebase objects to most: an operator debugging a
        latency problem read a p99 computed from half the samples with nothing
        in the payload to say so, and could not tell which half.

        **Why the fix is disclosure and not a shared store.** Putting a write on
        the request path is the incident CLAUDE.md §4 documents — a long or
        contended write on SQLite blocked every reader including ``/api/health``
        — and metrics are written on *every* request, which is the worst
        possible shape for that. Redis is provisioned next to the API but
        nothing imports it and no feature may require it (``config.REDIS_URL``
        says so). So the honest thing is for the payload to state what it is,
        and to carry the identity of the process that produced it:

        - ``scope`` — ``"worker"``. Not the deployment.
        - ``worker`` — this process, from ``lease.holder_id()``: the same
          ``host:pid:rand`` string the scheduler lease is arbitrated with,
          reused rather than reinvented so one process has one name everywhere
          it appears. Stable for the life of the process and re-derived after a
          fork, which is what makes two scrapes comparable — same id means the
          same worker's counters and the deltas mean something; a different id
          means a different population and subtracting them is meaningless.
        - ``workers_configured`` — how many workers the supervisor was *told*
          to start, or ``null`` when nothing declared it. Declared, not
          observed: this process cannot see its siblings. It is here so a
          scraper knows how many distinct ``worker`` values it should expect to
          collect before it has the whole picture, and ``null`` says "unknown",
          never "one".
        - ``composition`` — how to combine several workers' payloads, because
          it differs by metric type and getting it wrong is silent. Counters
          and gauges sum. Percentiles do **not**: p99 of a union is not the mean
          of the p99s, so they are compared per worker, not averaged.
        """
        with self.lock:
            exported = [m.export() for m in self._metrics.values()]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scope": "worker",
            "worker": holder_id(),
            "workers_configured": settings.UVICORN_WORKERS,
            "composition": (
                "One API worker's counters, not the deployment's. Scrape until "
                "the worker ids repeat to cover them all. Across workers, "
                "counters and gauges sum; percentiles (p50/p95/p99) do not — "
                "compare them per worker rather than averaging."
            ),
            "metrics": exported,
        }

    def clear(self) -> None:
        """Clear all metrics (for testing)."""
        with self.lock:
            self._metrics.clear()


# Global metrics registry
metrics = MetricRegistry()
