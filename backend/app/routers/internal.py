"""Internal / operational endpoints (not user-facing).

Maps to spec §12 internal surface: health + Zoho sync trigger. Detector-run and
audit endpoints are deferred to later phases.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner
from ..config import settings
from ..db import get_session
from ..ingestion.sync import SyncService, get_source
from ..seed import ensure_org_and_users

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
