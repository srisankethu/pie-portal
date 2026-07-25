"""Decision-platform authentication (demo).

Authenticates a seeded platform User by email (any password, as in the legacy
demo auth) and returns an HMAC token carrying user_id + org_id. Role/scope are
resolved from the User row on every request, never from the token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import issue_token
from ..db import get_session
from ..domain import models

router = APIRouter(prefix="/api/v1/auth", tags=["platform-auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    organization_id: str
    role: str
    name: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    email = (body.email or "").strip().lower()
    if not body.password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password required")
    user = session.scalar(select(models.User).where(models.User.email == email))
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active account for that email")
    token = issue_token(user.user_id, user.organization_id)
    return LoginResponse(token=token, user_id=user.user_id,
                         organization_id=user.organization_id, role=user.role, name=user.name)
