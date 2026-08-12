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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..config import settings
from ..domain import models
from .zoho_client import ZohoCredentials

#: The connector name every Zoho pull records itself under (``sync.SyncService``).
ZOHO_CONNECTOR = "zoho"


class CredentialNotUsable(PermissionError):
    """A credential this organization has not been given access to."""


class ConnectionNotFound(LookupError):
    """No such connection on this organization."""


# ── the Zoho scopes this platform needs, and why ────────────────────────────
# Surfaced in the UI because a half-granted scope is the single most common
# reason a connection authenticates and then returns nothing: the token works,
# the endpoint 401s, and the sync reports zero rows with no obvious cause.
#
# This list is what an owner is told to paste into the Zoho console, so a scope
# the pull uses and this list omits is one *nobody can ever have granted*.
# ``ZohoBooks.creditnotes.READ`` was exactly that: the client had called the
# endpoint since credit notes were added, the scope was never named here, and
# because the credit-note stage degrades gracefully the only symptom was one
# skip line in a sync report. ``test_every_scope_the_pull_uses_is_declared``
# pins the two lists together in both directions.
REQUIRED_SCOPES: tuple[tuple[str, str, bool], ...] = (
    ("ZohoBooks.contacts.READ", "Customers and vendors", True),
    ("ZohoBooks.settings.READ", "Items — the product master", True),
    ("ZohoBooks.invoices.READ", "Invoices — what was sold, and for how much", True),
    ("ZohoBooks.bills.READ",
     "Bills — what it cost. Without this there is no margin anywhere in the "
     "platform, only revenue.", True),
    ("ZohoBooks.customerpayments.READ",
     "Payments — when money actually arrived. The Cash screen, every payment "
     "pattern, and the collection factor the incentive is earned on all read "
     "this. Without it an invoice looks paid the day it was raised.", True),
    ("ZohoBooks.creditnotes.READ",
     "Credit notes — credit given back. Optional because Zoho already nets "
     "applied credit into an invoice's balance, so today's receivable is right "
     "without it; what it buys is the ability to reconstruct a *past* "
     "position.", False),
    ("ZohoBooks.salesorders.READ",
     "Sales orders — what customers have ordered and we have not yet shipped or "
     "billed. This is demand and a promise, before any accounting entry exists; "
     "without it the platform sees only what has already been invoiced.", False),
    ("ZohoBooks.vendorpayments.READ",
     "Money out. Receipts alone are not cash — they are revenue collected — so "
     "without this, liquidity and working capital cannot be computed from one "
     "side of the ledger.", False),
    ("ZohoBooks.purchaseorders.READ",
     "Purchase orders — what is on the way from suppliers, and how late. Feeds "
     "the Supply screen. Optional: without it, stock on hand is still read, "
     "but nothing knows what has been ordered against it.", False),
    ("ZohoBooks.users.READ",
     "Users — maps a Zoho salesperson to a platform account. Optional: without "
     "it accounts stay unassigned and every decision routes to management.",
     False),
)

SCOPE_STRING = ",".join(s for s, _, _ in REQUIRED_SCOPES)


# ── resolution ──────────────────────────────────────────────────────────────
def list_connections(session: Session, organization_id: str,
                     enabled_only: bool = False) -> list[models.ZohoConnection]:
    """Every Zoho company this organization pulls from, oldest first."""
    stmt = select(models.ZohoConnection).where(
        models.ZohoConnection.organization_id == organization_id)
    if enabled_only:
        stmt = stmt.where(models.ZohoConnection.enabled.is_(True))
    return list(session.scalars(stmt.order_by(models.ZohoConnection.created_at)))


def get_connection(session: Session, organization_id: str,
                   connection_id: str) -> models.ZohoConnection:
    row = session.get(models.ZohoConnection, connection_id)
    if row is None or row.organization_id != organization_id:
        raise ConnectionNotFound("No such connection")
    return row


def credentials_for(session: Session,
                    connection: models.ZohoConnection) -> ZohoCredentials:
    """Turn one connection row into the credentials a pull needs."""
    cred = connection.credential
    if cred is not None:
        if not cred.is_usable_by(connection.organization_id):
            raise CredentialNotUsable(
                f"Organization {connection.organization_id!r} is no longer permitted "
                f"to use credential {cred.credential_id!r}")
        return ZohoCredentials(
            organization_id=connection.zoho_organization_id,
            client_id=cred.client_id,
            client_secret=crypto.decrypt(cred.client_secret_encrypted),
            refresh_token=crypto.decrypt(cred.refresh_token_encrypted),
            accounts_base=cred.accounts_base, api_base=cred.api_base)
    # Legacy inline row, pre-split.
    return ZohoCredentials(
        organization_id=connection.zoho_organization_id,
        client_id=connection.client_id,
        client_secret=crypto.decrypt(connection.client_secret_encrypted),
        refresh_token=crypto.decrypt(connection.refresh_token_encrypted),
        accounts_base=connection.accounts_base, api_base=connection.api_base)


