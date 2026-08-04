"""Break-glass staff access — justified, time-boxed, and visible to the tenant.

The likeliest breach of a platform like this is not an attacker at the
perimeter; it is a support engineer with a legitimate login and a broad reach.
"Can your staff see my margins?" is the right question to ask, and the useful
answer is not "no" — it is almost always yes, for support to be possible at all
— but "yes, and here is the entry naming who opened your data, when, and why."

A log the customer cannot see is an internal control. A log the customer *can*
see is a constraint on us, which is the party they are actually worried about.
So the events here are exposed on a customer-facing endpoint, and there is no
mechanism to suppress one.

Three properties, each of which exists because its absence is a known failure:

  * **A grant needs a stated reason.** Free text, recorded verbatim. A reason
    nobody typed is a reason nobody can be asked about later.
  * **A grant expires.** Standing access is the thing that turns one
    compromised staff account into every tenant's data. Default is short.
  * **Every use is an event, not just the grant.** Opening the door once and
    opening it fifty times are different facts, and only per-use records tell
    them apart.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import aware as _aware, now as _now
from ..domain import models

#: Long enough to work a support ticket, short enough that forgetting to revoke
#: is not the same as granting forever.
DEFAULT_TTL = timedelta(hours=4)
MAX_TTL = timedelta(hours=24)


class AccessDenied(PermissionError):
    """Staff reached into a tenant with no active grant."""


def grant(session: Session, *, organization_id: str, staff_user_id: str,
          justification: str, ttl: Optional[timedelta] = None) -> models.AccessGrant:
    """Open break-glass access to one tenant, for a stated reason."""
    reason = (justification or "").strip()
    if len(reason) < 10:
        raise ValueError(
            "Break-glass access needs a real justification — a ticket reference "
            "and what you intend to look at. This text is shown to the customer.")

    window = ttl or DEFAULT_TTL
    if window > MAX_TTL:
        raise ValueError(f"A grant cannot exceed {MAX_TTL}.")

    row = models.AccessGrant(
        organization_id=organization_id, staff_user_id=staff_user_id,
        justification=reason, expires_at=_now() + window)
    session.add(row)
    session.flush()
    _event(session, row, action="GRANTED", detail=reason)
    return row


def active_grant(session: Session, organization_id: str,
                 staff_user_id: str) -> Optional[models.AccessGrant]:
    now = _now()
    rows = session.scalars(
        select(models.AccessGrant).where(
            models.AccessGrant.organization_id == organization_id,
            models.AccessGrant.staff_user_id == staff_user_id,
            models.AccessGrant.revoked_at.is_(None))
        .order_by(models.AccessGrant.granted_at.desc())).all()
    for row in rows:
        if (_aware(row.expires_at) or now) > now:
            return row
    return None


def revoke(session: Session, grant_id: str, actor_user_id: Optional[str]) -> None:
    row = session.get(models.AccessGrant, grant_id)
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = _now()
    session.flush()
    _event(session, row, action="REVOKED",
           detail=f"Revoked by {actor_user_id or 'system'}")


def record_use(session: Session, *, organization_id: str, staff_user_id: str,
               resource: str) -> models.AccessGrant:
    """Assert an active grant and log this particular reach.

    Raises rather than returning a boolean: a caller that forgets to check a
    return value silently becomes unlogged access, and the whole value of this
    module is that there is no such path.
    """
    row = active_grant(session, organization_id, staff_user_id)
    if row is None:
        raise AccessDenied(
            f"{staff_user_id} has no active break-glass grant for "
            f"{organization_id}. Open one with a justification first — it will "
            f"be shown to the customer.")
    _event(session, row, action="ACCESSED", detail=resource)
    return row


def _event(session: Session, grant: models.AccessGrant, *, action: str,
           detail: str) -> None:
    session.add(models.AccessEvent(
        organization_id=grant.organization_id, access_grant_id=grant.grant_id,
        staff_user_id=grant.staff_user_id, action=action, detail=detail))
    session.flush()


def events_for(session: Session, organization_id: str,
               limit: int = 200) -> list[models.AccessEvent]:
    """The customer-visible history. No filter parameter, on purpose — there is
    no supported way to ask this for a redacted subset."""
    return list(session.scalars(
        select(models.AccessEvent)
        .where(models.AccessEvent.organization_id == organization_id)
        .order_by(models.AccessEvent.created_at.desc())
        .limit(limit)).all())
