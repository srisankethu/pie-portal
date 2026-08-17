"""Sign up, and find out what is left to do.

Three endpoints, and the first two are the only unauthenticated surface in this
application that *writes*. That is worth saying at the top rather than burying,
because everything odd about this file follows from it:

- ``GET  /api/v1/signup``     — is sign-up offered here at all? (public, read)
- ``POST /api/v1/signup``     — create a tenant and sign its owner in (public, write)
- ``GET  /api/v1/onboarding`` — what this organization still has to do (authed)

**The rate limit is a speed bump and is documented as one.** It counts sign-ups
per client address in this process's memory, so it does not survive a restart,
is not shared between workers, and is defeated by anyone with a second address.
What it actually buys is protection against the accidental case — a retried
form, a script in a loop — and against the cost that makes this endpoint worth
limiting at all: hashing a password is 240,000 rounds of PBKDF2 by design, so an
unthrottled sign-up endpoint is a CPU amplifier pointed at the API. A deployment
that expects real abuse puts a limiter in front of the app; this is what stops
the endpoint from being the cheapest way to spend the server's CPU in the
meantime. Stating that is the point: a limiter that looks like a control while
being a speed bump is worse than one that says which it is.

**Sign-up is off unless the deployment turns it on** (``SELF_SERVE_SIGNUP``).
The GET exists so the landing page can offer the button only where pressing it
would work, rather than leading to a form that always refuses.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import clock, onboarding
from ..authz import Principal, current_principal, open_session, set_session_cookie
from ..config import settings
from ..db import get_session
from ..domain import models

log = logging.getLogger("pie_portal.onboarding")

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


# ── the speed bump ───────────────────────────────────────────────────────────
#: address -> the times it signed up, newest last. Trimmed on every read, so it
#: cannot grow without bound while the process is up.
_RECENT: dict[str, deque[float]] = {}
_WINDOW_SECONDS = 3600.0


def _too_many(request: Request) -> bool:
    """Whether this address has already used its hour's sign-ups.

    ``request.client.host`` is the peer, which behind the reverse proxy in
    ``deploy/`` is the proxy itself — so in that topology this limits sign-ups
    globally rather than per client. That is the conservative direction to be
    wrong in (it over-limits rather than under-limits), and it is why the limit
    is 5/hour rather than 1: a genuine second tenant signing up from the same
    office in the same hour must not be refused. Trusting a forwarded header
    instead would let the caller pick their own bucket, which is worse than the
    imprecision.
    """
    if settings.SIGNUP_RATE_LIMIT_PER_HOUR <= 0:
        return False
    who = request.client.host if request.client else "unknown"
    now = time.monotonic()
    seen = _RECENT.setdefault(who, deque())
    while seen and now - seen[0] > _WINDOW_SECONDS:
        seen.popleft()
    if len(seen) >= settings.SIGNUP_RATE_LIMIT_PER_HOUR:
        return True
    seen.append(now)
    return False


# ── is this offered? ─────────────────────────────────────────────────────────
@router.get("/signup")
def signup_offered() -> dict:
    """Whether this deployment accepts sign-ups, and what one gets.

    Public and deliberately uninformative beyond that: it says nothing about
    which organizations exist or how many, only whether the door is there.
    """
    return {
        "enabled": onboarding.enabled(),
        "plan": onboarding.SIGNUP_PLAN.value,
        "trial_days": settings.INTELLIGENCE_TRIAL_DAYS,
        "note": ("A free Quote Desk account. Connecting your first Zoho Books "
                 f"company starts a {settings.INTELLIGENCE_TRIAL_DAYS}-day trial "
                 "of Commercial Intelligence."),
    }


class SignUpRequest(BaseModel):
    company: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    currency: str = Field(default="INR", max_length=8)


class SignUpResponse(BaseModel):
    """Deliberately the same shape ``POST /auth/login`` returns.

    A sign-up that handed back a different envelope would mean the client had
    two ways to become signed in, and the second one would be the one that
    forgets to apply the currency or the timezone. The client stores this with
    exactly the code that stores a login.
    """

    token: str
    user_id: str
    organization_id: str
    role: str
    name: str
    email: str = ""
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"
    must_change_password: bool = False


@router.post("/signup", status_code=status.HTTP_201_CREATED,
             response_model=SignUpResponse)
def sign_up(body: SignUpRequest, request: Request, response: Response,
            session: Session = Depends(get_session)) -> SignUpResponse:
    """Create an organization and its owner, and sign that owner in.

    Signed in rather than sent to the login form: the person just chose the
    password, and asking them to type it again to reach the thing they signed up
    for is a step that exists only because it was easier to build.
    """
    if not onboarding.enabled():
        # 404 rather than 403. When sign-up is off, this endpoint is not a door
        # somebody lacks the key to — it is not a door.
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "This deployment does not accept sign-ups.")
    if _too_many(request):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-ups from here in the last hour. Try again later.")

    try:
        org_id, owner = onboarding.sign_up(
            session, company=body.company, owner_name=body.name,
            owner_email=body.email, password=body.password,
            currency=body.currency)
    except onboarding.SignupDisabled as e:            # pragma: no cover — guarded above
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except onboarding.SignupRefused as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    org = session.get(models.Organization, org_id)
    log.info("signup complete org=%s user=%s", org_id, owner.user_id)
    token, _row = open_session(session, owner, request.headers.get("user-agent"))
    # Committed before the token leaves, for the reason `auth.login` gives: a
    # token naming an uncommitted row is a credential that does not work.
    session.commit()
    set_session_cookie(response, token)
    return SignUpResponse(
        token=token,
        user_id=owner.user_id, organization_id=owner.organization_id,
        role=owner.role, name=owner.name, email=owner.email,
        currency=(getattr(org, "currency", None) or settings.DEFAULT_CURRENCY),
        timezone=(getattr(org, "timezone", None) or clock.DEFAULT_ZONE),
        must_change_password=owner.must_change_password)


# ── what is left ─────────────────────────────────────────────────────────────
@router.get("/onboarding")
def my_onboarding(principal: Principal = Depends(current_principal),
                  session: Session = Depends(get_session)) -> dict:
    """What this organization still has to do.

    Any signed-in role, which is not an oversight: a salesperson whose company
    has connected nothing sees empty screens everywhere, and "your owner has not
    connected Zoho yet" is a better answer than four blank panels. Every step
    names a route the *owner* can act on; the client decides what to offer a
    role that cannot press it, exactly as it does for the nav.

    No plan gate. Knowing that you have not connected your books is not a
    feature — it is the reason the product looks broken.
    """
    return onboarding.checklist(session, principal.organization_id)
