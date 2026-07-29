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

    return LoginResponse(
        token=issue_token(user.user_id, user.organization_id),
        user_id=user.user_id, organization_id=user.organization_id,
        role=user.role, name=user.name,
        must_change_password=user.must_change_password)
