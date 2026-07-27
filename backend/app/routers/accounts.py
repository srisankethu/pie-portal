"""Account directory — the customer list behind the Accounts screen.

The Accounts screen previously listed only customers that happened to have an
open decision, which made the common question — "what does this account look
like before I call them?" — unanswerable for every other customer. This exposes
the org's customers, scoped the same way decisions are: a salesperson sees the
accounts assigned to them, a manager or owner sees the organization.

It returns identity and assignment only. No cost, no margin, no revenue — the
facts behind an account still arrive through the decision projection, which is
where the permission gating for RESTRICTED data lives.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal
from ..db import get_session
from ..domain import models

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("")
def list_accounts(
    q: Optional[str] = None,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[dict]:
    stmt = select(models.Customer).where(
        models.Customer.organization_id == principal.organization_id)
    if principal.is_salesperson:
        # Same scope rule as decisions: a salesperson's own assigned accounts.
        stmt = stmt.where(models.Customer.assigned_user_id == principal.user_id)
    rows = session.scalars(stmt.order_by(models.Customer.name)).all()

    needle = (q or "").strip().lower()
    if needle:
        rows = [c for c in rows if needle in c.name.lower()]

    return [
        {
            "customer_id": c.customer_id,
            "name": c.name,
            "status": c.status,
            "assigned_user_id": c.assigned_user_id,
        }
        for c in rows
    ]
