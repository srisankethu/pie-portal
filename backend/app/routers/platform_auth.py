"""Decision-platform authentication.

Verifies a password against the stored PBKDF2 hash and returns an HMAC token
carrying user_id + org_id. Role and scope are resolved from the User row on
every request, never from the token.

This endpoint previously accepted *any* non-empty password for any known email,
which meant the platform's three roles were a presentation choice rather than a
boundary: the salesperson view, the manager view and the owner view were all one
guessable address apart. Everything downstream — decision-type gating, the
cost/margin redaction, the approval authority split — was being enforced
faithfully on top of an identity nobody had checked.

A user row with no password hash cannot sign in at all. That is deliberate: the
failure mode of "no credential set" must be denial, not admission.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import (
    Principal, clear_session_cookie, current_principal, is_demo_org,
    is_login_throttled,
    open_session, record_login_failure, reset_login_failures, revoke_all_sessions,
    revoke_session, set_session_cookie)
from .. import clock
from ..config import settings
from ..db import get_session
from ..domain import models
from ..passwords import hash_password, needs_rehash, verify_password
from ..trust import audit

log = logging.getLogger("pie_portal.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["platform-auth"])

# One message for every failure. Distinguishing "no such account" from "wrong
# password" tells an attacker which addresses are worth attacking.
_REJECTED = "Incorrect email or password"
_THROTTLED = "Too many login failures. Please try again in {delay} seconds."

# A real hash of a fixed dummy, verified against when no usable account hash
# exists, so an absent/inactive/no-password account costs the same PBKDF2 work
# (and time) as a real one. Built at import with the live iteration count, so
# its timing tracks real hashes even if that count changes — a hardcoded digest
# would drift. It matches no real password: the dummy is not a valid credential.
_SENTINEL_HASH = hash_password("pie-portal login-timing sentinel — not a password")


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    organization_id: str
    role: str
    name: str
    # The address this account signs in with, echoed back for one reason: a
    # change-password form with two password fields and no username makes a
    # password manager save the new secret against nothing, or against the
    # wrong entry. `autocomplete="username"` needs a value, and the only
    # correct one is the address the person just signed in with. Not a
    # disclosure: it is their own address, on a response they authenticated for.
    email: str = ""
    # The currency this organization trades in. Sent at sign-in because every
    # screen renders money and none of them should be guessing: the client used
    # to hardcode a rupee sign in four places, which is correct for exactly one
    # tenant and silently wrong for the next.
    currency: str = "INR"
    # The zone this business's day is measured in. Sent at sign-in for the same
    # reason the currency is: every screen renders a timestamp, and a browser
    # in another zone would otherwise show a sync that ran this morning as
    # yesterday — or, for an owner travelling, silently shift the whole book by
    # a day. The business's day is the one worth showing.
    timezone: str = "Asia/Kolkata"
    must_change_password: bool = False
    #: This session is in the public demonstration workspace, which reads and
    #: never writes. Sent so the client can say so on every screen: a visitor
    #: who does not know the numbers are invented is being misled by a product
    #: whose whole argument is that its numbers are real.
    is_demo: bool = False


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response,
          session: Session = Depends(get_session)) -> LoginResponse:
    email = (body.email or "").strip().lower()
    user = session.scalar(select(models.User).where(models.User.email == email))

    # Check throttling early to deny early without exposing account existence.
    if user is not None and user.active:
        throttle_delay = is_login_throttled(user)
        if throttle_delay is not None:
            log.warning("throttled sign-in attempt for %r (%d failures, retry in %ds)",
                        email, user.login_failures_count, throttle_delay)
            # Deliberately NOT audited, and this replaced an entry that was.
            #
            # The burst is already in the record: every failure that moved the
            # counter appended a LOGIN_FAILED, and those are what show a
            # credential-stuffing run. An entry *here* adds nothing to that
            # picture and costs the one thing an audit log cannot afford — a
            # write on an unauthenticated path with no bound on it.
            # `is_login_throttled` reads the counter without incrementing it, so
            # this branch fires for every request for the whole backoff window,
            # and there is no rate limiter in front of /auth/login. Anyone who
            # can reach the endpoint could therefore grow `audit_entries`
            # without limit, degrading the read and verify surface of the log
            # that exists to catch them.
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                               _THROTTLED.format(delay=throttle_delay))

    # Always spend exactly one PBKDF2 verification, against a real hash, even
    # when the account is absent, inactive, or has no password set. Otherwise the
    # 240k-iteration hash runs only for real, password-bearing accounts, and the
    # ~35x response-time gap turns the deliberately uniform `_REJECTED` message
    # into an account-enumeration oracle — it tells an attacker which addresses
    # exist, which is the exact thing the single message is meant to hide.
    # `verify_password` returns False early (no hashing) on a missing/blank hash,
    # so the sentinel must be a real hash and must be used whenever the row has
    # none.
    stored = user.password_hash if (user is not None and user.password_hash) else _SENTINEL_HASH
    # Computed on its own line, before the guard below — folding it into
    # `user is None or ... or not verify_password(...)` would let `user is None`
    # short-circuit the `or` and skip the hash for absent accounts, which is the
    # very timing gap this closes. Always one verification, every path.
    password_ok = verify_password(body.password, stored)
    if user is None or not user.active or not password_ok:
        log.info("failed sign-in for %r", email)
        # Record the failure if we have a user to update. Failures on non-existent
        # accounts are not tracked (would require creating them, which would leak).
        if user is not None and user.active:
            record_login_failure(user)
            audit.append(
                session, organization_id=user.organization_id,
                action=audit.LOGIN_FAILED, actor_user_id=user.user_id,
                actor_label=email, actor_role=user.role or "",
                subject_type="USER", subject_id=user.user_id,
                detail={"email": email,
                        "consecutive_failures": int(user.login_failures_count or 0),
                        "user_agent": (request.headers.get("user-agent") or "")[:255]})
            # `commit`, not `flush`: this handler raises HTTPException below, and
            # `get_session` rolls back on any exception — a flush would be undone
            # with it, so the counter never persisted and throttling never
            # engaged (5 failures stayed 0). Commit the increment before raising.
            #
            # The audit entry above is in the same transaction and needs the
            # same commit for the same reason, which is why it is written before
            # this line and not after the raise. A failed sign-in that leaves no
            # row is the case an audit log is most often opened to look at.
            session.commit()
        # A failed attempt against an address with no account is deliberately
        # *not* recorded. There is no organization to file it under — the chain
        # is scoped per tenant — and writing one keyed on a guessed address
        # would build a register of which addresses somebody probed, in a table
        # the tenant can export. The application log line above keeps it
        # visible to an operator without persisting it as tenant data.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _REJECTED)

    # Password verified; reset the failure counter.
    reset_login_failures(user)

    # Opportunistic upgrade: a hash made at a lower work factor is replaced now
    # that the correct password is in hand, which is the only moment it can be.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    user.last_login_at = datetime.now(timezone.utc)
    session.flush()

    org = session.get(models.Organization, user.organization_id)

    token, opened = open_session(session, user, request.headers.get("user-agent"))
    audit.append(
        session, organization_id=user.organization_id,
        action=audit.LOGIN_SUCCEEDED, actor_user_id=user.user_id,
        actor_label=user.email or email, actor_role=user.role or "",
        subject_type="USER", subject_id=user.user_id,
        # The session id, not the token. The token is the credential; a log
        # holding one would be a log worth stealing, which is the same reason
        # `user_sessions` is excluded from the tenant export.
        detail={"session_id": opened.session_id,
                "user_agent": (request.headers.get("user-agent") or "")[:255]})
    # Committed here, not left to the request teardown. The token about to go out
    # names that row, and a token naming a row nobody else can read yet is a dead
    # credential — the same "a flush is invisible outside its own transaction"
    # trap CLAUDE.md §4 describes, except the invisible thing is the session
    # itself. Every path that mints a token commits before handing it over.
    session.commit()
    # The browser's copy goes in an httpOnly cookie it cannot read. The token is
    # still in the body for programmatic callers — scripts, tests, anything that
    # is not a browser and so has nowhere for a cookie to live and no ambient
    # sending of it to defend against.
    set_session_cookie(response, token)

    return LoginResponse(
        token=token,
        user_id=user.user_id, organization_id=user.organization_id,
        role=user.role, name=user.name, email=user.email,
        currency=(getattr(org, "currency", None) or settings.DEFAULT_CURRENCY),
        timezone=(getattr(org, "timezone", None) or clock.DEFAULT_ZONE),
        must_change_password=user.must_change_password,
        is_demo=is_demo_org(user.organization_id))


class SessionView(BaseModel):
    """One row of "where am I signed in", as the person should read it."""
    session_id: str
    issued_at: datetime
    last_seen_at: datetime
    user_agent: str = ""
    #: The session this very request is riding on. Marked so the list can say
    #: "this device" rather than inviting someone to guess and revoke the wrong
    #: one.
    current: bool = False


@router.get("/me", response_model=LoginResponse)
def me(request: Request, session: Session = Depends(get_session),
       principal: Principal = Depends(current_principal)) -> LoginResponse:
    """Who the current cookie belongs to.

    The app used to rebuild its whole session — role, currency, timezone — from
    a blob in `localStorage`, because that blob held the token too. With the
    token in an httpOnly cookie the browser cannot read, the app has to ask, and
    this is the ask. It also means a role change or a revoked session is noticed
    on the next page load rather than at the first request that happens to fail.
    """
    user = session.get(models.User, principal.user_id)
    org = session.get(models.Organization, principal.organization_id)
    return LoginResponse(
        # Deliberately empty: the caller already holds the cookie, and echoing
        # the token into a readable body would undo the point of the cookie.
        token="",
        user_id=principal.user_id, organization_id=principal.organization_id,
        role=principal.role.value, name=principal.name, email=principal.email or "",
        currency=(getattr(org, "currency", None) or settings.DEFAULT_CURRENCY),
        timezone=(getattr(org, "timezone", None) or clock.DEFAULT_ZONE),
        must_change_password=bool(user.must_change_password) if user else False)


@router.post("/logout")
def logout(response: Response, session: Session = Depends(get_session),
           principal: Principal = Depends(current_principal)) -> dict:
    """End this session, on the server.

    Signing out used to be `localStorage.removeItem`, which ended nothing: the
    token stayed valid for its full life, so a session signed out on a shared
    machine was still a working credential to anyone who had copied it.
    """
    if principal.session_id:
        revoke_session(session, principal.session_id)
        audit.append(
            session, organization_id=principal.organization_id,
            action=audit.SESSION_ENDED, actor=principal,
            subject_type="USER_SESSION", subject_id=principal.session_id, detail={"scope": "THIS_SESSION"})
    session.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(response: Response, session: Session = Depends(get_session),
               principal: Principal = Depends(current_principal)) -> dict:
    """End every session for this account, including this one.

    The button for "I think someone else has my session". It ends this device
    too, deliberately: a person reaching for this wants a clean slate, and one
    surviving session they forgot about is the thing they were trying to remove.
    """
    ended = revoke_all_sessions(session, principal.user_id)
    audit.append(
        session, organization_id=principal.organization_id,
        action=audit.SESSIONS_ENDED_ALL, actor=principal,
        subject_type="USER", subject_id=principal.user_id,
        detail={"scope": "EVERY_SESSION", "ended": int(ended)})
    session.commit()
    clear_session_cookie(response)
    return {"ok": True, "ended": ended}


@router.get("/sessions", response_model=list[SessionView])
def list_sessions(session: Session = Depends(get_session),
                  principal: Principal = Depends(current_principal)) -> list[SessionView]:
    """This account's live sessions, newest first."""
    rows = session.scalars(
        select(models.UserSession)
        .where(models.UserSession.user_id == principal.user_id,
               models.UserSession.revoked_at.is_(None))
        .order_by(models.UserSession.issued_at.desc())
    ).all()
    return [
        SessionView(
            session_id=r.session_id,
            issued_at=clock.aware(r.issued_at),
            last_seen_at=clock.aware(r.last_seen_at),
            user_agent=r.user_agent or "",
            current=(r.session_id == principal.session_id),
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}")
def revoke_one(session_id: str, session: Session = Depends(get_session),
               principal: Principal = Depends(current_principal)) -> dict:
    """End one named session — "sign out that other laptop"."""
    row = session.get(models.UserSession, session_id)
    # Ownership is checked, and a session belonging to somebody else answers the
    # same 404 as one that does not exist. Distinguishing them would turn this
    # endpoint into a way to ask whether a given session id is live.
    if row is None or row.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session")
    revoke_session(session, session_id)
    audit.append(
        session, organization_id=principal.organization_id,
        action=audit.SESSION_ENDED, actor=principal,
        subject_type="USER_SESSION", subject_id=session_id,
        detail={"scope": "NAMED_SESSION",
                "was_current": session_id == principal.session_id})
    session.commit()
    return {"ok": True}
