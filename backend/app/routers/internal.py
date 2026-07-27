"""Internal / operational endpoints (not user-facing).

Maps to spec §12 internal surface: health + Zoho sync trigger. Detector-run and
audit endpoints are deferred to later phases.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
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


@router.post("/sync/zoho")
def sync_zoho(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Trigger a Zoho read sync into the org's read model (owner/manager only)."""
    ensure_org_and_users(session)
    service = SyncService(session, get_source(), principal.organization_id)
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
