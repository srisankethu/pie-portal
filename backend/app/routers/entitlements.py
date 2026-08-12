"""What this organization's plan lets it use — the read surface.

One GET, any signed-in role: a screen deciding whether to show the decisions
nav, a locked-features teaser, or a trial countdown needs the server's answer,
not a client-side guess. Deliberately no write surface — plans are set by the
operator (``python -m app.entitlements set-plan``), never by a tenant, and an
endpoint that let an owner upgrade themselves would make the plan a suggestion.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import entitlements
from ..authz import Principal, current_principal
from ..db import get_session

router = APIRouter(prefix="/api/v1/entitlements", tags=["entitlements"])


@router.get("")
def my_entitlements(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    return entitlements.describe(session, principal.organization_id)
