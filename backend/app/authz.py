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

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from . import clock
from .config import settings
from .db import get_session
from .domain import models
from .domain.enums import RESTRICTED_DECISION_TYPES, DecisionType, Role


@dataclass
class Principal:
    user_id: str
    organization_id: str
    role: Role
    name: str
    email: Optional[str]
    #: This account is holding a password it was issued rather than one it chose.
    #: `current_principal` refuses everything but the change itself while it is
    #: set, so the flag is a gate and not just a label on an admin screen.
    must_change_password: bool = False

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
    # Fractional seconds, not whole ones. `load_principal` retires a token whose
    # `iat` predates the account's `password_changed_at`, and at whole-second
    # resolution the token being replaced shares a second with the change that
    # retires it — so the compromised session survived exactly the case this is
    # for. Older tokens carrying an integer still parse as floats.
    body = {"uid": user_id, "oid": organization_id, "iat": time.time()}
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"{raw}.{_sign(raw.encode())}"


def verify_token(token: str) -> Optional[tuple[str, str, float]]:
    """`(user_id, organization_id, issued_at)`, or None if the token is not ours.

    `iat` comes back because it is how a credential change retires the sessions
    opened before it — see `load_principal`. It was already in the payload and
    simply discarded here.
    """
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(raw.encode())):
        return None
    try:
        body = json.loads(_b64pad(raw))
        return str(body["uid"]), str(body["oid"]), float(body.get("iat") or 0)
    except Exception:  # noqa: BLE001
        return None


# ── principal resolution ─────────────────────────────────────────────────────
def load_principal(session: Session, token: str) -> Optional[Principal]:
    parsed = verify_token(token)
    if parsed is None:
        return None
    user_id, org_id, issued_at = parsed
    user = session.get(models.User, user_id)
    if user is None or not user.active or user.organization_id != org_id:
        return None
    # A token minted before the password changed is no longer a valid session.
    # Without this, changing a password left every session opened with the old one
    # working indefinitely — which is precisely what the change is for when a
    # credential is thought to be compromised. Whole-second resolution, so a token
    # `iat` carries fractional seconds so the replacement token the change hands
    # back — minted after the timestamp is stamped — is strictly newer, while the
    # token that made the request is strictly older and dies here.
    changed = clock.aware(user.password_changed_at)
    if changed is not None and issued_at < changed.timestamp():
        return None
    try:
        role = Role(user.role)
    except ValueError:
        return None
    return Principal(user_id=user.user_id, organization_id=user.organization_id,
                     role=role, name=user.name, email=user.email,
                     must_change_password=bool(user.must_change_password))


#: The only thing an account owing a password change may reach. Deliberately one
#: path: anything wider is a way to keep using a credential somebody else issued.
#: `/api/v1/auth/*` does not appear because it does not depend on this at all.
PASSWORD_CHANGE_PATH = "/api/v1/admin/me/password"


def current_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    principal = load_principal(session, authorization.split(" ", 1)[1].strip())
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    # `must_change_password` was set by the seed and by every owner-issued reset,
    # read in exactly two places — the login response and a label on the admin
    # grid — and enforced nowhere. So `change-me-now` stayed live on every seeded
    # account indefinitely, and the flag named a control that did not exist.
    # Enforced here rather than in the sign-in screen: a rule the client owns is a
    # rule that anything not the client ignores.
    if (principal.must_change_password
            and request.url.path.rstrip("/") != PASSWORD_CHANGE_PATH):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Change your password before using this account. It is still the one "
            "you were issued.")
    return principal


def require_manager_or_owner(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_manager_or_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manager or owner role required")
    return principal


def require_owner(principal: Principal = Depends(current_principal)) -> Principal:
    """Owner-only surface (AI cost/health metrics are an owner concern)."""
    if principal.role is not Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
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


def decision_queue_scope(principal: Principal,
                         requested_type: Optional[str] = None) -> dict:
    """``decision_list_scope`` plus the queue's own exclusion. One definition.

    The proactive queue is not simply "this principal's decisions": QUOTE_CONTEXT
    is on-demand support assembled from inside the Quote Builder, not an
    attention item, so it stays out unless a caller asks for it by type.

    That rule lived inline in ``list_decisions``, which meant the landing page's
    "Decisions in the queue" tile — a plain org-wide ``count(*)`` over every OPEN
    row — could report a large number while the screen it linked to showed two.
    For a salesperson the gap is most of the taxonomy: nineteen of the twenty-two
    decision types are RESTRICTED and never reach them.

    This is the same defect, and the same fix, as ``approvals.pending_count``
    eleven lines above the tile's query: a count and the list it promises to
    count have to come from one place, or they eventually disagree about
    something nobody can reproduce.
    """
    scope = decision_list_scope(principal)
    if requested_type != DecisionType.QUOTE_CONTEXT.value:
        existing = tuple(scope.get("exclude_types", ()))
        scope["exclude_types"] = existing + (DecisionType.QUOTE_CONTEXT.value,)
    return scope


def can_view_customer(principal: Principal,
                      customer: Optional[models.Customer]) -> bool:
    """Whether this principal may see this account at all.

    The same rule `/api/v1/accounts` applies to a list, applied to one row: a
    per-customer route that skips it is a way around all of it, since the id is
    the only thing standing between a salesperson and every relationship in the
    book, and ids travel.

    Here rather than in a router because it was written out twice — inline in
    `accounts.list_account_items` and again inside `insight._require_visible_customer`
    — and a scope rule with two copies is one that eventually disagrees with
    itself about a reassigned account. `can_view_decision` above is the same rule
    for the other subject, which is why this belongs beside it.

    Takes ``None`` so a caller can pass a failed `session.get` straight in: a
    customer that does not exist and one this principal cannot see must give the
    same answer, or the difference between them is an enumeration oracle.
    **What each caller does with a False is deliberately theirs** — the item
    picker answers with an empty list because a dropdown that errors is a field
    that breaks, and the timeline answers 404 because a screen that draws itself
    empty claims the account exists. Both are indistinguishable from the
    not-found case, which is the property this rule is for.
    """
    if customer is None or customer.organization_id != principal.organization_id:
        return False
    if principal.is_salesperson:
        return customer.assigned_user_id == principal.user_id
    return True


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
