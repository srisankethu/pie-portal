"""Organization administration — users, roles, and approval policy.

The owner is the super admin: only an owner creates users, changes a role,
resets a password, or edits the approval policy. A sales manager may *see* the
team and the policy, because a manager who cannot tell who reports to them or
what they are allowed to approve cannot do the job — but they cannot grant
themselves authority they were not given.

Two rules the endpoints below enforce rather than merely document:

**An organization cannot be left without an owner.** Demoting or deactivating
the last active owner is refused. The alternative is a tenant nobody can
administer, recoverable only from the database.

**Nobody edits their own role.** Not even the owner. An account takeover that
also gets to promote itself is a different class of problem from one that does
not, and the check costs one line.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status as http
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import approvals
from ..authz import Principal, current_principal, require_manager_or_owner, require_owner
from ..commercial.config import load_commercial_thresholds
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..passwords import (
    generate_password,
    hash_password,
    password_problem,
    verify_password,
)

log = logging.getLogger("pie_portal.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _user_dict(u: models.User, names: dict[str, str]) -> dict:
    return {
        "user_id": u.user_id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "active": u.active,
        "has_password": bool(u.password_hash),
        "must_change_password": u.must_change_password,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "created_by": names.get(u.created_by_user_id or "", None),
        "role_changed_by": names.get(u.role_changed_by_user_id or "", None),
        "role_changed_at": (u.role_changed_at.isoformat()
                            if u.role_changed_at else None),
    }


def _org_users(session: Session, org: str) -> list[models.User]:
    return list(session.scalars(
        select(models.User).where(models.User.organization_id == org)
        .order_by(models.User.name)))


def _active_owners(session: Session, org: str) -> list[models.User]:
    return [u for u in _org_users(session, org)
            if u.role == Role.OWNER.value and u.active]


# ── users ───────────────────────────────────────────────────────────────────
@router.get("/users")
def list_users(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    rows = _org_users(session, principal.organization_id)
    names = {u.user_id: u.name for u in rows}
    return {
        "users": [_user_dict(u, names) for u in rows],
        "roles": [r.value for r in Role],
        "can_manage": principal.role is Role.OWNER,
    }


class CreateUser(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    role: Role
    # Omit to have one generated and returned once. A password the creator
    # invents and emails is usually worse than one the system generates.
    password: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        """Shape only, deliberately. Full RFC validation needs a dependency and
        rejects addresses that work; the real check is whether the person can
        receive the temporary password, which no regex can answer."""
        v = v.strip().lower()
        local, _, domain = v.partition("@")
        if not local or not domain or "." not in domain or " " in v:
            raise ValueError("That does not look like an email address")
        return v


@router.post("/users", status_code=http.HTTP_201_CREATED)
def create_user(
    body: CreateUser,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Create a user. Owner only.

    The temporary password is returned in this response and never again — it is
    stored only as a hash. The account is flagged to change it at first sign-in.
    """
    email = body.email
    if session.scalar(select(models.User).where(models.User.email == email)):
        raise HTTPException(http.HTTP_409_CONFLICT,
                            "An account already exists with that email")

    password = body.password or generate_password()
    problem = password_problem(password)
    if problem:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, problem)

    user = models.User(
        organization_id=principal.organization_id, email=email, name=body.name.strip(),
        role=body.role.value, active=True,
        password_hash=hash_password(password), must_change_password=True,
        created_by_user_id=principal.user_id)
    session.add(user)
    session.flush()
    log.info("user created org=%s role=%s by=%s", principal.organization_id,
             body.role.value, principal.user_id)
    return {
        "user": _user_dict(user, {principal.user_id: principal.name}),
        # Shown once, in the response to the owner who created the account.
        "temporary_password": password,
    }


