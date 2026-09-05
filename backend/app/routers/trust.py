"""The trust surface — what a customer can see and demand about their own data.

Every endpoint here exists to make a promise checkable by the person it was
made to. That is the whole design rule: a control the customer cannot observe is
an internal process, and internal processes are what they are being asked to
take on faith.

  ``GET  /trust/disclosure``   what reaches a model, and what never does
  ``GET  /trust/payloads``     the actual text sent, decrypted for its owner
  ``GET  /trust/access``       every time our staff opened this tenant, and why
  ``GET  /trust/audit``        the hash-chained record of who acted, and how
  ``GET  /trust/audit/verify`` whether that chain has been altered
  ``GET  /trust/audit/export`` the chain as JSON or CSV, re-verifiable elsewhere
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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..authz import Principal, require_owner
from ..db import get_session
from ..domain import models
from ..trust import access, audit, disclosure, erasure, keys

log = logging.getLogger("pie_portal.trust")

router = APIRouter(prefix="/api/v1/trust", tags=["trust"])


def _held(session: Session, model: Any, organization_id: str) -> int:
    """How many rows of one table this organization holds.

    The two list endpoints below truncate server-side, and until this existed
    neither said so: a screen holding fifty payloads could not tell a complete
    log from the head of a longer one, so every sentence on it had to be worded
    as a claim about the rows in hand rather than about the record. A total is
    what lets it say which of the two it is showing.

    Cheap on purpose — a count over the same indexed ``organization_id`` the
    list is drawn from, not a second read of the rows themselves, which for
    ``ModelPayload`` are ciphertext.

    It sits in the router rather than beside ``payloads_for`` and
    ``events_for``, which is the weaker of the two placements: a count whose
    filter is written out apart from the list's is a second copy of "which rows
    are in this set", and the copy that drifts. It holds today because both sets
    are defined by tenancy alone — ``events_for`` says in as many words that
    there is no supported way to ask for a subset — so there is nothing for the
    two to disagree about. The moment either list grows a filter, the count
    belongs beside it rather than here.
    """
    return int(session.scalar(
        select(func.count()).select_from(model)
        .where(model.organization_id == organization_id)) or 0)


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

    ``total`` is how many payloads this organization has logged, against the
    page of them ``payloads`` holds. ``summary`` is **not** the same number and
    is not being replaced by it: that counts the rows in this response, which is
    the set the checker's findings were read off. A reader must not divide one
    by the other — ``flagged`` is a defect report rather than a rate, and
    counting findings across the whole table would mean a predicate over a JSON
    column that is not the same query on SQLite and on PostgreSQL.
    """
    rows = disclosure.payloads_for(session, principal.organization_id, limit)
    # One key for the page. Revealing per row read and unwrapped this
    # organization's data key once per payload, which is the work the
    # ``reveal=false`` default exists to avoid — and it was still being done
    # a hundred times over whenever somebody did ask to see them.
    plaintext = disclosure.reveal_many(session, rows) if reveal else {}
    return {
        "total": _held(session, models.ModelPayload, principal.organization_id),
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

    ``total`` is how many events there are, against the ``limit`` of them
    ``events`` holds. Truncation is fine; truncation a reader cannot see is not
    — "we show you everything" and "we show you the last two hundred" are
    different promises, and this is the surface where the difference is the
    whole point.
    """
    rows = access.events_for(session, principal.organization_id, limit)
    return {
        "total": _held(session, models.AccessEvent, principal.organization_id),
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


# ── who acted, in order, provably ───────────────────────────────────────────
#
# OWNER-only, like everything else on this router, and here the gate is load
# bearing rather than conventional. §1: cost and margin never reach a
# salesperson, and this chain carries a policy transition — the margin floor
# before and after somebody edited it. That is the tightest classification of
# anything it records, so it sets the gate for all of it.
#
# The alternative — filtering the list per role so a manager sees the entries
# they are cleared for — was rejected. A per-role projection of an audit log is
# a second place the redaction rules have to be right, and the one place nobody
# tests, because the tests that matter are about the entries being *present*.
# Worse, a filtered chain does not verify: the links between the entries a role
# may not see are exactly the links a verifier walks. An audit surface that
# hands out a chain it says is unbroken while withholding the middle of it
# would be a worse lie than not offering it at all.

@router.get("/audit")
def get_audit(limit: int = Query(200, ge=1, le=1000),
              action: str = Query("", description="One action, e.g. POLICY_CHANGED"),
              principal: Principal = Depends(require_owner),
              session: Session = Depends(get_session)) -> dict:
    """The chain for this organization, newest first, with its verdict.

    The verdict is computed over the *whole* chain, not over the page returned:
    "has anything been altered" is a question about the history, and answering
    it from the two hundred rows somebody happened to ask for would report a
    clean bill for a tampered log the moment the tamper scrolled off the page.
    """
    rows = audit.entries_for(session, principal.organization_id,
                             limit=limit, action=(action or None))
    return {
        "verification": audit.verify(session, principal.organization_id),
        "counts": audit.actions_seen(rows),
        "entries": [dict(audit.covered_body(row),
                         entry_id=row.entry_id, entry_hash=row.entry_hash)
                    for row in rows],
        "note": ("Each entry names the hash of the one before it. Altering a row "
                 "invalidates it and every row after it, and the hashes are keyed "
                 "by a secret that is not in the database — so this is a record "
                 "we cannot quietly edit either. Take a copy from "
                 "/trust/audit/export and check it yourself."),
    }


@router.get("/audit/verify")
def get_audit_verify(principal: Principal = Depends(require_owner),
                     session: Session = Depends(get_session)) -> dict:
    """Walk the chain and report the first break, or that there is none.

    Its own endpoint as well as a field on the list above, because this is the
    one somebody polls. A break is an incident, and an incident check should not
    have to download two hundred entries to reach its answer.
    """
    return audit.verify(session, principal.organization_id)


@router.get("/audit/export")
def get_audit_export(fmt: str = Query("json", pattern="^(json|csv)$"),
                     principal: Principal = Depends(require_owner),
                     session: Session = Depends(get_session)):
    """The whole chain, in a form a third party can re-check without us.

    Both formats carry every field the signature covers plus the signature
    itself, and the JSON one carries the method statement as well — the point of
    an audit log is that somebody who does not trust us can accept it, and they
    can only do that if they can recompute what we claim.
    """
    if fmt == "csv":
        return PlainTextResponse(
            audit.export_csv(session, principal.organization_id),
            media_type="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="audit-{principal.organization_id}.csv"'})
    return audit.export_json(session, principal.organization_id)


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
