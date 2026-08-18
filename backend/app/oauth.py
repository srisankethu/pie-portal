"""Zoho's authorization-code flow: the redirect out, and the redirect back.

This existed once and was removed as dead code — it never completed an
authorization end to end. Both reasons were about where the CSRF state lived,
and ``models.OAuthState`` is the fix for both; that docstring carries the
history. What is here is the flow itself, and the three properties that make it
safe to expose the callback publicly:

**The state is the correlation.** The browser arrives at the callback having
followed Zoho's redirect, with no session and no bearer token. It carries the
state token, which is 32 random bytes issued to one organization, so the
callback resolves whose authorization it is completing from the state alone —
which is what the parameter is for. Demanding a header instead is what broke it
before.

**The state is single-use and short-lived.** Ten minutes, and ``consumed_at``
set the moment it is spent, so a replayed redirect is refused rather than
silently re-run against a code Zoho has already invalidated.

**Nothing sensitive is claimable by the redirect.** The exchange creates the
credential, but the browser is handed only a *handoff* token, and redeeming it
requires an authenticated owner in the organization the state named. So the
public half of the flow can prove which authorization completed and can prove
nothing else — the credential itself is only reachable through a session.

Both tokens are stored as ``sha256``, never in the clear: this table is
readable by anything that reaches the database, and a live token is a usable
half of an authorization.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import clock, crypto
from .config import settings
from .domain import models
from .ingestion.zoho_client import ZohoAuthError, ZohoError

log = logging.getLogger("pie_portal.oauth")

#: RFC 6749 §10.12 wants a value an attacker cannot guess; 32 bytes is the
#: usual answer and is what both tokens here use.
TOKEN_BYTES = 32

#: Zoho runs one estate per data centre and a grant is not portable between
#: them: a code issued by ``accounts.zoho.com`` is not redeemable at
#: ``accounts.zoho.in``. The pair is chosen at the start and carried on the
#: state row, because the redirect cannot be trusted to hand it back.
DATA_CENTRES: dict[str, tuple[str, str]] = {
    "in": ("https://accounts.zoho.in", "https://www.zohoapis.in/books/v3"),
    "com": ("https://accounts.zoho.com", "https://www.zohoapis.com/books/v3"),
    "eu": ("https://accounts.zoho.eu", "https://www.zohoapis.eu/books/v3"),
    "com.au": ("https://accounts.zoho.com.au",
               "https://www.zohoapis.com.au/books/v3"),
    "jp": ("https://accounts.zoho.jp", "https://www.zohoapis.jp/books/v3"),
}


class OAuthNotConfigured(RuntimeError):
    """This deployment has no Zoho application registered.

    A configuration state, not a fault: the manual path still works, and the
    screen should say so rather than offering a button that cannot complete.
    That dead button is what got the first implementation deleted.
    """


class StateInvalid(PermissionError):
    """The redirect carried a state this server did not issue, or cannot spend.

    One class for unknown, expired and already-spent on purpose. They are
    different facts to us and the same fact to the browser: telling them apart
    in the response would confirm to an attacker which of their guesses had
    once been real.
    """


def configured() -> bool:
    return bool(settings.ZOHO_OAUTH_CLIENT_ID
                and settings.ZOHO_OAUTH_CLIENT_SECRET
                and settings.ZOHO_OAUTH_REDIRECT_URI)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── the state, out and back ─────────────────────────────────────────────────
def issue_state(session: Session, *, organization_id: str, accounts_base: str,
                api_base: str, connector: str = "zoho") -> tuple[str, models.OAuthState]:
    """Record an authorization about to start; return ``(token, row)``.

    The token is returned once and never again — only its hash is kept, so a
    leaked database cannot be used to complete somebody's authorization.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = models.OAuthState(
        state_hash=_hash(token),
        organization_id=organization_id,
        connector=connector,
        accounts_base=accounts_base,
        api_base=api_base,
        expires_at=clock.now() + timedelta(
            seconds=settings.ZOHO_OAUTH_STATE_TTL_SECONDS))
    session.add(row)
    session.flush()
    return token, row


