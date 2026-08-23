"""Credentials and the connections that use them — Zoho's OAuth grants and
every registered ERP connector's alike.

A **credential** is one sign-in: for Zoho, an app registration plus one user's
refresh token in the typed columns; for every other connector, that system's
secrets as one encrypted JSON document whose shape ``ingestion/erp`` declares.
A **connection** points one platform organization at one company in one
system, through a credential. ``connect_erp`` is the generic connect path;
everything below it — sharing, rotation-in-one-place, the multi-company plan
gate, the trial — is one mechanism for all connectors, which is the reason
the tables are shared rather than per-connector.

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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..domain import models
from .url_safety import require_safe_source_url, require_safe_source_urls
# The type Zoho's scope list shares with every registered connector's — one
# declaration of "what this sign-in must be granted", read by one screen.
from .erp.base import Permission
from .zoho_client import ZohoCredentials

log = logging.getLogger("pie_portal.connections")

#: The connector name every Zoho pull records itself under (``sync.SyncService``).
ZOHO_CONNECTOR = "zoho"

#: How far back a first pull reads when nobody has said otherwise. Eighteen
#: months gives the detectors a full recent window, a full comparison window,
#: and room above the six-month history floor — so the first sync produces an
#: analysis rather than a screen full of "not enough history".
#:
#: Here rather than in the router that computes the date from it, because the
#: onboarding checklist tells a new owner how long their first pull reads and
#: had the number written out in prose. Two copies of one figure means the
#: sentence a person is shown goes stale the day the constant moves, and the
#: sentence is the one nobody greps for.
DEFAULT_HISTORY_MONTHS = 18


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
#
# Same type as every other connector's list (``erp.Permission``), because it is
# the same fact — what this sign-in must be granted before it can read — and it
# is read by the same screen. It was its own tuple shape once, and the screen
# that rendered it showed *these* scopes whichever system was selected: a
# NetSuite connect form above ten Zoho scope strings nobody could grant.
REQUIRED_SCOPES: tuple[Permission, ...] = (
    Permission("ZohoBooks.contacts.READ", "Customers and vendors",
               reads=("contacts", "vendors")),
    Permission("ZohoBooks.settings.READ", "Items — the product master",
               reads=("items",)),
    Permission("ZohoBooks.invoices.READ",
               "Invoices — what was sold, and for how much", reads=("invoices",)),
    Permission("ZohoBooks.bills.READ",
               "Bills — what it cost. Without this there is no margin anywhere "
               "in the platform, only revenue.", reads=("bills",)),
    Permission("ZohoBooks.customerpayments.READ",
               "Payments — when money actually arrived. The Cash screen, every "
               "payment pattern, and the collection factor the incentive is "
               "earned on all read this. Without it an invoice looks paid the "
               "day it was raised.", reads=("customer_payments",)),
    Permission("ZohoBooks.creditnotes.READ",
               "Credit notes — credit given back. Optional because Zoho already "
               "nets applied credit into an invoice's balance, so today's "
               "receivable is right without it; what it buys is the ability to "
               "reconstruct a *past* position.", required=False),
    Permission("ZohoBooks.salesorders.READ",
               "Sales orders — what customers have ordered and we have not yet "
               "shipped or billed. This is demand and a promise, before any "
               "accounting entry exists; without it the platform sees only what "
               "has already been invoiced.",
               required=False, reads=("sales_orders",)),
    Permission("ZohoBooks.vendorpayments.READ",
               "Money out. Receipts alone are not cash — they are revenue "
               "collected — so without this, liquidity and working capital "
               "cannot be computed from one side of the ledger.",
               required=False, reads=("vendor_payments",)),
    Permission("ZohoBooks.purchaseorders.READ",
               "Purchase orders — what is on the way from suppliers, and how "
               "late. Feeds the Supply screen. Optional: without it, stock on "
               "hand is still read, but nothing knows what has been ordered "
               "against it.", required=False, reads=("purchase_orders",)),
    Permission("ZohoBooks.users.READ",
               "Users — maps a Zoho salesperson to a platform account. "
               "Optional: without it accounts stay unassigned and every "
               "decision routes to management.",
               required=False, reads=("users",)),
    # The write grants. ``required`` means "no sync runs without it", which is
    # not true of these — a connection missing them reads everything and only
    # the send refuses. They are still in the full string the screen leads
    # with, which is the string an owner who wants the send should paste.
    Permission("ZohoBooks.estimates.CREATE",
               "Creating the quote in Zoho — the Send to Zoho action. Without "
               "it everything else works and every send refuses.",
               required=False, writes=("sales_quotes",)),
    Permission("ZohoBooks.estimates.READ",
               "Reading estimates back: the duplicate check that stops one "
               "quote becoming two, and finding out what happened when a "
               "write's reply is lost. Needed by the send even though it only "
               "reads — an estimate that cannot be looked up cannot be created "
               "safely, so without this the send refuses too.",
               required=False),
    Permission("ZohoBooks.settings.CREATE",
               "Creating an item in the books — the Create in books action on "
               "a NOT IN BOOKS line. Leave it out if items should only ever be "
               "created by a person in Zoho; everything else still works.",
               required=False),
)

def scope_string(*, minimum: bool = False) -> str:
    """The scopes as one pasteable string — everything, or the minimum.

    Parameterised rather than two constants built two ways: the *fact* is the
    list above, and these are two projections of it. A second hand-written
    tuple would be the copy that stops agreeing with it the first time a scope
    moves between required and optional.

    ``minimum=True`` is the set without which no sync runs at all — what
    ``Permission.required`` already means, and what ``_scope_note`` in the
    connections router already checks against. It exists as a string because
    the console the person is reading takes a string, and until now the only
    one offered was the full set: an owner who wanted to grant the minimum had
    to assemble it by hand from the table, which is how a scope gets missed.

    The full set stays the default and stays what the screen leads with. The
    note below says why, and it is not a nudge — a half-granted connection
    authenticates and then reads nothing, which is the failure this whole list
    exists to prevent.
    """
    return ",".join(p.name for p in REQUIRED_SCOPES
                    if p.required or not minimum)


#: Everything this platform can read. The recommended grant.
SCOPE_STRING = scope_string()

#: The subset without which a sync does not run. Offered beside the full set
#: for the owner whose policy is to grant the least that works — and the set a
#: customer-facing authorization should ask for first, whenever one exists
#: again: a consent screen listing ten scopes is refused more often than one
#: listing five, and the other five buy screens rather than the sync.
MINIMUM_SCOPE_STRING = scope_string(minimum=True)

#: Where the scopes above are granted, said once and rendered above the list.
ZOHO_PERMISSION_NOTE = (
    "Paste the string below into the scope field when you generate the token "
    "in the Zoho API console. Granting fewer does not fail loudly; it fails "
    "quietly, later — so grant the full set unless something stops you, and "
    "use the minimum only if it does.")


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
    """Turn one Zoho connection row into the credentials a pull needs.

    Zoho only, by type: another connector's connection resolves through
    ``credential_material`` instead, and asking this function for one is a
    caller bug worth an exception rather than a half-shaped credential.
    """
    if (getattr(connection, "connector", None) or ZOHO_CONNECTOR) != ZOHO_CONNECTOR:
        raise ValueError(
            f"Connection {connection.connection_id} reads "
            f"{connection.connector}, which has no Zoho credentials — "
            "resolve it through credential_material.")
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


def companies_visible_to(credential: models.ZohoCredential) -> list[dict]:
    """Every Zoho company this grant can reach, before any of them is connected.

    The counterpart of ``discover_erp_companies`` for Zoho, and the step an
    authorization needs between "the sign-in worked" and "which books did you
    mean": one refresh token reaches every company its user can see, which is
    the whole reason a credential is separate from a connection.

    Reads it through ``ping``, which already asks ``/organizations`` and already
    shapes the answer — the alternative was a second call to the same endpoint,
    parsed a second way, which is how two lists of one thing start disagreeing.
    ``ping`` wants an organization id to say whether *that* one was found; there
    is none yet, so it is passed empty and only ``visible_organizations`` is
    read. Nothing is persisted.
    """
    creds = ZohoCredentials(
        organization_id="",
        client_id=credential.client_id,
        client_secret=crypto.decrypt(credential.client_secret_encrypted),
        refresh_token=crypto.decrypt(credential.refresh_token_encrypted),
        accounts_base=credential.accounts_base, api_base=credential.api_base)
    from .zoho_client import ZohoApiSource

    return list(ZohoApiSource(credentials=creds).ping().get(
        "visible_organizations") or [])


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

    # The first enabled *Zoho* company. Another connector's connection cannot
    # answer this question, and picking one would hand a NetSuite row to a
    # caller about to build a Zoho client.
    rows = [r for r in list_connections(session, organization_id,
                                        enabled_only=True)
            if (getattr(r, "connector", None) or ZOHO_CONNECTOR) == ZOHO_CONNECTOR]
    if rows:
        return credentials_for(session, rows[0])
    # No environment fallback, not even for the default org: every organization —
    # the seeded one included — connects through a stored, per-tenant credential.
    # A platform serving many tenants cannot resolve one tenant's Zoho grant from
    # a process-wide ZOHO_* variable.
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


def connector_of(connection: Any) -> str:
    """A connection's connector, defaulting to Zoho for rows written before the
    column existed — the same inline idiom used at a dozen sites, named once
    here because the write path now asks it on every branch."""
    return getattr(connection, "connector", None) or ZOHO_CONNECTOR


def writes_for(connector: str) -> tuple[str, ...]:
    """What this connector can *create* in its system, in ``WRITE_STAGES`` terms.

    The one place that knows capability is answered from two registries, and
    the reason it has to exist: every other connector declares itself through a
    ``ConnectorSpec`` in ``ingestion/erp``, while Zoho's grants live in
    ``REQUIRED_SCOPES`` above, because its connect flow predates that registry
    and is richer than a field list. Zoho is therefore invisible to
    ``erp.get_spec`` — which raises for it — and it is the one connector that
    can actually write.

    Callers ask this instead of comparing a connector key, so the quote path
    holds no connector name at all: ``erp/__init__``'s rule is that generic code
    reads the registry and never branches on a key, and this function is how
    that rule survives a connector the registry cannot hold. Both sides are
    pinned to their implementations in both directions — ``test_connector_writes``
    for the registry, ``test_zohos_declared_writes_match_what_the_adapter_can_
    actually_create`` for Zoho — so a wrong answer here fails a test rather than
    silently refusing a send.

    An unknown connector writes nothing. That is the safe direction: a refusal
    naming a system is recoverable, a write into one nobody declared is not.
    """
    if connector == ZOHO_CONNECTOR:
        return tuple(sorted({stage for p in REQUIRED_SCOPES for stage in p.writes}))
    from . import erp
    try:
        return erp.get_spec(connector).writes
    except erp.UnknownConnectorError:
        return ()


def can_write_quotes(connector: str) -> bool:
    """Whether the grant for this connector covers creating a quote there.

    A statement about the *permission*, not about whether this platform can act
    on it — see ``quote_writer_ready``. ``book_for_customer`` asks this first
    because a connector that cannot be written to at all needs a different
    sentence from one that can but has no writer here yet.
    """
    return "sales_quotes" in writes_for(connector)


def quote_term_for(connector: str) -> str:
    """What this system calls the document a quote becomes there.

    So a message can say "Zoho estimate SQ-1001" or "Business Central sales
    quote SQ-1001" rather than one word for both. Naming a Business Central
    document an "estimate" sends its user looking for a record type their own
    system does not have.
    """
    if connector == ZOHO_CONNECTOR:
        return "estimate"
    from . import erp
    try:
        return erp.get_spec(connector).quote_term
    except erp.UnknownConnectorError:
        return "quote"


def system_label_for(connector: str) -> str:
    """That system's own name, as its users call it."""
    from ..domain.origin import CONNECTORS
    return (CONNECTORS.get(connector) or {}).get("label") or connector


