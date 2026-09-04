"""The vendor's console: PIE operating the platform, not a tenant using it.

Every endpoint here already existed as a subcommand. ``app.contact list`` is
the enquiry queue, ``app.entitlements requests`` is the three ask-queues,
``app.entitlements set-plan`` grants. They work and they need an SSH session,
which is what makes a console worth building at all — and it is why this router
computes nothing of its own. It maps HTTP onto the modules those commands
already call, exactly as CLAUDE.md §3 requires of a router: no arithmetic, no
policy, no second implementation of a queue that would then disagree with the
one an operator reads over the wire.

**Two scopes, and the boundary between them is enforced by the database.**

*Vendor scope* — ``organizations`` and ``contact_requests`` — needs a live
operator key and nothing more. Neither table carries a row-level security
policy (``d2rls``, ``d3rls``), which is correct rather than an omission: a
tenant's name, plan and start date are the vendor's own billing metadata, and
an enquiry arrived before there was a tenant to scope it to.

*Tenant scope* — everything inside a customer's book — needs a break-glass
grant with a stated reason the customer can read. The other 58 tables are
fail-closed under policy, so this is not a rule the router remembers to apply:
without ``operator.reach_into`` the queries return nothing at all.

**What is deliberately not here.** Provisioning a tenant
(``app.provision_org``), seeding the demo (``app.demo``) and running syncs
(``app.sync_all``) stay on the command line. Each writes a great deal on one
call, none is reversible from a screen, and none is a thing anybody does often
enough to want a button for — a console whose most destructive actions are its
least used is a console built around the wrong verbs.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock, contact, entitlements, operator
from ..db import get_session
from ..domain import models
from ..domain.enums import PlanTier
from ..trust import access

log = logging.getLogger("pie_portal.operator")
router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


def current_operator(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> operator.Operator:
    """FastAPI dependency: the operator this request is from, or 401.

    Header only — no cookie, and therefore no CSRF surface to defend. The
    console holds its key in ``sessionStorage`` and sends it on each call,
    which also means closing the tab ends the session without anything having
    to expire.
    """
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization.split(" ", 1)[1].strip()
    if not presented:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Operator console requires an operator key.")
    who = operator.authenticate(session, presented)
    if who is None:
        # One message for a malformed key, an unknown id and a wrong secret.
        # Which check failed is not the caller's business.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "That is not a live operator key.")
    return who


# ── who am I ─────────────────────────────────────────────────────────────────
@router.get("/whoami")
def whoami(who: operator.Operator = Depends(current_operator)) -> dict:
    """Confirms a key works and names its holder.

    The console calls this on sign-in rather than guessing from a 200 elsewhere,
    so a bad key is reported at the door instead of as an empty queue.
    """
    return {"operator_id": who.operator_id, "key_id": who.key_id, "name": who.name}


# ── the enquiry queue ────────────────────────────────────────────────────────
def _enquiry(row: models.ContactRequest) -> dict:
    return {
        "id": row.contact_request_id,
        "company": row.company, "name": row.name, "email": row.email,
        "phone": row.phone, "plan": row.plan, "erp": row.erp,
        "message": row.message,
        "created_at": clock.iso(row.created_at),
        "status": row.status,
        "handled_at": clock.iso(row.handled_at),
        "handled_by": row.handled_by,
    }


@router.get("/enquiries")
def enquiries(handled: bool = False,
              limit: int = 200,
              who: operator.Operator = Depends(current_operator),
              session: Session = Depends(get_session)) -> dict:
    """The demo-request form's queue.

    ``handled=false`` (the default) is ``contact.pending`` — what the CLI
    prints, oldest first, because the top of that list is the enquiry ignored
    longest. ``handled=true`` is the history the CLI has no way to show at all,
    newest first, which is the one thing this endpoint adds over the command.
    """
    if not handled:
        return {"enquiries": [_enquiry(r) for r in contact.pending(session)],
                "showing": "waiting"}
    rows = session.scalars(
        select(models.ContactRequest)
        .where(models.ContactRequest.status == contact.HANDLED)
        .order_by(models.ContactRequest.created_at.desc())
        .limit(max(1, min(limit, 1000)))).all()
    return {"enquiries": [_enquiry(r) for r in rows], "showing": "handled"}


@router.post("/enquiries/{contact_request_id}/handled")
def mark_handled(contact_request_id: str,
                 who: operator.Operator = Depends(current_operator),
                 session: Session = Depends(get_session)) -> dict:
    """Record that somebody replied. Stamped with the operator's own id.

    ``handled_by`` is the operator, never a free-text field the caller supplies:
    the point of the stamp is that it says who, and a value the client chooses
    is a value the client can choose wrongly.
    """
    try:
        row = contact.mark_handled(session, contact_request_id,
                                   handled_by=who.operator_id)
    except contact.ContactRefused as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    session.commit()
    return _enquiry(row)


# ── who is asking to buy something ───────────────────────────────────────────
@router.get("/requests")
def requests(who: operator.Operator = Depends(current_operator),
             session: Session = Depends(get_session)) -> dict:
    """The three ask-queues, exactly as ``app.entitlements requests`` prints
    them and for the reason that command gives: an operator asking "who wants to
    buy something" must get one answer rather than having to know there are
    three tables.

    They stay three lists rather than being merged into one, because they are
    genuinely different questions and only one of them carries a decision this
    endpoint can apply.
    """
    at_signup = []
    for org in session.scalars(select(models.Organization)
                               .order_by(models.Organization.created_at)):
        want = entitlements.wants_more(org)
        if want is not None:
            at_signup.append({
                "organization_id": org.organization_id, "name": org.name,
                "on": entitlements.licensed_plan(org).value,
                "wants": want.value,
            })
    in_product = [
        {"request_id": r.request_id, "organization_id": r.organization_id,
         "from": r.plan_at_request, "to": r.requested_plan,
         "requested_at": clock.iso(r.requested_at),
         "requested_by": r.requested_by, "note": r.note}
        for r in session.scalars(
            select(models.PlanChangeRequest)
            .where(models.PlanChangeRequest.status == entitlements.REQUESTED)
            .order_by(models.PlanChangeRequest.requested_at))]
    return {
        "at_signup": at_signup,
        "in_product": in_product,
        "enquiries": [_enquiry(r) for r in contact.pending(session)],
    }


class DecideBody(BaseModel):
    apply: bool


@router.post("/requests/{request_id}/decide")
def decide(request_id: str, body: DecideBody,
           who: operator.Operator = Depends(current_operator),
           session: Session = Depends(get_session)) -> dict:
    """Grant or refuse one in-product plan request.

    One endpoint taking a boolean rather than ``/apply`` and ``/decline``,
    because ``entitlements.decide_request`` is one function taking the same
    boolean: two routes onto it would be two places to forget the stamp.
    """
    try:
        row = entitlements.decide_request(session, request_id, apply=body.apply,
                                          decided_by=who.operator_id)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except entitlements.PlanRequestRefused as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    session.commit()
    return {"request_id": row.request_id, "status": row.status,
            "organization_id": row.organization_id}


# ── the tenants, and their plans ─────────────────────────────────────────────
@router.get("/organizations")
def organizations(who: operator.Operator = Depends(current_operator),
                  session: Session = Depends(get_session)) -> dict:
    """Every tenant, with the plan it is licensed on. Vendor metadata only.

    Name, plan, currency and when it started — nothing from inside the book.
    That is not this endpoint being careful; ``organizations`` is one of the
    two tables with no policy, and everything that would say how the business is
    *doing* lives in the 58 that have one.
    """
    rows = []
    for org in session.scalars(select(models.Organization)
                               .order_by(models.Organization.created_at)):
        want = entitlements.wants_more(org)
        rows.append({
            "organization_id": org.organization_id,
            "name": org.name,
            "plan": entitlements.licensed_plan(org).value,
            "wants": want.value if want else None,
            "currency": getattr(org, "currency", None),
            "created_at": clock.iso(org.created_at),
        })
    return {"organizations": rows}


class SetPlanBody(BaseModel):
    plan: PlanTier


@router.post("/organizations/{organization_id}/plan")
def put_on_plan(organization_id: str, body: SetPlanBody,
                who: operator.Operator = Depends(current_operator),
                session: Session = Depends(get_session)) -> dict:
    """Put a tenant on a plan — the console's one genuinely granting action.

    Calls ``entitlements.set_plan``, which is the only function in this codebase
    that moves a plan, so this route adds a door and not a second mechanism.
    """
    try:
        entitlements.set_plan(session, organization_id, body.plan)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    session.commit()
    log.info("operator %s put %s on %s", who.operator_id, organization_id,
             body.plan.value)
    return {"organization_id": organization_id, "plan": body.plan.value}


# ── break-glass: reaching inside a tenant ────────────────────────────────────
class GrantBody(BaseModel):
    #: Shown to the customer verbatim, which is what makes it get written
    #: honestly. ``trust.access.grant`` refuses anything under 10 characters.
    justification: str = Field(min_length=10, max_length=1024)


@router.post("/organizations/{organization_id}/access")
def open_access(organization_id: str, body: GrantBody,
                who: operator.Operator = Depends(current_operator),
                session: Session = Depends(get_session)) -> dict:
    """Open a time-boxed break-glass grant over one tenant.

    The console cannot read a customer's rows without one and does not pretend
    otherwise: there is no "just this once" path, and the grant, its reason and
    every subsequent reach appear on the customer's own access log with no
    mechanism to suppress them.
    """
    if session.get(models.Organization, organization_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"No such organization {organization_id!r}")
    try:
        row = access.grant(session, organization_id=organization_id,
                           staff_user_id=who.operator_id,
                           justification=body.justification)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    session.commit()
    return {"grant_id": row.grant_id, "organization_id": organization_id,
            "expires_at": clock.iso(row.expires_at)}


@router.delete("/access/{grant_id}")
def close_access(grant_id: str,
                 who: operator.Operator = Depends(current_operator),
                 session: Session = Depends(get_session)) -> dict:
    """Hand the key back before it expires. Idempotent."""
    access.revoke(session, grant_id, actor_user_id=who.operator_id)
    session.commit()
    return {"grant_id": grant_id, "revoked": True}


@router.get("/organizations/{organization_id}/support")
def support_view(organization_id: str,
                 who: operator.Operator = Depends(current_operator),
                 session: Session = Depends(get_session)) -> dict:
    """The one panel that reads inside a tenant: is their platform working?

    Deliberately the smallest useful answer to the support question — how many
    people, when the book last synced, and whether that sync succeeded. No
    quotes, no customers, no money: the questions this console exists to answer
    are the vendor's, and a screen that showed a customer's margins because it
    could would be the leak §1 keeps describing.

    Every read below happens after ``reach_into``, which raises when there is no
    live grant and writes an access event when there is. A 403 here means the
    grant is missing or has expired, and the remedy is to open one with a reason
    — not to find another route.
    """
    if session.get(models.Organization, organization_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"No such organization {organization_id!r}")
    try:
        operator.reach_into(session, who, organization_id,
                            resource="operator-console:support")
    except access.AccessDenied as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    people = session.scalars(
        select(models.User).where(models.User.organization_id == organization_id)).all()
    last_sync = session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == organization_id)
        .order_by(models.SyncRun.started_at.desc()).limit(1)).first()
    session.commit()
    return {
        "organization_id": organization_id,
        "people": {"total": len(people),
                   "active": sum(1 for u in people if u.active)},
        "last_sync": None if last_sync is None else {
            "started_at": clock.iso(last_sync.started_at),
            "finished_at": clock.iso(last_sync.finished_at),
            "status": last_sync.status,
        },
    }


@router.get("/organizations/{organization_id}/access")
def access_log(organization_id: str,
               who: operator.Operator = Depends(current_operator),
               session: Session = Depends(get_session)) -> dict:
    """What staff have done to this tenant, and whether a grant is open now.

    Readable without a grant on purpose: this is the record *of* break-glass,
    not tenant data, and needing a grant to see whether you hold one would be
    circular. The customer reads the same events on their own endpoint.
    """
    live = access.active_grant(session, organization_id, who.operator_id)
    events = session.scalars(
        select(models.AccessEvent)
        .where(models.AccessEvent.organization_id == organization_id)
        .order_by(models.AccessEvent.created_at.desc()).limit(50)).all()
    return {
        "active_grant": None if live is None else {
            "grant_id": live.grant_id,
            "expires_at": clock.iso(live.expires_at),
            "justification": live.justification,
        },
        "events": [{"at": clock.iso(e.created_at), "action": e.action,
                    "staff": e.staff_user_id, "detail": e.detail}
                   for e in events],
    }
