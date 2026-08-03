"""Identity management — review, link, unlink, and the audit behind each.

Read by managers and owners; changed by owners only. Deciding that two ERP
records are one company reshapes every margin figure that rolls up from them,
which puts it in the same class as editing the margin policy rather than in the
same class as running a sync.

Nothing here knows what a connector is beyond its name as a string. Adding
Tally or ERPNext changes the importer and nothing on this surface.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner, require_owner
from ..db import get_session
from ..domain import models
from ..identity import service as identity
from ..identity.service import CUSTOMER, ITEM

log = logging.getLogger("pie_portal.identity_api")

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])

_TYPES = {"customers": CUSTOMER, "items": ITEM}


def _entity(kind: str) -> str:
    if kind not in _TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Identities exist for 'customers' and 'items'")
    return _TYPES[kind]


def _record_dict(r: Any, entity_type: str) -> dict:
    common = {
        "record_id": r.record_id,
        "connector": r.connector,
        "connection_id": r.connection_id,
        "external_id": r.external_id,
        "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
        # The raw values exactly as the connector supplied them. Kept visible
        # because the point of not merging is that you can always see what each
        # system actually said.
        "source_ref": r.source_ref or {},
    }
    if entity_type == CUSTOMER:
        return {**common, "name": r.name, "gstin": r.gstin,
                "customer_id": r.customer_id}
    return {**common, "sku": r.sku, "description": r.description,
            "product_id": r.product_id}


def _display(records: list[Any], entity_type: str) -> str:
    """A name for the identity, derived rather than stored.

    Taken from the linked records, longest first — a fuller name is usually the
    more complete one ("ABC Industries Pvt Ltd" over "ABC Industries"). Derived
    on read rather than cached on the identity so that no single connector
    becomes the authority on what the entity is called.
    """
    field = "name" if entity_type == CUSTOMER else "description"
    names = [getattr(r, field, "") or "" for r in records]
    return max(names, key=len, default="")


def _identity_dict(session: Session, row: Any, entity_type: str,
                   *, with_records: bool = True) -> dict:
    records = identity.records_for(session, row.organization_id, entity_type,
                                   row.identity_id)
    out = {
        "identity_id": row.identity_id,
        "label": row.label,
        "display_name": row.label or _display(records, entity_type),
        "active": row.active,
        "connector_count": len({r.connector for r in records}),
        "record_count": len(records),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if with_records:
        out["records"] = [_record_dict(r, entity_type) for r in records]
    return out


# ── the auto-link setting ───────────────────────────────────────────────────
class PolicyIn(BaseModel):
    auto_link_customers: Optional[bool] = None
    auto_link_items: Optional[bool] = None


@router.get("/settings/policy")
def get_policy(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    p = identity.get_policy(session, principal.organization_id)
    return {
        "auto_link_customers": p.auto_link_customers,
        "auto_link_items": p.auto_link_items,
        "can_manage": principal.role.value == "OWNER",
        "note": ("With these off, a sync that finds an exact match records a "
                 "suggestion and waits. Turning one on lets the sync link "
                 "without asking — faster, and unrecoverable when the match was "
                 "a group trading under one registration."),
    }


@router.patch("/settings/policy")
def update_policy(
    body: PolicyIn,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    p = identity.get_policy(session, principal.organization_id)
    for field_name, value in body.model_dump(exclude_none=True).items():
        setattr(p, field_name, value)
    p.updated_by_user_id = principal.user_id
    session.flush()
    log.info("identity policy updated org=%s by=%s", principal.organization_id,
             principal.user_id)
    return {"auto_link_customers": p.auto_link_customers,
            "auto_link_items": p.auto_link_items}


# ── identities ──────────────────────────────────────────────────────────────
#
# Declared *after* the settings routes above, deliberately. FastAPI matches in
# declaration order, and "/{kind}/{identity_id}" happily swallows
# "/settings/policy" as kind="settings", identity_id="policy" — which is how
# that endpoint 404'd until a test caught it. Anything with a literal path must
# come first.
@router.get("/{kind}")
def list_identities(
    kind: str,
    q: str = "",
    linked_only: bool = Query(False, description="Only identities with 2+ connectors"),
    limit: int = Query(100, le=500),
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Identities for this organization, most recently created first."""
    entity_type = _entity(kind)
    shape = identity._SHAPES[entity_type]
    org = principal.organization_id

    stmt = (select(shape["identity"])
            .where(shape["identity"].organization_id == org,
                   shape["identity"].active.is_(True))
            .order_by(shape["identity"].created_at.desc()))
    rows = list(session.scalars(stmt))

    out = [_identity_dict(session, r, entity_type) for r in rows]
    if q:
        needle = q.strip().lower()
        out = [d for d in out
               if needle in (d["display_name"] or "").lower()
               or any(needle in str(v).lower()
                      for rec in d["records"] for v in rec.values() if v)]
    if linked_only:
        out = [d for d in out if d["connector_count"] > 1]

    return {
        "identities": out[:limit],
        "total": len(out),
        "can_manage": principal.role.value == "OWNER",
        "pending_suggestions": _pending_count(session, org, entity_type),
    }


