"""Zoho OAuth 2.0 Authorization Code flow management.

Handles state token generation/validation and OAuth credential exchange with Zoho.
The flow is:

1. User clicks "Connect Zoho Books"
2. Backend generates OAuth state, stores it (session-scoped)
3. Redirect user to Zoho authorization endpoint
4. User authorizes PIE in Zoho
5. Zoho redirects back to /callback with authorization code
6. Backend validates state, exchanges code for tokens
7. Backend fetches organizations user can access
8. User selects an organization
9. Backend creates ZohoConnection using the tokens

This implementation never stores authorization codes, only refresh tokens (encrypted).
Access tokens are refreshed on-demand and cached only in memory during sync.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from . import crypto, clock
from .config import settings
from .domain import models
from .ingestion.zoho_client import ZohoAuthError, ZohoError

log = logging.getLogger("pie_portal.oauth")

# OAuth 2.0 RFC 6234: state parameter should be cryptographically random
# and verify the authorization response came from the same request.
# We use a 32-byte random token.
STATE_TOKEN_BYTES = 32
DEFAULT_STATE_TTL_SECONDS = 600  # 10 minutes


@dataclass
class OAuthState:
    """Ephemeral OAuth state token for CSRF protection.

    Created when user initiates authorization, validated on callback.
    Once consumed (used successfully), the token is invalidated.
    """

    token: str
    organization_id: str  # the PIE organization requesting authorization
    created_at: datetime
    accounts_base: str  # the Zoho data centre (from user's selection or default)

    @property
    def expires_at(self) -> datetime:
        """State token expiration time."""
        return self.created_at + timedelta(seconds=settings.ZOHO_OAUTH_STATE_TTL_SECONDS)

    def is_expired(self) -> bool:
        """Whether this state has expired."""
        return clock.now() > self.expires_at

    def hash_for_storage(self) -> str:
        """Hash the token for storage. Never store the plaintext token."""
        return hashlib.sha256(self.token.encode()).hexdigest()


def generate_state(organization_id: str, accounts_base: str) -> OAuthState:
    """Generate a new OAuth state token."""
    return OAuthState(
        token=secrets.token_urlsafe(STATE_TOKEN_BYTES),
        organization_id=organization_id,
        created_at=datetime.now(timezone.utc),
        accounts_base=accounts_base,
    )


def authorization_url(
    state: OAuthState,
    scope: str = "",
) -> str:
    """Generate the Zoho OAuth authorization URL.

    Directs the user's browser to Zoho, where they authorize PIE to access
    their Books organization. Zoho will redirect back to ZOHO_OAUTH_REDIRECT_URI
    with code and state.

    Args:
        state: The state object from generate_state()
        scope: Space-separated scope string (e.g., "ZohoBooks.contacts.READ ...")

    Returns:
        The full authorization URL to redirect the user to.
    """
    if not settings.ZOHO_OAUTH_CLIENT_ID or not settings.ZOHO_OAUTH_REDIRECT_URI:
        raise ValueError(
            "OAuth is not configured. Set ZOHO_OAUTH_CLIENT_ID and "
            "ZOHO_OAUTH_REDIRECT_URI to enable customer-facing OAuth flow."
        )

    url = f"{state.accounts_base}/oauth/authorize"
    params = {
        "client_id": settings.ZOHO_OAUTH_CLIENT_ID,
        "response_type": "code",
        "scope": scope,
        "redirect_uri": settings.ZOHO_OAUTH_REDIRECT_URI,
        "state": state.token,
    }
    return f"{url}?{urlencode(params)}"


@dataclass
class OAuthTokens:
    """Zoho OAuth tokens returned from token endpoint."""

    access_token: str
    refresh_token: str
    expires_in: int  # seconds until access token expires


async def exchange_code_for_tokens(
    code: str,
    accounts_base: str,
) -> OAuthTokens:
    """Exchange authorization code for access and refresh tokens.

    Calls the Zoho token endpoint to convert the authorization code (valid for
    10 minutes) into a refresh token (valid for years) and an access token
    (valid for 1 hour).

    Args:
        code: The authorization code from Zoho callback
        accounts_base: The Zoho data centre endpoint (e.g., accounts.zoho.in)

    Returns:
        OAuthTokens with access_token, refresh_token, and expires_in

    Raises:
        ZohoAuthError: If Zoho rejects the code or credentials
    """
    if not settings.ZOHO_OAUTH_CLIENT_ID or not settings.ZOHO_OAUTH_CLIENT_SECRET:
        raise ValueError(
            "OAuth client credentials not configured. "
            "Set ZOHO_OAUTH_CLIENT_ID and ZOHO_OAUTH_CLIENT_SECRET."
        )

    url = f"{accounts_base}/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.ZOHO_OAUTH_CLIENT_ID,
        "client_secret": settings.ZOHO_OAUTH_CLIENT_SECRET,
        "redirect_uri": settings.ZOHO_OAUTH_REDIRECT_URI,
        "code": code,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                data=data,
                timeout=settings.ZOHO_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            error_code = body.get("error")
            error_msg = body.get("error_description", "Unknown error")
            log.warning("OAuth token exchange failed: %s — %s", error_code, error_msg)
            raise ZohoAuthError(
                f"Zoho rejected the authorization code: {error_code}. "
                f"Details: {error_msg}. Try authorizing again."
            )

        if "access_token" not in body or "refresh_token" not in body:
            log.error("Unexpected Zoho token response: missing required fields")
            raise ZohoError(
                "Zoho returned an incomplete token response. Try authorizing again."
            )

        return OAuthTokens(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_in=body.get("expires_in", 3600),
        )

    except httpx.HTTPError as e:
        log.error("HTTP error during token exchange: %s", e)
        raise ZohoError(f"Network error while authorizing: {e}") from e


@dataclass
class ZohoOrganization:
    """One Zoho Books organization the authenticated user can access."""

    organization_id: str
    name: str
    currency: str


async def list_organizations(
    access_token: str,
    api_base: str,
) -> list[ZohoOrganization]:
    """Fetch the list of Zoho Books organizations the user can access.

    Uses the access token to call Zoho GET /organizations and returns
    basic org details. This is used to let the user select which org to connect.

    Args:
        access_token: The OAuth access token from token exchange
        api_base: The Zoho API endpoint (e.g., www.zohoapis.in/books/v3)

    Returns:
        List of ZohoOrganization objects

    Raises:
        ZohoAuthError: If the access token is invalid
        ZohoError: If the API call fails
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_base}/organizations",
                headers=headers,
                timeout=settings.ZOHO_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()

        if not body.get("code") == 0:
            error_code = body.get("code")
            error_msg = body.get("message", "Unknown error")
            log.warning("Failed to list organizations: %s — %s", error_code, error_msg)
            raise ZohoError(f"Zoho error listing organizations: {error_msg}")

        orgs = []
        for org_data in body.get("organizations", []):
            orgs.append(
                ZohoOrganization(
                    organization_id=org_data["organization_id"],
                    name=org_data.get("name", org_data["organization_id"]),
                    currency=org_data.get("currency_code", ""),
                )
            )
        return orgs

    except httpx.HTTPError as e:
        log.error("HTTP error listing organizations: %s", e)
        raise ZohoError(f"Network error listing organizations: {e}") from e


