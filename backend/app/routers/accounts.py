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

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import approvals, clock
from ..authz import Principal, current_principal
from ..db import get_session
from ..domain import models
from ..domain.origin import Companies

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


#: What "active" means for an account. Zoho's own word, upper-cased on the way
#: in by ``normalize_customer``.
ACTIVE = "ACTIVE"


@router.get("")
def list_accounts(
    q: Optional[str] = None,
    status: str = Query("active", pattern="^(active|inactive|all)$"),
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[dict]:
    """The account directory, with enough trade on each row to choose from it.

    **``status`` defaults to active.** The pull now reads inactive contacts as
    well — it has to, or their history cannot be resolved — and a directory that
    silently mixed a dormant 2019 account into the list somebody scans before a
    call would be worse than the old behaviour, not better. Inactive accounts
    are one click away and never more than that.

    Every figure here is OPERATIONAL: when they last ordered, how often, and how
    much they spent. No cost and no margin — those reach a manager through the
    Customer × Item surface, which is where the permission gating lives.
    """
    stmt = select(models.Customer).where(
        models.Customer.organization_id == principal.organization_id)
    if principal.is_salesperson:
        # Same scope rule as decisions: a salesperson's own assigned accounts.
        stmt = stmt.where(models.Customer.assigned_user_id == principal.user_id)
    rows = session.scalars(stmt.order_by(models.Customer.name)).all()

    if status != "all":
        want_active = status == "active"
        rows = [c for c in rows
                if ((c.status or ACTIVE).upper() == ACTIVE) == want_active]

    needle = (q or "").strip().lower()
    if needle:
        rows = [c for c in rows if needle in c.name.lower()]

    trade = _trade_summary(session, principal.organization_id,
                           [c.customer_id for c in rows])
    # Which company each account belongs to. The directory is the screen where
    # this matters most and the one place it was missing: "ABC Industries" can
    # legitimately exist in all three connected books, and a flat list of names
    # cannot be chosen from — somebody picks one and finds out later it was the
    # wrong company's account. Same projection as the item picker, so the two
    # cannot end up describing their source two different ways.
    companies = Companies(session, principal.organization_id)
    # Who covers this account. `assigned_user_id` has been on the wire all along
    # and rendered nowhere, so no screen could answer "whose account is this" —
    # on a landing page called "Team focus". Resolved here rather than in the
    # browser: the client would need the user directory, which a salesperson
    # cannot read, and it is one indexed query for the whole list.
    assignees = approvals.user_names(
        session, principal.organization_id,
        [c.assigned_user_id for c in rows if c.assigned_user_id])
    return [
        {
            "customer_id": c.customer_id,
            "name": c.name,
            "status": c.status,
            "assigned_user_id": c.assigned_user_id,
            "assigned_to": assignees.get(c.assigned_user_id),
            "origin": companies.of(c).to_dict(),
            # One connected company means every badge says the same thing, and a
            # column of identical badges is width spent on decoration.
            "sources_differ": companies.count > 1,
            **trade.get(c.customer_id, _NO_TRADE),
        }
        for c in rows
    ]


#: An account with no invoices at all — a contact created and never sold to.
#: Zeroes rather than nulls for the counts, because "0 orders" is a fact and a
#: blank cell is a question.
_NO_TRADE: dict = {"last_order": None, "orders_12m": 0, "revenue_12m": 0.0}


def _trade_summary(session: Session, org: str,
                   customer_ids: list[str]) -> dict[str, dict]:
    """Last order, and the rolling twelve months, for a page of accounts.

    One grouped query for the whole list rather than one per row: the directory
    is the screen most likely to hold four hundred accounts, and an N+1 here is
    four hundred round trips before anybody has clicked anything.
    """
    if not customer_ids:
        return {}
    since = clock.today() - timedelta(days=365)
    rows = session.execute(
        select(models.SalesTxn.customer_id,
               func.max(models.SalesTxn.date),
               func.count(func.distinct(models.SalesTxn.external_ref)),
               func.sum(models.SalesTxn.line_revenue))
        .where(models.SalesTxn.organization_id == org,
               models.SalesTxn.customer_id.in_(customer_ids),
               models.SalesTxn.date >= since)
        .group_by(models.SalesTxn.customer_id)).all()
    recent = {
        cid: {"last_order": last.isoformat() if last else None,
              "orders_12m": int(orders or 0),
              "revenue_12m": float(revenue or 0)}
        for cid, last, orders, revenue in rows
    }
    # An account whose last order predates the window still has a last order,
    # and "never ordered" and "not for a year" are different things to say.
    missing = [cid for cid in customer_ids if cid not in recent]
    if missing:
        for cid, last in session.execute(
                select(models.SalesTxn.customer_id, func.max(models.SalesTxn.date))
                .where(models.SalesTxn.organization_id == org,
                       models.SalesTxn.customer_id.in_(missing))
                .group_by(models.SalesTxn.customer_id)).all():
            recent[cid] = {"last_order": last.isoformat() if last else None,
                           "orders_12m": 0, "revenue_12m": 0.0}
    return recent


@router.get("/{customer_id}/items")
def list_account_items(
    customer_id: str,
    q: Optional[str] = None,
    status: str = Query("active", pattern="^(active|inactive|all)$"),
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

    companies = Companies(session, principal.organization_id)
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
        # Discontinued items are excluded by default and offered on request.
        # They are in the master now — the pull reads inactive items so their
        # history resolves — but an item Zoho says is retired should not be the
        # one somebody picks by accident on a live quote.
        is_active = bool(getattr(p, "active", True)) if p is not None else False
        if status != "all" and is_active != (status == "active"):
            continue
        out.append({
            "product_id": r.product_id,
            "name": name,
            "sku": sku,
            "active": is_active,
            "last_bought": r.last_bought.isoformat() if r.last_bought else None,
            # Same shape as every other imported entity. One projection means a
            # customer picker and an item picker cannot end up describing their
            # source two different ways.
            "origin": (companies.of(p).to_dict() if p is not None else None),
            "sources_differ": companies.count > 1,
        })
    return out
