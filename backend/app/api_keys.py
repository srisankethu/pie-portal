"""API keys: minting, verifying, and turning one into an ordinary Principal.

The public resolution API (``routers/resolve.py``) is authenticated by a key
rather than a sign-in, and the whole design of this module is one sentence:
**an API key is a recipient like any other** (CLAUDE.md §1). It resolves to the
same :class:`authz.Principal` a browser session resolves to, carrying the same
``organization_id`` and the same :class:`Role` — so the cost/margin withholding
in ``commercial.quote_service.project`` and the scope checks in ``authz`` act
on an API caller through the code that already exists, rather than through a
second set of rules written for machines.

That is the reuse §2 asks for, and it is load-bearing rather than tidy. The
defect this repository keeps re-finding is a number that was correctly withheld
on one path and quietly served on another — ``filterCounts.MFLOOR``, then the
rule-code walk on ``quote-intelligence/assess``. A second principal type for
machines would have been a third such path, and it would have been the one
nobody looked at.

**The credential.** ``pie_<key_id>_<secret>``. The key id is the non-secret
lookup handle: it goes in logs, in error messages, and on the revoke endpoint.
The secret is 32 random bytes, hashed with the same PBKDF2 helper a password
uses, shown once at creation and never again — a credential a support engineer
can read out of a table is not one the holder can say only they hold.

Two prefixed halves rather than one opaque string, because the alternative is
hashing every live key on every request to find which row was presented. That
is either a table scan of PBKDF2 verifications or a second, unsalted index of
the secret; the first does not scale and the second is the thing being avoided.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, ratelimit, tenancy
from .authz import Principal
from .config import settings
from .db import get_session
from .domain import models
from .domain.enums import Role
from .passwords import hash_password, verify_password

log = logging.getLogger("pie_portal.api_keys")

#: How the credential is recognised. Prefixed so a key pasted into an issue or
#: committed to a repository is greppable by a secret scanner, and so a caller
#: who sends their platform session token instead gets a clear refusal rather
#: than a puzzling one.
PREFIX = "pie"

#: How long the key id is, in bytes before hex encoding. Long enough not to
#: collide, short enough to read back over a phone when somebody is asking
#: which key is failing. Hex rather than url-safe base64 because the id sits
#: between two underscores in the credential and must not contain one — see
#: :func:`split`.
_KEY_ID_BYTES = 12
_SECRET_BYTES = 32

#: The window the per-key allowance is measured over. A minute rather than an
#: hour: the thing this limit exists to make expensive is a bisection sweep
#: over ``proposed_price`` (CLAUDE.md §1), and a sweep is fast or it is not
#: worth doing. An hourly bucket would let two hundred probes through in the
#: first second and then refuse the partner's ordinary traffic for an hour.
RATE_WINDOW_SECONDS = 60.0

#: One message for every authentication failure. Distinguishing "no such key"
#: from "wrong secret" from "revoked" tells a caller which half of a guess was
#: right — the same reasoning as ``platform_auth._REJECTED``.
_REJECTED = "Invalid or revoked API key"


@dataclass(frozen=True)
class IssuedKey:
    """A freshly minted key: the row, and the secret that will never be shown
    again."""

    row: models.ApiKey
    secret: str


def _compose(key_id: str, secret: str) -> str:
    return f"{PREFIX}_{key_id}_{secret}"


def split(presented: str) -> Optional[tuple[str, str]]:
    """``(key_id, secret)`` from a presented credential, or None if malformed.

    ``maxsplit=2``, and the key id is hex, and both halves of that matter. The
    secret comes from ``token_urlsafe``, whose alphabet **includes the
    underscore** — so an unbounded split cuts a perfectly valid key into four
    pieces and rejects it, intermittently, for roughly a third of the keys ever
    minted. Bounding the split makes the last field "everything after the
    second separator", which is the secret however many underscores it holds;
    the id being hex is what guarantees the second separator is the right one.

    Returns None rather than raising: a malformed key is an authentication
    failure like any other, and this function has no business deciding the
    status code.
    """
    parts = (presented or "").strip().split("_", 2)
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def issue(session: Session, organization_id: str, *, name: str,
          role: Role = Role.SALESPERSON,
          rate_limit_per_minute: Optional[int] = None,
          created_by_user_id: Optional[str] = None) -> IssuedKey:
    """Mint a key for one organization. Does **not** commit.

    Uncommitted deliberately, following ``trust.audit.append``: the caller owns
    the transaction, and a key row that commits separately from the audit entry
    describing it is a credential with no record of who created it.
    """
    key_id = secrets.token_hex(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    row = models.ApiKey(
        key_id=key_id,
        organization_id=organization_id,
        name=(name or "").strip()[:120],
        secret_hash=hash_password(secret),
        secret_hint=secret[-4:],
        role=role.value,
        rate_limit_per_minute=(settings.API_KEY_RATE_LIMIT_PER_MINUTE
                               if rate_limit_per_minute is None
                               else max(0, int(rate_limit_per_minute))),
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    session.flush()
    return IssuedKey(row=row, secret=_compose(key_id, secret))


def revoke(session: Session, organization_id: str, key_id: str) -> bool:
    """End a key. Idempotent — an already-revoked row keeps its first time.

    Scoped to the organization rather than trusting the id: ``key_id`` is the
    half of the credential that travels in the clear, so a caller holding one
    tenant's key knows the shape of the argument. A foreign id is treated
    exactly like an unknown one.
    """
    row = session.get(models.ApiKey, key_id)
    if row is None or row.organization_id != organization_id:
        return False
    if row.revoked_at is None:
        row.revoked_at = clock.now()
    return True


def keys_for(session: Session, organization_id: str) -> list[models.ApiKey]:
    return list(session.scalars(
        select(models.ApiKey)
        .where(models.ApiKey.organization_id == organization_id)
        .order_by(models.ApiKey.created_at.desc())))


def to_dict(row: models.ApiKey) -> dict:
    """The management view of a key. Never the secret, and never its hash."""
    return {
        "key_id": row.key_id,
        "name": row.name,
        "role": row.role,
        "secret_hint": row.secret_hint,
        "rate_limit_per_minute": row.rate_limit_per_minute,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by_user_id": row.created_by_user_id,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "active": row.revoked_at is None,
    }


#: How coarsely ``last_used_at`` is advanced, for the reason
#: ``authz.LAST_SEEN_RESOLUTION_SECONDS`` gives: writing it on every call would
#: put a write in front of every read, which on SQLite means a write lock in
#: front of every read.
LAST_USED_RESOLUTION_SECONDS = 60


#: How many failed attempts one key id may draw in a minute before the door
#: shuts on it. Deliberately generous — a partner whose deployment is holding a
#: rotated key will burn several before anyone notices, and refusing them
#: faster helps nobody.
FAILED_ATTEMPTS_PER_MINUTE = 10


def _authenticate(session: Session, presented: str) -> Optional[models.ApiKey]:
    """The live key this credential is, or None. Never says which check failed.

    **Two things this deliberately does not do, and why.**

    It does not verify a dummy hash for an unknown key id, the way
    ``platform_auth.login`` does. That sentinel exists so an *email address* —
    something an attacker can enumerate from a company website — costs the same
    whether or not it names an account. A key id is 96 bits of randomness that
    appears nowhere but in the credential itself; the only thing the timing
    difference reveals is that a key id somebody already holds is real, and
    paying PBKDF2 on every malformed request to hide that would buy a denial of
    service rather than a secret. Copying the mitigation here would be
    cargo-culting one whose threat does not apply.

    It does rate-limit *failed* attempts per key id, which is the shape that
    could ever succeed. Guessing the 256-bit secret for a key id you hold is
    infeasible; bounding it costs one counter and removes the argument.
    Attempts against ids that match no row never reach ``verify_password`` at
    all, so they cost a lookup rather than a key derivation.
    """
    parsed = split(presented)
    if parsed is None:
        return None
    key_id, secret = parsed

    if ratelimit.too_many("api_key_failures", key_id,
                          limit=FAILED_ATTEMPTS_PER_MINUTE,
                          window_seconds=RATE_WINDOW_SECONDS):
        log.warning("too many failed attempts against api key %s", key_id)
        return None

    # The one narrow cross-tenant question, exactly as sign-in asks it. A no-op
    # on SQLite; see `tenancy.adopt_tenant_for_api_key`.
    tenancy.adopt_tenant_for_api_key(session, key_id)

    row = session.get(models.ApiKey, key_id)
    if row is None:
        return None
    if not verify_password(secret, row.secret_hash):
        # Logged at the id, which is the non-secret half. A wrong secret for a
        # real key id is worth being able to see; the secret itself is not.
        log.warning("api key %s presented with a bad secret", key_id)
        return None
    if row.revoked_at is not None:
        log.info("api key %s was presented after revocation", key_id)
        return None
    # Only a *successful* authentication clears the failure counter, so a key
    # in ordinary use never accumulates one and a key being guessed at never
    # gets it reset by the guesser.
    ratelimit.reset_key("api_key_failures", key_id)
    return row


def _touch(session: Session, row: models.ApiKey) -> None:
    now = clock.now()
    last = clock.aware(row.last_used_at)
    if last is None or (now - last).total_seconds() >= LAST_USED_RESOLUTION_SECONDS:
        row.last_used_at = now
        session.flush()


def principal_for(session: Session, presented: str) -> tuple[Principal, models.ApiKey]:
    """The principal this credential acts as, or raise 401.

    The role comes from the row, and an unreadable one resolves to the
    narrowest role rather than to the widest. That direction is the whole
    point: a corrupted or hand-edited ``role`` column must cost the caller
    information, never grant it.
    """
    row = _authenticate(session, presented)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _REJECTED,
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        role = Role(row.role)
    except ValueError:
        log.error("api key %s carries an unknown role %r; treating it as the "
                  "narrowest", row.key_id, row.role)
        role = Role.SALESPERSON
    _touch(session, row)
    principal = Principal(
        # Prefixed so nothing downstream can mistake this for a user id: it is
        # written into `confirmed_code_mappings.confirmed_by_user_id` and into
        # the audit log, and a bare key id sitting in a user column would read
        # as a person who does not exist.
        user_id=f"apikey:{row.key_id}",
        organization_id=row.organization_id,
        role=role,
        name=row.name or f"API key {row.key_id}",
        email=None,
    )
    return principal, row


def enforce_rate_limit(row: models.ApiKey) -> int:
    """Spend one of this key's requests, or raise 429. Returns what is left.

    Per key rather than per tenant, and stated as a speed bump rather than a
    quota — ``app/ratelimit.py`` says why the counters being in-process makes
    the effective limit scale with the number of replicas.
    """
    limit = int(row.rate_limit_per_minute or 0)
    if ratelimit.too_many("api_key", row.key_id, limit=limit,
                          window_seconds=RATE_WINDOW_SECONDS):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"This API key is limited to {limit} requests per minute.",
            headers={"Retry-After": str(int(RATE_WINDOW_SECONDS))})
    return ratelimit.remaining("api_key", row.key_id, limit=limit,
                               window_seconds=RATE_WINDOW_SECONDS)


@dataclass(frozen=True)
class ApiCaller:
    """Who is calling, and the key they called with.

    The key travels alongside the principal rather than inside it because
    ``Principal`` is shared with the browser path and must not grow a field
    only one caller can populate (§5, interface segregation). The rate limit
    and the ``X-RateLimit-*`` headers need the row; nothing else does.
    """

    principal: Principal
    key: models.ApiKey
    remaining: int


#: Declared as security schemes rather than as plain headers, because these
#: routes publish an OpenAPI document a partner generates a client from. A bare
#: ``Header(default=None)`` produces two optional string parameters, which
#: describes an endpoint anyone may call without a credential — the opposite of
#: what is true — and a generated client would not know to send one at all.
#: ``auto_error=False`` on both so that *neither* header present is refused by
#: :func:`current_caller`'s own message rather than by whichever scheme
#: happened to be evaluated first.
_BEARER = APIKeyHeader(name="Authorization", auto_error=False,
                       scheme_name="bearerApiKey",
                       description="`Bearer pie_<key id>_<secret>`")
_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False,
                       scheme_name="apiKeyHeader",
                       description="The same credential, for clients that "
                                   "cannot set an Authorization header.")


def current_caller(
    authorization: Optional[str] = Depends(_BEARER),
    x_api_key: Optional[str] = Depends(_HEADER),
    session: Session = Depends(get_session),
) -> ApiCaller:
    """FastAPI dependency: authenticate an API key and spend one request.

    Two ways to present it. ``Authorization: Bearer pie_…`` is what an HTTP
    client library does by default and what the published spec documents;
    ``X-API-Key`` is accepted because several ERP middlewares cannot set an
    Authorization header on an outbound webhook at all. The same credential
    either way — this is one door with two handles, not two credentials.
    """
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization.split(" ", 1)[1].strip()
    elif x_api_key:
        presented = x_api_key.strip()
    if not presented:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This endpoint needs an API key in `Authorization: Bearer …` or "
            "`X-API-Key`.",
            headers={"WWW-Authenticate": "Bearer"})
    principal, row = principal_for(session, presented)
    remaining = enforce_rate_limit(row)
    return ApiCaller(principal=principal, key=row, remaining=remaining)
