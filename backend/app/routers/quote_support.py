"""Quote-context decision support (spec §9.5) — the Quote Builder ↔ Decision
Platform integration seam.

``POST /api/v1/quote-support`` takes a customer + product(s) a salesperson is
quoting and returns the deterministic FACTS plus a clearly-separated AI
RECOMMENDATION, and persists a QUOTE_CONTEXT decision so the recommendation and
the salesperson's later accept/modify/reject (captured via the existing
``/decisions/{id}/action``) feed the Decision Store.

Scope is enforced server-side: a salesperson never receives RESTRICTED cost/
margin facts (they are absent from the response, not masked).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal
from ..db import get_session
from ..decisions.quote_support import quote_support

router = APIRouter(prefix="/api/v1/quote-support", tags=["quote-support"])


class QuoteSupportRequest(BaseModel):
    customer: str
    products: list[str] = []
    proposed_price: Optional[float] = None


@router.post("")
def get_quote_support(
    body: QuoteSupportRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    if not body.customer.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A customer is required")
    return quote_support(
        session, principal,
        customer_ref=body.customer.strip(),
        product_refs=[p for p in body.products if p and p.strip()],
        proposed_price=body.proposed_price,
    )