async def create_credential_from_oauth(
    session: Session,
    organization_id: str,
    tokens: OAuthTokens,
    zoho_accounts_base: str,
    zoho_api_base: str,
    label: str = "",
) -> models.ZohoCredential:
    """Create a ZohoCredential from OAuth tokens.

    Creates or updates a credential row to store the refresh token.
    The credential is owned by the calling organization.

    Args:
        session: Database session
        organization_id: The PIE organization that authorized
        tokens: The tokens from exchange_code_for_tokens()
        zoho_accounts_base: Zoho accounts endpoint
        zoho_api_base: Zoho API endpoint
        label: Human-readable label for the credential

    Returns:
        The created or updated ZohoCredential

    Raises:
        Various SQLAlchemy exceptions on DB errors
    """
    from .ingestion.zoho_client import ZohoCredentials

    # Extract client_id from Zoho (or use configured one)
    client_id = settings.ZOHO_OAUTH_CLIENT_ID

    # Encrypt the refresh token before storing
    refresh_token_encrypted = crypto.encrypt(tokens.refresh_token)

    # Check if organization already has a credential created via OAuth
    existing = session.query(models.ZohoCredential).filter(
        models.ZohoCredential.owner_organization_id == organization_id,
    ).first()

    if existing:
        # Update existing credential with new tokens
        existing.refresh_token_encrypted = refresh_token_encrypted
        existing.updated_at = clock.now()
        existing.rotated_at = clock.now()
        session.flush()
        return existing

    # Create new credential
    cred = models.ZohoCredential(
        credential_id=models._uuid(),
        owner_organization_id=organization_id,
        label=label or "Zoho OAuth",
        client_id=client_id,
        client_secret_encrypted=crypto.encrypt(settings.ZOHO_OAUTH_CLIENT_SECRET or ""),
        refresh_token_encrypted=refresh_token_encrypted,
        accounts_base=zoho_accounts_base,
        api_base=zoho_api_base,
    )
    session.add(cred)
    session.flush()
    return cred
