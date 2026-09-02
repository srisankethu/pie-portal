"""Organization administration — members, roles, and approval policy.

The owner is the super admin: only an owner adds a member, changes a role,
resets a password, or edits the approval policy. A sales manager may *see* the
team and the policy, because a manager who cannot tell who reports to them or
what they are allowed to approve cannot do the job — but they cannot grant
themselves authority they were not given.

Two rules the endpoints below enforce rather than merely document:

**An organization cannot be left without an owner.** Demoting, removing or
deactivating the last active owner is refused. The alternative is a tenant
nobody can administer, recoverable only from the database. The rule lives in
``memberships.py`` rather than here, so that it holds for every caller instead
of for the endpoint that remembered it — this router is the HTTP mapping of a
decision made there.

**What these endpoints manage is the membership, not the person.** Removing
somebody ends their grant to *this* organization and touches neither their
login nor any workspace they hold elsewhere; the organization keeps every row
it owns, because nothing in the tenant's data is keyed on a user. That is the
John-leaves-Acme case, and it is a status change on one row.

**Nobody edits their own role.** Not even the owner. An account takeover that
also gets to promote itself is a different class of problem from one that does
not, and the check costs one line.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     Response, status as http)
from pydantic import BaseModel, Field, create_model, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock, approvals, memberships, quote_fields
from ..authz import (Principal, current_principal, open_session,
                     require_manager_or_owner, require_owner,
                     revoke_all_sessions, set_session_cookie)
from ..commercial import policy as commercial_policy
from ..db import get_session
from ..domain import models
from ..domain.enums import MembershipStatus, Role
from ..passwords import (
    generate_password,
    hash_password,
    password_problem,
    verify_password,
)

log = logging.getLogger("pie_portal.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _member_dict(u: models.User, m: models.OrganizationMembership,
                 names: dict[str, str]) -> dict:
    """One member, as the members screen reads them.

    A join of the two halves the split created: the identity (name, address,
    whether they can sign in at all) and the grant (role in *this* workspace,
    its status, who made it and who last changed it). Both are needed to answer
    "can this person open this organization today", and neither answers it
    alone.

    ``role`` and ``role_changed_*`` come from the membership, never from
    ``users.role`` — that column is the deprecated mirror, and reading it here
    would show somebody their role in a *different* workspace.
    """
    return {
        "user_id": u.user_id,
        "email": u.email,
        "name": u.name,
        "role": m.role,
        # Whether the login works at all, anywhere. Kept under its old name
        # because it is the same fact it always was and the grid column that
        # reads it has not changed meaning.
        "active": u.active,
        # Whether *this* organization admits them. A deactivated login with a
        # live membership and a live login with an ended one both read as "no
        # access here", and the screen needs to be able to say which.
        "membership_status": m.status,
        "has_password": bool(u.password_hash),
        "must_change_password": u.must_change_password,
        "last_login_at": clock.iso(u.last_login_at),
        "created_at": clock.iso(u.created_at),
        "joined_at": clock.iso(m.created_at),
        "created_by": names.get(u.created_by_user_id or "", None),
        "invited_by": names.get(m.invited_by_user_id or "", None),
        "role_changed_by": names.get(m.role_changed_by_user_id or "", None),
        "role_changed_at": clock.iso(m.role_changed_at),
    }


def _members(session: Session, org: str) -> list[tuple[models.User,
                                                       models.OrganizationMembership]]:
    """Everyone with a membership of this organization, ended ones included.

    Ended memberships are listed rather than filtered, because an owner asking
    "who has had access to our numbers" is asking about them specifically. The
    screen renders them greyed with a status chip; `memberships_of` is the
    filtered form the authorization paths use.
    """
    rows = memberships.memberships_of(session, org, active_only=False)
    out = []
    for m in rows:
        user = session.get(models.User, m.user_id)
        if user is not None:
            out.append((user, m))
    out.sort(key=lambda pair: pair[0].name or "")
    return out


def _member_names(session: Session, org: str) -> dict[str, str]:
    return {u.user_id: u.name for u, _ in _members(session, org)}


# ── members ─────────────────────────────────────────────────────────────────
@router.get("/users")
def list_users(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """The organization's members. Path kept: the client contract has not moved."""
    rows = _members(session, principal.organization_id)
    names = {u.user_id: u.name for u, _ in rows}
    return {
        "users": [_member_dict(u, m, names) for u, m in rows],
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
    """Add a member. Owner only.

    Two things happen and they are separable on purpose: an identity is created
    if this address has never signed in here before, and a membership of *this*
    organization is granted. Only the second is what "adding somebody" means —
    and the day this grows an invitation flow, only the second will happen for
    an address that already has an account elsewhere on the platform.

    Today an address that exists anywhere is still refused, and that refusal is
    honest rather than lazy: emails are unique across the platform, so the
    account it names might belong to another tenant entirely, and silently
    attaching a stranger's login to this workspace because somebody typed their
    address is the wrong half of the ambiguity to resolve automatically.

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
    membership = memberships.add_member(
        session, organization_id=principal.organization_id, user_id=user.user_id,
        role=body.role, invited_by_user_id=principal.user_id)
    log.info("member added org=%s role=%s by=%s", principal.organization_id,
             body.role.value, principal.user_id)
    return {
        "user": _member_dict(user, membership, {principal.user_id: principal.name}),
        # Shown once, in the response to the owner who created the account.
        "temporary_password": password,
    }


class UpdateUser(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    role: Optional[Role] = None
    #: Whether this identity can sign in at all. Unchanged in meaning.
    active: Optional[bool] = None
    #: Whether this organization admits them — the membership. Separate from
    #: ``active`` because they are separate acts: removing John from Acme when
    #: he moves to another company is not the same as closing his login, and a
    #: single flag could not express "left Acme, still owns his own workspace".
    member: Optional[bool] = None


def _require_member(session: Session, principal: Principal,
                    user_id: str) -> tuple[models.User, models.OrganizationMembership]:
    """The user and their membership *here*, or 404.

    The membership lookup is the authorization, not a detail of the read: a
    user id from another tenant resolves to no membership in this organization
    and gets the same answer as one that does not exist, which is the property
    that keeps an id from being an enumeration oracle.
    """
    user = session.get(models.User, user_id)
    membership = memberships.membership_for(session, user_id,
                                            principal.organization_id)
    if user is None or membership is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "User not found")
    return user, membership


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUser,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    user, membership = _require_member(session, principal, user_id)

    if body.role is not None and body.role.value != membership.role:
        if user.user_id == principal.user_id:
            raise HTTPException(http.HTTP_400_BAD_REQUEST,
                                "You cannot change your own role")
        try:
            memberships.set_role(session, membership, role=body.role,
                                 changed_by_user_id=principal.user_id)
        except memberships.MembershipRefused as e:
            raise HTTPException(http.HTTP_409_CONFLICT, str(e)) from e

    if body.member is not None:
        if user.user_id == principal.user_id:
            raise HTTPException(
                http.HTTP_400_BAD_REQUEST,
                "You cannot remove your own membership")
        try:
            if body.member:
                memberships.reinstate(
                    session, membership,
                    role=Role(membership.role), reinstated_by_user_id=principal.user_id)
            else:
                memberships.remove_member(session, membership,
                                          removed_by_user_id=principal.user_id)
        except memberships.MembershipRefused as e:
            raise HTTPException(http.HTTP_409_CONFLICT, str(e)) from e

    if body.active is not None and body.active != user.active:
        if user.user_id == principal.user_id and not body.active:
            raise HTTPException(http.HTTP_400_BAD_REQUEST,
                                "You cannot deactivate your own account")
        # Counted through `memberships.active_owners`, which requires the login
        # to work *and* the membership to be live — so this refuses exactly when
        # switching the account off would leave nobody able to administer the
        # tenant, and not merely when an owner-shaped row exists.
        if (not body.active and membership.role == Role.OWNER.value
                and membership.status == MembershipStatus.ACTIVE.value
                and len(memberships.active_owners(
                    session, principal.organization_id)) <= 1):
            raise HTTPException(http.HTTP_409_CONFLICT,
                                "This is the only active owner")
        user.active = body.active

    if body.name is not None and body.name.strip():
        user.name = body.name.strip()

    session.flush()
    return _member_dict(user, membership,
                        _member_names(session, principal.organization_id))


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Issue a new temporary password. Owner only; returned once."""
    user, _ = _require_member(session, principal, user_id)
    password = generate_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    # Retires whatever sessions this account had open. An owner resetting a
    # password because it may be compromised should not leave the compromised
    # session working.
    user.password_changed_at = clock.now()
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
    request: Request,
    response: Response,
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
    user.password_changed_at = clock.now()
    session.flush()
    # End every session this account had, this one included. `password_changed_at`
    # already retires their tokens, but that check lives in one function and only
    # covers tokens; revoking the rows is the same intent written where the
    # session list and every future transport can see it. This is what makes
    # "change my password" the answer to "someone else is signed in as me".
    revoke_all_sessions(session, user.user_id)
    # A fresh session, because the lines above just retired the one this request
    # arrived with. Without handing one back, changing your own password would
    # sign you out — and on the forced-change path that is a loop: the only thing
    # the old token could still reach was the change it had already made.
    token, _row = open_session(session, user, request.headers.get("user-agent"))
    # Committed before the token leaves, for the reason `auth.login` gives: a
    # token naming an uncommitted row is a credential that does not work.
    session.commit()
    set_session_cookie(response, token)
    return {"ok": True, "token": token}


# ── approval policy ─────────────────────────────────────────────────────────
def _policy_dict(p: models.OrgPolicy) -> dict:
    return {
        "require_approval_for_quotes": p.require_approval_for_quotes,
        "require_approval_below_review_floor": p.require_approval_below_review_floor,
        "below_cost_requires_owner": p.below_cost_requires_owner,
        "allow_self_approval": p.allow_self_approval,
        "escalation_creates_approval": p.escalation_creates_approval,
        "managers_may_edit_any_quote": p.managers_may_edit_any_quote,
        "updated_at": clock.iso(p.updated_at),
    }


@router.get("/policy")
def get_policy(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Approval policy (who signs off) and margin policy (what trips it).

    The margin policy is editable by an owner. It used to be read-only on the
    grounds that a silently-editable threshold cannot be reproduced against —
    but that is answered by the version hash rather than by refusing to edit:
    every metric row, signal and quote snapshot records the threshold version
    that produced it, and editing produces a new one.
    """
    org = principal.organization_id
    th = commercial_policy.load_for_org(session, org)
    return {
        "policy": _policy_dict(approvals.get_policy(session, org)),
        "can_manage": principal.role is Role.OWNER,
        "margin_policy": commercial_policy.describe(session, org),
        # Analysis internals, shown for context and deliberately not editable:
        # window lengths and evidence floors are not preferences, and moving
        # them silently changes what "eroding" means.
        "fixed": {
            "recent_days": th.recent_days,
            "previous_days": th.previous_days,
            "historical_lookback_days": th.historical_lookback_days,
            "min_transactions": th.min_transactions,
            "min_peer_customers": th.min_peer_customers,
            "min_cost_coverage": th.min_cost_coverage,
            "peer_recency_days": th.peer_recency_days,
        },
    }


#: The Python type each policy kind arrives as. Keyed on ``policy._kind`` so
#: there is one answer to "what shape is this field" for the screen that renders
#: it, the parser that coerces it and the schema that accepts it.
_PATCH_TYPE: dict[str, type] = {
    "ratio": float,
    # A ratio the owner is allowed not to have decided. The same wire type as a
    # ratio; what differs is that the screen must render it empty rather than
    # as 0%, and that ``policy._coerce`` keeps a blank as None instead of
    # turning it into zero. See ``policy._NULLABLE_RATES``.
    "optional_ratio": float,
    "money": float,
    "days": int,
    "flag": bool,
    "band_edges": list[int],
    "family_margins": dict[str, float],
    # (entity, financial year, amount) rows. Strings all the way across,
    # including the amount, and that is the point rather than an oversight: it
    # is money, so a JSON number would already have been through a float before
    # this line ever saw it. Requiring a string means the figure an owner read
    # off an audited account is the figure that gets stored. See
    # ``CommercialThresholds.retained_pat``.
    "retained_pat": list[list[str]],
}


def _update_margin_policy_model() -> type[BaseModel]:
    """Build the PATCH body from ``policy.EDITABLE``.

    This was a hand-written list of nine fields, and ``EDITABLE`` had grown to
    fourteen. Pydantic drops unknown keys silently, so the five that were never
    added — the rounding increment, both carrying-cost fields and both stock-age
    thresholds — rendered on the settings screen, accepted an edit, reported
    success and changed nothing. Nothing failed; the value simply did not move.

    Deriving it removes the possibility. Adding a field to ``EDITABLE`` now
    makes it editable end to end, which is what putting it in a list called
    EDITABLE was always supposed to mean.
    """
    fields: dict[str, Any] = {
        # Optional means "leave alone", not "clear" — see `clear` below.
        name: (Optional[_PATCH_TYPE[commercial_policy._kind(name)]], None)
        for name in commercial_policy.EDITABLE
    }
    # Fields sent as null normally mean "leave alone". Listing them here says
    # "clear this override" instead — otherwise a reset would be impossible.
    fields["clear"] = (list[str], Field(default_factory=list))
    return create_model(
        "UpdateMarginPolicy",
        __doc__="Any subset of the editable fields. Null clears an override.",
        **fields,
    )


UpdateMarginPolicy = _update_margin_policy_model()


@router.patch("/margin-policy")
def update_margin_policy(
    body: UpdateMarginPolicy,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Edit the margin policy. Owner only.

    Validated as a whole, not as a diff: one edit that is fine alone can invert
    the floor ladder once combined with what is already saved, and only the
    combination is what quotes are judged against.
    """
    updates: dict = {k: v for k, v in body.model_dump(exclude_none=True).items()
                     if k != "clear"}
    for field in body.clear:
        updates[field] = None
    if not updates:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, "Nothing to change")

    try:
        th = commercial_policy.save_for_org(session, principal.organization_id,
                                            updates, principal.user_id)
    except commercial_policy.PolicyError as e:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, str(e)) from e

    log.info("margin policy updated org=%s by=%s version=%s",
             principal.organization_id, principal.user_id, th.version)
    return {
        "margin_policy": commercial_policy.describe(session,
                                                    principal.organization_id),
        # Surfaced because it is the honest consequence of an edit: figures
        # already computed carry the old version until they are recomputed.
        "note": ("Saved. Existing metrics keep the version they were computed "
                 "with until the next recompute."),
    }


