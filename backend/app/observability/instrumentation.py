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

    # Track request size
    try:
        if hasattr(request, 'body'):
            body = await request.body()
            api_request_size.observe(len(body), {"endpoint": request.url.path})
    except Exception:
        pass

    # Track timing
    start_time = time.time()
    response = None

    try:
        response = await call_next(request)
        duration = time.time() - start_time

        # Record metrics
        api_requests.inc(
            labels={"method": request.method, "endpoint": request.url.path, "status": response.status_code}
        )
        api_request_duration.observe(duration, {"endpoint": request.url.path})

        if response.status_code >= 400:
            api_errors.inc(labels={"endpoint": request.url.path, "status": response.status_code})

        # Track response size if possible
        if hasattr(response, 'body'):
            api_response_size.observe(len(response.body), {"endpoint": request.url.path})

        return response
    except Exception as e:
        duration = time.time() - start_time
        api_errors.inc(labels={"endpoint": request.url.path, "error": type(e).__name__})
        api_request_duration.observe(duration, {"endpoint": request.url.path})
        log.exception("unhandled error in request %s", req_id)
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
