"""FastAPI dependencies: current principal + the Zoho adapter.

Swap ``get_zoho`` to return a live Zoho Books adapter (implementing
``ZohoService``) to go from the mock to production without touching routers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from .security import Principal, verify_token
from .zoho import ZohoService, mock_zoho


def get_zoho() -> ZohoService:
    return mock_zoho


def current_principal(authorization: Optional[str] = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    principal = verify_token(authorization.split(" ", 1)[1].strip())
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return principal


def require_mgmt(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_mgmt:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Management role required")
    return principal
