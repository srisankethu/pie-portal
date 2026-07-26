"""Authorization for the Commercial Decision Platform.

Extends the existing HMAC-token approach (same ``AUTH_SECRET``, same stateless
signing) to the spec's three roles and DB-backed users. Role is a property of
``User`` (§2); a token carries only ``user_id`` + ``organization_id`` and the
Principal is resolved from the read model, so role/assignment can't be forged in
the token.

Scope (§2, §14):
- SALESPERSON → only their assigned customers' decisions; RESTRICTED decision
  types are excluded.
- SALES_MANAGER / OWNER → whole organization (team-hierarchy scoping is a later
  refinement; V1 read model embeds assignment on the customer only).

This module owns role + assignment resolution and API scope enforcement. Field
-level cost/margin redaction (context assembly) is a later phase and not done here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .config import settings
from .db import get_session
from .domain import models
from .domain.enums import RESTRICTED_DECISION_TYPES, Role


@dataclass
class Principal:
    user_id: str
    organization_id: str
    role: Role
    name: str
    email: Optional[str]

    @property
    def is_salesperson(self) -> bool:
        return self.role is Role.SALESPERSON

    @property
    def is_manager_or_owner(self) -> bool:
        return self.role in (Role.SALES_MANAGER, Role.OWNER)


# ── token (HMAC over user_id + org_id) ───────────────────────────────────────
def _sign(payload: bytes) -> str:
    sig = hmac.new(settings.AUTH_SECRET.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def _b64pad(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user_id: str, organization_id: str) -> str:
    body = {"uid": user_id, "oid": organization_id, "iat": int(time.time())}
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"{raw}.{_sign(raw.encode())}"


def verify_token(token: str) -> Optional[tuple[str, str]]:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(raw.encode())):
        return None
    try:
        body = json.loads(_b64pad(raw))
        return str(body["uid"]), str(body["oid"])
    except Exception:  # noqa: BLE001
        return None


# ── principal resolution ─────────────────────────────────────────────────────
def load_principal(session: Session, token: str) -> Optional[Principal]:
    parsed = verify_token(token)
    if parsed is None:
        return None
    user_id, org_id = parsed
    user = session.get(models.User, user_id)
    if user is None or not user.active or user.organization_id != org_id:
        return None
    try:
        role = Role(user.role)
    except ValueError:
        return None
    return Principal(user_id=user.user_id, organization_id=user.organization_id,
                     role=role, name=user.name, email=user.email)


def current_principal(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    principal = load_principal(session, authorization.split(" ", 1)[1].strip())
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return principal


def require_manager_or_owner(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_manager_or_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manager or owner role required")
    return principal


# ── scope resolution (used by the decision service/API) ──────────────────────
def decision_list_scope(principal: Principal) -> dict:
    """Kwargs for ``DecisionRepository.list`` that enforce this principal's scope.

    A salesperson is restricted to decisions assigned to them and never sees the
    RESTRICTED decision types (§14 decision-type gating).
    """
    if principal.is_salesperson:
        return {
            "assigned_user_id": principal.user_id,
            "exclude_types": tuple(t.value for t in RESTRICTED_DECISION_TYPES),
        }
    return {}


def can_view_decision(principal: Principal, decision: models.Decision) -> bool:
    """Server-side authorization for a single decision (defense in depth)."""
    if decision.organization_id != principal.organization_id:
        return False
    if principal.is_manager_or_owner:
        return True
    # salesperson
    if decision.decision_type in {t.value for t in RESTRICTED_DECISION_TYPES}:
        return False
    return decision.assigned_user_id == principal.user_id
