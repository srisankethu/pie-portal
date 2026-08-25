"""Minting and revoking the credentials the public resolution API takes.

Owner-only, signed in with a session like every other administrative surface —
a key cannot mint another key. That is deliberate rather than incidental: a
machine credential that can issue machine credentials turns one leaked key into
a permanent foothold, and the person who has to answer for an integration's
access is the one who should have had to create it.

The secret is returned **once**, from ``POST``, and never again. ``GET`` lists
the keys with a four-character hint and no way to reconstruct one. There is no
"show key" endpoint and adding one would defeat storing a hash at all.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import api_keys
from ..authz import Principal, require_owner
from ..db import get_session
from ..domain.enums import Role
from ..trust import audit

log = logging.getLogger("pie_portal.api_keys")
router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    #: Defaults to the narrowest role rather than the creator's. An integration
    #: that only resolves nomenclature needs no economics at all, and a key that
    #: inherited its owner's role would hand a partner's server the whole book's
    #: cost basis because nobody thought about the field.
    role: Role = Role.SALESPERSON
    rate_limit_per_minute: Optional[int] = Field(default=None, ge=0, le=6000)


@router.get("")
def list_keys(principal: Principal = Depends(require_owner),
              session: Session = Depends(get_session)) -> dict:
    return {"keys": [api_keys.to_dict(row) for row in
                     api_keys.keys_for(session, principal.organization_id)]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_key(body: CreateKeyRequest,
               principal: Principal = Depends(require_owner),
               session: Session = Depends(get_session)) -> dict:
    """Mint a key. The secret in this response is the only copy that will exist.

    The audit entry names the role the key carries, because that is the fact
    somebody will want months later — not that a key was created, but what it
    was allowed to read.
    """
    issued = api_keys.issue(
        session, principal.organization_id, name=body.name, role=body.role,
        rate_limit_per_minute=body.rate_limit_per_minute,
        created_by_user_id=principal.user_id)
    audit.append(session, organization_id=principal.organization_id,
                 action="API_KEY_CREATED", actor=principal,
                 subject_type="api_key", subject_id=issued.row.key_id,
                 detail={"name": issued.row.name, "role": issued.row.role,
                         "rate_limit_per_minute":
                             issued.row.rate_limit_per_minute})
    # The row and its audit entry commit together, which is the arrangement
    # `trust.audit.append` asks for: a credential with no record of who created
    # it is the one nobody can answer for.
    session.commit()
    out = api_keys.to_dict(issued.row)
    out["secret"] = issued.secret
    out["detail"] = ("Copy this key now — it is stored as a hash and cannot be "
                     "shown again. Send it as `Authorization: Bearer <key>`.")
    return out


@router.delete("/{key_id}")
def revoke_key(key_id: str, principal: Principal = Depends(require_owner),
               session: Session = Depends(get_session)) -> dict:
    """End a key. A foreign or unknown id is a 404 — the two are one answer, so
    the endpoint never confirms that another tenant's key exists."""
    if not api_keys.revoke(session, principal.organization_id, key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such API key")
    audit.append(session, organization_id=principal.organization_id,
                 action="API_KEY_REVOKED", actor=principal,
                 subject_type="api_key", subject_id=key_id)
    session.commit()
    return {"key_id": key_id, "revoked": True}
