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


#: The most distinct label combinations one ``Counter`` will keep a tally for.
#:
#: A ceiling, not a budget. The endpoint label is a matched route template, so
#: the live cardinality of ``api_requests_total`` is bounded by the routing
#: table — 183 path x method pairs against roughly a dozen status codes this
#: application actually returns, so under ~2,400 combinations worst case and two
#: orders of magnitude fewer in practice. 4096 sits above that on purpose: it
#: must never truncate legitimate traffic, because a breakdown that quietly
#: stops at a round number is worse than no breakdown at all.
#:
#: What it is for is the *next* call site. An unbounded label — a quote id, a
#: raw URL, a customer name — is how a Prometheus TSDB is killed, and it would
#: be this codebase exporting the problem rather than merely holding it. At the
#: cap the tally stops growing, the increments still land in the total via
#: ``Counter._unattributed``, and the export says the breakdown is short.
#:
#: ~4096 entries of a small tuple key is a few hundred kB per counter, and it
#: does not grow after that.
MAX_LABEL_SETS = 4096

#: A label set's identity: its pairs, sorted, with both halves as strings.
LabelKey = tuple[tuple[str, str], ...]


def _label_key(labels: dict[str, Any]) -> LabelKey:
    """A stable, hashable identity for a label set.

    Sorted so that ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` are one series
    rather than two, and stringified because a label *value* is a string
    everywhere it is eventually read — the middleware passes
    ``response.status_code`` as an ``int``, which would otherwise make
    ``status=200`` and ``status="200"`` two different keys the day a call site
    changes.
    """
    return tuple(sorted((str(name), str(value)) for name, value in labels.items()))


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
    """A monotonically increasing total, with a **bounded** per-label breakdown.

    The breakdown was not bounded, and that is the defect this class was
    rewritten for. ``_by_label`` was a dict keyed by
    ``str(tuple(sorted(labels.items())))``, written on every observation and
    never trimmed, while the API middleware labelled every request with
    ``request.url.path`` — the *raw* path. So
    ``/api/v1/quotes/<uuid>/lines/<uuid>/supply`` was its own key, one key per
    distinct URL, on a process that runs for weeks. ``Histogram`` had exactly
    this and the fix there was deletion, because nothing read it. Here the
    breakdown *is* read — it is what makes ``rate(...) by (status)`` possible
    for a scraper — so it is bounded instead.

    Two things bound it, and they are different in kind:

    - **The call site supplies a bounded label set.** The endpoint label is the
      matched route *template* (``/api/v1/quotes/{quote_id}``), never the raw
      path — see ``instrumentation.route_template``. Methods x statuses x route
      templates is bounded by the routing table; raw paths are bounded by
      nothing.
    - **This class refuses to grow past ``label_capacity`` anyway**, so a future
      call site cannot reintroduce the leak by passing something unbounded. The
      cap is a backstop, deliberately set above what the routing table can
      produce, so it never truncates legitimate traffic.

    Nothing is lost when the cap bites, and nothing is silently misattributed:
    an increment that has no labels, or whose label set arrived after the cap
    was reached, is added to ``_unattributed``. So
    ``sum(label_series) + unattributed == value`` exactly, always — which is
    what lets the exposition emit the breakdown alone and still have the parts
    add up to the total.
    """

    def __init__(self, name: str, help_text: str,
                 label_capacity: int = MAX_LABEL_SETS):
        super().__init__(name, help_text)
        self._value = 0.0
        self._label_capacity = label_capacity
        self._by_label: dict[LabelKey, float] = {}
        #: Increments that carry no labels, plus those whose label set arrived
        #: after the cap. Never silently dropped — see the class docstring.
        self._unattributed = 0.0

    def inc(self, amount: float = 1.0, labels: Optional[dict[str, Any]] = None) -> None:
        """Increment the total, and the label set's own tally if there is room."""
        with self.lock:
            self._value += amount
            if not labels:
                self._unattributed += amount
                return
            key = _label_key(labels)
            if key in self._by_label:
                self._by_label[key] += amount
            elif len(self._by_label) < self._label_capacity:
                self._by_label[key] = amount
            else:
                self._unattributed += amount

    def get(self) -> float:
        """Get current value."""
        with self.lock:
            return self._value

    def export(self) -> dict[str, Any]:
        """The total, and the breakdown as a list of ``{labels, value}``.

        A list rather than the old dict, because the old dict's keys were
        ``str(tuple(sorted(...)))`` — ``"(('method', 'GET'), ('status', 200))"``
        — which no consumer could use without parsing Python repr back out of
        JSON. The exposition renders these as Prometheus labels directly.

        ``label_capacity`` and ``label_sets`` are published so a reader can see
        the breakdown is complete rather than assume it: they are equal only if
        the cap has been reached, and ``unattributed`` then says how much
        traffic the breakdown does not account for.
        """
        with self.lock:
            return {
                "name": self.name,
                "type": "counter",
                "help": self.help_text,
                "value": self._value,
                "label_series": [
                    {"labels": dict(key), "value": value}
                    for key, value in sorted(self._by_label.items())
                ],
                "label_sets": len(self._by_label),
                "label_capacity": self._label_capacity,
                "unattributed": self._unattributed,
            }


