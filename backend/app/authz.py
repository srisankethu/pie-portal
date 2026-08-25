"""Authorization for the Commercial Decision Platform.

Extends the existing HMAC-token approach (same ``AUTH_SECRET``, same stateless
signing) to the spec's three roles and DB-backed users. A token carries
``user_id`` + ``organization_id`` + ``session_id``, and **both identity and
role are resolved from rows on every request**, so neither can be forged in the
token.

The organization on a token is a *claim*. What honours it is an ACTIVE row in
``organization_memberships`` for that (user, organization) pair — see
``load_principal``. That indirection is the whole of the tenant boundary at the
application layer, and it replaced a check that could not survive a person
belonging to two workspaces: the old test was ``user.organization_id ==
token_org``, comparing a claim against a column that could only ever hold one
answer. Now the claim has to be *found*, and a token naming an organization the
holder was never granted — or was granted and has since lost — resolves to no
principal at all.

Role comes from that same membership row, which is why the same person can be
an owner in one workspace and a salesperson in another without either
statement being true of them in general.

Scope (§2, §14):
- SALESPERSON → only their assigned customers' decisions; RESTRICTED decision
  types are excluded.
- SALES_MANAGER / OWNER → whole organization (team-hierarchy scoping is a later
  refinement; V1 read model embeds assignment on the customer only).

This module owns role + assignment resolution and API scope enforcement.
``memberships.py`` owns the grants themselves and ``entitlements.py`` owns what
the organization may use. Field-level cost/margin redaction (context assembly)
is a later phase and not done here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, memberships, tenancy
from .commercial import ownership
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
    #: Which `UserSession` this request is riding on. Carried so signing out can
    #: end *this* session without touching the person's other devices, and so
    #: the session list can mark which row is the one you are reading it from.
    session_id: Optional[str] = None
    #: This request is riding a session in the public demonstration workspace,
    #: which anyone can open without an account. `current_principal` refuses it
    #: every unsafe method, so the flag is a gate rather than a label — the same
    #: arrangement as `must_change_password` above.
    is_demo: bool = False

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


def issue_token(user_id: str, organization_id: str, session_id: str) -> str:
    # Fractional seconds, not whole ones. `load_principal` retires a token whose
    # `iat` predates the account's `password_changed_at`, and at whole-second
    # resolution the token being replaced shares a second with the change that
    # retires it — so the compromised session survived exactly the case this is
    # for. Older tokens carrying an integer still parse as floats.
    #
    # `sid` names the `UserSession` row this token is a bearer of. It is what
    # makes the token revocable: without it the signature was the whole of the
    # authority and nothing short of changing the password could withdraw it.
    body = {"uid": user_id, "oid": organization_id, "sid": session_id,
            "iat": time.time()}
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"{raw}.{_sign(raw.encode())}"


def open_session(db: Session, user: models.User,
                 user_agent: Optional[str] = None,
                 organization_id: Optional[str] = None,
                 ) -> tuple[str, models.UserSession]:
    """Start a session for `user` in one organization, and return `(token, row)`.

    The one way to mint a token. Every caller that used to reach for
    `issue_token` directly goes through here, because a token whose `sid` names
    no row is rejected on its first request — issuing one is not a shortcut, it
    is a broken sign-in.

    ``organization_id`` names the workspace the session acts for and defaults
    to the user's home organization, which is what sign-in wants. Switching
    workspaces passes another one — and passing it here is not a grant: the
    caller has already found an ACTIVE membership for the pair, and
    ``load_principal`` finds it again on every request the session goes on to
    make. A session opened against an organization the holder is not a member
    of would resolve to no principal at all, which is a dead token rather than
    a hole.
    """
    row = models.UserSession(
        session_id="sess_" + secrets.token_urlsafe(24),
        user_id=user.user_id,
        organization_id=organization_id or user.organization_id,
        issued_at=clock.now(),
        last_seen_at=clock.now(),
        # Truncated to the column, not rejected: a long or absent User-Agent is
        # a cosmetic detail of the session list, never a reason to fail a
        # sign-in.
        user_agent=(user_agent or "")[:256] or None,
    )
    db.add(row)
    db.flush()
    return issue_token(user.user_id, row.organization_id, row.session_id), row


def verify_token(token: str) -> Optional[tuple[str, str, str, float]]:
    """`(user_id, organization_id, session_id, issued_at)`, or None if not ours.

    `iat` comes back because it is how a credential change retires the sessions
    opened before it — see `load_principal`.

    A token with no `sid` is refused rather than accepted-without-a-session.
    Those are the tokens minted before sessions became rows; honouring them
    would leave exactly the unrevocable credential this change exists to end,
    and the cost of refusing is that everyone signs in once more.
    """
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(raw.encode())):
        return None
    try:
        body = json.loads(_b64pad(raw))
        sid = str(body.get("sid") or "")
        if not sid:
            return None
        return str(body["uid"]), str(body["oid"]), sid, float(body.get("iat") or 0)
    except Exception:  # noqa: BLE001
        return None


# ── principal resolution ─────────────────────────────────────────────────────
#: How stale `last_seen_at` may get before a request advances it. The idle
#: timeout is measured against this column, so writing it on every request would
#: put a write in front of every read — on SQLite, a write lock in front of every
#: read, which is the contention §4 of CLAUDE.md is about. One minute is far
#: finer than the 12-hour timeout it feeds, so the imprecision cannot matter.
LAST_SEEN_RESOLUTION_SECONDS = 60


def revoke_session(db: Session, session_id: str) -> None:
    """End one session. Idempotent: an already-revoked row keeps its first time."""
    row = db.get(models.UserSession, session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = clock.now()


def revoke_all_sessions(db: Session, user_id: str,
                        except_session_id: Optional[str] = None) -> int:
    """End every live session for a user. Returns how many were ended.

    `except_session_id` keeps the caller's own session alive, which is what
    "sign out my other devices" means — signing yourself out in the act of
    securing the account is a good way to not finish securing the account.
    """
    rows = db.scalars(
        select(models.UserSession).where(
            models.UserSession.user_id == user_id,
            models.UserSession.revoked_at.is_(None),
        )
    ).all()
    now = clock.now()
    ended = 0
    for row in rows:
        if except_session_id is not None and row.session_id == except_session_id:
            continue
        row.revoked_at = now
        ended += 1
    return ended


def load_principal(session: Session, token: str) -> Optional[Principal]:
    parsed = verify_token(token)
    if parsed is None:
        return None
    user_id, org_id, session_id, issued_at = parsed

    # Announce the tenant before the first query, not after it.
    #
    # This is the only point in a request where that is possible, and it is
    # possible only because the organization is in the *signed token*: reading
    # it costs no query, so the setting is in place before `session.get` below
    # touches a tenant-scoped table. A setting established in `get_session`
    # could not know the tenant — `current_principal` depends on `get_session`,
    # so the session exists first — and one established after these lookups
    # would leave the two queries that decide who you are as the only ones
    # running unscoped.
    #
    # The token is a *claim*, not proof: it says which tenant this request is
    # acting as. The two checks below confirm the claim against the rows, and
    # under row-level security the policy enforces the same thing in SQL — a
    # user id from another tenant simply does not come back. Both are kept.
    # The Python check is the one that works on SQLite, where there is no
    # policy at all, and a control that exists on one dialect only is not a
    # control.
    tenancy.set_tenant(session, org_id)

    user = session.get(models.User, user_id)
    if user is None or not user.active:
        return None

    # **The tenant check.** The token said which organization this request acts
    # for; this is where that claim is honoured or refused, and it is a lookup
    # rather than a comparison on purpose.
    #
    # It used to read ``user.organization_id != org_id``. That is a sound check
    # for exactly as long as a person can belong to one organization, and it
    # stops being a check at all the moment they can belong to two — the column
    # holds one of their workspaces, so half of a legitimate multi-workspace
    # user's requests would fail and, worse, the shape invites "fix" it by
    # dropping the comparison. Requiring an ACTIVE membership for the *pair* is
    # both the correct answer for two workspaces and a stricter one for one:
    # a membership can be ended, and the moment it is, every session riding on
    # it stops resolving. A column cannot be revoked.
    #
    # Under row-level security the policy enforces the same thing in SQL — a
    # membership belonging to another tenant does not come back. Both are kept:
    # this is the one that works on SQLite, where there is no policy at all,
    # and a control that exists on one dialect only is not a control.
    role = memberships.role_in(session, user.user_id, org_id)
    if role is None:
        return None

    # The session row is the authority; the token is only a claim to it. A row
    # that is missing (revoked and purged, or a `sid` that never existed) or
    # revoked ends the request here — this is the check that makes signing out
    # mean something on the server.
    row = session.get(models.UserSession, session_id)
    if row is None or row.revoked_at is not None:
        return None
    if row.user_id != user.user_id or row.organization_id != org_id:
        return None

    now = clock.now()
    # Absolute age, measured from the row rather than the token's `iat`: they
    # agree today, and the row is the one a future refresh could not silently
    # extend past its issue date.
    issued = clock.aware(row.issued_at)
    if issued is not None and (now - issued).total_seconds() > settings.SESSION_MAX_AGE_SECONDS:
        return None
    # Idle age. A session nobody has used for the timeout is over, however
    # recently it was issued.
    seen = clock.aware(row.last_seen_at)
    if seen is not None and (now - seen).total_seconds() > settings.SESSION_IDLE_TIMEOUT_SECONDS:
        return None

    # A token minted before the password changed is no longer a valid session.
    # Without this, changing a password left every session opened with the old one
    # working indefinitely — which is precisely what the change is for when a
    # credential is thought to be compromised. Whole-second resolution, so a token
    # `iat` carries fractional seconds so the replacement token the change hands
    # back — minted after the timestamp is stamped — is strictly newer, while the
    # token that made the request is strictly older and dies here.
    #
    # Kept alongside the row checks rather than replaced by them: a password
    # change revokes the rows too, and both paths ending the session is the
    # point. If one is ever missed, the other still closes it.
    changed = clock.aware(user.password_changed_at)
    if changed is not None and issued_at < changed.timestamp():
        return None

    # Advance the idle clock, but only once a minute — see
    # LAST_SEEN_RESOLUTION_SECONDS. `flush`, not `commit`: this runs inside the
    # request's own transaction, which `get_session` commits.
    if seen is None or (now - seen).total_seconds() >= LAST_SEEN_RESOLUTION_SECONDS:
        row.last_seen_at = now
        session.flush()

    # ``org_id``, not ``user.organization_id``: the request acts for the
    # organization the session was opened against, which for somebody with two
    # workspaces is the one they switched into and not the one their identity
    # row happens to be filed under.
    return Principal(user_id=user.user_id, organization_id=org_id,
                     role=role, name=user.name, email=user.email,
                     must_change_password=bool(user.must_change_password),
                     session_id=row.session_id,
                     is_demo=is_demo_org(org_id))


def is_demo_org(organization_id: str) -> bool:
    """Is this the public demonstration workspace?

    Read from settings on every call rather than captured at import: a test that
    points the deployment at a demo organization must not have to reload this
    module, and the cost is a string comparison.

    Empty configuration means *no* organization is the demo one — never "every
    organization", which is the direction this must not be wrong in.
    """
    configured = (settings.PUBLIC_DEMO_ORG_ID or "").strip()
    return bool(configured) and organization_id == configured


#: What an account owing a password change may reach. Deliberately tiny:
#: anything wider is a way to keep using a credential somebody else issued.
#: `/api/v1/auth/login` does not appear because it does not depend on this at
#: all. Signing out does, and is allowed — refusing it would mean an account
#: holding an issued password could not put itself down, only keep sitting there.
PASSWORD_CHANGE_PATH = "/api/v1/admin/me/password"
PASSWORD_CHANGE_EXEMPT_PATHS = frozenset({
    PASSWORD_CHANGE_PATH,
    "/api/v1/auth/logout",
    "/api/v1/auth/logout-all",
    "/api/v1/auth/me",
})

# ── transport ────────────────────────────────────────────────────────────────
#: The browser's copy of the token. `httpOnly`, so script on the page cannot
#: read it — which is the whole point of moving it out of `localStorage`, where
#: any injected script or extension could lift it and where it survived the
#: browser being closed as a plain readable string.
SESSION_COOKIE = "pie_session"

#: Sent by the app on every request. The CSRF control for cookie-authenticated
#: writes: a cross-site form POST cannot set a header at all, and a cross-site
#: `fetch` that sets one triggers a preflight, which fails because this API
#: allows no foreign origin. `SameSite=Lax` already blocks the same attack; this
#: is the second lock, and it is a header rather than a double-submit cookie for
#: a concrete reason — the Vercel edge proxy rebuilds responses through
#: `new Headers()`, which folds multiple `Set-Cookie` values into one and
#: corrupts them, so the design gets exactly one cookie.
APP_HEADER = "x-pie-app"

#: Methods that cannot change anything, and so do not need the CSRF check.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        # Only over TLS in production. Not in development, where the app is
        # served over plain http on localhost and a Secure cookie would simply
        # never be sent — making every dev sign-in appear to succeed and every
        # subsequent request 401.
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    # Same path and flags as it was set with; a delete that does not match is a
    # cookie that stays.
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=settings.is_production, samesite="lax")


def current_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Principal:
    # Two transports, one token. The header is for programmatic callers and is
    # checked first because a caller that sets it means it. The cookie is the
    # browser's, and is the only one that is ambient — sent automatically,
    # therefore the only one that needs the CSRF check below.
    from_cookie = False
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    else:
        token = (request.cookies.get(SESSION_COOKIE) or "").strip()
        from_cookie = bool(token)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    # Checked before the token is resolved: a request that cannot pass this is
    # refused whether or not its credential is good, so the check cannot be used
    # to ask whether a session is live.
    if (from_cookie
            and request.method.upper() not in SAFE_METHODS
            and request.headers.get(APP_HEADER) is None):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Cookie-authenticated writes must send the {APP_HEADER} header.")

    principal = load_principal(session, token)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    # `must_change_password` was set by the seed and by every owner-issued reset,
    # read in exactly two places — the login response and a label on the admin
    # grid — and enforced nowhere. So `change-me-now` stayed live on every seeded
    # account indefinitely, and the flag named a control that did not exist.
    # Enforced here rather than in the sign-in screen: a rule the client owns is a
    # rule that anything not the client ignores.
    if (principal.must_change_password
            and request.url.path.rstrip("/") not in PASSWORD_CHANGE_EXEMPT_PATHS):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Change your password before using this account. It is still the one "
            "you were issued.")
    # The demonstration workspace reads and never writes. Enforced here, at the
    # one seam every authenticated route passes through, and by *method* rather
    # than by a list of endpoints — a list is a thing to forget to add to, and
    # the day somebody forgets, an unauthenticated stranger is writing to a
    # tenant. Fail-closed and enumeration-free is worth what it costs.
    #
    # What it costs is real and is not hidden: the read-only POSTs go too. The
    # quote desk and the simulator are POST because they take a body, not
    # because they change anything, so neither works in the demo. Allowlisting
    # them was the obvious fix and was rejected — `quote_support` genuinely does
    # persist a QUOTE_CONTEXT decision, so the allowlist would have had to be
    # right about which POSTs write, forever, including for endpoints written
    # later by somebody who has never read this comment.
    if principal.is_demo and request.method.upper() not in SAFE_METHODS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This is the demonstration workspace. It shows what the product "
            "does on made-up data and saves nothing — sign up for an account to "
            "make changes.")
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
                      customer: Optional[models.Customer],
                      session: Session) -> bool:
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

    Resolved through `commercial/ownership` rather than by comparing
    ``assigned_user_id`` directly. The column is Zoho's — the sync rewrites it
    from whoever was on the last invoice — while an account handed to somebody
    by hand lives in the typed table. Reading the column here would hide a
    reassigned account from the person it was given to and leave it visible to
    the person it was taken from, which is the failure this rule exists to
    prevent. That is also why the session is a parameter: the answer is a row,
    not a field.
    """
    if customer is None or customer.organization_id != principal.organization_id:
        return False
    if principal.is_salesperson:
        return ownership.owned_by(session, customer, principal.user_id)
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


