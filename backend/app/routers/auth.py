"""Authentication + role endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import current_principal
from ..schemas import LoginRequest, LoginResponse
from ..security import Principal, authenticate, issue_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    principal = authenticate(body.email, body.password)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "No account found for that email, or password missing.")
    token = issue_token(principal.email)
    assert token is not None
    return LoginResponse(token=token, role=principal.role,
                         name=principal.name, email=principal.email)


@router.get("/me", response_model=LoginResponse)
def me(principal: Principal = Depends(current_principal)) -> LoginResponse:
    # No token minted here; the client already holds one.
    return LoginResponse(token="", role=principal.role,
                         name=principal.name, email=principal.email)
