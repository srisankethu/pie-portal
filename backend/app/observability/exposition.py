"""Prometheus text exposition for this process's metric registry.

A pure renderer: it takes the dict ``MetricRegistry.export()`` already returns
and turns it into the text format Prometheus scrapes. It reads no database,
holds no state, and makes no decision about *what* is measured — that is
``metrics.py``'s job, and keeping the two apart is what lets the JSON export and
the exposition never disagree about a number.

**No ``prometheus_client`` dependency, and this is deliberate rather than
incidental.** The exposition format is a dozen lines of text; the library that
emits it also brings its own ``CollectorRegistry``, which would then be a
*second* metrics registry sitting beside ``MetricRegistry`` — two places a
counter can live, two answers to "how many requests has this worker served",
and a slow drift between them that nothing would catch. ``backend/requirements.txt``
is unchanged on purpose.

──────────────────────────────────────────────────────────────────────────────
What is emitted, and why the shapes differ
──────────────────────────────────────────────────────────────────────────────

**Every series carries ``worker``.** ``MetricRegistry`` is a per-process
singleton, so with ``UVICORN_WORKERS=2`` a scrape sees one worker's counters —
roughly ``1/N`` of the traffic, a different ``1/N`` each time. The JSON export
already says so in prose (``scope``, ``worker``, ``workers_configured``). Prose
does not survive a scrape, so the identity becomes a *label*: two workers then
produce two series rather than one series that jumps, and
``sum(rate(api_requests_total[5m]))`` is the deployment's rate rather than a
coin flip. ``docs/observability.md`` states which metric types may be summed
across workers and which may not.

**Counters.** A counter with a label breakdown emits the breakdown *only* —
``api_requests_total{worker,method,endpoint,status}`` — because emitting the
aggregate under the same name as well would double-count under ``sum()``. The
parts add up to the whole: any increment that carried no labels, or whose label
set arrived after ``MAX_LABEL_SETS``, is emitted as a single
``label_overflow="true"`` series rather than vanishing (``Counter`` tracks it as
``unattributed``). A counter that has no breakdown at all emits one plain
series.

**Gauges.** One series each. Gauges have no breakdown — see ``metrics.Gauge``.

**Histograms become Prometheus *summaries*, not histograms.** A Prometheus
histogram is cumulative bucket counts, and this registry does not keep bucket
counts — it keeps a bounded ring of recent samples and computes quantiles from
it. Claiming ``# TYPE histogram`` while emitting no ``_bucket`` series would
make ``histogram_quantile()`` return nothing on a metric that looks like it
should work. A summary is exactly what this is: pre-computed quantiles that
cannot be aggregated across instances, alongside a lifetime ``_sum`` and
``_count`` that can. A histogram with no observations emits ``_sum`` and
``_count`` and **no quantiles** — there is no answer to give, and a ``0``
quantile reads as a suspiciously fast p99.

**The ``api_requests`` rate is deliberately not computed here.**
``capacity.calculate_api_utilization`` returns ``None`` because a rate needs two
samples and an interval, and this process keeps one lifetime counter. Exporting
that raw counter is the fix: ``rate()`` differences it across scrapes, which is
the second sample that was missing. Computing a rate inside the exporter would
be inventing the interval.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

log = logging.getLogger("pie_portal.observability.exposition")

#: What a scraper must be served for Prometheus to parse the body. The version
#: is the exposition format's, not this application's.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: A valid metric name. Every name in this registry is a literal in
#: `instrumentation.py`, so a name that fails this is a typo, not user input —
#: which is why the response for one is a logged warning and a skipped metric
#: rather than a 500 on a scrape endpoint polled every 15 seconds.
_VALID_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")

#: The label a counter's residual is published under: increments with no labels,
#: plus any that arrived after the cardinality cap. Present only when non-zero.
_OVERFLOW_LABEL = "label_overflow"


def _escape(value: str) -> str:
    """Escape a label value per the exposition format.

    Backslash, double quote and newline, in that order — the backslash first,
    or the escapes this function adds would themselves be escaped.
    """
    return (value.replace("\\", "\\\\")
                 .replace('"', '\\"')
                 .replace("\n", "\\n"))


def _number(value: float) -> str:
    """A metric value as the format wants it.

    Whole numbers render without a trailing ``.0`` — a counter reading
    ``15432`` rather than ``15432.0`` is what every other exporter emits and
    what a human comparing two scrapes by eye can subtract. Infinities and NaN
    have their own spellings in this format and Python's do not match.
    """
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "+Inf" if number > 0 else "-Inf"
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def _series(name: str, labels: dict[str, Any], value: float) -> str:
    """One sample line. Labels are sorted, so two scrapes render identically."""
    if not labels:
        return f"{name} {_number(value)}"
    rendered = ",".join(
        f'{key}="{_escape(str(labels[key]))}"' for key in sorted(labels))
    return f"{name}{{{rendered}}} {_number(value)}"


def _help_line(name: str, help_text: str) -> str:
    """One HELP line.

    HELP is single-line by definition, so a newline inside the text would start
    a line the parser reads as a bogus sample. The format escapes a backslash
    as ``\\`` and a newline as ``\n``; both are done here rather than trusted
    not to occur, because help text is prose and prose gets edited.
    """
    escaped = help_text.replace("\\", "\\\\").replace("\n", "\\n")
    return f"# HELP {name} {escaped}"


def _render_counter(metric: dict[str, Any], base: dict[str, str],
                    out: list[str]) -> None:
    name = metric["name"]
    out.append(f"# TYPE {name} counter")
    series = metric.get("label_series") or []
    for entry in series:
        out.append(_series(name, {**base, **entry["labels"]}, entry["value"]))
    residual = metric.get("unattributed", 0.0)
    if series:
        # Only alongside a breakdown. With no breakdown the residual *is* the
        # total, and publishing it twice under two label sets would double it.
        if residual:
            out.append(_series(name, {**base, _OVERFLOW_LABEL: "true"}, residual))
    else:
        out.append(_series(name, base, metric["value"]))


def _render_gauge(metric: dict[str, Any], base: dict[str, str],
                  out: list[str]) -> None:
    out.append(f"# TYPE {metric['name']} gauge")
    out.append(_series(metric["name"], base, metric["value"]))


def _render_histogram(metric: dict[str, Any], base: dict[str, str],
                      out: list[str]) -> None:
    name = metric["name"]
    out.append(f"# TYPE {name} summary")
    for quantile, key in (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99")):
        value = metric.get(key)
        if value is not None:
            out.append(_series(name, {**base, "quantile": quantile}, value))
    out.append(_series(f"{name}_sum", base, metric.get("sum", 0.0)))
    out.append(_series(f"{name}_count", base, metric.get("count", 0)))


_RENDERERS = {
    "counter": _render_counter,
    "gauge": _render_gauge,
    "histogram": _render_histogram,
}


#: Help text by metric type, because `Metric.export` does not publish the
#: registry's own `help_text` and adding it is a change to the JSON contract.
#: Type-level text is thin but true; per-metric help lives in
#: `docs/observability.md`, which is where a reader can be given a paragraph.
_DEFAULT_HELP = {
    "counter": "Cumulative total for this worker; difference it with rate().",
    "gauge": "Current value for this worker.",
    "histogram": ("Lifetime sum and count for this worker; quantiles over its "
                  "most recent samples only, so they do not aggregate."),
}

#: The declared worker count, published so a scraper can tell whether it has
#: collected every worker yet.
_WORKERS_METRIC = "pie_workers_configured"


def _worker_count_series(payload: dict[str, Any],
                         base: dict[str, str]) -> list[str]:
    """How many workers the supervisor was *told* to start, or nothing at all.

    Declared, not observed — a worker cannot see its siblings. When nothing
    declared it the metric is **absent**, not ``1``: absence is a gap a scraper
    can notice, whereas ``1`` is a confident wrong answer that would tell it a
    single scrape had the whole deployment. Same value on every worker, so read
    it with ``max()``.
    """
    declared: Optional[int] = payload.get("workers_configured")
    if declared is None:
        return []
    return [
        _help_line(_WORKERS_METRIC,
                   "API workers the supervisor was told to start (declared, "
                   "not observed); absent when nothing declared it."),
        f"# TYPE {_WORKERS_METRIC} gauge",
        _series(_WORKERS_METRIC, base, declared),
    ]


def render(payload: dict[str, Any]) -> str:
    """The exposition body for one ``MetricRegistry.export()`` payload.

    Deterministic: registry insertion order, label sets sorted, label names
    sorted within a set. Two scrapes of an idle process are byte-identical,
    which is what makes a diff between them mean something.
    """
    worker = str(payload.get("worker") or "unknown")
    base = {"worker": worker}
    out: list[str] = []

    for metric in payload.get("metrics", []):
        name = metric.get("name", "")
        if not _VALID_NAME.match(name):
            log.warning("skipping metric with an invalid Prometheus name: %r", name)
            continue
        renderer = _RENDERERS.get(metric.get("type"))
        if renderer is None:
            log.warning("skipping metric %s of unknown type %r", name,
                        metric.get("type"))
            continue
        help_text = metric.get("help") or _DEFAULT_HELP.get(
            metric.get("type"), "")
        if help_text:
            out.append(_help_line(name, help_text))
        renderer(metric, base, out)

    out.extend(_worker_count_series(payload, base))
    out.append("")  # the format wants a trailing newline
    return "\n".join(out)
