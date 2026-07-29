"""The approval system: who may authorize what, and what is actually blocked.

Before this module the platform computed that a price was below the floor,
flagged it, recorded the salesperson's reason, and then let the quote go out.
Every part of that chain worked except the last one, which is the only part that
makes the others matter. "Escalate to management" had the same shape: it set the
decision to OVERRIDDEN and wrote a note, so the queue looked handled while
nobody upstream had been told anything.

Three things live here:

- **authority** — which role may decide a given request. A thin margin is a
  commercial judgement a sales manager is paid to make; selling below cost is a
  decision to lose money on purpose, and that is the owner's.
- **the gate** — ``quote_submission_block`` returns the reason a quote may not
  be sent, or None. It is called server-side, from the endpoint that sends.
- **the lifecycle** — raise, decide, withdraw, with an append-only thread.

A request freezes what it was about. If the price moves while an approver is
reading, the approval no longer covers the quote — otherwise an approval granted
at ₹430 can be spent at ₹300.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import models
from .domain.enums import (
    APPROVER_DECISIONS,
    OPEN_APPROVAL_STATUSES,
    ApprovalAuthority,
    ApprovalKind,
    ApprovalStatus,
    Role,
)

log = logging.getLogger("pie_portal.approvals")


class ApprovalError(Exception):
    """A request that cannot be made, or a decision that may not be taken."""


class NotAuthorized(ApprovalError):
    """The actor's role is below the authority this request requires."""


# ── policy ──────────────────────────────────────────────────────────────────
def get_policy(session: Session, org: str) -> models.OrgPolicy:
    """This org's policy, created with defaults on first read.

    Defaults are deliberately on: a platform that ships with enforcement
    disabled is a platform where enforcement never gets turned on.
    """
    policy = session.get(models.OrgPolicy, org)
    if policy is None:
        policy = models.OrgPolicy(organization_id=org)
        session.add(policy)
        session.flush()
    return policy


def authority_for(*, below_cost: bool, policy: models.OrgPolicy) -> ApprovalAuthority:
    if below_cost and policy.below_cost_requires_owner:
        return ApprovalAuthority.OWNER
    return ApprovalAuthority.MANAGER


def can_decide(role: Role, required: ApprovalAuthority) -> bool:
    if required is ApprovalAuthority.OWNER:
        return role is Role.OWNER
    return role in (Role.SALES_MANAGER, Role.OWNER)


def _assert_can_decide(principal, request: models.ApprovalRequest,
                       policy: models.OrgPolicy) -> None:
    required = ApprovalAuthority(request.required_authority)
    if not can_decide(principal.role, required):
        raise NotAuthorized(
            "Selling below what the item cost us is the owner's decision"
            if required is ApprovalAuthority.OWNER
            else "A sales manager or owner must decide this")
    if (request.requested_by_user_id == principal.user_id
            and not policy.allow_self_approval
            and principal.role is not Role.OWNER):
        raise NotAuthorized("You cannot approve your own request")


# ── raising ─────────────────────────────────────────────────────────────────
def _entry(principal, action: str, note: Optional[str]) -> dict[str, Any]:
    return {"at": datetime.now(timezone.utc).isoformat(), "user_id": principal.user_id,
            "name": principal.name, "action": action, "note": note or None}


def open_request_for(session: Session, org: str, kind: ApprovalKind, subject_id: str,
                     line_id: Optional[str] = None) -> Optional[models.ApprovalRequest]:
    """The live request for this subject, if there is one."""
    stmt = select(models.ApprovalRequest).where(
        models.ApprovalRequest.organization_id == org,
        models.ApprovalRequest.kind == kind.value,
        models.ApprovalRequest.subject_id == subject_id,
        models.ApprovalRequest.status.in_([s.value for s in OPEN_APPROVAL_STATUSES]))
    if line_id is not None:
        stmt = stmt.where(models.ApprovalRequest.subject_line_id == line_id)
    return session.scalars(stmt.order_by(
        models.ApprovalRequest.requested_at.desc())).first()