def get_zoho_credentials(session: Session, organization_id: str,
                         connection_id: Optional[str] = None
                         ) -> Optional[ZohoCredentials]:
    """Credentials for one connection, or for the organization's first enabled one.

    The no-argument form is what every existing caller uses and keeps the
    single-connection behaviour intact. Passing a connection id is how a
    multi-company organization says which books it means.

    Decryption failures (see ``crypto.CredentialDecryptionError``) propagate —
    they mean the stored value cannot be trusted, not that there is none.
    """
    if connection_id is not None:
        return credentials_for(session, get_connection(session, organization_id,
                                                       connection_id))

    rows = list_connections(session, organization_id, enabled_only=True)
    if rows:
        return credentials_for(session, rows[0])
    if organization_id == settings.DEFAULT_ORG_ID and settings.ZOHO_ORGANIZATION_ID:
        return ZohoCredentials.from_settings()
    return None


def has_zoho_connection(session: Session, organization_id: str) -> bool:
    return get_zoho_credentials(session, organization_id) is not None


@dataclass(frozen=True)
class CustomerBook:
    """Which connected company holds a customer, and their contact id in it."""

    connection: models.ZohoConnection
    contact_id: str

    @property
    def label(self) -> str:
        return self.connection.label or self.connection.zoho_organization_id


def book_for_customer(session: Session, organization_id: str,
                      customer: models.Customer) -> CustomerBook:
    """The one set of books this customer belongs to. Refuses to guess.

    Almost entirely a lookup rather than a search, because ``Customer`` already
    stores the identity triple ``domain/origin.py`` defines: connector, the
    connected company, and that system's own id. An estimate therefore goes to
    the company the customer was *imported from*, not to one matched by name —
    "ABC Industries" can exist in all three books and be three different
    customers, which is the reason that triple exists.

    Two things are refused rather than resolved. A connection that is disabled
    or gone cannot be written to. And a row whose ``connection_id`` is NULL —
    imported before provenance was recorded — is only placeable when the
    organization has a single connected company; with more than one, choosing
    would be inventing the provenance the column deliberately leaves blank.
    """
    enabled = {c.connection_id: c
               for c in list_connections(session, organization_id, enabled_only=True)}

    if customer.connector and customer.connector != ZOHO_CONNECTOR:
        raise ConnectionNotFound(
            f"{customer.name} was imported from "
            f"{customer.connector}, not Zoho Books, so this quote cannot be "
            f"written as a Zoho estimate.")

    if customer.connection_id:
        conn = enabled.get(customer.connection_id)
        if conn is None:
            raise ConnectionNotFound(
                f"The Zoho company {customer.name} came from is no longer "
                f"connected or has been disabled, so there is no ledger to "
                f"write this quote into.")
        return CustomerBook(conn, str(customer.external_id))

    # Provenance not recorded. One connected company leaves nothing to choose
    # between; more than one is the case that must not be guessed.
    if len(enabled) == 1 and customer.external_id:
        return CustomerBook(next(iter(enabled.values())), str(customer.external_id))
    if not enabled:
        raise ConnectionNotFound(
            f"This organization has no connected Zoho company, so there is no "
            f"ledger to write {customer.name}'s quote into.")
    raise ConnectionNotFound(
        f"{customer.name} was imported before the source company was recorded, "
        f"and this organization has {len(enabled)} connected Zoho companies. "
        f"Which one this quote belongs to cannot be decided from the quote "
        f"alone — re-sync the company this customer belongs to.")


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
def add_connection(session: Session, organization_id: str, *, credential_id: str,
                   zoho_organization_id: str, label: str = "") -> models.ZohoConnection:
    """Point this organization at another Zoho company through an existing grant.

    No secret is re-entered, so there is no second copy for a rotation to miss.
    A company already connected is updated rather than duplicated: two rows for
    one Zoho org id would sync it twice and double every figure.
    """
    cred = get_credential(session, organization_id, credential_id)
    zoho_organization_id = zoho_organization_id.strip()

    existing = session.scalar(select(models.ZohoConnection).where(
        models.ZohoConnection.organization_id == organization_id,
        models.ZohoConnection.zoho_organization_id == zoho_organization_id))

    # The plan boundary, enforced where the violation would happen. A second
    # *distinct* company on one organization is what the platform plan is;
    # below it the licence unit is one company's books, and refusing here —
    # rather than auditing later — is what makes that a rule instead of a
    # suggestion. Updating a company already connected is never a new company.
    if existing is None:
        from .. import entitlements

        others = session.scalar(select(models.ZohoConnection.connection_id).where(
            models.ZohoConnection.organization_id == organization_id,
            models.ZohoConnection.zoho_organization_id != zoho_organization_id))
        if others is not None:
            entitlements.assert_feature(session, organization_id, "multi_company")

    row = existing or models.ZohoConnection(organization_id=organization_id)
    if existing is None:
        session.add(row)

    row.zoho_organization_id = zoho_organization_id
    row.credential_id = cred.credential_id
    row.label = (label or "").strip()[:255]
    if existing is None:
        row.enabled = True
    # Any legacy inline secret is cleared: a stale copy that rotation would miss
    # is exactly the failure the credential split exists to prevent.
    row.client_id = None
    row.client_secret_encrypted = None
    row.refresh_token_encrypted = None
    row.accounts_base = cred.accounts_base
    row.api_base = cred.api_base
    session.flush()

    # The free intelligence month starts when books connect for the first time
    # anywhere — keyed to the books, so reconnecting the same company under a
    # fresh organization finds the trial already spent (see IntelligenceTrial).
    if existing is None:
        from .. import entitlements

        entitlements.begin_trial(session, organization_id, zoho_organization_id)
    return row