class UpdateUser(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    role: Optional[Role] = None
    active: Optional[bool] = None


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUser,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    user = session.get(models.User, user_id)
    if user is None or user.organization_id != principal.organization_id:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "User not found")

    if body.role is not None and body.role.value != user.role:
        if user.user_id == principal.user_id:
            raise HTTPException(http.HTTP_400_BAD_REQUEST,
                                "You cannot change your own role")
        if (user.role == Role.OWNER.value
                and len(_active_owners(session, principal.organization_id)) <= 1):
            raise HTTPException(
                http.HTTP_409_CONFLICT,
                "This is the only owner — promote someone else before changing it")
        user.role = body.role.value
        user.role_changed_by_user_id = principal.user_id
        user.role_changed_at = datetime.now(timezone.utc)
        log.info("role changed org=%s user=%s to=%s by=%s", principal.organization_id,
                 user.user_id, body.role.value, principal.user_id)

    if body.active is not None and body.active != user.active:
        if user.user_id == principal.user_id and not body.active:
            raise HTTPException(http.HTTP_400_BAD_REQUEST,
                                "You cannot deactivate your own account")
        if (not body.active and user.role == Role.OWNER.value
                and len(_active_owners(session, principal.organization_id)) <= 1):
            raise HTTPException(http.HTTP_409_CONFLICT,
                                "This is the only active owner")
        user.active = body.active

    if body.name is not None and body.name.strip():
        user.name = body.name.strip()

    session.flush()
    names = {u.user_id: u.name for u in _org_users(session, principal.organization_id)}
    return _user_dict(user, names)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Issue a new temporary password. Owner only; returned once."""
    user = session.get(models.User, user_id)
    if user is None or user.organization_id != principal.organization_id:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "User not found")
    password = generate_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    session.flush()
    log.info("password reset org=%s user=%s by=%s", principal.organization_id,
             user.user_id, principal.user_id)
    return {"user_id": user.user_id, "temporary_password": password}


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/password")
def change_own_password(
    body: ChangePassword,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Change your own password. Any role — this is not an admin action.

    The current password is required even though the caller is already
    authenticated: it is what stops an unattended session from being turned into
    permanent access.
    """
    user = session.get(models.User, principal.user_id)
    if user is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "User not found")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(http.HTTP_403_FORBIDDEN, "Current password is incorrect")
    problem = password_problem(body.new_password)
    if problem:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, problem)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    session.flush()
    return {"ok": True}


# ── approval policy ─────────────────────────────────────────────────────────
def _policy_dict(p: models.OrgPolicy) -> dict:
    return {
        "require_approval_for_quotes": p.require_approval_for_quotes,
        "require_approval_below_review_floor": p.require_approval_below_review_floor,
        "below_cost_requires_owner": p.below_cost_requires_owner,
        "allow_self_approval": p.allow_self_approval,
        "escalation_creates_approval": p.escalation_creates_approval,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/policy")
def get_policy(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    th = load_commercial_thresholds()
    return {
        "policy": _policy_dict(approvals.get_policy(session,
                                                    principal.organization_id)),
        "can_manage": principal.role is Role.OWNER,
        # The margin policy that decides *what* trips an approval, shown
        # alongside the rules about *who* signs it off. Read-only here: these
        # are environment configuration, and a threshold silently editable from
        # a settings screen is a threshold nobody can reproduce a number
        # against.
        "thresholds": {
            "version": th.version,
            "target_margin_default": th.target_margin_default,
            "target_margin_by_family": dict(th.target_margin_by_family),
            "min_margin": th.min_margin,
            "margin_floor": th.margin_floor,
            "sales_discretion_band": th.sales_discretion_band,
            "quantity_band_edges": list(th.quantity_band_edges),
            "min_quote_exception_impact_rupees": th.min_quote_exception_impact_rupees,
            "recent_days": th.recent_days,
            "min_transactions": th.min_transactions,
            "min_peer_customers": th.min_peer_customers,
        },
    }


class UpdatePolicy(BaseModel):
    require_approval_for_quotes: Optional[bool] = None
    require_approval_below_review_floor: Optional[bool] = None
    below_cost_requires_owner: Optional[bool] = None
    allow_self_approval: Optional[bool] = None
    escalation_creates_approval: Optional[bool] = None


@router.patch("/policy")
def update_policy(
    body: UpdatePolicy,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    policy = approvals.get_policy(session, principal.organization_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(policy, field, value)
    policy.updated_by_user_id = principal.user_id
    session.flush()
    log.info("policy updated org=%s by=%s", principal.organization_id,
             principal.user_id)
    return _policy_dict(policy)