def raise_request(
    session: Session, principal, *,
    kind: ApprovalKind,
    subject_id: str,
    subject: dict[str, Any],
    title: str,
    summary: str,
    required_authority: ApprovalAuthority = ApprovalAuthority.MANAGER,
    subject_line_id: Optional[str] = None,
    reason: Optional[str] = None,
    reason_code: Optional[str] = None,
    thresholds_version: str = "",
) -> models.ApprovalRequest:
    """Raise one request. Re-raising for the same subject reuses the open one.

    Reuse rather than duplicate: a salesperson pressing the button twice should
    not put two identical items in a manager's queue, and a manager should not
    have to work out which of them is live.
    """
    existing = open_request_for(session, principal.organization_id, kind, subject_id,
                                subject_line_id)
    if existing is not None:
        # A returned request that is being resubmitted goes back to pending with
        # the new facts; the thread keeps the whole exchange.
        existing.subject = subject
        existing.summary = summary
        existing.reason = reason or existing.reason
        existing.reason_code = reason_code or existing.reason_code
        existing.required_authority = required_authority.value
        existing.status = ApprovalStatus.PENDING.value
        existing.thread = list(existing.thread or []) + [
            _entry(principal, "RESUBMITTED", reason)]
        session.flush()
        return existing

    row = models.ApprovalRequest(
        organization_id=principal.organization_id,
        kind=kind.value,
        status=ApprovalStatus.PENDING.value,
        required_authority=required_authority.value,
        subject_id=subject_id,
        subject_line_id=subject_line_id,
        subject=subject,
        title=title[:255],
        summary=summary[:1024],
        reason=(reason or None),
        reason_code=(reason_code or None),
        requested_by_user_id=principal.user_id,
        thread=[_entry(principal, "REQUESTED", reason)],
        thresholds_version=thresholds_version,
    )
    session.add(row)
    session.flush()
    log.info("approval raised org=%s kind=%s subject=%s authority=%s",
             principal.organization_id, kind.value, subject_id,
             required_authority.value)
    return row


def decide(session: Session, principal, request: models.ApprovalRequest,
           status: ApprovalStatus, note: Optional[str] = None) -> models.ApprovalRequest:
    """Approve, reject, or return a request for changes."""
    if request.organization_id != principal.organization_id:
        raise NotAuthorized("That request belongs to another organization")
    if status not in APPROVER_DECISIONS:
        raise ApprovalError(f"{status.value} is not an approver decision")
    if ApprovalStatus(request.status) not in OPEN_APPROVAL_STATUSES:
        raise ApprovalError(f"This request is already {request.status}")

    policy = get_policy(session, principal.organization_id)
    _assert_can_decide(principal, request, policy)

    request.status = status.value
    request.decided_by_user_id = principal.user_id
    request.decided_at = datetime.now(timezone.utc)
    request.decision_note = (note or None)
    request.thread = list(request.thread or []) + [_entry(principal, status.value, note)]
    session.flush()

    if request.kind == ApprovalKind.DECISION_ESCALATION.value:
        _settle_escalated_decision(session, request, status)
    return request


def withdraw(session: Session, principal, request: models.ApprovalRequest,
             note: Optional[str] = None) -> models.ApprovalRequest:
    """The requester no longer needs it — typically they re-priced the line."""
    if request.organization_id != principal.organization_id:
        raise NotAuthorized("That request belongs to another organization")
    if ApprovalStatus(request.status) not in OPEN_APPROVAL_STATUSES:
        raise ApprovalError(f"This request is already {request.status}")
    if (request.requested_by_user_id != principal.user_id
            and principal.role is Role.SALESPERSON):
        raise NotAuthorized("Only the person who raised this can withdraw it")
    request.status = ApprovalStatus.WITHDRAWN.value
    request.decided_at = datetime.now(timezone.utc)
    request.thread = list(request.thread or []) + [_entry(principal, "WITHDRAWN", note)]
    session.flush()
    return request


