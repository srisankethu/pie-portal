"""The workspace a request is acting for, who is in it, and where else you can go.

Three reads and one write, and the write is the interesting one.

``GET /api/v1/organizations`` — every workspace this identity may open, with
the role it holds in each. It is what makes a switcher possible, and it is a
question that cannot be answered from inside a tenant: a request acting for
Acme is by construction forbidden to see Beta's membership rows. So it goes
through ``tenancy.user_memberships``, a SECURITY DEFINER function taking one
user id and returning that user's own doors, and falls back to the ordinary
query where there are no policies. Same split, same reason, as
``tenancy.email_registered``.

``POST /api/v1/organizations/{organization_id}/switch`` — open a session
against another workspace. **It grants nothing.** It looks the membership up
*inside the target tenant* and refuses without one; what it hands back is a
session whose organization every subsequent request re-checks in
``load_principal``. A caller who forges the organization on a token, or who
calls this for a workspace they were never granted, gets the same answer as one
naming a workspace that does not exist — which is the property that keeps an
organization id from being something worth guessing.

``GET /api/v1/organizations/current`` — this workspace and my role in it. A
screen has to be able to name which customer's data it is showing; on a
platform where one login reaches two, showing the wrong one is worse than
showing none.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .. import memberships, tenancy
from ..authz import (Principal, current_principal, open_session,
                     set_session_cookie)
from ..db import get_session
from ..domain import models

log = logging.getLogger("pie_portal.organizations")

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def my_organizations(session: Session, user_id: str) -> list[dict]:
    """Every workspace this identity may open. Shared with ``/auth/me``.

    One function rather than two queries that agree today: the sign-in response
    and this endpoint answer the same question, and a switcher listing
    workspaces the session response did not know about is the kind of
    disagreement nobody reproduces.
    """
    rows = tenancy.user_memberships(session, user_id)
    if rows is None:
        # No policies here, so the ordinary query is the authority — see
        # ``tenancy.user_memberships``.
        rows = []
        for m in memberships.organizations_for(session, user_id):
            org = session.get(models.Organization, m.organization_id)
            rows.append((m.organization_id, getattr(org, "name", "") or "", m.role))
    return [{"organization_id": oid, "name": name, "role": role}
            for oid, name, role in rows]


@router.get("")
def list_my_organizations(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    return {
        "organizations": my_organizations(session, principal.user_id),
        "current": principal.organization_id,
    }


@router.get("/current")
def current_organization(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """This workspace, and what this person is in it."""
    org = session.get(models.Organization, principal.organization_id)
    if org is None:  # pragma: no cover — a principal names an organization
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return {
        "organization_id": org.organization_id,
        "name": org.name,
        "currency": org.currency,
        "timezone": org.timezone,
        "country": org.country,
        "role": principal.role.value,
    }


@router.post("/{organization_id}/switch")
def switch_organization(
    organization_id: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Act for another workspace this identity belongs to.

    A new session rather than a mutated one, deliberately. A session is the
    record of one sign-in *to one workspace*: repointing an existing row would
    make "where am I signed in" unanswerable and would let a revoked-for-Acme
    session reappear as a live Beta one. The old session stays exactly as it
    is, so a second tab left on the first workspace keeps working.

    The membership is looked up inside the target tenant — announced first,
    then read — which is why a caller cannot reach a workspace by naming it.
    """
    target = (organization_id or "").strip()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")

    if target != principal.organization_id:
        # The connection is currently acting for the *current* workspace, whose
        # policy hides the target's membership rows. Announce the target before
        # asking, so the question is asked where the answer lives.
        tenancy.set_tenant(session, target)

    membership = memberships.active_membership_for(session, principal.user_id,
                                                   target)
    if membership is None:
        # Deliberately indistinguishable from "no such organization". A
        # different answer here would tell a caller which ids are real, one
        # request at a time.
        log.info("refused organization switch user=%s target=%s",
                 principal.user_id, target)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")

    user = session.get(models.User, principal.user_id)
    if user is None or not user.active:  # pragma: no cover — principal loaded it
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")

    token, opened = open_session(session, user,
                                 request.headers.get("user-agent"),
                                 organization_id=target)
    org = session.get(models.Organization, target)
    # Committed here, not left to the request teardown: the token about to go
    # out names that row, and a token naming a row nobody else can read yet is
    # a dead credential. Every path that mints one commits before handing it
    # over — see ``platform_auth.login``.
    session.commit()
    set_session_cookie(response, token)
    log.info("organization switched user=%s to=%s", principal.user_id, target)
    return {
        "token": token,
        "organization_id": target,
        "name": getattr(org, "name", "") or "",
        "role": membership.role,
        "session_id": opened.session_id,
    }


def default_organization(session: Session, user: models.User) -> Optional[str]:
    """Which workspace a sign-in should land in.

    The home organization when the user still holds a membership there, and
    otherwise the oldest one they do. The fallback is what makes a sign-in
    survive being removed from the workspace your identity row happens to be
    filed under — without it, somebody who left Acme but owns their own
    company could authenticate and then resolve no principal at all, which
    reads as a broken password.

    ``None`` means this identity belongs to nothing, and the caller refuses the
    sign-in rather than inventing somewhere for them to be.
    """
    if memberships.active_membership_for(session, user.user_id,
                                         user.organization_id) is not None:
        return user.organization_id
    rows = tenancy.user_memberships(session, user.user_id)
    if rows is None:
        held = memberships.organizations_for(session, user.user_id)
        return held[0].organization_id if held else None
    return rows[0][0] if rows else None
