"""Zoho connections — add, check, rename, enable, delete, and pull from any of them.

An organization now has as many Zoho companies as it has books to read. This is
the surface an owner manages them from.

Three things this makes visible that the single-connection screen could not:

**Which company.** "The organization is connected" stops meaning anything once
there are three and one has a revoked token, so health is per connection and the
list says which one is stale.

**Scopes.** A half-granted scope is the most common reason a connection
authenticates and then returns nothing: the token works, the endpoint 401s, and
the sync reports zero rows with no obvious cause. The required scopes are listed
with what each one buys, and a check reports what the grant can actually reach.

**What sharing costs.** Rows from every connection on an organization are
analysed together. That is said on the screen, not left to be discovered.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner, require_owner
from ..config import settings
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..ingestion import connections as conn
from ..ingestion.zoho_client import ZohoApiSource, ZohoAuthError

log = logging.getLogger("pie_portal.connections")

router = APIRouter(prefix="/api/v1/connections", tags=["connections"])


def _dict(session: Session, row: models.ZohoConnection) -> dict:
    cred = row.credential
    return {
        "connection_id": row.connection_id,
        "label": row.label or f"Zoho org {row.zoho_organization_id}",
        "zoho_organization_id": row.zoho_organization_id,
        "enabled": row.enabled,
        "credential_id": row.credential_id,
        # An identifier, not a secret — it is what tells two grants apart in a list.
        "client_id": cred.client_id if cred else row.client_id,
        "credential_label": (cred.label or "Zoho connection") if cred else "inline (legacy)",
        "credential_rotated_at": (cred.rotated_at.isoformat()
                                  if cred and cred.rotated_at else None),
        "accounts_base": row.accounts_base,
        "api_base": row.api_base,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "last_check_ok": row.last_check_ok,
        "last_check_detail": row.last_check_detail,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
def list_connections(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    org = principal.organization_id
    rows = conn.list_connections(session, org)
    credentials = conn.usable_credentials(session, org)
    return {
        "connections": [_dict(session, r) for r in rows],
        "credentials": [
            {"credential_id": c.credential_id,
             "label": c.label or "Zoho connection",
             "client_id": c.client_id,
             "is_owner": c.owner_organization_id == org,
             "rotated_at": c.rotated_at.isoformat() if c.rotated_at else None,
             "used_by": len(conn.connections_using(session, c.credential_id))}
            for c in credentials
        ],
        "required_scopes": [
            {"scope": s, "why": why, "required": required}
            for s, why, required in conn.REQUIRED_SCOPES
        ],
        "scope_string": conn.SCOPE_STRING,
        "can_manage": principal.role is Role.OWNER,
        "source_mode": settings.ZOHO_SOURCE,
        # Said on the screen rather than left to be discovered.
        "pooling_note": (
            "Every enabled connection here feeds this organization's analysis. "
            "Revenue and margin roll up across all of them. To keep legal "
            "entities apart, give each its own organization instead."),
    }


class NewConnection(BaseModel):
    """Either supply a credential already on file, or a fresh set of secrets."""

    zoho_organization_id: str = Field(min_length=1, max_length=64)
    label: str = ""
    credential_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    accounts_base: str = "https://accounts.zoho.in"
    api_base: str = "https://www.zohoapis.in/books/v3"


@router.post("", status_code=status.HTTP_201_CREATED)
def add_connection(
    body: NewConnection,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Add a Zoho company.

    Reusing a credential is the normal path for the second and third company: a
    Zoho refresh token belongs to a user and already reaches every company that
    user can see, so re-entering it would only create a copy for a future
    rotation to miss.
    """
    org = principal.organization_id
    zoho_org = body.zoho_organization_id.strip()

    try:
        if body.credential_id:
            row = conn.add_connection(
                session, org, credential_id=body.credential_id,
                zoho_organization_id=zoho_org, label=body.label)
        else:
            missing = [n for n, v in (("client_id", body.client_id),
                                      ("client_secret", body.client_secret),
                                      ("refresh_token", body.refresh_token))
                       if not (v or "").strip()]
            if missing:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Choose an existing connection, or supply "
                    + ", ".join(missing) + ".")
            row = conn.set_zoho_credentials(
                session, org, zoho_organization_id=zoho_org,
                client_id=body.client_id.strip(),
                client_secret=body.client_secret.strip(),
                refresh_token=body.refresh_token.strip(),
                accounts_base=body.accounts_base.strip().rstrip("/"),
                api_base=body.api_base.strip().rstrip("/"),
                label=body.label,
                # The grant is named after itself, not after the first company
                # it happened to connect — it will very likely serve others.
                credential_label=f"Zoho sign-in {body.client_id.strip()[:14]}")
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    _check(session, row)
    return _dict(session, row)


class EditConnection(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None


@router.patch("/{connection_id}")
def edit_connection(
    connection_id: str,
    body: EditConnection,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    try:
        row = conn.update_connection(session, principal.organization_id, connection_id,
                                     label=body.label, enabled=body.enabled)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return _dict(session, row)


@router.delete("/{connection_id}")
def delete_connection(
    connection_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Remove a connection. Data already pulled through it stays.

    Deliberately not a cascade: rows already synced are facts about what was
    invoiced, and disconnecting is an administrative act about credentials, not
    a decision to forget trading history.
    """
    try:
        row = conn.delete_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return {"removed": True, "connection_id": row.connection_id,
            "note": "Data already pulled through this connection is unchanged."}


@router.post("/{connection_id}/check")
def check_connection(
    connection_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Ask Zoho whether this connection works, and what it can reach."""
    try:
        row = conn.get_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return _check(session, row)


def _check(session: Session, row: models.ZohoConnection) -> dict:
    """Ping, record the result on the row, and report what the grant sees.

    Recorded even on failure: a connection that has never been checked and one
    that failed an hour ago look the same on a list, and only one of them is a
    problem.
    """
    if settings.ZOHO_SOURCE != "api":
        detail = f"Source is {settings.ZOHO_SOURCE!r}, so nothing was contacted."
        conn.record_check(session, row, ok=False, detail=detail)
        return {**_dict(session, row), "checked": False, "detail": detail}

    try:
        creds = conn.credentials_for(session, row)
        info = ZohoApiSource(credentials=creds).ping()
    except (ZohoAuthError, conn.CredentialNotUsable) as e:
        conn.record_check(session, row, ok=False, detail=str(e))
        return {**_dict(session, row), "checked": True, "ok": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — a check must report, not 500
        conn.record_check(session, row, ok=False, detail=f"{type(e).__name__}: {e}")
        return {**_dict(session, row), "checked": True, "ok": False,
                "detail": f"{type(e).__name__}: {e}"}

    found = info.get("organization_found")
    detail = ("Reached this company." if found else
              f"Authenticated, but company {row.zoho_organization_id} is not among "
              f"the ones this login can see.")
    conn.record_check(session, row, ok=bool(found), detail=detail)
    return {
        **_dict(session, row),
        "checked": True,
        "ok": bool(found),
        "detail": detail,
        "organization_name": info.get("organization_name"),
        "currency": info.get("currency"),
        # Every company this grant reaches — the answer to "do I need another
        # credential for the next entity?", which is almost always no.
        "visible_organizations": info.get("visible_organizations", []),
    }
