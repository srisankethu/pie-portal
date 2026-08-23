"""The trust surface — what a customer can see and demand about their own data.

Every endpoint here exists to make a promise checkable by the person it was
made to. That is the whole design rule: a control the customer cannot observe is
an internal process, and internal processes are what they are being asked to
take on faith.

  ``GET  /trust/disclosure``   what reaches a model, and what never does
  ``GET  /trust/payloads``     the actual text sent, decrypted for its owner
  ``GET  /trust/access``       every time our staff opened this tenant, and why
  ``GET  /trust/export``       everything, as JSON, to take elsewhere
  ``GET  /trust/erasure``      whether this tenant was erased, with the receipt
  ``POST /trust/erasure``      destroy the data key and issue that receipt

Read endpoints are owner-scoped rather than manager-scoped. Who has looked at
our data, and what left for a model, are ownership questions about the
relationship with the vendor, not operational ones about running the desk.

The erasure endpoint is the only destructive route in this application. It
demands the organization's own id in the body as confirmation — not as
security, which the role check provides, but because an irreversible action
triggered by a single click is a design that eventually fires by accident.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import clock
from ..authz import Principal, require_owner
from ..db import get_session
from ..trust import access, disclosure, erasure, keys

log = logging.getLogger("pie_portal.trust")

router = APIRouter(prefix="/api/v1/trust", tags=["trust"])


# ── what reaches a model ────────────────────────────────────────────────────
@router.get("/disclosure")
def get_disclosure(principal: Principal = Depends(require_owner)) -> dict:
    """The published statement, served from the constants the checker enforces.

    Not a static document: this is the same ``ALLOWED``/``NEVER`` the payload
    checker measures against and the test suite asserts on, so the statement
    and the behaviour cannot drift apart without a test failing.
    """
    return disclosure.statement()


@router.get("/payloads")
def get_payloads(limit: int = Query(50, ge=1, le=200),
                 reveal: bool = Query(False),
                 principal: Principal = Depends(require_owner),
                 session: Session = Depends(get_session)) -> dict:
    """Everything sent to a model about this organization.

    ``reveal=false`` by default: the list view is metadata, and decrypting a
    hundred payloads to render a table nobody reads is work done for nothing.
    """
    rows = disclosure.payloads_for(session, principal.organization_id, limit)
    # One key for the page. Revealing per row read and unwrapped this
    # organization's data key once per payload, which is the work the
    # ``reveal=false`` default exists to avoid — and it was still being done
    # a hundred times over whenever somebody did ask to see them.
    plaintext = disclosure.reveal_many(session, rows) if reveal else {}
    return {
        "summary": disclosure.findings_summary(rows),
        "payloads": [
            {
                "payload_id": r.payload_id,
                "decision_type": r.decision_type,
                "provider": r.provider,
                "model": r.model,
                "created_at": clock.iso(r.created_at),
                "findings": r.disclosure_findings or [],
                "payload": plaintext.get(r.payload_id) if reveal else None,
            }
            for r in rows
        ],
    }


# ── who looked ──────────────────────────────────────────────────────────────
@router.get("/access")
def get_access(limit: int = Query(200, ge=1, le=500),
               principal: Principal = Depends(require_owner),
               session: Session = Depends(get_session)) -> dict:
    """Every break-glass grant, use and revocation against this organization.

    No filter parameter and no suppression: a log the vendor can curate is a
    log that answers the vendor's question rather than the customer's.
    """
    rows = access.events_for(session, principal.organization_id, limit)
    return {
        "events": [
            {
                "event_id": e.event_id,
                "staff_user_id": e.staff_user_id,
                "action": e.action,
                "detail": e.detail,
                "at": clock.iso(e.created_at),
            }
            for e in rows
        ],
        "note": ("Staff access to your data requires a stated reason and expires "
                 "automatically. Every entry above is recorded at the moment it "
                 "happens and cannot be edited or removed."),
    }


# ── take it with you ────────────────────────────────────────────────────────
@router.get("/export")
def get_export(principal: Principal = Depends(require_owner),
               session: Session = Depends(get_session)) -> dict:
    """Everything this organization owns, as JSON.

    Includes the identity graph deliberately: the joined cross-connector view
    is the one thing here that no single source system holds, so an export
    without it would quietly be the export that keeps you.
    """
    return erasure.export(session, principal.organization_id)


# ── provable deletion ───────────────────────────────────────────────────────
class EraseRequest(BaseModel):
    confirm_organization_id: str = Field(
        ..., description="Must equal your own organization id. A deliberate "
                         "friction, not a security control.")
    reason: str = Field(..., min_length=10)


@router.get("/erasure")
def get_erasure(principal: Principal = Depends(require_owner),
                session: Session = Depends(get_session)) -> dict:
    state = erasure.status(session, principal.organization_id)
    state["key_destroyed"] = keys.is_destroyed(session, principal.organization_id)
    return state


@router.post("/erasure", status_code=status.HTTP_200_OK)
def post_erasure(body: EraseRequest,
                 principal: Principal = Depends(require_owner),
                 session: Session = Depends(get_session)) -> dict:
    """Destroy this organization's data key and return a signed receipt.

    Irreversible. Everything encrypted under that key — names, model payloads —
    becomes unreadable everywhere it exists, including in backups that cannot be
    selectively edited. That property is the reason this is the deletion
    mechanism rather than a cascade of DELETE statements.
    """
    if body.confirm_organization_id != principal.organization_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The confirmation did not match your organization id. Nothing was "
            "changed.")
    if keys.is_destroyed(session, principal.organization_id):
        return erasure.status(session, principal.organization_id)

    row = erasure.erase(session, principal.organization_id,
                        reason=body.reason, actor_user_id=principal.user_id)
    log.warning("erasure completed for %s by %s", principal.organization_id,
                principal.user_id)
    return {
        "erased": True,
        "receipt": {**erasure.receipt_body(row), "signature": row.signature,
                    "verified": erasure.verify_receipt(row)},
    }