def quote_writer_ready(connector: str) -> bool:
    """Whether a quote built here can actually be sent to this connector today.

    Both halves: the grant covers it *and* something here knows how. This is
    the question a screen asks — an owner reading "Can create quotes here"
    beside a send button that refuses has been told something false, and the
    two questions come apart exactly while a connector's writer is being built.
    """
    return can_write_quotes(connector) and connector in _QUOTE_ADAPTERS


#: Connectors this platform has a quote-write adapter wired for, as opposed to
#: merely permitted to write. The two are different questions and both have to
#: be Yes: ``writes_for`` says the *grant* covers creating a quote there, this
#: says there is code that knows how. Today they coincide at one entry and this
#: set is what the Business Central work replaces with real dispatch.
#:
#: It exists because capability alone is not enough to route on. A customer
#: from a connector that declares the write but has no adapter must still be
#: refused — falling through to whichever book happens to be connected would
#: write the quote into a different system's ledger and invent the provenance
#: the customer's own row never recorded.
_QUOTE_ADAPTERS: frozenset[str] = frozenset(
    {ZOHO_CONNECTOR, "dynamics365", "acumatica"})


def book_for_customer(session: Session, organization_id: str,
                      customer: models.Customer) -> CustomerBook:
    """The one set of books this customer belongs to. Refuses to guess.

    Almost entirely a lookup rather than a search, because ``Customer`` already
    stores the identity triple ``domain/origin.py`` defines: connector, the
    connected company, and that system's own id. An estimate therefore goes to
    the company the customer was *imported from*, not to one matched by name —
    "ABC Industries" can exist in all three books and be three different
    customers, which is the reason that triple exists.

    Three things are refused rather than resolved. A connection that is
    disabled or gone cannot be written to. A company in another system holds no
    Zoho estimate, so it never stands in for one — counted as a Zoho company it
    both overstated the number in the refusal below and, where it was the only
    connected company, was returned as the book: ``credentials_for`` then
    refused it with a bare ``ValueError``, which the caller does not catch, so
    the answer arrived as a 500 instead of as this refusal. And a row whose
    ``connection_id`` is NULL — imported before provenance was recorded — is
    only placeable when the organization has a single connected company; with
    more than one, choosing would be inventing the provenance the column
    deliberately leaves blank.
    """
    enabled = {c.connection_id: c
               for c in list_connections(session, organization_id, enabled_only=True)}
    # Only a company this platform can write to can hold this quote. The rest
    # stay in ``enabled`` because they are still companies an unattributed
    # customer may have come from, which is the question the ambiguity check
    # below asks. Read through ``quote_writer_ready`` rather than compared
    # against a connector name, so a connector gaining a writer changes this
    # by declaration rather than by somebody remembering to edit it here.
    writable = {cid: c for cid, c in enabled.items()
                if quote_writer_ready(connector_of(c))}
    read_only = sorted({connector_of(c) for c in enabled.values()
                        if not quote_writer_ready(connector_of(c))})

    if customer.connector and not can_write_quotes(customer.connector):
        raise ConnectionNotFound(
            f"{customer.name} was imported from {customer.connector}, which "
            f"this platform reads but cannot create a quote in, so there is "
            f"nowhere to write this quote.")
    if customer.connector and customer.connector not in _QUOTE_ADAPTERS:
        # Permitted to write there, but nothing here knows how yet. Refused
        # rather than resolved: the books below are another system's, and
        # writing this quote into one of them would put it on a ledger this
        # customer was never imported from.
        raise ConnectionNotFound(
            f"{customer.name} was imported from {customer.connector}. Quotes "
            f"can be created there, but this platform has no writer for it "
            f"yet, and its quote cannot be written into another system.")

    if customer.connection_id:
        conn = writable.get(customer.connection_id)
        if conn is None:
            elsewhere = enabled.get(customer.connection_id)
            if elsewhere is not None:
                raise ConnectionNotFound(
                    f"The company {customer.name} came from is a "
                    f"{connector_of(elsewhere)} connection, which this platform "
                    f"reads but cannot create a quote in, so this quote cannot "
                    f"be written into it.")
            raise ConnectionNotFound(
                f"The company {customer.name} came from is no longer "
                f"connected or has been disabled, so there is no ledger to "
                f"write this quote into.")
        return CustomerBook(conn, str(customer.external_id))

    # Provenance not recorded. One connected company leaves nothing to choose
    # between; more than one is the case that must not be guessed.
    if not writable:
        raise ConnectionNotFound(
            f"This organization has no connected company this platform can "
            f"create a quote in, so there is nowhere to write {customer.name}'s "
            f"quote."
            + (f" Its connected books read {', '.join(read_only)}, which this "
               f"platform reads but cannot write to." if read_only else ""))
    # A missing contact id blocks every book equally, so it is answered before
    # the which-book question rather than after it. It used to fall through to
    # whichever ambiguity sentence came next, and with two Zoho companies that
    # sentence said the choice between them could not be decided — sending the
    # reader to compare two books when the blocker was that this customer has
    # no contact in either. The remedy happens to be the same re-sync, which is
    # exactly why the wrong reason survived: it "worked".
    if not customer.external_id:
        where = (next(iter(writable.values())).label or "the connected company"
                 if len(writable) == 1 else "any connected company")
        raise ConnectionNotFound(
            f"{customer.name} carries no contact id in {where}, so there is no "
            f"contact to write this quote against — re-sync the company this "
            f"customer belongs to.")
    if len(enabled) == 1:
        return CustomerBook(next(iter(writable.values())), str(customer.external_id))

    # Two different questions are being refused here, and they read as one only
    # if the count is the whole sentence. With several Zoho companies the
    # question really is *which one*. With a single Zoho company beside a book
    # in another system, there is nothing to choose between Zoho companies —
    # the question is whether this customer is a Zoho customer at all — and
    # naming a choice between one thing tells the reader to go looking for a
    # second set of Zoho books that does not exist.
    unrecorded = (f"{customer.name} was imported before the source company was "
                  f"recorded, and ")
    if len(writable) > 1:
        raise ConnectionNotFound(
            unrecorded
            + f"this organization has {len(writable)} connected companies a "
              f"quote can be created in. Which one this quote belongs to cannot "
              f"be decided from the quote alone — re-sync the company this "
              f"customer belongs to."
            + (f" It may equally have come from a book this organization reads "
               f"through {', '.join(read_only)}, which cannot hold a quote at "
               f"all." if read_only else ""))
    if read_only:
        raise ConnectionNotFound(
            unrecorded
            + f"this organization also reads {', '.join(read_only)}. This "
              f"customer may have come from there rather than from its one "
              f"connected company a quote can be created in, and a system this "
              f"platform cannot write to cannot be quoted into — so writing the "
              f"quote into that one would invent the provenance that was never "
              f"recorded. Re-sync the company this customer belongs to.")
    # One writable company, nothing else connected, a contact id present — and the
    # resolve above did not take it, which means ``enabled`` holds a disabled
    # or otherwise unusable row this function has not accounted for. Refused
    # rather than resolved: reaching here at all is a gap in the reasoning
    # above, and guessing a book to close it is how provenance gets invented.
    raise ConnectionNotFound(
        f"{customer.name} cannot be placed against a connected company "
        f"from what is recorded on it — re-sync the company this customer "
        f"belongs to.")


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
    # These two become server-side fetch targets the moment the connection is
    # checked or synced; refuse one that points back inside our own network.
    require_safe_source_url(accounts_base, field="Accounts URL")
    require_safe_source_url(api_base, field="API URL")
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
                   zoho_organization_id: str, label: str = "",
                   config: Optional[dict] = None) -> models.ZohoConnection:
    """Point this organization at another company through an existing grant.

    Which *system* that company lives in comes from the credential — a
    connection's connector is its credential's, always, and taking it as a
    separate parameter would make the mismatch expressible. For a non-Zoho
    connector, ``zoho_organization_id`` carries that system's company id (the
    column name is historical; the model docstring owns that decision) and
    ``config`` carries the connection's own settings.

    No secret is re-entered, so there is no second copy for a rotation to miss.
    A company already connected is updated rather than duplicated: two rows for
    one company would sync it twice and double every figure.
    """
    cred = get_credential(session, organization_id, credential_id)
    connector = getattr(cred, "connector", None) or ZOHO_CONNECTOR
    zoho_organization_id = zoho_organization_id.strip()

    existing = session.scalar(select(models.ZohoConnection).where(
        models.ZohoConnection.organization_id == organization_id,
        models.ZohoConnection.connector == connector,
        models.ZohoConnection.zoho_organization_id == zoho_organization_id))

    # The plan boundary, enforced where the violation would happen. A second
    # *distinct* company on one organization is what the platform plan is;
    # below it the licence unit is one company's books, and refusing here —
    # rather than auditing later — is what makes that a rule instead of a
    # suggestion. Updating a company already connected is never a new company.
    # Distinctness is the (connector, company) pair: a NetSuite book beside a
    # Zoho book is two companies, exactly as two Zoho books are.
    if existing is None:
        from sqlalchemy import or_

        from .. import entitlements

        others = session.scalar(select(models.ZohoConnection.connection_id).where(
            models.ZohoConnection.organization_id == organization_id,
            or_(models.ZohoConnection.connector != connector,
                models.ZohoConnection.zoho_organization_id != zoho_organization_id)))
        if others is not None:
            entitlements.assert_feature(session, organization_id, "multi_company")

    row = existing or models.ZohoConnection(organization_id=organization_id)
    if existing is None:
        session.add(row)

    row.connector = connector
    row.zoho_organization_id = zoho_organization_id
    row.credential_id = cred.credential_id
    row.label = (label or "").strip()[:255]
    if config is not None:
        row.config = dict(config)
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
    # Non-Zoho books key as "connector:company", because two systems may issue
    # the same id string and a NetSuite book must not find a Zoho book's trial
    # already spent. Zoho keys stay bare so existing trial rows keep matching.
    if existing is None:
        from .. import entitlements
        from ..attribution import capture_baseline

        books_key = (zoho_organization_id if connector == ZOHO_CONNECTOR
                     else f"{connector}:{zoho_organization_id}")
        trial = entitlements.begin_trial(session, organization_id, books_key)
        # "Better" needs a "before", and the only moment the before-window is
        # unambiguous is the moment the trial starts. Captured in the caller's
        # transaction — no commit here — so a connection is one atomic act.
        #
        # It will usually be captured *empty*, because nothing has synced yet at
        # first connect. That is the intended behaviour rather than a gap in it:
        # the baseline names its own missing evidence, the report then says the
        # comparison is unavailable instead of drawing one against a thin window,
        # and a later recompute rewrites the row in place. The trial row it
        # points at is the entitlement fact and is never rewritten, which is why
        # the two are separate tables.
        if trial is not None:
            try:
                # A savepoint, for the same reason ``begin_trial`` is documented
                # as never raising: connecting a company must not fail because a
                # measurement over it could not be taken. Without one, a rolled
                # back statement would poison the caller's transaction and take
                # the connection down with it.
                with session.begin_nested():
                    capture_baseline(session, organization_id, trial)
            except Exception:  # noqa: BLE001 - a baseline is never worth a 500
                log.exception(
                    "baseline capture failed org=%s trial=%s — the trial stands "
                    "and the report will name the missing baseline",
                    organization_id, trial.trial_id)
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
        # The create path validates inside create_credential; this branch sets
        # the hosts on an existing credential directly, so it must too.
        require_safe_source_url(accounts_base, field="Accounts URL")
        require_safe_source_url(api_base, field="API URL")
        cred.accounts_base = accounts_base
        cred.api_base = api_base
    return add_connection(
        session, organization_id, credential_id=cred.credential_id,
        zoho_organization_id=zoho_organization_id, label=label)


