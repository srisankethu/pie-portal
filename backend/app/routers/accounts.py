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
from sqlalchemy import func, select
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


@router.get("/{customer_id}/items")
def list_account_items(
    customer_id: str,
    q: Optional[str] = None,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[dict]:
    """The items this account has actually bought, most recently bought first.

    Exists so a screen can offer a *name* where it used to demand an id. Asking
    somebody to paste ``3452161000001252021`` into a field is asking them to
    leave, find it, and come back with it — and the id is the one thing about an
    item that nobody can recognise or check.

    Scoped to the account rather than the whole catalogue on purpose: on a
    negotiation the relevant items are the ones this customer buys, and a
    six-thousand-item dropdown is a search box with extra steps. ``q`` filters
    by name or SKU for the case where it is a new item for them.

    Identity only — no price, no cost, no margin. The quantity and the last
    date are what make two similarly-named inserts distinguishable in a list.
    """
    customer = session.get(models.Customer, customer_id)
    if customer is None or customer.organization_id != principal.organization_id:
        return []
    if principal.is_salesperson and customer.assigned_user_id != principal.user_id:
        return []

    rows = session.execute(
        select(models.SalesTxn.product_id,
               func.max(models.SalesTxn.date).label("last_bought"))
        .where(models.SalesTxn.organization_id == principal.organization_id,
               models.SalesTxn.customer_id == customer_id)
        .group_by(models.SalesTxn.product_id)
        .order_by(func.max(models.SalesTxn.date).desc())).all()
    if not rows:
        return []

    products = {
        p.product_id: p
        for p in session.scalars(
            select(models.Product).where(
                models.Product.organization_id == principal.organization_id,
                models.Product.product_id.in_([r.product_id for r in rows])))
    }

    needle = (q or "").strip().lower()
    out: list[dict] = []
    for r in rows:
        p = products.get(r.product_id)
        if p is None:
            # Bought, but the item master has no such row — the same gap the
            # sync reports as UNKNOWN_PRODUCT. Listed by id rather than hidden,
            # because a line that exists in the history and not in the picker is
            # how somebody concludes the screen is broken.
            name, sku = f"Item {r.product_id}", ""
        else:
            name, sku = p.name, (p.source_ref or {}).get("sku") or ""
        if needle and needle not in name.lower() and needle not in str(sku).lower():
            continue
        out.append({
            "product_id": r.product_id,
            "name": name,
            "sku": sku,
            "last_bought": r.last_bought.isoformat() if r.last_bought else None,
        })
    return out