class Gauge(Metric):
    """A single current value that can go up or down.

    **No per-label breakdown, by deletion rather than by bound.** ``_by_label``
    here was the same unbounded dict ``Counter`` carried and ``Histogram``
    already had removed, and it was write-only in the same way: no call site
    ever passed labels to a gauge, no reader ever looked at the field. A
    breakdown nobody writes and nobody reads is pure cost with a leak attached,
    so it is gone rather than capped — the precedent ``Histogram`` set.

    ``labels`` stays in the signatures, accepted and ignored, because the three
    metric types have to keep one shape: a sibling that quietly rejects an
    argument its peers take is the Liskov problem CLAUDE.md §5 names. If a gauge
    ever needs a real breakdown it should be bounded the way ``Counter``'s is,
    and it will need a defensible answer for what a *set* means across label
    sets first.
    """

    def __init__(self, name: str, help_text: str):
        super().__init__(name, help_text)
        self._value = 0.0

    def set(self, value: float, labels: Optional[dict[str, Any]] = None) -> None:
        """Set gauge to value. ``labels`` is accepted and not stored."""
        with self.lock:
            self._value = value

    def get(self) -> float:
        """Get current value."""
        with self.lock:
            return self._value

    def inc(self, amount: float = 1.0, labels: Optional[dict[str, Any]] = None) -> None:
        """Increment gauge. ``labels`` is accepted and not stored."""
        with self.lock:
            self._value += amount

    def dec(self, amount: float = 1.0, labels: Optional[dict[str, Any]] = None) -> None:
        """Decrement gauge."""
        self.inc(-amount, labels)

    def export(self) -> dict[str, Any]:
        with self.lock:
            return {
                "name": self.name,
                "type": "gauge",
                "help": self.help_text,
                "value": self._value,
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
    - ``_by_label`` is **gone**. Not bounded — removed. Nothing ever read it,
      so it was pure cost. ``observe`` still *accepts* ``labels``, because the
      signature matches its siblings, but a histogram aggregates across labels.
      Per-endpoint percentiles would be a reasonable feature; they need a
      cardinality cap and a route template instead of the raw path first, and
      those two now exist — ``MAX_LABEL_SETS`` and
      ``instrumentation.route_template`` — so the remaining work is deciding
      what a per-endpoint p99 over a 2048-sample ring actually means when the
      samples are shared across every route. Until someone answers that, the
      call sites pass no labels here at all.

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
                    "help": self.help_text,
                    "count": 0,
                    "sum": 0.0,
                    "sampled": 0,
                    "capacity": self._values.maxlen,
                }
            ordered = sorted(v.value for v in self._values)
            return {
                "name": self.name,
                "type": "histogram",
                "help": self.help_text,
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
        #: When this worker's counters started at zero. Every number here is
        #: cumulative from that moment — there is no windowing anywhere in this
        #: module — so a reader who wants a rate needs the denominator, and a
        #: reader comparing two scrapes needs to know a restart reset them.
        #: Recorded rather than inferred, because "since the process started"
        #: is only knowable from inside the process.
        self.started_at = datetime.now(timezone.utc)

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
            "counting_since": self.started_at.isoformat(),
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
        """Clear all metrics (for testing).

        Moves ``started_at`` too. A cleared registry whose start time still
        pointed at process boot would report a rate over a period during which
        the counters were deliberately thrown away.
        """
        with self.lock:
            self._metrics.clear()
            self.started_at = datetime.now(timezone.utc)


# Global metrics registry
metrics = MetricRegistry()