# Kept under its old name: callers that meant "connect this org" still work.
connect_with_credential = add_connection


# ── the registered ERP connectors ───────────────────────────────────────────
#
# One connect path for every non-Zoho system, driven by the connector's own
# spec (``ingestion/erp``): the spec says which entered values are secrets,
# the secrets travel as one encrypted JSON document, and everything below the
# credential row — sharing, delete-guard, the plan gate, the trial — is the
# same mechanism Zoho already exercises, because it is literally the same
# rows.

def _decrypt_secrets(cred: models.ZohoCredential) -> dict[str, str]:
    import json

    if not cred.secrets_encrypted:
        return {}
    loaded = json.loads(crypto.decrypt(cred.secrets_encrypted))
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def credential_material(session: Session, connection: models.ZohoConnection):
    """One non-Zoho connection row → the decrypted material its source needs.

    The counterpart of ``credentials_for``, for the connectors whose secret
    shape is the spec's rather than Zoho's OAuth triple. Never logged, never
    persisted; decryption failures propagate for the reason they do there —
    an unreadable secret is not an absent one.
    """
    from . import erp

    connector = getattr(connection, "connector", None) or ZOHO_CONNECTOR
    if connector == ZOHO_CONNECTOR:
        raise ValueError(
            "A Zoho connection resolves through credentials_for, not here.")
    cred = connection.credential
    if cred is None:
        raise CredentialNotUsable(
            "This connection has no stored credential to sign in with — "
            "reconnect it with the sign-in details.")
    if not cred.is_usable_by(connection.organization_id):
        raise CredentialNotUsable(
            f"Organization {connection.organization_id!r} is no longer "
            f"permitted to use credential {cred.credential_id!r}")
    return erp.CredentialMaterial(
        connector=connector,
        secrets=_decrypt_secrets(cred),
        config=dict(cred.config or {}),
        connection_config=dict(connection.config or {}),
        external_org_id=connection.zoho_organization_id,
    )


