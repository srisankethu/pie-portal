"""Quote-building endpoints: intake, resolution grid, supply selection,
pricing, item creation, and estimate creation.

Every response is serialized through ``Quote.to_dict(mgmt=…)``, so a sales
principal never receives per-line economics.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

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


def _get_quote(quote_id: str) -> Quote:
    q = store.get(quote_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    return q


def _get_line(quote: Quote, line_id: str) -> Line:
    ln = next((l for l in quote.lines if l.id == line_id), None)
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
                    zoho: ZohoService = Depends(get_zoho)):
    q = _get_quote(quote_id)
    blockers = store.blockers(q)
    if blockers:
        return EstimateResponse(
            ok=False,
            blockers=[ln.id for ln in blockers],
            message=f"{len(blockers)} critical line(s) must be resolved first.",
        )
    lines = [{"code": ln.supplyCode, "qty": ln.reqQty, "rate": ln.quoted}
             for ln in q.lines if ln.supplyCode]
    est = zoho.create_estimate(q.customer, lines)
    return EstimateResponse(ok=True, estimateNumber=est.number,
                            lineCount=est.line_count,
                            message=f"Zoho estimate {est.number} created — {est.line_count} lines.")