def release_if_no_longer_needed(session: Session, principal, *, quote_id: str,
                                line_id: str, still_requires_approval: bool) -> None:
    """Close an open request whose line has been re-priced back within policy.

    Without this, a manager who says "try ₹150" and gets ₹150 is left with the
    request sitting in their queue forever: open, unanswerable, and quietly
    training them to ignore the list. The salesperson did the thing that was
    asked; there is nothing left to approve.
    """
    if still_requires_approval:
        return
    request = open_request_for(session, principal.organization_id,
                               ApprovalKind.QUOTE_LINE_PRICE, quote_id, line_id)
    if request is None:
        return
    request.status = ApprovalStatus.WITHDRAWN.value
    request.decided_at = datetime.now(timezone.utc)
    request.thread = list(request.thread or []) + [
        _entry(principal, "WITHDRAWN", "Re-priced within policy — approval no "
                                       "longer required")]
    session.flush()


def _settle_escalated_decision(session: Session, request: models.ApprovalRequest,
                               status: ApprovalStatus) -> None:
    """An escalated decision leaves ESCALATED when the escalation is answered.

    Approved means management endorsed acting on it; rejected means they did
    not, and it is dismissed with their note. Changes requested returns it to
    the salesperson's open queue rather than leaving it parked.
    """
    from .domain.enums import DecisionStatus

    decision = session.get(models.Decision, request.subject_id)
    if decision is None or decision.status != DecisionStatus.ESCALATED.value:
        return
    if status is ApprovalStatus.APPROVED:
        decision.status = DecisionStatus.ACTIONED.value
    elif status is ApprovalStatus.REJECTED:
        decision.status = DecisionStatus.DISMISSED.value
        decision.override_reason = request.decision_note
    else:
        decision.status = DecisionStatus.OPEN.value


# ── the gate ────────────────────────────────────────────────────────────────
def quote_submission_block(session: Session, org: str, quote_id: str) -> Optional[str]:
    """Why this quote may not be sent, or None if it may.

    Called from the endpoint that actually sends. A gate evaluated in the
    browser is a suggestion; this one is the reason ``create_estimate`` returns
    403 rather than an estimate number.

    The check runs against the *latest* snapshot per line, because a line
    re-priced above the floor should no longer be blocked by the request raised
    when it was below — and, symmetrically, an approval granted against an
    older, higher price must not carry over to a lower one.
    """
    policy = get_policy(session, org)
    if not policy.require_approval_for_quotes:
        return None

    latest = _latest_snapshot_per_line(session, org, quote_id)
    if not latest:
        return None

    needing = [s for s in latest.values() if s.requires_approval]
    if not needing:
        return None

    approvals = {
        (r.subject_line_id or ""): r
        for r in session.scalars(select(models.ApprovalRequest).where(
            models.ApprovalRequest.organization_id == org,
            models.ApprovalRequest.kind == ApprovalKind.QUOTE_LINE_PRICE.value,
            models.ApprovalRequest.subject_id == quote_id,
        ).order_by(models.ApprovalRequest.requested_at))
    }

    unapproved: list[str] = []
    for snap in needing:
        req = approvals.get(snap.quote_line_id or "")
        if req is None or req.status != ApprovalStatus.APPROVED.value:
            unapproved.append(snap.product_ref or snap.quote_line_id)
        elif not _covers(req, snap):
            unapproved.append(
                f"{snap.product_ref or snap.quote_line_id} (price changed since approval)")

    if not unapproved:
        return None
    listed = ", ".join(sorted(set(unapproved))[:5])
    more = len(set(unapproved)) - 5
    return (f"{len(set(unapproved))} line(s) need approval before this quote can be "
            f"sent: {listed}{f' and {more} more' if more > 0 else ''}.")