# ── login throttling ─────────────────────────────────────────────────────────
#: Number of consecutive failures before throttling activates.
LOGIN_THROTTLE_THRESHOLD = 5
#: Base backoff delay in seconds (doubles for each failure past threshold).
LOGIN_THROTTLE_BASE_DELAY_SECONDS = 30


def is_login_throttled(user: models.User) -> Optional[int]:
    """Returns the remaining backoff delay in seconds if login is throttled.

    Throttling activates after LOGIN_THROTTLE_THRESHOLD consecutive failures.
    The delay is 30s * 2^(failures - threshold), so:
    - 5 failures: 30s
    - 6 failures: 60s
    - 7 failures: 120s, etc.

    Returns None if the account is not throttled.
    """
    if user.login_failures_count < LOGIN_THROTTLE_THRESHOLD:
        return None
    if user.login_failures_last_at is None:
        return None

    now = datetime.now(timezone.utc)
    failures_since_threshold = user.login_failures_count - LOGIN_THROTTLE_THRESHOLD
    delay_seconds = LOGIN_THROTTLE_BASE_DELAY_SECONDS * (2 ** failures_since_threshold)
    # `clock.aware`, not the bare column: SQLite hands `login_failures_last_at`
    # back tz-naive, and adding a timedelta then comparing against the tz-aware
    # `now` raises "can't compare offset-naive and offset-aware datetimes" — a
    # 500 on the throttle path, i.e. exactly when the account is under attack.
    # `load_principal` already normalizes every other stored timestamp this way.
    next_allowed = clock.aware(user.login_failures_last_at) + timedelta(seconds=delay_seconds)

    if now < next_allowed:
        return int((next_allowed - now).total_seconds()) + 1
    return None


def record_login_failure(user: models.User) -> None:
    """Record a failed login attempt. Updates the user row in-place."""
    user.login_failures_count += 1
    user.login_failures_last_at = datetime.now(timezone.utc)


def reset_login_failures(user: models.User) -> None:
    """Clear failure count on successful login."""
    user.login_failures_count = 0
    user.login_failures_last_at = None
