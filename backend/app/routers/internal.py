"""Internal / operational endpoints (not user-facing).

Maps to spec §12 internal surface: health + Zoho sync trigger. Detector-run and
audit endpoints are deferred to later phases.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner, require_owner
from ..config import settings
from ..db import get_session
from ..ingestion.sync import SyncService, get_source
from ..seed import ensure_org_and_users
from ..signals.engine import run_detectors

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


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
    return run_detectors(session, principal.organization_id)


@router.post("/decisions/generate")
def decisions_generate(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Turn the org's latest signals into validated, persisted decisions via the
    AI Decision Layer (owner/manager only). Deterministic signals are the floor;
    AI failures degrade to templates, never suppress a real signal."""
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
