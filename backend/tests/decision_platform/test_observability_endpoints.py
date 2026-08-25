"""The observability endpoints, exercised over HTTP rather than at the service.

`DashboardService.__init__` gained a required `organization_id` and eight call
sites in `routers/internal.py` were edited to pass it. Every test of that change
constructed the service directly, so the wiring — the part that would 500 in
production if one call site had been missed — was the only part nothing checked.
A service-layer test cannot catch a router that forgot an argument.

These also pin the two honesty properties the payloads exist to carry, because
both are the kind of thing a later refactor quietly drops: a component nobody
measured must not band as healthy, and an empty window must be distinguishable
from a scheduler that has stopped queueing.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def owner_headers(api_client):
    from .test_api_authz import _hdr, _login
    return _hdr(_login(api_client, "s.menon@pie.example"))


@pytest.fixture()
def sales_headers(api_client):
    from .test_api_authz import _hdr, _login
    return _hdr(_login(api_client, "r.nair@pie.example"))


ENDPOINTS = (
    "/api/v1/internal/observability/dashboard",
    "/api/v1/internal/observability/health",
    "/api/v1/internal/observability/metrics",
    "/api/v1/internal/observability/jobs",
    "/api/v1/internal/observability/syncs",
    "/api/v1/internal/observability/capacity",
)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_observability_endpoint_answers(api_client, owner_headers, path):
    """The arity check: a missed call site is a 500, and only HTTP sees it."""
    r = api_client.get(path, headers=owner_headers)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:400]}"
    assert isinstance(r.json(), dict)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_observability_is_not_for_salespeople(api_client, sales_headers, path):
    assert api_client.get(path, headers=sales_headers).status_code == 403


def test_the_metrics_export_says_whose_view_it_is(api_client, owner_headers):
    """Two scrapes are only comparable if the reader can tell they are the same
    process. With UVICORN_WORKERS=2 the registry is per-process, so a payload
    that does not name its worker invites subtracting one worker's counter from
    another's."""
    body = api_client.get("/api/v1/internal/observability/metrics",
                          headers=owner_headers).json()
    assert body["scope"] == "worker"
    assert body["worker"], "the export must name the process it came from"


def test_an_unmeasured_capacity_component_does_not_read_as_healthy(
        api_client, owner_headers):
    """`api_requests` is not derivable from a lifetime counter, so it is null —
    and null must not band green. This is the assertion that would have failed
    while the old code divided a cumulative count by a per-second limit."""
    body = api_client.get("/api/v1/internal/observability/capacity",
                          headers=owner_headers).json()
    by_name = {c["name"]: c for c in body["components"]}
    api = by_name["api_requests"]
    assert api["current"] is None
    assert api["status"] == "unknown"
    assert api["basis"], "a null must name what is missing"
    assert "api_requests" in body["unmeasured"]


def test_a_quiet_window_is_distinguishable_from_a_dead_scheduler(
        api_client, owner_headers):
    """Zero completed runs is the same integer whether nothing was due or the
    scheduler stopped queueing a week ago. The history anchor is what separates
    them, so it is published even when the window is empty."""
    body = api_client.get("/api/v1/internal/observability/jobs",
                          headers=owner_headers).json()
    history = body["history"]
    assert "last_run_at" in history and "last_successful_run_at" in history
    assert history["basis"]
    assert history["ever_run"] is (history["last_run_at"] is not None)
