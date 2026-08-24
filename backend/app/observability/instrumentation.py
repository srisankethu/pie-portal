"""Instrumentation for API endpoints and database operations.

Tracks request metrics, latencies, errors, and database query performance.
Uses context variables to correlate operations across request boundaries.
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import Request, Response
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

from ..config import settings

from .metrics import metrics

log = logging.getLogger("pie_portal.observability.instrumentation")

# Context variables for correlation
request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
tenant_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)

# Metric definitions
api_requests = metrics.counter("api_requests_total", "Total API requests")
api_request_duration = metrics.histogram("api_request_duration_seconds", "API request duration")
api_request_size = metrics.histogram("api_request_bytes", "API request body size")
api_response_size = metrics.histogram("api_response_bytes", "API response body size")
api_errors = metrics.counter("api_errors_total", "Total API errors")

db_queries = metrics.counter("db_queries_total", "Total database queries")
db_query_duration = metrics.histogram("db_query_duration_seconds", "Database query duration")
db_connections = metrics.gauge("db_connections", "Active database connections")
db_pool_size = metrics.gauge("db_pool_size", "Database connection pool size")
db_errors = metrics.counter("db_errors_total", "Total database errors")


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return f"req_{uuid.uuid4().hex[:12]}"


def get_request_context() -> dict[str, Any]:
    """Get current request context."""
    return {
        "request_id": request_id.get(),
        "trace_id": trace_id.get(),
        "tenant_id": tenant_id.get(),
    }


#: The endpoint label for a request that matched no route.
#:
#: A 404 matches nothing, so there is no template to report. The tempting
#: fallback — use ``request.url.path`` when the route is missing — reintroduces
#: unbounded cardinality through the back door, and does it on exactly the
#: traffic most likely to be a scanner walking random URLs. One constant is the
#: honest answer: something was requested that this application does not serve,
#: and *which* thing belongs in the access log, not in a time series.
UNMATCHED_ROUTE = "<unmatched>"


def route_template(request: Request) -> str:
    """The matched route's template, e.g. ``/api/v1/quotes/{quote_id}``.

    **Never the raw path.** ``request.url.path`` is
    ``/api/v1/quotes/8f2a.../lines/1b7c.../supply``, one distinct value per
    quote, and a label with one value per business record is how a Prometheus
    TSDB falls over. The template collapses all of those onto one series, and
    the set of templates is the routing table — finite, and it changes only
    when someone adds a route.

    Starlette puts the matched route on the request scope while it dispatches,
    so this is only meaningful **after** ``call_next`` has returned; before
    that, nothing has matched yet.

    Two levels, because only FastAPI's ``APIRoute`` publishes itself on the
    scope. A route added with Starlette's own ``add_route`` — which is how
    ``/openapi.json``, ``/docs`` and ``/redoc`` get mounted — leaves ``route``
    unset and only ``endpoint`` and ``path_params`` behind. Reporting those as
    misses would file dev-console traffic alongside a scanner's random URLs, so:

    1. ``route.path_format`` when the route published itself. Parameterised or
       not, this is the template.
    2. Otherwise, if something matched (``endpoint`` is set) and it took **no**
       path parameters, the literal path *is* that route's template — it is one
       fixed string drawn from the routing table, so cardinality is unchanged.
       A route that took parameters and did not publish its template could only
       be reconstructed from the raw path, which is the unbounded thing this
       function exists to avoid, so it is a miss instead.
    3. Otherwise ``UNMATCHED_ROUTE``.

    Every branch's value comes from the routing table or is a constant. None
    comes from the URL a caller chose, which is the property that matters.
    """
    scope = request.scope
    template = getattr(scope.get("route"), "path_format", None)
    if template:
        return template
    if scope.get("endpoint") is not None and not scope.get("path_params"):
        return request.url.path
    return UNMATCHED_ROUTE


async def api_instrumentation_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware to track API metrics."""
    # Generate IDs for this request
    req_id = generate_request_id()
    trace = request.headers.get("X-Trace-ID", generate_request_id())
    request_id.set(req_id)
    trace_id.set(trace)

    # Extract tenant from headers or auth if available
    tenant = request.headers.get("X-Tenant-ID")
    if tenant:
        tenant_id.set(tenant)

    # Track request size. No labels: a histogram here aggregates across them
    # (see `metrics.Histogram`), and the only label this ever passed was the raw
    # path — which is discarded on arrival and cannot be a route template yet
    # anyway, because nothing has been matched at this point in the request.
    try:
        if hasattr(request, 'body'):
            body = await request.body()
            api_request_size.observe(len(body))
    except Exception:
        pass

    # Track timing
    start_time = time.time()
    response = None

    try:
        response = await call_next(request)
        duration = time.time() - start_time

        # Every label below is bounded: `method` by the HTTP verbs this app
        # routes, `status` by the codes it returns, `endpoint` by the routing
        # table. Their product is the ceiling on how many series one worker can
        # publish for these counters, and it is a number — see
        # `metrics.MAX_LABEL_SETS`, which backstops it. `request.url.path` was
        # here and is bounded by nothing.
        endpoint = route_template(request)
        api_requests.inc(
            labels={"method": request.method, "endpoint": endpoint,
                    "status": response.status_code}
        )
        api_request_duration.observe(duration)

        if response.status_code >= 400:
            api_errors.inc(labels={"endpoint": endpoint,
                                   "status": response.status_code})

        # Track response size if possible
        if hasattr(response, 'body'):
            api_response_size.observe(len(response.body))

        return response
    except Exception as e:
        duration = time.time() - start_time
        # The same two label names as the branch above, deliberately. This used
        # to be `{"endpoint": ..., "error": type(e).__name__}`: a second label
        # *set* on one metric, so `sum by (status)` swept every unhandled error
        # into an empty bucket, and the exception class name is bounded only by
        # what happens to be importable — any dependency's error type could
        # arrive. The class is what the reader wants, and it is already in the
        # log line below with a traceback attached, which is where an unbounded
        # string belongs. `main.unhandled_error` turns this into a 500 for the
        # client, so 500 is what the counter records.
        api_errors.inc(labels={"endpoint": route_template(request),
                               "status": 500})
        api_request_duration.observe(duration)
        log.exception("unhandled error in request %s (%s)", req_id,
                      type(e).__name__)
        raise


