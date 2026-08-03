"""Quote-building endpoints: intake, resolution grid, supply selection,
pricing, item creation, and estimate creation.

Every response is serialized through ``Quote.to_dict(mgmt=…)``, so a sales
principal never receives per-line economics.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .. import approvals
from ..authz import Principal as PlatformPrincipal, load_principal
from ..config import settings
from ..db import get_session
from ..deps import current_principal, get_zoho
from ..schemas import (
    CreateQuoteRequest,
    DiscountRequest,
    EstimateResponse,
    IntakeRequest,
    SelectSupplyRequest,
    SetPriceRequest,
)
from ..security import Principal
from ..store import Line, Quote, store
from ..zoho import ZohoService

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


def optional_platform_principal(
    x_platform_authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Optional[PlatformPrincipal]:
    """The platform identity the browser also holds, if it sent one.

    A separate header rather than ``Authorization`` because this router already
    authenticates a Quote Builder principal there, and the two identity systems
    are genuinely different: one is the quoting tool's demo login, the other is
    the org-scoped platform user the approval queue belongs to. Optional here,
    mandatory at the point of use when policy requires approvals — the endpoint
    decides that, not the dependency.
    """
    if not x_platform_authorization:
        return None
    token = x_platform_authorization.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    return load_principal(session, token)


def _get_quote(quote_id: str) -> Quote:
    q = store.get(quote_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    return q


def _get_line(quote: Quote, line_id: str) -> Line:
    ln = next((row for row in quote.lines if row.id == line_id), None)
    if ln is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return ln


@router.post("")
def create_quote(body: CreateQuoteRequest, principal: Principal = Depends(current_principal)):
    q = store.create(body.customer)
    return q.to_dict(principal.is_mgmt)


@router.get("/{quote_id}")
def get_quote(quote_id: str, principal: Principal = Depends(current_principal)):
    return _get_quote(quote_id).to_dict(principal.is_mgmt)


@router.post("/{quote_id}/intake")
def intake(quote_id: str, body: IntakeRequest,
           principal: Principal = Depends(current_principal),
           zoho: ZohoService = Depends(get_zoho)):
    q = _get_quote(quote_id)
    if not body.text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No RFQ text provided")
    store.add_rfq(q, body.text, zoho)
    return q.to_dict(principal.is_mgmt)


@router.get("/{quote_id}/lines/{line_id}/options")
def line_options(quote_id: str, line_id: str,
                 principal: Principal = Depends(current_principal)):
    """Ranked supply candidates for a line (the design's supply drawer)."""
    q = _get_quote(quote_id)
    ln = _get_line(q, line_id)
    return {
        "lineId": ln.id,
        "reqCode": ln.reqCode,
        "reqDesc": ln.reqDesc,
        "supplyCode": ln.supplyCode,
        "candidates": [c.to_dict() for c in ln.candidates],
        "notes": ln.notes,
    }


@router.post("/{quote_id}/lines/{line_id}/supply")
def select_supply(quote_id: str, line_id: str, body: SelectSupplyRequest,
                  principal: Principal = Depends(current_principal),
                  zoho: ZohoService = Depends(get_zoho)):
    q = _get_quote(quote_id)
    ln = _get_line(q, line_id)
    store.select_supply(ln, body.code, zoho, manual=body.manual)
    return q.to_dict(principal.is_mgmt)


@router.post("/{quote_id}/lines/{line_id}/price")
def set_price(quote_id: str, line_id: str, body: SetPriceRequest,
              principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id)
    ln = _get_line(q, line_id)
    store.set_price(ln, body.price)
    return q.to_dict(principal.is_mgmt)


@router.delete("/{quote_id}/lines/{line_id}")
def delete_line(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id)
    store.delete_line(q, line_id)
    return q.to_dict(principal.is_mgmt)


@router.post("/{quote_id}/discount")
def apply_discount(quote_id: str, body: DiscountRequest,
                   principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id)
    selected = [ln for ln in q.lines if ln.id in set(body.lineIds)]
    n = store.apply_discount(selected, body.percent)
    result = q.to_dict(principal.is_mgmt)
    result["applied"] = n
    return result


@router.post("/{quote_id}/lines/{line_id}/create-item")
def create_item(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal),
                zoho: ZohoService = Depends(get_zoho)):
    q = _get_quote(quote_id)
    ln = _get_line(q, line_id)
    if not ln.supplyCode:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Line has no supply product to create")
    store.create_item(ln, zoho)
    return q.to_dict(principal.is_mgmt)


@router.post("/{quote_id}/estimate", response_model=EstimateResponse)
def create_estimate(quote_id: str,
                    principal: Principal = Depends(current_principal),
                    zoho: ZohoService = Depends(get_zoho),
                    platform: Optional[PlatformPrincipal] = Depends(optional_platform_principal),
                    session: Session = Depends(get_session)):
    q = _get_quote(quote_id)
    blockers = store.blockers(q)
    if blockers:
        return EstimateResponse(
            ok=False,
            blockers=[ln.id for ln in blockers],
            message=f"{len(blockers)} critical line(s) must be resolved first.",
        )

    # ── the commercial gate ──────────────────────────────────────────────────
    # A line priced below the floor was previously computed, flagged, recorded
    # — and then sent anyway, because nothing refused it. This is where it is
    # refused. The check runs server-side against the recorded snapshots; a
    # client that simply does not call the approvals API cannot route around it.
    #
    # The gate needs an organization, which the Quote Builder's own demo
    # identity does not carry, so it reads the platform token the browser
    # already holds. When an org has enforcement on, that token is mandatory:
    # otherwise "send without signing in to the platform" would be the bypass.
    org = platform.organization_id if platform else settings.DEFAULT_ORG_ID
    policy = approvals.get_policy(session, org)
    if policy.require_approval_for_quotes:
        if platform is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Sign in to the Decisions platform to send a quote — approvals "
                "are required in this organization.")
        blocked = approvals.quote_submission_block(session, org, quote_id)
        if blocked:
            raise HTTPException(status.HTTP_403_FORBIDDEN, blocked)
    lines = [{"code": ln.supplyCode, "qty": ln.reqQty, "rate": ln.quoted}
             for ln in q.lines if ln.supplyCode]
    est = zoho.create_estimate(q.customer, lines)
    return EstimateResponse(ok=True, estimateNumber=est.number,
                            lineCount=est.line_count,
                            message=f"Zoho estimate {est.number} created — {est.line_count} lines.")