def _covers(request: models.ApprovalRequest, snapshot: models.QuoteDecision) -> bool:
    """Does an approval actually cover the price now on the line?

    An approval is for a number, not for a line. Approving ₹430 and then sending
    ₹300 under the same approval is the most obvious way to defeat this control,
    so a price at or above what was approved is covered and anything below is
    not.
    """
    approved_price = (request.subject or {}).get("quoted_unit_price")
    if approved_price is None or snapshot.quoted_unit_price is None:
        return False
    try:
        return Decimal(str(snapshot.quoted_unit_price)) >= Decimal(str(approved_price))
    except (TypeError, ValueError):
        return False


def _latest_snapshot_per_line(session: Session, org: str,
                              quote_id: str) -> dict[str, models.QuoteDecision]:
    rows = session.scalars(select(models.QuoteDecision).where(
        models.QuoteDecision.organization_id == org,
        models.QuoteDecision.quote_id == quote_id,
    ).order_by(models.QuoteDecision.created_at)).all()
    out: dict[str, models.QuoteDecision] = {}
    for r in rows:
        out[r.quote_line_id] = r     # later rows overwrite earlier ones
    return out


# ── reading ─────────────────────────────────────────────────────────────────
def inbox(session: Session, principal, *, status: Optional[ApprovalStatus] = None,
          limit: int = 100) -> list[models.ApprovalRequest]:
    """What this person should be looking at.

    An approver sees the organization's queue — but only the requests their role
    can actually decide, because a list full of items you are not allowed to
    touch is worse than no list. A salesperson sees their own requests.
    """
    stmt = select(models.ApprovalRequest).where(
        models.ApprovalRequest.organization_id == principal.organization_id)
    if status is not None:
        stmt = stmt.where(models.ApprovalRequest.status == status.value)

    if principal.role is Role.SALESPERSON:
        stmt = stmt.where(
            models.ApprovalRequest.requested_by_user_id == principal.user_id)
    elif principal.role is Role.SALES_MANAGER:
        # Owner-authority requests stay out of a manager's actionable queue.
        stmt = stmt.where(
            (models.ApprovalRequest.required_authority
             == ApprovalAuthority.MANAGER.value)
            | (models.ApprovalRequest.requested_by_user_id == principal.user_id))

    return list(session.scalars(
        stmt.order_by(models.ApprovalRequest.requested_at.desc()).limit(limit)))


def pending_count(session: Session, principal) -> int:
    return sum(1 for r in inbox(session, principal, status=ApprovalStatus.PENDING)
               if can_decide(principal.role, ApprovalAuthority(r.required_authority)))


def to_dict(request: models.ApprovalRequest, role: Role,
            names: Optional[dict[str, str]] = None) -> dict:
    """Serialize a request. ``subject`` carries cost and margin, so a
    salesperson — including the one who raised it — does not receive it."""
    names = names or {}
    is_sales = role is Role.SALESPERSON
    out: dict[str, Any] = {
        "approval_request_id": request.approval_request_id,
        "kind": request.kind,
        "status": request.status,
        "required_authority": request.required_authority,
        "subject_id": request.subject_id,
        "subject_line_id": request.subject_line_id,
        "title": request.title,
        "summary": request.summary,
        "reason": request.reason,
        "reason_code": request.reason_code,
        "requested_by": names.get(request.requested_by_user_id,
                                  request.requested_by_user_id),
        "requested_by_user_id": request.requested_by_user_id,
        "requested_at": request.requested_at.isoformat() if request.requested_at else None,
        "decided_by": (names.get(request.decided_by_user_id, request.decided_by_user_id)
                       if request.decided_by_user_id else None),
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "decision_note": request.decision_note,
        "thread": request.thread or [],
        "can_decide": can_decide(role, ApprovalAuthority(request.required_authority)),
        "is_open": ApprovalStatus(request.status) in OPEN_APPROVAL_STATUSES,
    }
    if not is_sales:
        out["subject"] = request.subject or {}
    return out


def user_names(session: Session, org: str, ids: Iterable[str]) -> dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.user_id: u.name for u in session.scalars(select(models.User).where(
        models.User.organization_id == org, models.User.user_id.in_(ids)))}
