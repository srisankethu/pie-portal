"""Internal / operational endpoints (not user-facing).

Maps to spec §12 internal surface: health + Zoho sync trigger. Detector-run and
audit endpoints are deferred to later phases.
"""
from __future__ import annotations

import hmac
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner, require_owner
from ..config import settings
from ..db import get_session
from ..ingestion.sync import SyncService, get_source
from ..seed import ensure_org_and_users
from ..signals.engine import run_detectors

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


def _require_intelligence(session: Session, principal: Principal) -> None:
    """Plan gate for the two endpoints that run the decision layer.

    Per endpoint rather than on the router: this router mixes health, sync and
    Zoho checks — all of which every plan needs — with the two triggers that
    are the paid product.
    """
    from .. import entitlements

    try:
        entitlements.assert_feature(session, principal.organization_id, "intelligence")
    except entitlements.PlanRefused as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"ok": db_ok, "service": "decision-platform", "database": db_ok,
            "zoho_source": settings.ZOHO_SOURCE}


@router.get("/ai-metrics")
def ai_metrics(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Rolling AI health and cost metrics for this organization (owner only).

    Reports degraded/failed/suppressed rates, cache hit rate, cost per decision
    and per day, and the failure-reason distribution, over 7- and 30-day windows.
    """
    from ..ai.metrics import report
    from ..repositories import AiTelemetryRepository

    return report(AiTelemetryRepository(session, principal.organization_id))


@router.get("/detector-outcomes")
def detector_outcomes(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """What each detector raised, and what humans did with it (owner only).

    The question the signal layer has never been able to answer about itself:
    how much of what it raises does somebody then throw away. Per signal type,
    over 7- and 30-day windows — signals emitted, decisions opened, the outcome
    distribution, and the dismissal rate with a two-sided band.

    Reads only rows that already exist. It computes nothing commercial, stores
    nothing, and calls no provider.
    """
    from ..decisions.outcomes import report
    from ..repositories import DecisionRepository, SignalRepository

    org = principal.organization_id
    return report(DecisionRepository(session, org), SignalRepository(session, org))


@router.get("/queue-adoption")
def queue_adoption(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Whether the queue is worked, and whether it deserves to be (owner only).

    The other half of the question `/detector-outcomes` asks. That endpoint asks
    whether a detector is noisy — how much of what it raises gets thrown away.
    This asks what the people did: acceptance by category and by user, the modify
    rate with a distance measured only where a distance genuinely exists, and
    acceptance against how deep the queue was when they ruled.

    Two endpoints and one module on purpose. They read the same rows and share
    one vocabulary for what a human did, so the definitions cannot drift; they
    are named separately because a payload carrying per-user acceptance and
    quote-line pricing distance is not a statement about detectors, and an
    endpoint whose name is wrong is worse than a second endpoint.

    Owner-only for the same two reasons `ai-metrics` is. It reads across every
    role's queue, so it is a view of how other people work, and the value-at-risk
    figures come from `impact.financial`, which is cost information.
    """
    from ..decisions.outcomes import adoption_report
    from ..repositories import DecisionRepository

    org = principal.organization_id
    return adoption_report(session, DecisionRepository(session, org))


@router.get("/ai-readiness")
def ai_readiness(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Is the AI actually on, and what would the next run cost? (owner only)

    The two questions that belong together and were previously answerable only
    by reading environment variables on the server: which provider will really
    run — a configured one that cannot be built falls back to the mock — and,
    for the signals standing right now, how many provider calls that is and what
    they would cost at the configured rates. Calls nothing and sends nothing.
    """
    from ..decisions.preflight import estimate

    return estimate(session, principal.organization_id)


@router.get("/zoho/check")
def zoho_check(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Verify this organization's Zoho credentials without pulling any data.

    Run this before the first sync: it distinguishes the things that actually
    go wrong — no connection configured, wrong data centre, revoked/incorrect
    token, and a valid login that cannot see the connected organization id.
    """
    if settings.ZOHO_SOURCE != "api":
        return {"ok": False, "source": settings.ZOHO_SOURCE,
                "detail": "ZOHO_SOURCE is not 'api' — the offline fixture source is in use."}

    from ..ingestion.connections import get_zoho_credentials
    from ..ingestion.zoho_client import ZohoApiSource, ZohoError

    creds = get_zoho_credentials(session, principal.organization_id)
    if creds is None:
        return {"ok": False, "source": "api",
                "detail": "This organization has no Zoho connection. Connect one via "
                          "PUT /api/v1/data/connection."}
    try:
        result = ZohoApiSource(credentials=creds).ping()
    except ZohoError as e:
        return {"ok": False, "source": "api", "detail": str(e),
                "api_base": creds.api_base, "accounts_base": creds.accounts_base}
    ok = bool(result.get("organization_found"))
    return {
        "ok": ok, "source": "api",
        "api_base": creds.api_base,
        "accounts_base": creds.accounts_base,
        "detail": None if ok else (
            "Authenticated, but this login cannot see the connected organization id. "
            "Pick one of visible_organizations."),
        **result,
    }


@router.post("/sync/zoho")
def sync_zoho(
    since: Optional[date] = Query(
        None, description="Start date for the pull (ISO). Defaults to ZOHO_SYNC_FROM, "
                          "then to the rolling ZOHO_HISTORY_DAYS window."),
    full: bool = Query(
        False, description="Discard the resume cursor and re-read every document."),
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Trigger a Zoho read sync into the org's read model (owner/manager only)."""
    ensure_org_and_users(session)
    source = get_source(session, principal.organization_id, since=since)
    service = SyncService(session, source, principal.organization_id, resume=not full)
    report = service.run()
    return report.to_dict()


@router.post("/detectors/run")
def detectors_run(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Run the deterministic Signal Engine over the org's read model (owner/manager
    only). Emits immutable signals; no AI, no recommendations."""
    _require_intelligence(session, principal)
    return run_detectors(session, principal.organization_id)


@router.post("/decisions/generate")
def decisions_generate(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Turn the org's latest signals into validated, persisted decisions via the
    AI Decision Layer (owner/manager only). Deterministic signals are the floor;
    AI failures degrade to templates, never suppress a real signal."""
    _require_intelligence(session, principal)
    from ..decisions.service import DecisionService
    return DecisionService(session, principal.organization_id).generate()


@router.post("/demo-seed")
def demo_seed(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Seed a realistic multi-account dataset and run the full pipeline so the UI
    has genuine, role-gated decisions to render (owner/manager only).

    Disabled in production: this writes fabricated customers/decisions into the
    org's read model and must never touch real data.
    """
    if settings.is_production:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Demo seeding is disabled in production.")
    from ..demo import seed_demo
    return seed_demo(session)


# ─────────────────────────────────────────────────────────────────────────────
# Observability Dashboard (owner/manager only)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/observability/dashboard")
def observability_dashboard(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Complete observability dashboard data: health, metrics, capacity (owner/manager only)."""
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_full_dashboard()


@router.get("/observability/health")
def observability_health(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """System health status for all components (owner/manager only)."""
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_system_health()


@router.get("/observability/capacity")
def observability_capacity(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Capacity analysis and safe headroom calculation (owner/manager only)."""
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_capacity()


@router.get("/observability/metrics")
def observability_metrics(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Raw metrics export — **one API worker's counters** (owner/manager only).

    Not the deployment's. The registry behind this is a per-process singleton
    fed by per-process middleware, and the deployment runs several workers, so
    a request is answered by whichever one the load balancer picked: roughly
    ``1/N`` of the traffic, a different ``1/N`` on the next scrape. A p99 read
    from a single response is computed from that worker's samples alone.

    The response says so. ``scope`` is ``"worker"``, ``worker`` is this
    process's identity (the same ``host:pid:rand`` string the scheduler lease
    uses), ``workers_configured`` is how many the supervisor was told to start
    or ``null`` if nothing declared it, and ``composition`` states how to
    combine several workers' payloads — counters and gauges sum, percentiles do
    not. Scrape until the worker ids repeat to cover the whole deployment.

    There is deliberately no shared backing store: metrics are written on every
    request, and putting that write on the request path is the SQLite locking
    incident CLAUDE.md §4 documents.
    """
    from ..observability.metrics import metrics
    return metrics.export()


def _require_scrape_token(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Authorize a Prometheus scrape, and nothing else.

    **A static token, checked here and on no other route.** A scraper is not a
    person: it has no organization, reads no tenant rows, and would be given a
    credential that lives in a config file on a monitoring host and is presented
    every 15 seconds forever. Minting a user or a service principal for it would
    make that credential replayable against every other endpoint; a value read
    only by this dependency cannot be. Prometheus speaks `bearer_token` natively,
    so nothing new is invented on the scraper's side either.

    **When the token is unset the answer is 401, the same 401 a wrong token
    gets.** Three options were on the table and two are wrong:

    - Serve unauthenticated when nothing is configured. This is how an
      observability endpoint ends up public on the deployment that never
      configured monitoring — which is most of them — and it is the benign
      default CLAUDE.md §1 forbids.
    - Fail with a 500. That turns a deliberate configuration state into an error
      an operator has to chase, and it announces, to anybody who asks, that this
      deployment has no scrape token.
    - Refuse identically to a bad token. An unauthenticated caller sees one
      response and learns nothing about whether a token exists — which matters,
      because "this deployment has no scrape secret" is itself worth knowing to
      someone probing it.

    The compare is `hmac.compare_digest`, in the same spirit as
    `trust/signing.verify` and `authz` — a token is guessed one character at a
    time when the comparison stops at the first mismatch. The empty-token guard
    runs first and deliberately does *not* reach the compare: `compare_digest("",
    "")` is `True`, so an unset token would otherwise be satisfied by an empty
    bearer.
    """
    configured = settings.METRICS_SCRAPE_TOKEN
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("bearer "):].strip()

    # Compared as BYTES, and that is not tidiness. ``hmac.compare_digest`` on
    # ``str`` raises TypeError the moment either side holds a non-ASCII
    # character, and Starlette decodes incoming header bytes as latin-1 — so a
    # caller sending ``Authorization: Bearer \xe9`` reached the compare with a
    # non-ASCII string. On a deployment WITH a token that raised, and the
    # unhandled error became a 500; on one WITHOUT, the guard above
    # short-circuited to 401. One unauthenticated request therefore answered
    # "does this deployment have a scrape secret?" — the exact question the
    # paragraph above says a caller cannot ask. Encoding first cannot raise on
    # content, so both states answer 401 whatever bytes arrive.
    if not configured or not hmac.compare_digest(
            configured.encode("utf-8", "surrogateescape"),
            presented.encode("utf-8", "surrogateescape")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Invalid or missing scrape credentials.",
                            headers={"WWW-Authenticate": "Bearer"})


@router.get("/observability/prometheus", response_class=PlainTextResponse)
def observability_prometheus(_: None = Depends(_require_scrape_token)) -> Response:
    """This worker's metrics in Prometheus text exposition format.

    Not role-scoped like its neighbours — a static scrape token instead, checked
    by `_require_scrape_token` and accepted nowhere else. What it exposes is
    process-level counters: request and query totals, latency quantiles,
    connection gauges. No cost, no price, no margin, no customer, no tenant, and
    no metric name or label that answers a margin question — which
    `tests/decision_platform/test_prometheus_exposition.py` walks the body to
    assert, because a surface behind a token rather than a role deserves the
    tightest reading of CLAUDE.md §1 rather than the most convenient one.

    **It touches no database.** No session is injected and none is opened: the
    registry is in-memory, and this is polled every 15 seconds for the life of
    the deployment. Putting a read — let alone a write — on that path is how the
    SQLite locking incident in CLAUDE.md §4 starts.

    Every series carries a `worker` label, because the registry is a per-process
    singleton and a scrape reaches one of `UVICORN_WORKERS` processes. Without
    it two workers' counters look like one series sawing up and down.
    `docs/observability.md` says which types sum across workers and which do not.

    No rate is computed here. `capacity.calculate_api_utilization` refuses to
    derive one from a lifetime counter, correctly, and says the honest place for
    it is an exporter — which is this: Prometheus differences the raw counter
    across scrapes, and the second sample it needs is the next scrape.
    """
    from ..observability.exposition import CONTENT_TYPE, render
    from ..observability.metrics import metrics

    return Response(content=render(metrics.export()), media_type=CONTENT_TYPE)


@router.get("/observability/api-performance")
def observability_api_performance(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
    window_minutes: int = Query(5, ge=1, le=1440),
) -> dict:
    """API performance metrics over a time window (owner/manager only)."""
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_api_performance(window_minutes)


@router.get("/observability/database")
def observability_database(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Database performance and status metrics (owner/manager only)."""
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_database_status()


@router.get("/observability/jobs")
def observability_jobs(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Background job status for this organization (owner/manager only).

    Read from ``sync_runs``, the persisted job model — authoritative, visible
    to every worker, and it survives a restart. The ERP sync is the only
    background job kind this deployment records, which the payload states in
    ``job_kinds`` so an empty block reads as "no sync running" rather than as
    some other job type silently unmeasured.
    """
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_background_jobs()


@router.get("/observability/syncs")
def observability_syncs(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """ERP sync status for this organization (owner/manager only).

    Read from ``sync_runs``. A run whose heartbeat has gone cold is reported
    under ``stalled`` rather than counted as active, and a throughput that
    cannot be computed comes back ``null`` with ``throughput_basis`` naming
    what is missing — never zero, which is what a failing sync looks like.
    """
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_zoho_sync_status()


@router.get("/observability/tenants")
def observability_tenants(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Tenant usage rankings (owner/manager only)."""
    from ..observability.dashboard import DashboardService
    service = DashboardService(session, principal.organization_id)
    return service.get_tenant_usage(limit)