def update_connection(session: Session, organization_id: str, connection_id: str, *,
                      label: Optional[str] = None,
                      enabled: Optional[bool] = None) -> models.ZohoConnection:
    row = get_connection(session, organization_id, connection_id)
    if label is not None:
        row.label = label.strip()[:255]
    if enabled is not None:
        row.enabled = enabled
    session.flush()
    return row


def record_check(session: Session, connection: models.ZohoConnection, *, ok: bool,
                 detail: str = "") -> None:
    """Remember whether this connection was reachable, and what Zoho said.

    Per connection, because "the organization is connected" stops meaning
    anything once there are three and one has a revoked token.
    """
    connection.last_checked_at = datetime.now(timezone.utc)
    connection.last_check_ok = ok
    connection.last_check_detail = (detail or "")[:1024] or None
    session.flush()


def delete_connection(session: Session, organization_id: str,
                      connection_id: str) -> models.ZohoConnection:
    """Remove one connection. Data already pulled through it is left alone.

    Deliberately not cascading into the read model: rows already synced are
    facts about what was invoiced, and deleting a connection is an
    administrative act about credentials, not a decision to forget trading
    history. Purging that data is a separate, explicit choice.
    """
    row = get_connection(session, organization_id, connection_id)
    session.delete(row)
    session.flush()
    return row


def set_zoho_credentials(
    session: Session, organization_id: str, *,
    zoho_organization_id: str, client_id: str, client_secret: str, refresh_token: str,
    accounts_base: str = "https://accounts.zoho.in",
    api_base: str = "https://www.zohoapis.in/books/v3",
    label: str = "",
    credential_label: str = "",
) -> models.ZohoConnection:
    """Connect by supplying the secrets directly.

    The first-connection path, and what every existing caller uses. Identical
    secrets attach to the credential already on file rather than creating a
    second copy of it.

    ``label`` names the *company*; ``credential_label`` names the *sign-in*.
    They are separate because one sign-in commonly serves several companies,
    and a grant named after the first company it happened to connect reads as
    a lie the moment it also serves the second.
    """
    cred = find_matching_credential(
        session, organization_id, client_id=client_id, client_secret=client_secret,
        refresh_token=refresh_token)
    if cred is None:
        cred = create_credential(
            session, organization_id, client_id=client_id, client_secret=client_secret,
            refresh_token=refresh_token, label=credential_label or label,
            accounts_base=accounts_base, api_base=api_base)
    else:
        cred.accounts_base = accounts_base
        cred.api_base = api_base
    return add_connection(
        session, organization_id, credential_id=cred.credential_id,
        zoho_organization_id=zoho_organization_id, label=label)


# Kept under its old name: callers that meant "connect this org" still work.
connect_with_credential = add_connection


def clear_zoho_connection(session: Session, organization_id: str) -> bool:
    """Remove every connection on this organization. Returns whether any existed.

    Credentials are deliberately left in place: other organizations may be using
    them, and even when none are, keeping them means reconnecting does not mean
    re-entering a secret. The default organization falls back to ``ZOHO_*``
    afterwards, same as if it had never connected one.
    """
    rows = list_connections(session, organization_id)
    for row in rows:
        session.delete(row)
    session.flush()
    return bool(rows)
