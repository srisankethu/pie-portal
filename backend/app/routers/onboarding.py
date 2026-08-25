"""Sign up, and find out what is left to do.

Three endpoints, and the first two are the only unauthenticated surface in this
application that *writes*. That is worth saying at the top rather than burying,
because everything odd about this file follows from it:

- ``GET  /api/v1/signup``     — is sign-up offered here at all? (public, read)
- ``POST /api/v1/signup``     — create a tenant and sign its owner in (public, write)
- ``GET  /api/v1/demo``       — is there a demonstration workspace? (public, read)
- ``POST /api/v1/demo``       — enter it, with no account (public, read-only session)
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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock, entitlements, memberships, onboarding, ratelimit, tenancy
from ..authz import Principal, current_principal, open_session, set_session_cookie
from ..config import settings
from ..db import get_session
from ..domain import models

log = logging.getLogger("pie_portal.onboarding")

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


# ── the speed bump ───────────────────────────────────────────────────────────
#: One hour, and the reason the two doors below have separate buckets: the
#: demonstration door and the sign-up door are different doors. A stranger who
#: looked at the demo five times must still be able to sign up, which is the
#: entire point of having let them look.
_WINDOW_SECONDS = 3600.0


def _too_many(request: Request, *, bucket: str, limit: int) -> bool:
    """Whether this address has already used its hour's allowance of ``bucket``.

    ``request.client.host`` is the peer, which behind the reverse proxy in
    ``deploy/`` is the proxy itself — so in that topology this limits sign-ups
    globally rather than per client. That is the conservative direction to be
    wrong in (it over-limits rather than under-limits), and it is why the limit
    is 5/hour rather than 1: a genuine second tenant signing up from the same
    office in the same hour must not be refused. Trusting a forwarded header
    instead would let the caller pick their own bucket, which is worse than the
    imprecision.

    The window itself lives in ``app/ratelimit.py`` — one deque and one trim
    loop for every door in this codebase that needs one.
    """
    return ratelimit.too_many(
        bucket, request.client.host if request.client else "unknown",
        limit=limit, window_seconds=_WINDOW_SECONDS)


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
        "note": (f"A {settings.INTELLIGENCE_TRIAL_DAYS}-day trial of Commercial "
                 "Intelligence, in full, starting when you sign up. No card. "
                 "When it ends the decision layer locks and everything you have "
                 "put in stays exactly where it is."),
        # The ladder, so the form can ask which plan a business wants without
        # holding its own copy of what the plans are. `plan` above is still the
        # one every sign-up lands on, whichever of these they pick — the answer
        # is recorded for the operator, never granted. Saying both here is what
        # stops the form from implying it sells anything.
        "plans": entitlements.ladder(),
    }


class SignUpRequest(BaseModel):
    company: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    currency: str = Field(default="INR", max_length=8)
    #: Which plan this business wants. Optional, constrained to the ladder by
    #: ``onboarding.parse_requested_plan``, and **granted by nothing** — every
    #: sign-up lands on ``onboarding.SIGNUP_PLAN``. It is a field on a form, not
    #: an order: there is no billing here and no API that sets a plan.
    plan: Optional[str] = Field(default=None, max_length=32)


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
    #: True only from the demonstration door below. Same field the login
    #: envelope carries, because the client stores both with one code path.
    is_demo: bool = False


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
    if _too_many(request, bucket="signup",
                 limit=settings.SIGNUP_RATE_LIMIT_PER_HOUR):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-ups from here in the last hour. Try again later.")

    try:
        org_id, owner = onboarding.sign_up(
            session, company=body.company, owner_name=body.name,
            owner_email=body.email, password=body.password,
            currency=body.currency, wants_plan=body.plan)
    except onboarding.SignupDisabled as e:            # pragma: no cover — guarded above
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except onboarding.SignupRefused as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    org = session.get(models.Organization, org_id)
    log.info("signup complete org=%s user=%s", org_id, owner.user_id)
    # From the membership, not from the deprecated ``users.role`` mirror. The
    # two agree here — a brand-new organization's founder holds exactly one —
    # but reading the mirror is how a deprecated column stays load-bearing.
    role = memberships.role_in(session, owner.user_id, org_id)
    token, _row = open_session(session, owner, request.headers.get("user-agent"))
    # Committed before the token leaves, for the reason `auth.login` gives: a
    # token naming an uncommitted row is a credential that does not work.
    session.commit()
    set_session_cookie(response, token)
    return SignUpResponse(
        token=token,
        user_id=owner.user_id, organization_id=owner.organization_id,
        role=(role.value if role else ""), name=owner.name, email=owner.email,
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


# ── the demonstration workspace ──────────────────────────────────────────────
def _demo_user(session: Session):
    """The account the public demo signs a stranger in as, or ``None``.

    Both halves of the configuration have to be present *and* agree: the named
    address must exist, be active, and belong to the named organization. A
    mismatch resolves to no demo rather than to whichever row the address found,
    because "the demo points at the wrong tenant" must fail as an absent door
    and never as an open one into somebody's real book.
    """
    org_id = (settings.PUBLIC_DEMO_ORG_ID or "").strip()
    email = (settings.PUBLIC_DEMO_EMAIL or "").strip().lower()
    if not org_id or not email:
        return None
    # Announced from configuration, not discovered. The demo's organization is
    # named in settings, so unlike sign-in this path needs no lookup that can
    # see across tenants — it already knows which tenant it wants, and a policy
    # then makes the mismatch check below true in SQL as well as in Python.
    tenancy.set_tenant(session, org_id)
    user = session.scalar(select(models.User).where(models.User.email == email))
    if user is None or not user.active or user.organization_id != org_id:
        return None
    return user


@router.get("/demo")
def demo_offered(session: Session = Depends(get_session)) -> dict:
    """Whether this deployment has a demonstration workspace to walk into.

    Public and says nothing beyond yes or no — not which organization it is, not
    who is in it, and nothing about any other tenant. The landing page reads
    this to decide whether to render the button at all, so an install without a
    demo shows no door rather than a door that 404s.
    """
    return {
        "enabled": _demo_user(session) is not None,
        "note": ("A worked example on made-up data. Read-only: it shows what "
                 "the product does and saves nothing."),
    }


@router.post("/demo", response_model=SignUpResponse)
def enter_demo(request: Request, response: Response,
               session: Session = Depends(get_session)) -> SignUpResponse:
    """Sign a stranger into the demonstration workspace. No account, no password.

    The reason this exists: `app/demo.py` builds a realistic multi-account book
    whose histories deliberately trigger all five signal families, then runs the
    real pipeline over it — the best answer this platform has to "what does it
    actually do", and it sat behind `/api/v1/internal/demo-seed`, which needs an
    account. So the one artefact written to convince somebody was reachable only
    by people already convinced.

    **What stops this being a hole.** The session it mints is an ordinary one,
    so every existing role check applies unchanged; and `authz.current_principal`
    refuses a demo principal every unsafe method, so the whole of what this
    opens is a read of fabricated data. It is off unless a deployment names an
    organization, and it resolves to off if the configuration does not point at
    a real active user in that organization.

    **What it does not stop, stated rather than implied.** It writes a
    `UserSession` row per visit without a credential, so a determined caller can
    grow that table — the speed bump above is a speed bump, exactly as the
    sign-up one says of itself. It is the same exposure `POST /auth/login`
    already has, minus the password, and the answer if it ever matters is a real
    limiter in front of the process rather than a cleverer one inside it.

    Returns the login envelope, deliberately: the client stores this with the
    same code that stores a sign-in, so there is one way to become signed in
    rather than two, and the second one is not the one that forgets the
    currency.
    """
    user = _demo_user(session)
    if user is None:
        # 404, as sign-up does when it is off: this is not a door somebody
        # lacks the key to, it is not a door.
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "This deployment has no demonstration workspace.")
    if _too_many(request, bucket="demo",
                 limit=settings.SIGNUP_RATE_LIMIT_PER_HOUR):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many demo sessions from here in the last hour. Try again later.")

    org = session.get(models.Organization, user.organization_id)
    token, _row = open_session(session, user, request.headers.get("user-agent"))
    # Committed before the token goes out, for the reason `login` gives: a token
    # naming a session row nobody else can read yet is a dead credential.
    session.commit()
    log.info("demo session opened org=%s user=%s", user.organization_id,
             user.user_id)
    set_session_cookie(response, token)

    return SignUpResponse(
        token=token, user_id=user.user_id,
        organization_id=user.organization_id,
        role=(getattr(memberships.role_in(session, user.user_id,
                                          user.organization_id), "value", "")),
        name=user.name,
        email=user.email or "",
        currency=(getattr(org, "currency", None) or settings.DEFAULT_CURRENCY),
        timezone=(getattr(org, "timezone", None) or clock.DEFAULT_ZONE),
        must_change_password=False,
        is_demo=True)