def consume_state(session: Session, token: str) -> models.OAuthState:
    """Spend a state token exactly once, or refuse.

    Marks ``consumed_at`` before the caller does anything with the result, so a
    redirect delivered twice — a refresh, a retried request, a replay — cannot
    run the exchange twice.
    """
    row = session.get(models.OAuthState, _hash(token or ""))
    if row is None:
        raise StateInvalid("This authorization link is not one we issued.")
    if row.consumed_at is not None:
        raise StateInvalid("This authorization link has already been used.")
    expires = clock.aware(row.expires_at)
    if expires is not None and clock.now() > expires:
        raise StateInvalid("This authorization link has expired. Start again.")
    row.consumed_at = clock.now()
    session.flush()
    return row


def sweep_expired(session: Session, *, older_than_days: int = 1) -> int:
    """Delete states long past use. Returns how many went.

    Kept for a day after expiry rather than deleted on the minute: a support
    question about an authorization that failed an hour ago is answerable while
    the row is there, and unanswerable once it is not.
    """
    cutoff = clock.now() - timedelta(days=older_than_days)
    result = session.execute(
        delete(models.OAuthState).where(models.OAuthState.expires_at < cutoff))
    return int(result.rowcount or 0)


# ── the handoff, back into an authenticated session ─────────────────────────
def issue_handoff(session: Session, state: models.OAuthState,
                  credential_id: str) -> str:
    """Attach a one-time claim code to a completed authorization."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    state.handoff_hash = _hash(token)
    state.credential_id = credential_id
    session.flush()
    return token


def claim_handoff(session: Session, *, organization_id: str,
                  token: str) -> models.OAuthState:
    """The completed authorization this code refers to, for this organization.

    Scoped to the caller's organization, and that is the control the public
    callback leans on: the redirect can prove an authorization finished, and
    only a session inside the right tenant can pick up what it produced.
    """
    row = session.scalar(select(models.OAuthState).where(
        models.OAuthState.handoff_hash == _hash(token or "")))
    if row is None or row.organization_id != organization_id:
        # One answer for "no such code" and "not yours", so the endpoint cannot
        # be used to discover that somebody else's authorization exists.
        raise StateInvalid("That authorization is not available to claim.")
    expires = clock.aware(row.expires_at)
    if expires is not None and clock.now() > expires:
        raise StateInvalid("That authorization has expired. Start again.")
    return row


# ── talking to Zoho ─────────────────────────────────────────────────────────
def authorization_url(token: str, accounts_base: str, *, scope: str) -> str:
    """Where to send the browser. Raises if this deployment has no application."""
    if not configured():
        raise OAuthNotConfigured(
            "This deployment has no Zoho application registered, so it cannot "
            "authorize on your behalf. Connect with a Self Client refresh "
            "token instead.")
    params = {
        "client_id": settings.ZOHO_OAUTH_CLIENT_ID,
        "response_type": "code",
        "scope": scope,
        "redirect_uri": settings.ZOHO_OAUTH_REDIRECT_URI,
        # Zoho issues a refresh token only when explicitly asked, and only on
        # the first consent for a client. Without both of these the exchange
        # returns an access token that dies in an hour and a connection that
        # stops working the same afternoon.
        "access_type": "offline",
        "prompt": "consent",
        "state": token,
    }
    return f"{accounts_base}/oauth/authorize?{urlencode(params)}"


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int


async def exchange_code_for_tokens(code: str, accounts_base: str) -> OAuthTokens:
    """Turn the authorization code into a refresh token.

    The code is valid for minutes and once; the refresh token is what every
    later pull runs on.
    """
    if not configured():
        raise OAuthNotConfigured("This deployment has no Zoho application registered.")

    data = {
        "grant_type": "authorization_code",
        "client_id": settings.ZOHO_OAUTH_CLIENT_ID,
        "client_secret": settings.ZOHO_OAUTH_CLIENT_SECRET,
        "redirect_uri": settings.ZOHO_OAUTH_REDIRECT_URI,
        "code": code,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{accounts_base}/oauth/token", data=data,
                                         timeout=settings.ZOHO_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as e:
        log.error("HTTP error during token exchange: %s", e)
        raise ZohoError(f"Network error while authorizing: {e}") from e

    if "error" in body:
        detail = body.get("error_description") or body.get("error")
        log.warning("OAuth token exchange refused: %s", detail)
        raise ZohoAuthError(f"Zoho refused the authorization: {detail}")
    if not body.get("refresh_token"):
        # Not a network fault and not a lie about success. Zoho withholds the
        # refresh token when this user has consented to this client before, so
        # the useful answer names the fix rather than reporting an empty grant.
        raise ZohoAuthError(
            "Zoho returned a sign-in with no refresh token, which happens when "
            "this account has already authorized this application. Remove it "
            "under Zoho's connected apps and authorize again.")
    return OAuthTokens(access_token=body["access_token"],
                       refresh_token=body["refresh_token"],
                       expires_in=int(body.get("expires_in") or 3600))


@dataclass
class ZohoOrganization:
    """One Zoho Books company this sign-in can reach."""

    organization_id: str
    name: str
    currency: str

    def to_dict(self) -> dict:
        return {"organization_id": self.organization_id, "name": self.name,
                "currency": self.currency}


async def list_organizations(access_token: str,
                             api_base: str) -> list[ZohoOrganization]:
    """Every company the authorizing user can see.

    One grant reaches all of them — which is the whole reason a credential is
    separate from a connection — so this is a list to choose from, not a single
    answer.
    """
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{api_base}/organizations", headers=headers,
                                        timeout=settings.ZOHO_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as e:
        log.error("HTTP error listing organizations: %s", e)
        raise ZohoError(f"Network error listing your companies: {e}") from e

    if body.get("code") != 0:
        raise ZohoError(f"Zoho error listing your companies: "
                        f"{body.get('message', 'unknown')}")
    return [ZohoOrganization(organization_id=str(o["organization_id"]),
                             name=o.get("name") or str(o["organization_id"]),
                             currency=o.get("currency_code") or "")
            for o in body.get("organizations", [])]


def credential_from_oauth(session: Session, *, organization_id: str,
                          tokens: OAuthTokens, accounts_base: str, api_base: str,
                          label: str = "") -> models.ZohoCredential:
    """Store the refresh token as a credential this organization owns.

    Rotates the credential **this application** previously issued, and creates
    one otherwise. Matching on the client id rather than on "any credential this
    organization has" is the difference between rotating our own grant and
    overwriting the Self Client refresh token somebody typed in by hand — which
    is what the removed implementation did, silently, to a working connection.
    """
    client_id = settings.ZOHO_OAUTH_CLIENT_ID
    existing = session.scalar(select(models.ZohoCredential).where(
        models.ZohoCredential.owner_organization_id == organization_id,
        models.ZohoCredential.client_id == client_id,
        models.ZohoCredential.accounts_base == accounts_base))
    now = clock.now()
    if existing is not None:
        existing.refresh_token_encrypted = crypto.encrypt(tokens.refresh_token)
        existing.client_secret_encrypted = crypto.encrypt(
            settings.ZOHO_OAUTH_CLIENT_SECRET)
        existing.rotated_at = now
        existing.updated_at = now
        session.flush()
        return existing

    cred = models.ZohoCredential(
        owner_organization_id=organization_id,
        connector="zoho",
        label=label or "Zoho sign-in (authorized)",
        client_id=client_id,
        client_secret_encrypted=crypto.encrypt(settings.ZOHO_OAUTH_CLIENT_SECRET),
        refresh_token_encrypted=crypto.encrypt(tokens.refresh_token),
        accounts_base=accounts_base,
        api_base=api_base,
        shared_with_organization_ids=[],
        rotated_at=now)
    session.add(cred)
    session.flush()
    return cred


def return_url(*, ok: bool, handoff: Optional[str] = None,
               reason: Optional[str] = None) -> str:
    """Where the callback sends the browser when it is done.

    A redirect, never a JSON body: what arrives here is a person following a
    link, and handing them a rendered dict is how the first implementation
    ended a flow it could not finish.
    """
    base = (settings.FRONTEND_ORIGIN or "").rstrip("/")
    params = {"oauth": "ok" if ok else "error"}
    if handoff:
        params["handoff"] = handoff
    if reason:
        params["reason"] = reason[:200]
    return f"{base}/#/data?{urlencode(params)}"
