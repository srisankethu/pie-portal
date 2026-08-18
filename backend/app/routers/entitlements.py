"""What this organization's plan lets it use, and asking for a different one.

One GET, any signed-in role: a screen deciding whether to show the decisions
nav, a locked-features teaser, or a trial countdown needs the server's answer,
not a client-side guess.

One POST, owner only, and it **grants nothing**. Plans are still set by the
operator (``python -m app.entitlements apply <request>``), because an endpoint
that let an owner upgrade themselves would make the plan a suggestion. What this
adds is the other half of that sentence, which was missing: an owner had no way
to *say* they wanted a different plan, so the platform sold three tiers and
offered no way to buy the upper two. The trial notice said as much out loud —
"a button that opened a checkout nobody built would be worse than no button".

This is that button, and it is not a checkout. It records the ask; a person
decides it. When billing exists it drains the same queue through the same
``decide_request``, so this surface does not change again.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import entitlements
from ..authz import Principal, current_principal, require_owner
from ..db import get_session
from ..domain.enums import PlanTier

router = APIRouter(prefix="/api/v1/entitlements", tags=["entitlements"])


@router.get("")
def my_entitlements(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    return entitlements.describe(session, principal.organization_id)


class PlanRequest(BaseModel):
    """Which plan, and anything the owner wants whoever reads it to know."""

    plan: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=2000)


@router.post("", status_code=status.HTTP_201_CREATED)
def request_plan(
    body: PlanRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Ask to move plan. Owner only, and nothing is granted here.

    Owner rather than manager because it is a commitment to spend, and a
    manager who could commit the business to a subscription is a different
    product decision from a manager who can read economics.

    Returns the whole entitlement view rather than just the new row, so the
    screen that called it re-renders from one answer instead of stitching the
    response together with what it already had — which is how a button ends up
    still saying "Upgrade" after somebody pressed it.
    """
    plan = entitlements.parse_plan(body.plan, source="request body")
    if plan is None or plan.value != body.plan.strip().lower():
        # `parse_plan` degrades an unrecognised value to free and logs, which is
        # right where a stored plan is being resolved and wrong here: silently
        # recording a request for the *free* plan because somebody typo'd
        # "platfrom" is a request nobody made.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown plan {body.plan!r}. Expected one of: "
            + ", ".join(p.value for p in PlanTier))

    try:
        entitlements.request_plan_change(
            session, principal.organization_id,
            requested_plan=plan, requested_by=principal.user_id, note=body.note)
    except entitlements.PlanRequestRefused as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    session.commit()
    return entitlements.describe(session, principal.organization_id)