def instrument_database(engine: Engine) -> None:
    """Instrument a SQLAlchemy engine for metrics collection."""

    @event.listens_for(Pool, "connect")
    def receive_connect(dbapi_conn, connection_record):
        db_connections.inc()
        db_pool_size.set(engine.pool.size())

    @event.listens_for(Pool, "close")
    def receive_close(dbapi_conn, connection_record):
        db_connections.dec()

    @event.listens_for(Engine, "before_cursor_execute")
    def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start_time", []).append(time.time())

    @event.listens_for(Engine, "after_cursor_execute")
    def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        total_time = time.time()
        times = conn.info.get("query_start_time", [])
        if times:
            start_time = times.pop()
            duration = total_time - start_time

            db_queries.inc()
            db_query_duration.observe(duration)

            # Log slow queries. Threshold from DB_SLOW_QUERY_MS (0 = off);
            # the compose stack sets 500. The line carries the statement text
            # with placeholders and never the bound parameters — binds hold
            # customer names, payload text and credentials, and trust/ exists
            # so those never reach a log file.
            threshold_ms = settings.DB_SLOW_QUERY_MS
            if threshold_ms > 0 and duration * 1000 >= threshold_ms:
                log.warning("slow query (%.0f ms): %s", duration * 1000,
                            " ".join(statement.split())[:500])

    @event.listens_for(Engine, "handle_error")
    def receive_handle_error(exception_context):
        db_errors.inc()
