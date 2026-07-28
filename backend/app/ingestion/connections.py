"""Per-organization Zoho connections.

Each platform organization is a fully separate tenant, so each one has at most
one Zoho connection — stored here, encrypted, rather than in process-wide
settings. The platform's original default organization is the one exception:
it falls back to the ``ZOHO_*`` environment variables when it has no stored
connection, so an existing single-tenant deployment keeps working with no
migration step required of it.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .. import crypto
from ..config import settings
from ..domain import models
from .zoho_client import ZohoCredentials


def get_zoho_credentials(session: Session, organization_id: str) -> Optional[ZohoCredentials]:
    """This organization's Zoho credentials, or None if it has not connected one.

    Decryption failures (see ``crypto.CredentialDecryptionError``) propagate —
    they mean the stored value cannot be trusted, not that there is none.
    """
    row = session.get(models.ZohoConnection, organization_id)
    if row is not None:
        return ZohoCredentials(
            organization_id=row.zoho_organization_id,
            client_id=row.client_id,
            client_secret=crypto.decrypt(row.client_secret_encrypted),
            refresh_token=crypto.decrypt(row.refresh_token_encrypted),
            accounts_base=row.accounts_base,
            api_base=row.api_base,
        )
    if organization_id == settings.DEFAULT_ORG_ID and settings.ZOHO_ORGANIZATION_ID:
        return ZohoCredentials.from_settings()
    return None


def has_zoho_connection(session: Session, organization_id: str) -> bool:
    return get_zoho_credentials(session, organization_id) is not None


def set_zoho_credentials(
    session: Session, organization_id: str, *,
    zoho_organization_id: str, client_id: str, client_secret: str, refresh_token: str,
    accounts_base: str = "https://accounts.zoho.in",
    api_base: str = "https://www.zohoapis.in/books/v3",
) -> models.ZohoConnection:
    """Create or replace this organization's Zoho connection.

    A full replace, not a merge: a caller updating one field must resend the
    others (the API layer is responsible for that), so a stale field can never
    silently persist alongside a changed one.
    """
    row = session.get(models.ZohoConnection, organization_id)
    if row is None:
        row = models.ZohoConnection(organization_id=organization_id)
        session.add(row)
    row.zoho_organization_id = zoho_organization_id
    row.client_id = client_id
    row.client_secret_encrypted = crypto.encrypt(client_secret)
    row.refresh_token_encrypted = crypto.encrypt(refresh_token)
    row.accounts_base = accounts_base
    row.api_base = api_base
    session.flush()
    return row


def clear_zoho_connection(session: Session, organization_id: str) -> bool:
    """Remove this organization's connection. Returns whether one existed.

    The default organization falls back to ZOHO_* afterwards, same as if it had
    never connected one — clearing is how you deliberately drop back to that.
    """
    row = session.get(models.ZohoConnection, organization_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