@router.get("/{kind}/{identity_id}")
def get_identity(
    kind: str,
    identity_id: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    entity_type = _entity(kind)
    shape = identity._SHAPES[entity_type]
    row = session.get(shape["identity"], identity_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such identity")
    return {
        **_identity_dict(session, row, entity_type),
        "history": [
            {"action": e.action, "actor": e.actor, "detail": e.detail,
             "record_id": e.record_id, "at": e.at.isoformat() if e.at else None}
            for e in identity.history(session, principal.organization_id,
                                      entity_type, identity_id)
        ],
    }


def _pending_count(session: Session, org: str, entity_type: str) -> int:
    return session.scalar(
        select(func.count()).select_from(models.IdentitySuggestion).where(
            models.IdentitySuggestion.organization_id == org,
            models.IdentitySuggestion.entity_type == entity_type,
            models.IdentitySuggestion.status == "PENDING")) or 0


@router.get("/{kind}/suggestions/pending")
def list_suggestions(
    kind: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Matches found during a sync that are waiting for a person.

    Each carries the evidence that produced it — "GSTIN 29ABCDE1234F1Z5" — so
    the reviewer judges the match rather than trusting a score.
    """
    entity_type = _entity(kind)
    shape = identity._SHAPES[entity_type]
    org = principal.organization_id

    rows = list(session.scalars(
        select(models.IdentitySuggestion).where(
            models.IdentitySuggestion.organization_id == org,
            models.IdentitySuggestion.entity_type == entity_type,
            models.IdentitySuggestion.status == "PENDING")
        .order_by(models.IdentitySuggestion.created_at.desc())))

    out = []
    for s in rows:
        record = session.get(shape["record"], s.record_id)
        target = session.get(shape["identity"], s.target_identity_id)
        if record is None or target is None or not target.active:
            continue
        out.append({
            "suggestion_id": s.suggestion_id,
            "strategy": s.strategy,
            "evidence": s.evidence,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "incoming": _record_dict(record, entity_type),
            "incoming_identity_id": record.identity_id,
            "target": _identity_dict(session, target, entity_type),
        })
    return {"suggestions": out, "can_manage": principal.role.value == "OWNER"}


class Decide(BaseModel):
    accept: bool


@router.post("/{kind}/suggestions/{suggestion_id}")
def decide_suggestion(
    kind: str,
    suggestion_id: str,
    body: Decide,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    _entity(kind)
    try:
        row = identity.decide_suggestion(
            session, principal.organization_id, suggestion_id,
            accept=body.accept, actor=principal.user_id)
    except identity.IdentityError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"suggestion_id": row.suggestion_id, "status": row.status}


class LinkRequest(BaseModel):
    record_id: str = Field(min_length=1)
    identity_id: str = Field(min_length=1)
    reason: str = ""


@router.post("/{kind}/link")
def link(
    kind: str,
    body: LinkRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Attach a connector record to an identity. Nothing is merged or copied."""
    entity_type = _entity(kind)
    try:
        record = identity.link_record(
            session, principal.organization_id, entity_type=entity_type,
            record_id=body.record_id, identity_id=body.identity_id,
            actor=principal.user_id, detail=body.reason or "linked by hand")
    except identity.IdentityError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"record_id": record.record_id, "identity_id": record.identity_id}


class UnlinkRequest(BaseModel):
    record_id: str = Field(min_length=1)
    reason: str = ""


@router.post("/{kind}/unlink")
def unlink(
    kind: str,
    body: UnlinkRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Split a record onto its own identity. The record itself is unchanged."""
    entity_type = _entity(kind)
    try:
        record = identity.unlink_record(
            session, principal.organization_id, entity_type=entity_type,
            record_id=body.record_id, actor=principal.user_id, reason=body.reason)
    except identity.IdentityError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"record_id": record.record_id, "identity_id": record.identity_id}


class Relabel(BaseModel):
    label: str = ""


@router.patch("/{kind}/{identity_id}")
def relabel(
    kind: str,
    identity_id: str,
    body: Relabel,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    entity_type = _entity(kind)
    try:
        row = identity.relabel(session, principal.organization_id,
                               entity_type=entity_type, identity_id=identity_id,
                               label=body.label, actor=principal.user_id)
    except identity.IdentityError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _identity_dict(session, row, entity_type)
