"""Who may open which organization, and as what. The one authority.

``authz.py`` answers "is this request carrying a live session, and whose";
``entitlements.py`` answers "what may this organization use". This module
answers the question between them — **which organizations can this identity
open, and with what role in each** — and it is the only place that answers it.

Before it, the answer was two columns on ``users``: ``organization_id`` and
``role``. That shape made three things unsayable and one thing unsafe.

- Unsayable: a person in two workspaces; a person who owns one and sells in
  another; the history of a seat that changed hands.
- Unsafe: the organization a request acted for came from the *token*, and the
  check was ``user.organization_id == token_org``. That compares a claim
  against a column, which is fine exactly while a user has one organization,
  and stops being a check at all the moment they can have two. The membership
  is what makes the token's organization a claim that has to be *found* in a
  row before it is honoured — see ``authz.load_principal``.

**Every function here is about grants, none is about identity.** Creating the
person is ``seed``/``provision_org``; ending their ability to sign in anywhere
is ``users.active``. This module opens and closes doors.

**The home organization.** ``users.organization_id`` still exists and still has
work to do (the RLS policy on ``users`` is written against it, and sign-in has
to announce a tenant before it can read anything). It means "where this identity
row lives" — `add_member` fills it in for a user who has no home yet, and
nothing here ever moves it afterwards, because the policy's write half
deliberately forbids one tenant re-homing an account into another. Where a
sign-in *lands* is a separate question with a separate answer
(``routers.organizations.default_organization``), and it is a membership
question. Nothing authorizes on the column.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .domain import models
from .domain.enums import MembershipStatus, Role

log = logging.getLogger("pie_portal.memberships")


class MembershipRefused(ValueError):
    """This grant cannot be made. The message is written for the person asking."""


# ── reading ──────────────────────────────────────────────────────────────────
def membership_for(session: Session, user_id: str,
                   organization_id: str) -> Optional[models.OrganizationMembership]:
    """This user's membership of this organization, whatever its status.

    Status is deliberately *not* filtered here. A caller deciding access wants
    `active_membership_for`; a caller re-adding somebody wants to find the
    REMOVED row so it can refuse to write a second one for the same pair, and a
    lookup that hid it would turn the unique constraint into an integrity error
    at flush time instead of a sentence somebody can read.
    """
    return session.scalar(
        select(models.OrganizationMembership).where(
            models.OrganizationMembership.user_id == user_id,
            models.OrganizationMembership.organization_id == organization_id))


def active_membership_for(session: Session, user_id: str,
                          organization_id: str) -> Optional[models.OrganizationMembership]:
    """The membership that grants access, or None. **The authorization lookup.**

    None means no access, and the three ways to get it are indistinguishable to
    the caller on purpose: no membership was ever made, one was made and ended,
    or one exists but has not been accepted. A refusal that said which would
    tell a stranger whether an organization has an account for an address.
    """
    row = membership_for(session, user_id, organization_id)
    return row if row is not None and row.status == MembershipStatus.ACTIVE.value else None


def memberships_of(session: Session, organization_id: str, *,
                   active_only: bool = True) -> list[models.OrganizationMembership]:
    """Everyone in one organization. The members screen, and every owner check."""
    stmt = select(models.OrganizationMembership).where(
        models.OrganizationMembership.organization_id == organization_id)
    if active_only:
        stmt = stmt.where(
            models.OrganizationMembership.status == MembershipStatus.ACTIVE.value)
    return list(session.scalars(stmt.order_by(
        models.OrganizationMembership.created_at)))


def users_in(session: Session, organization_id: str) -> list[models.User]:
    """The people in this organization, as ``User`` rows. Active grants only.

    **The one definition of "who works here".** It was written out five times
    as ``select(User).where(User.organization_id == org)`` — in the ownership
    map, the approval name lookup, two insight screens and the setup
    checklist — and every one of those became subtly wrong the moment a person
    could belong to two organizations: their identity row is filed under one of
    them, so the other's screens simply lost their name.

    Ordered by name, because every caller that renders this renders it sorted
    and the ones that did not were sorting it again themselves.
    """
    rows = memberships_of(session, organization_id)
    users = [u for u in (session.get(models.User, m.user_id) for m in rows)
             if u is not None]
    users.sort(key=lambda u: (u.name or "").lower())
    return users


def active_owners(session: Session, organization_id: str) -> list[models.OrganizationMembership]:
    """Active owner memberships whose *user* can still sign in.

    Both halves, because either one alone is a way to strand a tenant: an owner
    membership held by a deactivated login administers nothing, and an active
    login with no owner membership administers nothing either. The
    last-owner refusals below count this list, so it has to mean "people who
    can actually administer this organization right now".
    """
    rows = memberships_of(session, organization_id)
    owners = []
    for row in rows:
        if row.role != Role.OWNER.value:
            continue
        user = session.get(models.User, row.user_id)
        if user is not None and user.active:
            owners.append(row)
    return owners


def organizations_for(session: Session,
                      user_id: str) -> list[models.OrganizationMembership]:
    """Every workspace this identity can open. Ordered oldest first.

    Under row-level security this returns only the memberships of the tenant
    the connection has announced, which for the request that asks "where else
    can I go" is exactly the wrong answer — so the HTTP surface goes through
    ``tenancy.user_memberships`` and falls back to this. Same split, and for
    the same reason, as ``tenancy.email_registered``: one authority per
    dialect, with the ordinary query clearly the fallback.
    """
    return list(session.scalars(
        select(models.OrganizationMembership).where(
            models.OrganizationMembership.user_id == user_id,
            models.OrganizationMembership.status == MembershipStatus.ACTIVE.value)
        .order_by(models.OrganizationMembership.created_at)))


def role_in(session: Session, user_id: str, organization_id: str) -> Optional[Role]:
    """The role this user holds here, or None if they hold none.

    Returns None rather than a default for an unrecognised stored value: a role
    nobody can parse is not a narrower role, it is a row somebody has to look
    at, and answering SALESPERSON would quietly grant a corrupted membership
    the run of the quote desk.
    """
    row = active_membership_for(session, user_id, organization_id)
    if row is None:
        return None
    try:
        return Role(row.role)
    except ValueError:
        log.error("membership %s holds unrecognised role %r — refusing access",
                  row.membership_id, row.role)
        return None


# ── writing ──────────────────────────────────────────────────────────────────
def _mirror_home_role(session: Session, user_id: str) -> None:
    """Keep the deprecated ``users.role`` equal to the home membership's role.

    One writer, named so the grep for it lands in one place. See the column's
    comment in ``domain/models.py`` for why the copy exists at all and when it
    goes; the short version is that it is a rollback affordance with a stated
    expiry, and a mirror with two writers would be the drift that makes such
    an affordance worse than nothing.
    """
    user = session.get(models.User, user_id)
    if user is None:
        return
    row = active_membership_for(session, user_id, user.organization_id)
    if row is not None:
        user.role = row.role


def add_member(session: Session, *, organization_id: str, user_id: str,
               role: Role, invited_by_user_id: Optional[str] = None,
               status: MembershipStatus = MembershipStatus.ACTIVE,
               ) -> models.OrganizationMembership:
    """Grant this identity access to this organization. The only way in.

    Refuses a second membership for a pair that already has one, in either
    direction: an ACTIVE row means the grant is already made, and a REMOVED one
    means somebody is re-admitting a person who was let go, which is a
    deliberate act and gets `reinstate` rather than a silently-created
    duplicate the unique constraint would have caught anyway with a worse
    message.

    Fills in the user's home organization when they have none yet — a user
    created as part of provisioning an organization, or (later) an invitation
    accepted by somebody whose first workspace this is.
    """
    existing = membership_for(session, user_id, organization_id)
    if existing is not None:
        raise MembershipRefused(
            "That account is already a member of this organization."
            if existing.status == MembershipStatus.ACTIVE.value else
            "That account was a member of this organization before. "
            "Reinstate the existing membership rather than adding a second.")

    row = models.OrganizationMembership(
        organization_id=organization_id, user_id=user_id, role=role.value,
        status=status.value, invited_by_user_id=invited_by_user_id,
        created_at=clock.now(), updated_at=clock.now())
    session.add(row)

    user = session.get(models.User, user_id)
    if user is not None and not (user.organization_id or "").strip():
        user.organization_id = organization_id
    session.flush()
    _mirror_home_role(session, user_id)
    session.flush()
    log.info("membership added org=%s user=%s role=%s by=%s", organization_id,
             user_id, role.value, invited_by_user_id or "-")
    return row


def ensure_member(session: Session, *, organization_id: str, user_id: str,
                  role: Role) -> models.OrganizationMembership:
    """`add_member`, for a caller that may be running for the second time.

    The seeders and both provisioning CLIs are idempotent by design — running
    them again converges rather than fails — and `add_member`'s refusal is
    correct for a person typing an address into a form and wrong for a script
    that is meant to be re-runnable. This is the same grant with the
    already-granted case as a no-op.

    It does **not** correct a role that has since been changed. Re-running the
    seed must not quietly demote somebody an owner promoted last week; the
    grant is ensured, not enforced.
    """
    existing = membership_for(session, user_id, organization_id)
    if existing is not None:
        return existing
    return add_member(session, organization_id=organization_id, user_id=user_id,
                      role=role)


def set_role(session: Session, membership: models.OrganizationMembership, *,
             role: Role, changed_by_user_id: str) -> models.OrganizationMembership:
    """Change what somebody may do here. Refuses to remove the last owner.

    The refusal is the same one ``routers/admin`` used to make against
    ``users.role``, moved here so that the rule holds for every caller rather
    than for the one endpoint that remembered it. An organization with no owner
    is a tenant nobody can administer, recoverable only from the database.
    """
    if membership.role == role.value:
        return membership
    if (membership.role == Role.OWNER.value
            and len(active_owners(session, membership.organization_id)) <= 1):
        raise MembershipRefused(
            "This is the only owner — promote someone else before changing it")
    membership.role = role.value
    membership.role_changed_by_user_id = changed_by_user_id
    membership.role_changed_at = clock.now()
    session.flush()
    _mirror_home_role(session, membership.user_id)
    session.flush()
    log.info("membership role changed org=%s user=%s to=%s by=%s",
             membership.organization_id, membership.user_id, role.value,
             changed_by_user_id)
    return membership


def remove_member(session: Session, membership: models.OrganizationMembership, *,
                  removed_by_user_id: str) -> models.OrganizationMembership:
    """End a membership. The organization keeps everything.

    This is the John-leaves-Acme path, and what it does *not* touch is the
    point: no customer, quote, decision or signal is keyed on a user, so
    nothing here cascades into the tenant's data. It closes one door.

    Refuses on the last owner, for the reason `set_role` gives.

    **``users.organization_id`` is deliberately left alone**, including for
    somebody whose home membership this was and who still holds another
    workspace. Repointing it looks tidier and is not available: the write half
    of the row-level-security policy on ``users`` compares the new value
    against the announced tenant, so an organization removing a member cannot
    re-home their account — which is exactly the property that policy is for. A
    tenant that could move an identity row into another tenant would be a
    tenant that could take an account.

    The column being briefly stale costs nothing, because nothing authorizes on
    it: ``routers.organizations.default_organization`` looks for an ACTIVE
    membership at home and falls through to another one when there is none, so
    the next sign-in lands in a workspace this person actually holds.
    """
    if (membership.role == Role.OWNER.value
            and len(active_owners(session, membership.organization_id)) <= 1):
        raise MembershipRefused(
            "This is the only owner — promote someone else before removing them")

    membership.status = MembershipStatus.REMOVED.value
    membership.removed_by_user_id = removed_by_user_id
    membership.removed_at = clock.now()
    session.flush()
    log.info("membership removed org=%s user=%s by=%s",
             membership.organization_id, membership.user_id, removed_by_user_id)
    return membership


def reinstate(session: Session, membership: models.OrganizationMembership, *,
              role: Role, reinstated_by_user_id: str) -> models.OrganizationMembership:
    """Let a former member back in, on a role somebody chose again.

    A separate verb from `add_member` so that re-admitting a person is a
    decision in the log rather than a side effect of typing their address into
    the same form. The role is required rather than restored: a seat that comes
    back should come back on purpose, not on whatever it held the day it ended.
    """
    membership.status = MembershipStatus.ACTIVE.value
    membership.removed_by_user_id = None
    membership.removed_at = None
    membership.role = role.value
    membership.role_changed_by_user_id = reinstated_by_user_id
    membership.role_changed_at = clock.now()
    session.flush()
    _mirror_home_role(session, membership.user_id)
    session.flush()
    log.info("membership reinstated org=%s user=%s role=%s by=%s",
             membership.organization_id, membership.user_id, role.value,
             reinstated_by_user_id)
    return membership
