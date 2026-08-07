"""Quote-building endpoints: intake, resolution grid, supply selection,
pricing, item creation, and estimate creation.

Every response is serialized through ``Quote.to_dict(mgmt=…)``, so a sales
principal never receives per-line economics.

**One identity.** These endpoints authenticate the platform user in
``Authorization``, the same token every ``/api/v1`` endpoint takes. They used to
take a Quote Builder login of their own — two fixed demo accounts, any password
— and read the *real* identity out of a second ``X-Platform-Authorization``
header when the browser happened to send one. That is why opening the Quote
Builder asked you to sign in again and then showed somebody else's name: the two
logins were unrelated, and the one on screen was the demo one. It also meant the
org-scoped half of resolution (confirmed mappings, equivalence bands, the
approval gate) silently degraded to packaged defaults whenever the second header
was missing, which looks exactly like the feature not working.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import approvals
from ..authz import Principal, current_principal
from ..commercial import policy as policy_service, quote_service
from ..identity import service as identity_service
from ..identity.mapping_store import OrgMappingStore
from ..db import get_session
from ..deps import get_zoho
from ..schemas import (
    CreateQuoteRequest,
    DiscountRequest,
    EstimateResponse,
    IntakeRequest,
    SelectSupplyRequest,
    SetPriceRequest,
)
from ..pie_service import Bands
from ..store import Line, Quote, store
from ..zoho import ZohoService

log = logging.getLogger("pie_portal.quote")
router = APIRouter(prefix="/api/quotes", tags=["quotes"])


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
    return q.to_dict(principal.is_manager_or_owner)


@router.get("/{quote_id}")
def get_quote(quote_id: str, principal: Principal = Depends(current_principal)):
    return _get_quote(quote_id).to_dict(principal.is_manager_or_owner)


@router.post("/{quote_id}/intake")
def intake(quote_id: str, body: IntakeRequest,
           principal: Principal = Depends(current_principal),
           zoho: ZohoService = Depends(get_zoho),
           session: Session = Depends(get_session)):
    q = _get_quote(quote_id)
    if not body.text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No RFQ text provided")
    store.add_rfq(q, body.text, zoho,
                  _customer_scope(session, principal, q.customer),
                  _bands(session, principal),
                  _mapping_store(session, principal))
    return q.to_dict(principal.is_manager_or_owner)


def _mapping_store(session: Session, principal: Principal) -> Optional[Any]:
    """This organization's confirmed code mappings, for the engine to read.

    None only when they could not be loaded: pie-parser then falls back to its
    packaged store, which is empty, so resolution degrades to the codes alone
    rather than failing the intake.
    """
    try:
        return OrgMappingStore(session, principal.organization_id)
    except Exception:  # noqa: BLE001 — resolution proceeds without them
        log.exception("could not load confirmed mappings for %s",
                      principal.organization_id)
        return None


def _bands(session: Session, principal: Principal) -> Optional[Bands]:
    """This organization's equivalence bands, or None for the packaged defaults.

    What counts as a technical equivalent is commercial policy, so it belongs to
    the org and moves with its threshold version — the same reason the pricing
    floors stopped being module constants.
    """
    try:
        t = policy_service.load_for_org(session, principal.organization_id)
        return Bands(tech=t.equivalence_tech_band, compat=t.equivalence_compat_band)
    except Exception:  # noqa: BLE001 — policy is never a reason to fail intake
        log.exception("could not load equivalence bands for %s",
                      principal.organization_id)
        return None


def _customer_scope(session: Session, principal: Principal,
                    reference: str) -> Optional[str]:
    """The identity to resolve this quote's lines under, or None.

    Two ways to get None, and both mean "resolve on the codes alone": no
    customer matched, or a customer who has not been linked across connectors
    yet. That last one is the normal early state — linking is manual by design —
    so the fallback has to be the unscoped behaviour rather than a stand-in key.
    Substituting the connector's own id would mean every mapping confirmed today
    is filed under a name we intend to replace the moment somebody links the
    record.
    """
    try:
        customer = quote_service.resolve_customer(
            session, principal.organization_id, reference)
        return identity_service.identity_for_customer(
            session, principal.organization_id, customer)
    except Exception:  # noqa: BLE001 — scope is an optimisation, never a blocker
        log.exception("could not resolve an identity scope for %r", reference)
        return None


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
                  zoho: ZohoService = Depends(get_zoho),
                  session: Session = Depends(get_session)):
    q = _get_quote(quote_id)
    ln = _get_line(q, line_id)
    confirmed = _confirm_identity(session, principal, q, ln, body.code)
    store.select_supply(ln, body.code, zoho, manual=body.manual)
    out = q.to_dict(principal.is_manager_or_owner)
    if confirmed:
        # Worth saying out loud: the person has just taught the system something
        # permanent, and a change with no feedback reads as a change that did
        # not happen.
        out["note"] = (f"Recorded: this customer's {ln.reqCode} means {body.code}. "
                       "It will resolve on its own from now on.")
    return out


def _confirm_identity(session: Session, principal: Principal,
                      quote: Quote, ln: Line, code: str) -> bool:
    """Record a confirmation when the user answers the engine's own question.

    Only when they select the record the engine *proposed* as this line's
    identity. Picking a different product is a substitution on one quote, and
    filing that as "their code means this" would teach the system something the
    person did not say — and would then resolve it that way silently forever.
    """
    if not ln.customerScope or ln.identityCandidate != code:
        return False
    try:
        row = identity_service.confirm_code_mapping(
            session, principal.organization_id,
            identity_id=ln.customerScope, code=ln.reqCode,
            target_record_id=code,
            source_ref=f"quote {quote.id} line {ln.id}",
            user_id=principal.user_id)
        if row is not None:
            session.commit()
        return row is not None
    except Exception:  # noqa: BLE001 — a quote must not fail over bookkeeping
        log.exception("could not record a confirmed mapping for line %s", ln.id)
        session.rollback()
        return False


@router.post("/{quote_id}/lines/{line_id}/price")
def set_price(quote_id: str, line_id: str, body: SetPriceRequest,
              principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id)
    ln = _get_line(q, line_id)
    store.set_price(ln, body.price)
    return q.to_dict(principal.is_manager_or_owner)


@router.delete("/{quote_id}/lines/{line_id}")
def delete_line(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id)
    store.delete_line(q, line_id)
    return q.to_dict(principal.is_manager_or_owner)


@router.post("/{quote_id}/discount")
def apply_discount(quote_id: str, body: DiscountRequest,
                   principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id)
    selected = [ln for ln in q.lines if ln.id in set(body.lineIds)]
    n = store.apply_discount(selected, body.percent)
    result = q.to_dict(principal.is_manager_or_owner)
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
    return q.to_dict(principal.is_manager_or_owner)


@router.post("/{quote_id}/estimate", response_model=EstimateResponse)
def create_estimate(quote_id: str,
                    principal: Principal = Depends(current_principal),
                    zoho: ZohoService = Depends(get_zoho),
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
    # The gate needs an organization. It reads it from the signed-in principal
    # now rather than from a second optional header — an identity the caller
    # could omit was an approval gate the caller could skip.
    org = principal.organization_id
    approval_policy = approvals.get_policy(session, org)
    if approval_policy.require_approval_for_quotes:
        blocked = approvals.quote_submission_block(session, org, quote_id)
        if blocked:
            raise HTTPException(status.HTTP_403_FORBIDDEN, blocked)
    lines = [{"code": ln.supplyCode, "qty": ln.reqQty, "rate": ln.quoted}
             for ln in q.lines if ln.supplyCode]
    est = zoho.create_estimate(q.customer, lines)
    return EstimateResponse(ok=True, estimateNumber=est.number,
                            lineCount=est.line_count,
                            message=f"Zoho estimate {est.number} created — {est.line_count} lines.")
