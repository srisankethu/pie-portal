"""Zoho credentials and the connections that use them.

A **credential** is one OAuth grant: an app registration plus one Zoho user's
refresh token. A **connection** points one platform organization at one Zoho
Books company id, through a credential.

They are separate because Zoho separates them. A refresh token belongs to a
user, not a company; ``organization_id`` is a request parameter, and
``GET /organizations`` lists every company that user can see. A business with
three legal entities under one Zoho login therefore needs one credential and
three connections — and one rotation, not three.

Sharing a credential does not share data. Each platform organization stays a
fully separate tenant with its own users, decisions and margins; this is only
the key used to fetch its rows. Sharing is explicit and owner-controlled: a
credential every tenant could reach would be a cross-tenant hole regardless of
intent.

The default organization still falls back to the ``ZOHO_*`` environment
variables when it has no connection, so an existing single-tenant deployment
keeps working with no migration step required of it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..config import settings
from ..domain import models
from .zoho_client import ZohoCredentials


class CredentialNotUsable(PermissionError):
    """A credential this organization has not been given access to."""


# ── resolution ──────────────────────────────────────────────────────────────
def get_zoho_credentials(session: Session, organization_id: str) -> Optional[ZohoCredentials]:
    """This organization's Zoho credentials, or None if it has not connected.

    Decryption failures (see ``crypto.CredentialDecryptionError``) propagate —
    they mean the stored value cannot be trusted, not that there is none.
    """
    row = session.get(models.ZohoConnection, organization_id)
    if row is not None:
        cred = row.credential
        if cred is not None:
            # Re-checked at use, not only at attach time: an organization
            # removed from the share list must stop syncing immediately, not at
            # the next time somebody edits the connection.
            if not cred.is_usable_by(organization_id):
                raise CredentialNotUsable(
                    f"Organization {organization_id!r} is no longer permitted to use "
                    f"credential {cred.credential_id!r}")
            return ZohoCredentials(
                organization_id=row.zoho_organization_id,
                client_id=cred.client_id,
                client_secret=crypto.decrypt(cred.client_secret_encrypted),
                refresh_token=crypto.decrypt(cred.refresh_token_encrypted),
                accounts_base=cred.accounts_base,
                api_base=cred.api_base,
            )
        if row.client_id:                       # legacy inline row, pre-split
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


# ── credentials ─────────────────────────────────────────────────────────────
def usable_credentials(session: Session, organization_id: str) -> list[models.ZohoCredential]:
    """Every credential this organization may connect through — its own, plus
    any another organization has explicitly shared with it."""
    rows = session.scalars(select(models.ZohoCredential)).all()
    return [c for c in rows if c.is_usable_by(organization_id)]


def get_credential(session: Session, organization_id: str,
                   credential_id: str) -> models.ZohoCredential:
    cred = session.get(models.ZohoCredential, credential_id)
    if cred is None or not cred.is_usable_by(organization_id):
        # Same answer for "does not exist" and "not yours": otherwise this
        # endpoint enumerates other tenants' credential ids.
        raise CredentialNotUsable("No such credential")
    return cred


def find_matching_credential(session: Session, organization_id: str, *, client_id: str,
                             client_secret: str,
                             refresh_token: str) -> Optional[models.ZohoCredential]:
    """An existing credential holding exactly these secrets.

    Re-entering the same grant for a second entity should attach to what is
    already there rather than mint a duplicate — two rows holding one secret is
    the state that makes a rotation miss one of them.
    """
    for cred in usable_credentials(session, organization_id):
        try:
            if (cred.client_id == client_id
                    and crypto.decrypt(cred.client_secret_encrypted) == client_secret
                    and crypto.decrypt(cred.refresh_token_encrypted) == refresh_token):
                return cred
        except Exception:  # noqa: BLE001 — an undecryptable row is not a match
            continue
    return None


def create_credential(session: Session, organization_id: str, *, client_id: str,
                      client_secret: str, refresh_token: str, label: str = "",
                      accounts_base: str = "https://accounts.zoho.in",
                      api_base: str = "https://www.zohoapis.in/books/v3",
                      ) -> models.ZohoCredential:
    cred = models.ZohoCredential(
        owner_organization_id=organization_id,
        label=label[:255],
        client_id=client_id,
        client_secret_encrypted=crypto.encrypt(client_secret),
        refresh_token_encrypted=crypto.encrypt(refresh_token),
        accounts_base=accounts_base,
        api_base=api_base,
        shared_with_organization_ids=[],
        rotated_at=datetime.now(timezone.utc),
    )
    session.add(cred)
    session.flush()
    return cred


def rotate_credential(session: Session, organization_id: str, credential_id: str, *,
                      refresh_token: str, client_id: Optional[str] = None,
                      client_secret: Optional[str] = None) -> models.ZohoCredential:
    """Replace the secrets on one credential. Every connection using it follows.

    This is the whole point of the split: rotating after a leak, or on a
    schedule, is one operation regardless of how many companies are connected
    through it. Only the owning organization may do it — an organization that
    was merely granted use should not be able to change the key underneath
    everyone else.
    """
    cred = session.get(models.ZohoCredential, credential_id)
    if cred is None or cred.owner_organization_id != organization_id:
        raise CredentialNotUsable(
            "Only the organization that owns a credential can rotate it")
    if client_id:
        cred.client_id = client_id
    if client_secret:
        cred.client_secret_encrypted = crypto.encrypt(client_secret)
    cred.refresh_token_encrypted = crypto.encrypt(refresh_token)
    cred.rotated_at = datetime.now(timezone.utc)
    session.flush()
    return cred


def share_credential(session: Session, organization_id: str, credential_id: str, *,
                     with_organization_ids: list[str]) -> models.ZohoCredential:
    """Set the list of other organizations that may connect through this grant.

    A full replace, so removing access is the same operation as granting it and
    cannot be forgotten. The owning organization is never in the list — it does
    not need to grant itself anything.
    """
    cred = session.get(models.ZohoCredential, credential_id)
    if cred is None or cred.owner_organization_id != organization_id:
        raise CredentialNotUsable(
            "Only the organization that owns a credential can share it")
    known = {o for (o,) in session.execute(select(models.Organization.organization_id))}
    cleaned = sorted({o for o in with_organization_ids
                      if o in known and o != cred.owner_organization_id})
    cred.shared_with_organization_ids = cleaned
    session.flush()
    return cred


def delete_credential(session: Session, organization_id: str, credential_id: str) -> bool:
    """Remove a credential. Refused while anything is still connected through it.

    Deleting out from under a live connection would leave an organization that
    looks connected and silently cannot sync — a worse state than an explicit
    refusal.
    """
    cred = session.get(models.ZohoCredential, credential_id)
    if cred is None or cred.owner_organization_id != organization_id:
        raise CredentialNotUsable(
            "Only the organization that owns a credential can remove it")
    in_use = session.scalars(select(models.ZohoConnection).where(
        models.ZohoConnection.credential_id == credential_id)).all()
    if in_use:
        raise ValueError(
            f"{len(in_use)} connection(s) still use this credential — disconnect "
            f"them first")
    session.delete(cred)
    session.flush()
    return True


def connections_using(session: Session, credential_id: str) -> list[models.ZohoConnection]:
    return list(session.scalars(select(models.ZohoConnection).where(
        models.ZohoConnection.credential_id == credential_id)))


# ── connections ─────────────────────────────────────────────────────────────
def connect_with_credential(session: Session, organization_id: str, *,
                            credential_id: str,
                            zoho_organization_id: str) -> models.ZohoConnection:
    """Point this organization at a Zoho company, using an existing grant.

    The second and third entity go through here: pick the credential already on
    file, name the company id, done. No secret is re-entered, so there is no
    second copy to rotate.
    """
    cred = get_credential(session, organization_id, credential_id)
    row = session.get(models.ZohoConnection, organization_id)
    if row is None:
        row = models.ZohoConnection(organization_id=organization_id)
        session.add(row)
    row.zoho_organization_id = zoho_organization_id
    row.credential_id = cred.credential_id
    # Any legacy inline secret is cleared, not left behind: a stale copy that
    # rotation would miss is exactly the failure this split exists to prevent.
    row.client_id = None
    row.client_secret_encrypted = None
    row.refresh_token_encrypted = None
    row.accounts_base = cred.accounts_base
    row.api_base = cred.api_base
    session.flush()
    return row


def set_zoho_credentials(
    session: Session, organization_id: str, *,
    zoho_organization_id: str, client_id: str, client_secret: str, refresh_token: str,
    accounts_base: str = "https://accounts.zoho.in",
    api_base: str = "https://www.zohoapis.in/books/v3",
    label: str = "",
) -> models.ZohoConnection:
    """Connect by supplying the secrets directly.

    Kept as the first-connection path and for compatibility. Identical secrets
    attach to the credential already on file rather than creating a second copy
    of it.
    """
    cred = find_matching_credential(
        session, organization_id, client_id=client_id, client_secret=client_secret,
        refresh_token=refresh_token)
    if cred is None:
        cred = create_credential(
            session, organization_id, client_id=client_id, client_secret=client_secret,
            refresh_token=refresh_token, label=label,
            accounts_base=accounts_base, api_base=api_base)
    else:
        cred.accounts_base = accounts_base
        cred.api_base = api_base
    return connect_with_credential(
        session, organization_id, credential_id=cred.credential_id,
        zoho_organization_id=zoho_organization_id)


def clear_zoho_connection(session: Session, organization_id: str) -> bool:
    """Remove this organization's connection. Returns whether one existed.

    The credential is deliberately left in place: other organizations may be
    using it, and even when none are, keeping it means reconnecting does not
    mean re-entering a secret. The default organization falls back to ``ZOHO_*``
    afterwards, same as if it had never connected one.
    """
    row = session.get(models.ZohoConnection, organization_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
