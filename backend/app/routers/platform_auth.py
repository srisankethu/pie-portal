"""Decision-platform authentication.

Verifies a password against the stored PBKDF2 hash and returns an HMAC token
carrying user_id + org_id. Role and scope are resolved from the User row on
every request, never from the token.

This endpoint previously accepted *any* non-empty password for any known email,
which meant the platform's three roles were a presentation choice rather than a
boundary: the salesperson view, the manager view and the owner view were all one
guessable address apart. Everything downstream — decision-type gating, the
cost/margin redaction, the approval authority split — was being enforced
faithfully on top of an identity nobody had checked.

A user row with no password hash cannot sign in at all. That is deliberate: the
failure mode of "no credential set" must be denial, not admission.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import issue_token
from .. import clock
from ..config import settings
from ..db import get_session
from ..domain import models
from ..passwords import hash_password, needs_rehash, verify_password

log = logging.getLogger("pie_portal.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["platform-auth"])

# One message for every failure. Distinguishing "no such account" from "wrong
# password" tells an attacker which addresses are worth attacking.
_REJECTED = "Incorrect email or password"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    organization_id: str
    role: str
    name: str
    # The address this account signs in with, echoed back for one reason: a
    # change-password form with two password fields and no username makes a
    # password manager save the new secret against nothing, or against the
    # wrong entry. `autocomplete="username"` needs a value, and the only
    # correct one is the address the person just signed in with. Not a
    # disclosure: it is their own address, on a response they authenticated for.
    email: str = ""
    # The currency this organization trades in. Sent at sign-in because every
    # screen renders money and none of them should be guessing: the client used
    # to hardcode a rupee sign in four places, which is correct for exactly one
    # tenant and silently wrong for the next.
    currency: str = "INR"
    # The zone this business's day is measured in. Sent at sign-in for the same
    # reason the currency is: every screen renders a timestamp, and a browser
    # in another zone would otherwise show a sync that ran this morning as
    # yesterday — or, for an owner travelling, silently shift the whole book by
    # a day. The business's day is the one worth showing.
    timezone: str = "Asia/Kolkata"
    must_change_password: bool = False


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    email = (body.email or "").strip().lower()
    user = session.scalar(select(models.User).where(models.User.email == email))

    if user is None or not user.active or not verify_password(body.password,
                                                              user.password_hash):
        log.info("failed sign-in for %r", email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _REJECTED)

    # Opportunistic upgrade: a hash made at a lower work factor is replaced now
    # that the correct password is in hand, which is the only moment it can be.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    user.last_login_at = datetime.now(timezone.utc)
    session.flush()

    org = session.get(models.Organization, user.organization_id)

    return LoginResponse(
        token=issue_token(user.user_id, user.organization_id),
        user_id=user.user_id, organization_id=user.organization_id,
        role=user.role, name=user.name, email=user.email,
        currency=(getattr(org, "currency", None) or settings.DEFAULT_CURRENCY),
        timezone=(getattr(org, "timezone", None) or clock.DEFAULT_ZONE),
        must_change_password=user.must_change_password)
