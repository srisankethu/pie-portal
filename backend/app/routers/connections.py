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

import inspect
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner, require_owner
from ..config import settings
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..ingestion import connections as conn
from ..ingestion.zoho_client import ZohoApiSource, ZohoAuthError

log = logging.getLogger("pie_portal.connections")


class SchemaBehind(RuntimeError):
    """The database is older than the code reading it."""


def _schema_guard(call):
    """Turn a database that is behind into a sentence someone can act on.

    Every endpoint here goes through it, because the failure is not specific
    to one of them: a bare 500 with an empty body is what a browser console
    shows, and it names neither the cause nor the fix.
    """
    def wrapper(*args, **kwargs):
        try:
            return call(*args, **kwargs)
        except SchemaBehind as e:
            log.error("schema behind: %s", e)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    wrapper.__name__ = call.__name__
    wrapper.__doc__ = call.__doc__
    # FastAPI reads the signature to build the dependency graph, so it has to
    # survive the wrapping.
    wrapper.__signature__ = inspect.signature(call)
    wrapper.__annotations__ = call.__annotations__
    return wrapper


class _Router(APIRouter):
    """An APIRouter that wraps every endpoint in the schema guard."""

    def add_api_route(self, path, endpoint, **kwargs):  # type: ignore[override]
        return super().add_api_route(path, _schema_guard(endpoint), **kwargs)


router = _Router(prefix="/api/v1/connections", tags=["connections"])


#: How far back a first pull reads when nobody has said otherwise. Eighteen
#: months gives the detectors a full recent window, a full comparison window,
#: and room above the six-month history floor — so the first sync produces an
#: analysis rather than a screen full of "not enough history".
DEFAULT_HISTORY_MONTHS = 18


def _default_since() -> date:
    today = date.today()
    year, month = divmod(today.year * 12 + (today.month - 1) - DEFAULT_HISTORY_MONTHS, 12)
    return date(year, month + 1, 1)


def _last_run(session: Session, row: models.ZohoConnection) -> Optional[models.SyncRun]:
    """The most recent pull aimed at this company specifically.

    Runs that covered every connection are excluded on purpose: this answers
    "when did *this* company last come in", and a company that has never been
    pulled individually should say so rather than borrow another run's date.

    ``sync_runs`` has gained columns since this table existed, so a deployment
    running new code against an un-migrated database fails here — and used to
    fail as a bare 500 with no body, unreadable from a browser console. Worse,
    it only appeared once a connection existed: with none, the query never ran
    and the screen looked healthy.

    The missing columns are looked up rather than guessed. An earlier version
    matched the error text for "connection_id", which appears in the SELECT
    list of *every* such failure — so a database missing ``notes`` was told to
    go and fix ``connection_id``, which it already had. A diagnostic that names
    the wrong thing is worse than a generic one.
    """
    try:
        return session.scalars(
            select(models.SyncRun)
            .where(models.SyncRun.organization_id == row.organization_id,
                   models.SyncRun.connection_id == row.connection_id)
            .order_by(models.SyncRun.started_at.desc())
            .limit(1)).first()
    except OperationalError as e:
        session.rollback()   # Postgres aborts the whole transaction otherwise
        from ..schema_check import FIX, missing_columns

        gaps = missing_columns(session.get_bind())
        if not gaps:
            raise                      # a real database error, not a stale schema
        detail = "; ".join(f"{t} ({', '.join(cols)})" for t, cols in sorted(gaps.items()))
        raise SchemaBehind(
            f"This database is behind the code — missing {detail}. Run "
            f"`{FIX}` against it and reload. No data is lost.") from e


def _dict(session: Session, row: models.ZohoConnection) -> dict:
    cred = row.credential
    last = _last_run(session, row)
    return {
        # The date this company was last read from, and the date to offer next
        # time. Carried per connection because the answer genuinely differs:
        # one entity may have four years of books worth reading and another
        # four months, and a single date box for all of them either over-reads
        # or under-reads at least one.
        "last_sync": None if last is None else {
            "status": last.status,
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "since": last.since.isoformat() if last.since else None,
            "sales_txns": last.sales_txns,
            "cost_records": last.cost_records,
            "error": last.error,
        },
        "suggested_since": (last.since.isoformat() if last is not None and last.since
                            else _default_since().isoformat()),
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


class RotateToken(BaseModel):
    """A fresh Zoho grant. The refresh token is the thing that actually expires
    or gets revoked; the client pair is optional because it usually has not
    changed and re-typing a secret that is already correct is how a working
    connection gets broken."""

    refresh_token: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


@router.post("/{connection_id}/rotate")
def rotate_connection_token(
    connection_id: str,
    body: RotateToken,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Replace the Zoho grant this connection signs in with.

    **Rotation lives here, on the connection, because it is a Zoho mechanism
    rather than a platform one.** A refresh token that can be revoked and
    reissued is how Zoho's OAuth works; another connector might use a static
    key, a certificate, or nothing that is ever rotated at all. It was
    previously a "credentials" surface of its own, which made a Zoho detail
    look like a concept every connector would need — and put the control on a
    different screen from the thing somebody was looking at when they decided
    to rotate.

    **One grant can reach several companies, and rotating from any of them
    rotates it for all of them.** That is deliberate — it is what makes
    rotating after a leak one operation rather than three — but it must not be
    a surprise, so the response names every connection that just changed
    underneath. The arithmetic is not repeated here: this delegates to
    ``ingestion.connections.rotate_credential``, which is where the ownership
    rule lives.
    """
    if not body.refresh_token.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "A refresh token is required")
    try:
        row = conn.get_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    if not row.credential_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This connection has no stored grant to rotate. Re-enter it with "
            "Edit instead.")

    also = [c for c in conn.connections_using(session, row.credential_id)
            if c.connection_id != row.connection_id]
    try:
        conn.rotate_credential(
            session, principal.organization_id, row.credential_id,
            refresh_token=body.refresh_token.strip(),
            client_id=(body.client_id or "").strip() or None,
            client_secret=(body.client_secret or "").strip() or None)
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    # Checked immediately: a rotation that silently left the connection broken
    # is worse than no rotation, because the next failure looks like Zoho's.
    checked = _check(session, row)
    return {
        **checked,
        "rotated": True,
        "also_rotated": [
            {"connection_id": c.connection_id, "label": c.label,
             "zoho_organization_id": c.zoho_organization_id}
            for c in also
        ],
        "note": ("Rotated." if not also else
                 f"Rotated. {len(also)} other compan"
                 f"{'y' if len(also) == 1 else 'ies'} sign in through the same "
                 f"grant and now use the new token too."),
    }


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
    # Zoho knows which zone the books are kept in. Recorded on the platform's
    # organization while we have it, so "which day is it for this business?"
    # stops depending on where the server happens to be hosted. Only ever
    # filled in, never overwritten — an owner who has set it meant it.
    zone = str(info.get("time_zone") or "").strip()
    if found and zone:
        org = session.get(models.Organization, row.organization_id)
        if org is not None and not (org.timezone or "").strip():
            org.timezone = zone
            session.flush()

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
        "time_zone": info.get("time_zone"),
        # Every company this grant reaches — the answer to "do I need another
        # credential for the next entity?", which is almost always no.
        "visible_organizations": info.get("visible_organizations", []),
    }