def build_erp_source(session: Session, connection: models.ZohoConnection,
                     since=None):
    """A live source for one non-Zoho connection — the registry's factory fed
    with this row's decrypted material. What ``get_source`` and the connect
    check both call, so the two can never build the client differently."""
    from . import erp

    material = credential_material(session, connection)
    return erp.get_spec(material.connector).build_source(material, since=since)


def find_matching_erp_credential(
        session: Session, organization_id: str, connector: str,
        secrets: dict[str, str],
        config: dict) -> Optional[models.ZohoCredential]:
    """An existing credential of this connector holding exactly these values —
    the same one-secret-one-row rule ``find_matching_credential`` keeps for
    Zoho, for the same rotation reason."""
    for cred in usable_credentials(session, organization_id):
        if (getattr(cred, "connector", None) or ZOHO_CONNECTOR) != connector:
            continue
        try:
            if (_decrypt_secrets(cred) == secrets
                    and dict(cred.config or {}) == dict(config)):
                return cred
        except Exception:  # noqa: BLE001 — an undecryptable row is not a match
            continue
    return None


def connect_erp(session: Session, organization_id: str, *, connector: str,
                values: dict, label: str = "",
                credential_label: str = "") -> models.ZohoConnection:
    """Connect one company of a registered ERP from its entered form values.

    Validation is the spec's (missing required fields refuse by label before
    anything is stored), identical secrets attach to the credential already on
    file, and the connection itself goes through ``add_connection`` so the
    duplicate-company update, the multi-company plan gate and the trial all
    apply exactly as they do to Zoho.
    """
    import json

    from . import erp

    spec = erp.get_spec(connector)
    # A connector's base_url is a server-side fetch target; refuse one aimed at
    # our own network before it is stored.
    require_safe_source_urls(values, label=spec.label)
    secrets, cred_config, conn_config, external = erp.split_inputs(spec, values)

    cred = find_matching_erp_credential(session, organization_id, connector,
                                        secrets, cred_config)
    if cred is None:
        # Not create_credential: that function is the Zoho OAuth shape (typed
        # columns, accounts/api hosts) and this is the spec shape (one
        # encrypted document). Same table, same lifecycle, different halves
        # of the row.
        cred = models.ZohoCredential(
            owner_organization_id=organization_id,
            connector=connector,
            label=(credential_label or label or spec.label)[:255],
            secrets_encrypted=crypto.encrypt(
                json.dumps(secrets, sort_keys=True)),
            config=cred_config,
            shared_with_organization_ids=[],
            rotated_at=datetime.now(timezone.utc),
        )
        session.add(cred)
        session.flush()
    return add_connection(session, organization_id,
                          credential_id=cred.credential_id,
                          zoho_organization_id=external, label=label,
                          config=conn_config)


def rotate_erp_credential(session: Session, organization_id: str,
                          credential_id: str, *,
                          values: dict) -> models.ZohoCredential:
    """Replace a registered connector's secrets. Every connection follows —
    the same one-operation rotation the credential split exists for, with the
    same owner-only rule ``rotate_credential`` enforces."""
    import json

    from . import erp

    cred = session.get(models.ZohoCredential, credential_id)
    if cred is None or cred.owner_organization_id != organization_id:
        raise CredentialNotUsable(
            "Only the organization that owns a credential can rotate it")
    spec = erp.get_spec(getattr(cred, "connector", None) or "")
    require_safe_source_urls(values, label=spec.label)
    secrets, cred_config = erp.split_credential_inputs(spec, values)
    cred.secrets_encrypted = crypto.encrypt(json.dumps(secrets, sort_keys=True))
    cred.config = cred_config
    cred.rotated_at = datetime.now(timezone.utc)
    session.flush()
    return cred


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