@router.get("/margin-policy/backtest")
def backtest_margin_policy(
    min_margin: float = Query(..., ge=0, lt=1,
                              description="Variant approval floor as a ratio "
                                          "(0.14, not 14)."),
    margin_floor: Optional[float] = Query(None, ge=0, lt=1),
    since: Optional[date] = Query(None),
    until: Optional[date] = Query(None),
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """What a different approval floor would have done to quotes already priced.

    Read-only, writes nothing, and saves nothing — this is the question an owner
    should be able to ask *before* the PATCH above, not after. It was reachable
    only as ``python -m app.commercial.backtest``, which meant the one edit on
    the settings screen with a blast radius across every future quote was also
    the one edit nobody could model first.

    **Owner only, and the reason is arithmetic rather than convention.**
    ``shortfall_to_new_floor`` is ``(floor − price) × quantity`` and the caller
    supplies the margin, so cost falls straight out:
    ``cost = (price + shortfall/qty) × (1 − min_margin)``. That is not a
    boundary a caller has to walk — it is a closed form, exact, from one
    response. §1 permits two residual boundaries for a salesperson and this is
    not one of them; it is full disclosure, and it is correct here only because
    an owner may see cost outright. **Do not widen this to a role that may not.**
    Matching ``update_margin_policy`` also keeps read and write on the same
    person: a manager who could model a floor change still could not make one.

    ``min_margin`` is bounded ``lt=1`` structurally, not as policy — the floor is
    ``cost / (1 − margin)`` and 1.0 divides by zero. The bound also catches the
    mistake the CLI help warns about, where 14 is passed for 14% and every line
    in the book comes back newly gated.

    Not plan-gated, with the rest of ``/admin``. Margin floors and approvals are
    the free Quote Desk, this reads the ``QuoteDecision`` rows that desk writes,
    and it tunes that desk's own guardrail — gating the tuning of a free feature
    behind the paid plan is a commercial choice nobody has made, and this is not
    the file to make it in.

    Unbounded by default. ``QuoteDecision`` holds only quotes priced *in* PIE,
    so for most organizations this is a small table and a full replay is the
    answer an owner actually wants; ``since``/``until`` are there for the book
    where it is not.
    """
    from ..commercial import backtest

    report = backtest.run(session, principal.organization_id,
                          min_margin=min_margin, margin_floor=margin_floor,
                          since=since, until=until)
    return report.to_dict()


class UpdatePolicy(BaseModel):
    require_approval_for_quotes: Optional[bool] = None
    require_approval_below_review_floor: Optional[bool] = None
    below_cost_requires_owner: Optional[bool] = None
    allow_self_approval: Optional[bool] = None
    escalation_creates_approval: Optional[bool] = None
    managers_may_edit_any_quote: Optional[bool] = None


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


# ── quote fields ────────────────────────────────────────────────────────────
class QuoteFieldSpec(BaseModel):
    key: Optional[str] = None
    label: str
    kind: str = "TEXT"
    required: bool = False
    choices: list[str] = []


class ReplaceQuoteFields(BaseModel):
    fields: list[QuoteFieldSpec]


@router.get("/quote-fields")
def get_quote_fields(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """The quote-level fields this organization asks for and which are
    mandatory. Managers read; an owner edits (``PUT``)."""
    return {
        "fields": [quote_fields.to_dict(d) for d in
                   quote_fields.definitions_for(session, principal.organization_id)],
        "kinds": list(quote_fields.KINDS),
        "can_manage": principal.role is Role.OWNER,
    }


@router.put("/quote-fields")
def replace_quote_fields(
    body: ReplaceQuoteFields,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Make the organization's fields exactly this list, in this order. A
    field left out is hidden rather than deleted — a draft may hold a value
    under it (``quote_fields.replace_definitions``)."""
    try:
        rows = quote_fields.replace_definitions(
            session, principal.organization_id,
            [f.model_dump() for f in body.fields])
    except quote_fields.FieldError as e:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, str(e))
    log.info("quote fields updated org=%s by=%s n=%d", principal.organization_id,
             principal.user_id, len(rows))
    return {"fields": [quote_fields.to_dict(d) for d in rows],
            "kinds": list(quote_fields.KINDS), "can_manage": True}
